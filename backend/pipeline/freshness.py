"""Freshness jobs (event-schema skill, "Freshness").

- expire_ended (nightly): events whose end (or start + 1 day) has passed -> 'expired'.
- recheck_missing: events whose primary source stopped listing them for 3 consecutive
  successful crawls are re-fetched; 404/410 -> 'cancelled'. Only for adapters that define
  check_gone (see scrapers.base); others' events just expire when they end.
"""

import logging
from collections import Counter
from collections.abc import Mapping

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CrawlRun, Event
from scrapers.base import SourceAdapter, SourceBlocked
from scrapers.http import PoliteClient

logger = logging.getLogger(__name__)

MISSED_CRAWLS = 3


async def expire_ended(session: AsyncSession) -> int:
    rows = await session.execute(
        text(
            """
            UPDATE events SET status = 'expired', status_reason = 'ended'
            WHERE status = 'active'
              AND coalesce(ends_at, starts_at + interval '1 day') < now()
            RETURNING id
            """
        )
    )
    expired = len(rows.all())
    await session.commit()
    if expired:
        logger.info("expired %d ended events", expired)
    return expired


_MISSING_SQL = text(
    """
    SELECT e.id, s.source_url
    FROM events e
    JOIN event_sources s ON s.event_id = e.id AND s.source_id = :source_id
    WHERE e.status = 'active'
      AND coalesce(e.ends_at, e.starts_at) >= now()
      AND s.last_seen_at < :cutoff
      AND NOT EXISTS (   -- still listed somewhere else: not missing
            SELECT 1 FROM event_sources o WHERE o.event_id = e.id AND o.last_seen_at >= :cutoff)
    ORDER BY e.id
    LIMIT :limit
    """
)


async def recheck_missing(
    session: AsyncSession,
    adapters: Mapping[str, SourceAdapter],
    client: PoliteClient,
    *,
    limit_per_source: int = 50,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    for source_id, adapter in adapters.items():
        check_gone = getattr(adapter, "check_gone", None)
        if check_gone is None:
            continue
        starts = (
            await session.scalars(
                select(CrawlRun.started_at)
                .where(CrawlRun.source_id == source_id, CrawlRun.status == "ok")
                .order_by(CrawlRun.started_at.desc())
                .limit(MISSED_CRAWLS)
            )
        ).all()
        if len(starts) < MISSED_CRAWLS:
            continue  # not enough history to call anything missing
        cutoff = starts[-1]
        rows = (
            await session.execute(_MISSING_SQL, {"source_id": source_id, "cutoff": cutoff, "limit": limit_per_source})
        ).all()
        for event_id, url in rows:
            try:
                gone = await check_gone(client, url)
            except SourceBlocked:
                logger.error("%s blocked during re-check; stopping", source_id)
                break
            except Exception:  # noqa: BLE001
                logger.exception("re-check of %s failed", url)
                counts["recheck_error"] += 1
                continue
            counts["rechecked"] += 1
            if gone:
                await session.execute(
                    update(Event)
                    .where(Event.id == event_id)
                    .values(status="cancelled", status_reason=f"{source_id} page gone (404/410)")
                )
                counts["cancelled"] += 1
        await session.commit()
    return counts


async def http_gone(client: PoliteClient, url: str, *, source_id: str) -> bool:
    """A check_gone helper for adapters whose event pages may be re-fetched."""
    return (await client.get(url, source_id=source_id)).status in (404, 410)

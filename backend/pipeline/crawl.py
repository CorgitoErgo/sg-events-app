"""Run one source end to end and record it in crawl_runs. Used by the CLI and the worker.

fetch (adapter + PoliteClient) -> normalize -> store -> enrich -> record -> monitor.
One failing page doesn't stop the run; a blocked source stops it (never work around blocks).
"""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CrawlRun
from pipeline.normalize import NormalizationError, NormalizedEvent, normalize
from pipeline.store import upsert_event
from scrapers.base import RawEvent, SourceAdapter, SourceBlocked
from scrapers.http import PoliteClient

logger = logging.getLogger(__name__)


@dataclass
class Collected:
    raws: list[RawEvent]
    urls_fetched: int
    urls_failed: int
    blocked: str | None = None


@dataclass
class CrawlResult:
    run_id: int
    source_id: str
    status: str
    collected: Collected
    normalize_errors: int
    stored: Counter[str]
    enriched: Counter[str]
    alerts: list[str] = field(default_factory=list)
    error: str | None = None


async def collect(adapter: SourceAdapter, client: PoliteClient, limit: int | None = None) -> Collected:
    """discover + fetch_and_parse; one RawEvent per source_url (source tags merged)."""
    by_url: dict[str, RawEvent] = {}
    failed = 0
    blocked = None
    start_ok, start_failed = client.stats["ok"], client.stats["failed"]
    async for url in adapter.discover(client):
        try:
            raws = await adapter.fetch_and_parse(client, url)
        except SourceBlocked as exc:
            logger.error("%s blocked (%s); stopping this source", adapter.source_id, exc.reason)
            blocked = exc.reason
            break
        except Exception:  # noqa: BLE001 - one bad page mustn't stop the source
            logger.exception("%s: failed on %s", adapter.source_id, url)
            failed += 1
            continue
        for raw in raws:
            seen = by_url.setdefault(raw.source_url, raw)
            if seen is not raw:
                seen.source_categories += [c for c in raw.source_categories if c not in seen.source_categories]
        if limit and len(by_url) >= limit:
            break
    events = list(by_url.values())
    return Collected(
        raws=events[:limit] if limit else events,
        urls_fetched=client.stats["ok"] - start_ok,
        urls_failed=client.stats["failed"] - start_failed + failed,
        blocked=blocked,
    )


def normalize_all(raws: list[RawEvent]) -> tuple[list[NormalizedEvent], int]:
    out, errors = [], 0
    for raw in raws:
        try:
            out.append(normalize(raw))
        except NormalizationError as exc:
            logger.warning("skipped %s: %s", raw.source_url, exc)
            errors += 1
    return out, errors


async def store_events(session: AsyncSession, events: list[NormalizedEvent]) -> tuple[Counter[str], list[int]]:
    counts: Counter[str] = Counter()
    new_ids: list[int] = []
    for ev in events:
        try:
            async with session.begin_nested():  # one bad event doesn't sink the batch
                event_id, action = await upsert_event(session, ev)
            counts[action] += 1
            if action == "inserted":
                new_ids.append(event_id)
        except Exception:  # noqa: BLE001
            logger.exception("failed to store %s", ev.source_url)
            counts["error"] += 1
    await session.commit()
    return counts, new_ids


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
ClientFactory = Callable[[], AbstractAsyncContextManager[PoliteClient]]
Enricher = Callable[[AsyncSession, list[int]], Awaitable[Counter[str]]]
Alerter = Callable[[str], Awaitable[None]]


async def crawl_source(
    adapter: SourceAdapter,
    *,
    session_factory: SessionFactory,
    client_factory: ClientFactory = PoliteClient,
    enricher: Enricher | None = None,
    alerter: Alerter | None = None,
    limit: int | None = None,
) -> CrawlResult:
    from pipeline.monitor import check_run, send_alert

    alerter = alerter or send_alert
    async with session_factory() as session:
        run = CrawlRun(source_id=adapter.source_id)
        session.add(run)
        await session.commit()

        collected = Collected([], 0, 0)
        normalize_errors = 0
        stored: Counter[str] = Counter()
        enriched: Counter[str] = Counter()
        status, error = "ok", None
        try:
            async with client_factory() as client:
                collected = await collect(adapter, client, limit)
            if collected.blocked:
                status, error = "blocked", collected.blocked
            events, normalize_errors = normalize_all(collected.raws)
            stored, new_ids = await store_events(session, events)
            if enricher is not None:
                enriched = await enricher(session, new_ids)
        except Exception as exc:  # noqa: BLE001 - recorded and alerted, not swallowed silently
            logger.exception("%s: crawl failed", adapter.source_id)
            await session.rollback()
            status, error = "failed", repr(exc)

        run.finished_at = datetime.now(UTC)
        run.status = status
        run.error = error
        run.urls_fetched = collected.urls_fetched
        run.urls_failed = collected.urls_failed
        run.events_found = len(collected.raws)
        run.normalize_errors = normalize_errors
        run.events_stored = sum(v for k, v in stored.items() if k != "error")
        run.store_errors = stored["error"]
        session.add(run)
        await session.commit()

        alerts = await check_run(session, run)
        for message in alerts:
            await alerter(message)
        return CrawlResult(
            run.id, adapter.source_id, status, collected, normalize_errors, stored, enriched, alerts, error
        )

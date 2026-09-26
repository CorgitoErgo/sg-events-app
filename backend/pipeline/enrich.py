"""Enrichment after events are stored: geocode venues -> dedup new events -> classify -> embed.

Dedup runs before classification so duplicates never cost an LLM call; embedding runs last
because its text includes the categories and summary. Each step skips
itself when its credentials are missing (see pipeline.clients).

    uv run python -m pipeline.enrich              # everything that needs enriching
    uv run python -m pipeline.enrich --dedup-all  # also re-check all upcoming events for duplicates
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter
from collections.abc import Iterable
from contextlib import AsyncExitStack

import anthropic
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from db.models import Event, Venue
from pipeline.classify import (
    MANUAL_HASH,
    Classification,
    ClassificationError,
    EventText,
    classify_with_llm,
    enrichment_hash,
    fallback_categories,
    rule_classification,
)
from pipeline.clients import make_llm, make_onemap, make_voyage
from pipeline.dedup import find_duplicate, llm_judge, merge_events
from pipeline.embed import VoyageClient, embed_events
from pipeline.geo.geocode import VenueQuery, geocode
from pipeline.geo.onemap import OneMapClient, OneMapError
from pipeline.sg import region_for
from pipeline.store import wkt_point

logger = logging.getLogger(__name__)

LLM_CONCURRENCY = 4
GEOCODE_BATCH = 200


async def enrich(
    session: AsyncSession,
    *,
    new_event_ids: Iterable[int] = (),
    llm: anthropic.AsyncAnthropic | None,
    onemap: OneMapClient | None,
    model: str,
    voyage: VoyageClient | None = None,
) -> Counter[str]:
    counts: Counter[str] = Counter()
    if onemap is not None:
        await geocode_venues(session, onemap, counts)
        await session.commit()
    await dedup_events(session, new_event_ids, llm=llm, model=model, counts=counts)
    await session.commit()
    await classify_events(session, llm=llm, model=model, counts=counts)
    await session.commit()
    if voyage is not None:
        await embed_events(session, voyage, counts)
        await session.commit()
    return counts


async def enrich_with_configured_clients(session: AsyncSession, new_event_ids: Iterable[int]) -> Counter[str]:
    settings = get_settings()
    async with AsyncExitStack() as stack:
        llm = make_llm(settings)
        if llm is not None:
            stack.push_async_callback(llm.close)
        onemap = make_onemap(settings)
        if onemap is not None:
            await stack.enter_async_context(onemap)
        voyage = make_voyage(settings)
        if voyage is not None:
            await stack.enter_async_context(voyage)
        return await enrich(
            session,
            new_event_ids=new_event_ids,
            llm=llm,
            onemap=onemap,
            voyage=voyage,
            model=settings.anthropic_fast_model,
        )


# --- geocoding ---------------------------------------------------------------------------------

async def geocode_venues(
    session: AsyncSession,
    onemap: OneMapClient,
    counts: Counter[str],
    *,
    only_ids: Iterable[int] | None = None,
) -> None:
    query = select(Venue).where(Venue.geocoded_at.is_(None)).order_by(Venue.id).limit(GEOCODE_BATCH)
    if only_ids is not None:
        query = query.where(Venue.id.in_(list(only_ids)))
    venues = (await session.scalars(query)).all()
    for venue in venues:
        try:
            await _geocode_venue(session, onemap, venue, counts)
        except OneMapError as exc:
            logger.warning("geocoding venue %s (%r) failed: %s", venue.id, venue.name, exc)
            counts["geocode_error"] += 1
            continue  # geocoded_at stays NULL: retried next run
        venue.geocoded_at = func.now()
        await session.flush()


async def _geocode_venue(
    session: AsyncSession, onemap: OneMapClient, venue: Venue, counts: Counter[str]
) -> None:
    point = await _venue_point(session, venue.id)
    if point is None:
        cached = await session.scalar(
            select(Venue)
            .where(
                Venue.id != venue.id,
                Venue.geom.is_not(None),
                or_(
                    and_(Venue.postal_code.is_not(None), Venue.postal_code == venue.postal_code),
                    func.lower(Venue.name) == venue.name.lower(),
                ),
            )
            .limit(1)
        )
        if cached is not None:
            venue.geom, venue.geocode_source = cached.geom, cached.geocode_source
            venue.planning_area, venue.region = cached.planning_area, cached.region
            counts["geocode_cache_hit"] += 1
        else:
            hit = await geocode(onemap, VenueQuery(venue.name, venue.address, venue.postal_code))
            if hit is None:
                logger.info("no OneMap match for venue %s %r; fix it in the venues table", venue.id, venue.name)
                counts["geocode_miss"] += 1
                await session.execute(
                    update(Event)
                    .where(Event.venue_id == venue.id, Event.geom.is_(None), Event.confidence == "high")
                    .values(confidence="medium")
                )
                return
            venue.geom, venue.geocode_source = wkt_point(hit.lat, hit.lng), "onemap"
            counts["geocoded"] += 1
        await session.flush()
        await session.execute(
            update(Event)
            .where(Event.venue_id == venue.id, Event.geom.is_(None))
            .values(geom=select(Venue.geom).where(Venue.id == venue.id).scalar_subquery())
        )
        point = await _venue_point(session, venue.id)

    if venue.planning_area is None and point is not None:
        area = await onemap.planning_area(*point)
        venue.planning_area, venue.region = area, region_for(area)
        if area:
            counts["planning_area"] += 1


async def _venue_point(session: AsyncSession, venue_id: int) -> tuple[float, float] | None:
    row = (
        await session.execute(
            text("SELECT ST_Y(geom::geometry), ST_X(geom::geometry) FROM venues WHERE id = :id AND geom IS NOT NULL"),
            {"id": venue_id},
        )
    ).first()
    return (row[0], row[1]) if row else None


# --- dedup --------------------------------------------------------------------------------------

async def dedup_events(
    session: AsyncSession,
    event_ids: Iterable[int],
    *,
    llm: anthropic.AsyncAnthropic | None,
    model: str,
    counts: Counter[str],
) -> None:
    judge = llm_judge(llm, model=model) if llm is not None else None
    for event_id in event_ids:
        if await session.get(Event, event_id) is None:
            continue  # already merged into another event
        other = await find_duplicate(session, event_id, judge=judge)
        if other is not None:
            await merge_events(session, event_id, other)
            counts["merged"] += 1


# --- classification -------------------------------------------------------------------------

_UPCOMING_EVENTS_SQL = text(
    """
    SELECT e.id, e.title, e.description, e.organizer, v.name AS venue,
           e.enrichment_hash, e.categories, e.audience,
           coalesce((SELECT array_agg(DISTINCT s.source_id ORDER BY s.source_id)
                     FROM event_sources s WHERE s.event_id = e.id), '{}') AS source_ids,
           coalesce((SELECT array_agg(DISTINCT t ORDER BY t)
                     FROM event_sources s, unnest(s.source_tags) t WHERE s.event_id = e.id), '{}') AS source_tags
    FROM events e
    LEFT JOIN venues v ON v.id = e.venue_id
    WHERE e.status = 'active' AND coalesce(e.ends_at, e.starts_at) >= now()
      AND (CAST(:only_ids AS bigint[]) IS NULL OR e.id = ANY(CAST(:only_ids AS bigint[])))
    ORDER BY e.id
    """
)


async def classify_events(
    session: AsyncSession,
    *,
    llm: anthropic.AsyncAnthropic | None,
    model: str,
    counts: Counter[str],
    only_ids: Iterable[int] | None = None,
) -> None:
    ids = list(only_ids) if only_ids is not None else None
    jobs = []
    for row in (await session.execute(_UPCOMING_EVENTS_SQL, {"only_ids": ids})).all():
        event = EventText(
            title=row.title,
            description=row.description,
            organizer=row.organizer,
            venue=row.venue,
            source_ids=tuple(row.source_ids),
            source_tags=tuple(row.source_tags),
        )
        digest = enrichment_hash(event, model)
        if row.enrichment_hash in (digest, MANUAL_HASH):
            continue  # unchanged, or categories chosen by a person
        if (by_rules := rule_classification(event)) is not None:
            await _apply(session, row, by_rules, digest)
            counts["classified_by_rules"] += 1
        elif llm is None:
            # Rules-only placeholder; never overwrite an earlier LLM result.
            categories = fallback_categories(event)
            if row.enrichment_hash is None and categories and categories != list(row.categories):
                await session.execute(update(Event).where(Event.id == row.id).values(categories=categories))
                counts["classified_by_rules"] += 1
        else:
            jobs.append((row, event, digest))
    if not jobs:
        return

    semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    results: list[tuple[object, Classification | None, Exception | None, str]] = []

    async def run(row, event: EventText, digest: str) -> None:
        async with semaphore:
            try:
                results.append((row, await classify_with_llm(llm, event, model=model), None, digest))
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
                raise  # bad key: stop the whole batch
            except (ClassificationError, anthropic.APIError) as exc:
                results.append((row, None, exc, digest))

    try:
        async with asyncio.TaskGroup() as tg:
            for job in jobs:
                tg.create_task(run(*job))
    except* (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as group:
        logger.error("Anthropic rejected the API key (%r); classification skipped", group.exceptions[0])
        counts["classify_auth_error"] += 1

    for row, result, exc, digest in results:
        if result is None:
            logger.warning("classifying event %s failed: %r", row.id, exc)
            counts["classify_error"] += 1
            continue
        if result.confidence == "low":
            logger.info("low-confidence categories %s for event %s %r", result.categories, row.id, row.title)
        await _apply(session, row, result, digest)
        counts["classified_by_llm"] += 1


async def _apply(session: AsyncSession, row, result: Classification, digest: str) -> None:
    values: dict[str, object] = {"categories": result.categories, "enrichment_hash": digest}
    if result.summary:
        values["summary"] = result.summary
    if row.audience in (None, "public") and result.audience != "public":
        values["audience"] = result.audience
    await session.execute(update(Event).where(Event.id == row.id).values(**values))


# --- CLI -----------------------------------------------------------------------------------------

async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dedup-all", action="store_true", help="check every upcoming event for duplicates")
    args = parser.parse_args(argv)

    from db.session import SessionLocal, engine

    try:
        async with SessionLocal() as session:
            ids: list[int] = []
            if args.dedup_all:
                ids = list(
                    await session.scalars(
                        select(Event.id)
                        .where(Event.status == "active", func.coalesce(Event.ends_at, Event.starts_at) >= func.now())
                        .order_by(Event.id)
                    )
                )
            counts = await enrich_with_configured_clients(session, ids)
    finally:
        await engine.dispose()
    print("enriched: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to do"))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))

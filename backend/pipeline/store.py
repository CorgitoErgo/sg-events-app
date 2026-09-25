"""Persist NormalizedEvents.

Matching order (event-schema skill, Dedup steps 1-2):
1. Exact: same (source_id, source_url), or same (source_id, source_event_id) -> update in place.
2. Fingerprint: same fingerprint -> attach this source to the existing event, fill gaps only.
3. Otherwise insert.
Fuzzy cross-source matching and source-priority merging come with dedup in step 3.
"""

import logging
from typing import Literal

from geoalchemy2 import WKTElement
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Event, EventSource, Venue
from pipeline.normalize import NormalizedEvent, NormalizedVenue

logger = logging.getLogger(__name__)

Action = Literal["inserted", "updated", "attached"]

# Fields a source owns; `categories` and `summary` belong to the classifier.
_EVENT_FIELDS = (
    "title", "description", "starts_at", "ends_at", "all_day", "is_online",
    "price_min_sgd", "price_max_sgd", "is_free", "language", "organizer",
    "image_url", "registration_url", "confidence", "status",
)  # fmt: skip


def wkt_point(lat: float | None, lng: float | None) -> WKTElement | None:
    if lat is None or lng is None:
        return None
    return WKTElement(f"POINT({lng} {lat})", srid=4326)  # WKT is (lng lat)


async def upsert_event(session: AsyncSession, ev: NormalizedEvent) -> tuple[int, Action]:
    source = await session.get(EventSource, (ev.source_id, ev.source_url))
    if source is None and ev.source_event_id:
        source = await session.scalar(
            select(EventSource)
            .where(
                EventSource.source_id == ev.source_id,
                EventSource.source_event_id == ev.source_event_id,
            )
            .limit(1)
        )
    venue_id = await _venue_id(session, ev.venue)

    if source is not None:
        event = await session.get(Event, source.event_id)
        assert event is not None  # FK guarantees it
        _apply(event, ev, venue_id)
        await _maybe_update_fingerprint(session, event, ev.fingerprint)
        event.last_seen_at = func.now()
        if source.source_url == ev.source_url:
            source.source_event_id = ev.source_event_id
            source.source_tags = ev.source_tags
            source.raw_payload = ev.raw_payload
            source.last_seen_at = func.now()
        else:  # same source event, new URL: keep both links
            session.add(_source_row(event.id, ev))
        await session.flush()
        return event.id, "updated"

    event = await session.scalar(select(Event).where(Event.fingerprint == ev.fingerprint))
    if event is not None:
        _fill_missing(event, ev, venue_id)
        event.last_seen_at = func.now()
        action: Action = "attached"
    else:
        event = Event(fingerprint=ev.fingerprint)
        _apply(event, ev, venue_id)
        session.add(event)
        await session.flush()  # assigns event.id
        action = "inserted"
    session.add(_source_row(event.id, ev))
    await session.flush()
    return event.id, action


def _apply(event: Event, ev: NormalizedEvent, venue_id: int | None) -> None:
    for name in _EVENT_FIELDS:
        setattr(event, name, getattr(ev, name))
    # The normalizer only spots explicit restrictions; "public" there means "none found",
    # so it must not undo a restriction the classifier detected.
    if ev.audience != "public" or event.audience is None:
        event.audience = ev.audience
    event.venue_id = venue_id
    event.geom = wkt_point(ev.lat, ev.lng)


def _fill_missing(event: Event, ev: NormalizedEvent, venue_id: int | None) -> None:
    for name in _EVENT_FIELDS:
        if getattr(event, name) is None and getattr(ev, name) is not None:
            setattr(event, name, getattr(ev, name))
    if event.venue_id is None and venue_id is not None:
        event.venue_id = venue_id
    if event.geom is None and ev.lat is not None:
        event.geom = wkt_point(ev.lat, ev.lng)


async def _maybe_update_fingerprint(session: AsyncSession, event: Event, new: str) -> None:
    """A retitled or moved event gets a new fingerprint, unless another event already has it."""
    if new == event.fingerprint:
        return
    clash = await session.scalar(select(Event.id).where(Event.fingerprint == new))
    if clash is None:
        event.fingerprint = new
    else:
        logger.info("event %s now matches event %s by fingerprint; left for dedup", event.id, clash)


def _source_row(event_id: int, ev: NormalizedEvent) -> EventSource:
    return EventSource(
        event_id=event_id,
        source_id=ev.source_id,
        source_url=ev.source_url,
        source_event_id=ev.source_event_id,
        source_tags=ev.source_tags,
        raw_payload=ev.raw_payload,
    )


async def _venue_id(session: AsyncSession, venue: NormalizedVenue | None) -> int | None:
    if venue is None:
        return None
    venue_id = await session.scalar(
        insert(Venue)
        .values(
            name=venue.name,
            address=venue.address,
            postal_code=venue.postal_code,
            geom=wkt_point(venue.lat, venue.lng),
            geocode_source="source" if venue.lat is not None else None,
        )
        .on_conflict_do_nothing(constraint="venues_name_postal_code_key")
        .returning(Venue.id)
    )
    if venue_id is None:  # already known
        venue_id = await session.scalar(
            select(Venue.id).where(
                Venue.name == venue.name,
                Venue.postal_code.is_not_distinct_from(venue.postal_code),
            )
        )
    return venue_id

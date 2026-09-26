"""Save one RawEvent end to end: normalize -> store -> geocode -> dedup -> classify -> embed.

Shared by the admin console (events added by hand) and the discovery agent (events found
by auto-search), so both get exactly the same treatment as crawled events.
"""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import AppClients
from app.config import get_settings
from db.models import Event
from pipeline.classify import MANUAL_HASH
from pipeline.dedup import find_duplicate, llm_judge, merge_events
from pipeline.embed import embed_events
from pipeline.enrich import classify_events, geocode_venues
from pipeline.normalize import NormalizedEvent, normalize
from pipeline.store import upsert_event
from scrapers.base import RawEvent


@dataclass
class IngestResult:
    event_id: int
    action: str  # inserted | updated | attached
    merged_into: int | None = None
    enriched: Counter[str] = field(default_factory=Counter)


async def ingest(
    session: AsyncSession,
    raw: RawEvent,
    clients: AppClients,
    *,
    categories: list[str] | None = None,
    news: bool = False,
    normalized: NormalizedEvent | None = None,
) -> IngestResult:
    """Raises pipeline.normalize.NormalizationError when the event has no usable start."""
    ev = normalized or normalize(raw)
    if news:
        ev.description = None  # news: facts and link only (CLAUDE.md)
        ev.confidence = "low"

    event_id, action = await upsert_event(session, ev)
    if categories:
        await session.execute(
            update(Event).where(Event.id == event_id).values(categories=categories[:3], enrichment_hash=MANUAL_HASH)
        )
    await session.commit()

    settings = get_settings()
    counts: Counter[str] = Counter()
    event = await session.get(Event, event_id)
    if clients.onemap is not None and event.venue_id is not None:
        await geocode_venues(session, clients.onemap, counts, only_ids=[event.venue_id])
        await session.commit()

    merged_into = None
    judge = llm_judge(clients.llm, model=settings.anthropic_fast_model) if clients.llm is not None else None
    if (other := await find_duplicate(session, event_id, judge=judge)) is not None:
        merged_into = event_id = await merge_events(session, event_id, other)
        await session.commit()

    await classify_events(session, llm=clients.llm, model=settings.anthropic_fast_model, counts=counts, only_ids=[event_id])
    await session.commit()
    if clients.voyage is not None:
        await embed_events(session, clients.voyage, counts, only_ids=[event_id])
        await session.commit()
    return IngestResult(event_id, action, merged_into, counts)

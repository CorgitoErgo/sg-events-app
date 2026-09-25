"""pipeline.store against the local Postgres (docker compose). Skipped if it isn't running.

Each test runs inside a transaction that is rolled back, so real data is untouched.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from db.models import Event, EventSource, Venue
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from scrapers.base import RawEvent

pytestmark = pytest.mark.anyio

FETCHED = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)


@pytest.fixture
async def session():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        conn = await engine.connect()
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc!r}")
    trans = await conn.begin()
    if await conn.scalar(text("SELECT to_regclass('public.events')")) is None:
        await trans.rollback()
        await conn.close()
        await engine.dispose()
        pytest.skip("schema not migrated (run alembic upgrade head)")

    s = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield s
    finally:
        await s.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


def raw(**kwargs) -> RawEvent:
    tag = uuid.uuid4().hex[:8]  # unique titles so tests never collide with real rows
    defaults = dict(
        source_id="test",
        source_url=f"https://example.com/e/{tag}",
        source_event_id=f"evt-{tag}",
        title=f"Career Fair {tag}",
        fetched_at=FETCHED,
        start_raw="20261003T020000Z",
        end_raw="20261003T080000Z",
        venue_raw=f"Test Hall {tag}",
        address_raw="1 Tampines Walk, Singapore 528523",
        postal_code="528523",
        lat=1.3533,
        lng=103.9404,
        organizer="e2i",
    )
    return RawEvent(**{**defaults, **kwargs})


async def count(session: AsyncSession, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def test_insert_then_same_source_updates_in_place(session):
    r = raw()
    event_id, action = await upsert_event(session, normalize(r))
    assert action == "inserted"

    r.description = "Now with 40 employers."
    again_id, action = await upsert_event(session, normalize(r))
    assert (again_id, action) == (event_id, "updated")
    assert await count(session, EventSource, EventSource.event_id == event_id) == 1

    event = await session.get(Event, event_id, populate_existing=True)
    assert event.description == "Now with 40 employers."
    assert event.venue_id is not None and event.geom is not None


async def test_same_fingerprint_from_another_source_attaches(session):
    r = raw()
    event_id, _ = await upsert_event(session, normalize(r))

    other = raw(
        source_id="other_site",
        source_url="https://other.example.sg/fair",
        source_event_id=None,
        title=f"{r.title} 2026 @ Singapore",  # normalizes to the same title
        description="Extra detail only the other site has.",
    )
    other_id, action = await upsert_event(session, normalize(other))
    assert (other_id, action) == (event_id, "attached")
    assert await count(session, EventSource, EventSource.event_id == event_id) == 2

    event = await session.get(Event, event_id, populate_existing=True)
    assert event.description == "Extra detail only the other site has."  # gap filled


async def test_venue_is_created_once_and_reused(session):
    first = raw()
    second = raw(venue_raw=first.venue_raw)  # different event, same venue
    await upsert_event(session, normalize(first))
    await upsert_event(session, normalize(second))
    assert await count(session, Venue, Venue.name == first.venue_raw) == 1


async def test_search_tsv_is_maintained(session):
    event_id, _ = await upsert_event(session, normalize(raw()))
    matches = await session.scalar(
        text("SELECT search_tsv @@ to_tsquery('english', 'career & fair') FROM events WHERE id = :id"),
        {"id": event_id},
    )
    assert matches is True


async def test_retitled_event_gets_new_fingerprint(session):
    r = raw()
    event_id, _ = await upsert_event(session, normalize(r))
    old_fp = (await session.get(Event, event_id)).fingerprint

    r.title = f"{r.title} (Hiring Now)"
    await upsert_event(session, normalize(r))
    event = await session.get(Event, event_id, populate_existing=True)
    assert event.fingerprint != old_fp

"""Fuzzy dedup against the local Postgres (rolled back). Skipped if the DB isn't running."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Event, EventSource
from pipeline.dedup import find_duplicate, merge_events, title_similarity
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from tests.factories import make_raw

pytestmark = pytest.mark.anyio


async def add(session: AsyncSession, **kwargs) -> int:
    event_id, action = await upsert_event(session, normalize(make_raw(**kwargs)))
    assert action == "inserted"
    return event_id


async def sources_of(session: AsyncSession, event_id: int) -> list[str]:
    rows = await session.scalars(select(EventSource.source_id).where(EventSource.event_id == event_id))
    return sorted(rows)


def test_title_similarity_bands():
    assert title_similarity("NTUC Career Fair @ Our Tampines Hub", "Career Fair at Our Tampines Hub by e2i") >= 85
    assert 70 <= title_similarity("Tampines Career Fair", "Tampines Jobs Fair") < 85
    assert title_similarity("Tampines Career Fair", "Beach Clean-up at Changi") < 70


async def test_same_event_from_two_sources_is_found_and_merged(db_session):
    luma = await add(db_session, source_id="luma", title="NTUC Career Fair @ Our Tampines Hub",
                     description="Meet 40 employers.")
    other = await add(db_session, source_id="news_cna", title="Career Fair at Our Tampines Hub by e2i",
                      hour_utc=3, image_url="https://img.example/fair.jpg")  # an hour later, same day

    assert await find_duplicate(db_session, other) == luma
    kept = await merge_events(db_session, other, luma)

    assert kept == luma  # ticketing platform (tier 2) outranks news (default tier 3)
    assert await db_session.get(Event, other) is None
    assert await sources_of(db_session, kept) == ["luma", "news_cna"]  # no source link lost
    event = await db_session.get(Event, kept, populate_existing=True)
    assert event.image_url == "https://img.example/fair.jpg"  # gap filled from the other record
    assert event.description == "Meet 40 employers."


async def test_one_source_never_duplicates_itself(db_session):
    await add(db_session, source_id="luma", title="NTUC Career Fair @ Our Tampines Hub")
    second = await add(db_session, source_id="luma", title="Career Fair at Our Tampines Hub by e2i")
    assert await find_duplicate(db_session, second) is None


async def test_different_events_at_the_same_place_are_kept_apart(db_session):
    await add(db_session, source_id="luma", title="Tampines Career Fair")
    other = await add(db_session, source_id="other", title="Beach Clean-up at Changi")
    assert await find_duplicate(db_session, other) is None


async def test_far_apart_events_are_not_candidates(db_session):
    await add(db_session, source_id="luma", title="NTUC Career Fair @ Our Tampines Hub")
    far = await add(db_session, source_id="other", title="Career Fair at Our Tampines Hub by e2i",
                    postal_code="119077", address_raw="NUS, Singapore 119077", lat=1.2966, lng=103.7764)
    assert await find_duplicate(db_session, far) is None


async def test_different_days_are_not_candidates(db_session):
    await add(db_session, source_id="luma", title="NTUC Career Fair @ Our Tampines Hub", days_ahead=7)
    later = await add(db_session, source_id="other", title="Career Fair at Our Tampines Hub by e2i", days_ahead=9)
    assert await find_duplicate(db_session, later) is None


async def test_borderline_titles_go_to_the_judge(db_session):
    first = await add(db_session, source_id="luma", title="Tampines Career Fair")
    second = await add(db_session, source_id="other", title="Tampines Jobs Fair")
    asked = []

    async def yes(a, b):
        asked.append((a.title, b.title))
        return True

    async def no(a, b):
        return False

    assert await find_duplicate(db_session, second) is None  # no judge: leave it
    assert await find_duplicate(db_session, second, judge=no) is None
    assert await find_duplicate(db_session, second, judge=yes) == first
    assert asked == [("Tampines Jobs Fair", "Tampines Career Fair")]


async def test_merge_keeps_sessions_and_categories(db_session):
    a = await add(db_session, source_id="luma", title="NTUC Career Fair @ Our Tampines Hub")
    b = await add(db_session, source_id="other", title="Career Fair at Our Tampines Hub by e2i")
    (await db_session.get(Event, a)).categories = ["career_fair"]
    (await db_session.get(Event, b)).categories = ["career_fair", "career_dev"]
    await db_session.flush()

    kept = await merge_events(db_session, a, b)
    event = await db_session.get(Event, kept, populate_existing=True)
    assert event.categories == ["career_fair", "career_dev"]
    count = await db_session.scalar(select(func.count()).select_from(Event).where(Event.id.in_([a, b])))
    assert count == 1

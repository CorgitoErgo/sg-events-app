"""The enrichment pass against the local Postgres (rolled back), with stub LLM and OneMap."""

from collections import Counter

import pytest
from sqlalchemy import select, text

from db.models import Event, Venue
from pipeline.enrich import classify_events, dedup_events, geocode_venues
from pipeline.geo.onemap import SearchResult
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from tests.factories import make_raw
from tests.test_classify import MODEL, StubLLM

pytestmark = pytest.mark.anyio


async def add(session, **kwargs) -> int:
    event_id, _ = await upsert_event(session, normalize(make_raw(**kwargs)))
    return event_id


async def reload(session, event_id: int) -> Event:
    return await session.get(Event, event_id, populate_existing=True)


# --- classification -------------------------------------------------------------------------

async def test_llm_classification_is_stored_and_not_repeated(db_session):
    event_id = await add(db_session, title="Tampines Career Fair", description="Meet 40 employers.")
    llm, counts = StubLLM(), Counter()

    await classify_events(db_session, llm=llm, model=MODEL, counts=counts, only_ids=[event_id])
    event = await reload(db_session, event_id)
    assert event.categories == ["career_fair"]
    assert event.summary == "Walk-in career fair with employers hiring on the spot."
    assert event.enrichment_hash is not None
    assert counts["classified_by_llm"] == 1

    await classify_events(db_session, llm=llm, model=MODEL, counts=counts, only_ids=[event_id])
    assert len(llm.calls) == 1  # unchanged input: no second call


async def test_changed_description_is_reclassified(db_session):
    raw = make_raw(title="Tampines Career Fair", description="Meet 40 employers.")
    event_id, _ = await upsert_event(db_session, normalize(raw))
    llm = StubLLM()
    await classify_events(db_session, llm=llm, model=MODEL, counts=Counter(), only_ids=[event_id])

    raw.description = "Now with 60 employers and resume reviews."
    await upsert_event(db_session, normalize(raw))
    await classify_events(db_session, llm=llm, model=MODEL, counts=Counter(), only_ids=[event_id])
    assert len(llm.calls) == 2


async def test_without_an_llm_rules_fill_in_and_the_llm_runs_later(db_session):
    event_id = await add(db_session, title="Tampines Career Fair")
    await classify_events(db_session, llm=None, model=MODEL, counts=Counter(), only_ids=[event_id])
    event = await reload(db_session, event_id)
    assert event.categories == ["career_fair"] and event.enrichment_hash is None

    llm = StubLLM()
    await classify_events(db_session, llm=llm, model=MODEL, counts=Counter(), only_ids=[event_id])
    assert len(llm.calls) == 1


async def test_llm_audience_restriction_survives_later_crawls(db_session):
    raw = make_raw(title="Tampines Career Fair", description="Registration required.")
    event_id, _ = await upsert_event(db_session, normalize(raw))
    llm = StubLLM({"categories": ["career_fair"], "audience": "students_only", "confidence": "high", "summary": ""})
    await classify_events(db_session, llm=llm, model=MODEL, counts=Counter(), only_ids=[event_id])
    assert (await reload(db_session, event_id)).audience == "students_only"

    await upsert_event(db_session, normalize(raw))  # the next crawl: the normalizer says "public"
    assert (await reload(db_session, event_id)).audience == "students_only"


# --- geocoding ---------------------------------------------------------------------------------

class StubOneMap:
    has_credentials = True

    def __init__(self, hit: SearchResult | None, area: str | None = "TAMPINES") -> None:
        self.hit, self.area = hit, area
        self.searches: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.searches.append(query)
        return [self.hit] if self.hit else []

    async def planning_area(self, lat: float, lng: float) -> str | None:
        return self.area


OUR_TAMPINES_HUB = SearchResult("OUR TAMPINES HUB", "OUR TAMPINES HUB", "1 TAMPINES WALK", "528523", 1.35313, 103.94041)


async def venue_of(session, event_id: int) -> Venue:
    return await session.get(Venue, (await reload(session, event_id)).venue_id, populate_existing=True)


async def test_venue_without_coordinates_is_geocoded_and_events_get_the_point(db_session):
    event_id = await add(db_session, lat=None, lng=None)
    venue = await venue_of(db_session, event_id)
    onemap, counts = StubOneMap(OUR_TAMPINES_HUB), Counter()

    await geocode_venues(db_session, onemap, counts, only_ids=[venue.id])

    venue = await venue_of(db_session, event_id)
    assert (venue.geocode_source, venue.planning_area, venue.region) == ("onemap", "TAMPINES", "EAST")
    assert venue.geocoded_at is not None
    assert onemap.searches == ["528523"]
    lat = await db_session.scalar(text("SELECT ST_Y(geom::geometry) FROM events WHERE id = :id"), {"id": event_id})
    assert lat == pytest.approx(1.35313)


async def test_source_coordinates_only_need_a_planning_area(db_session):
    event_id = await add(db_session)  # factory events carry source lat/lng
    venue = await venue_of(db_session, event_id)
    onemap = StubOneMap(hit=None)
    await geocode_venues(db_session, onemap, Counter(), only_ids=[venue.id])
    venue = await venue_of(db_session, event_id)
    assert onemap.searches == []  # nothing to look up
    assert (venue.geocode_source, venue.planning_area) == ("source", "TAMPINES")


async def test_unresolvable_venue_lowers_confidence(db_session):
    event_id = await add(db_session, lat=None, lng=None, postal_code=None, address_raw=None, venue_raw="Somewhere Vague")
    venue = await venue_of(db_session, event_id)
    counts = Counter()
    await geocode_venues(db_session, StubOneMap(hit=None), counts, only_ids=[venue.id])
    assert counts["geocode_miss"] == 1
    assert (await reload(db_session, event_id)).confidence == "medium"
    assert (await venue_of(db_session, event_id)).geocoded_at is not None  # not retried every run


# --- dedup in the pass ---------------------------------------------------------------------------

async def test_dedup_pass_merges_new_duplicates(db_session):
    first = await add(db_session, source_id="luma", title="NTUC Career Fair @ Our Tampines Hub")
    second = await add(db_session, source_id="other", title="Career Fair at Our Tampines Hub by e2i")
    counts = Counter()
    await dedup_events(db_session, [second], llm=None, model=MODEL, counts=counts)
    assert counts["merged"] == 1
    remaining = await db_session.scalars(select(Event.id).where(Event.id.in_([first, second])))
    assert list(remaining) == [first]

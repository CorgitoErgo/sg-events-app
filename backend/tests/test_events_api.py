"""GET /events and /events/{id} against the local Postgres (rolled back).

The scenario sits around Jurong West, far from the real (central) Luma events in the dev
database, so radius results are exact. Queries without a location may also return real
events, so those tests only look at the scenario's own events.
"""

from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import update

from app.main import app
from db.models import Event, Venue
from db.session import get_session
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from tests.factories import make_raw

pytestmark = pytest.mark.anyio

P_LAT, P_LNG = 1.3404, 103.7090  # Jurong West
KM_LAT = 1 / 111.2  # degrees of latitude per km


@dataclass
class Scenario:
    near: int  # 0.5 km, career fair, in 2 days
    workshop: int  # 2 km, free workshop, tomorrow
    far: int  # 8 km, career fair, in 3 days
    online: int  # online career fair, tomorrow
    students: int  # 1 km, students-only career fair, in 4 days
    cancelled: int  # 1 km career fair, cancelled
    ended: int  # 1 km career fair, two days ago
    later: int  # 1 km career fair, in 40 days

    @property
    def ids(self) -> set[int]:
        return set(vars(self).values())


async def add(session, *, km: float | None, categories: list[str], **kwargs) -> int:
    geo = {"lat": P_LAT + km * KM_LAT, "lng": P_LNG} if km is not None else {"lat": None, "lng": None}
    raw = make_raw(postal_code=None, address_raw="Jurong West", **geo, **kwargs)
    event_id, _ = await upsert_event(session, normalize(raw))
    await session.execute(update(Event).where(Event.id == event_id).values(categories=categories))
    return event_id


@pytest.fixture
async def scenario(db_session) -> Scenario:
    s = db_session
    sc = Scenario(
        near=await add(s, km=0.5, categories=["career_fair"], days_ahead=2),
        workshop=await add(s, km=2, categories=["workshop_class"], days_ahead=1, price_raw="Free"),
        far=await add(s, km=8, categories=["career_fair"], days_ahead=3),
        online=await add(s, km=None, categories=["career_fair"], days_ahead=1, venue_raw="Zoom"),
        students=await add(s, km=1, categories=["career_fair"], days_ahead=4,
                           description="Open to NTU students only."),
        cancelled=await add(s, km=1, categories=["career_fair"], days_ahead=2,
                            raw_payload={"ics_status": "CANCELLED"}),
        ended=await add(s, km=1, categories=["career_fair"], days_ahead=-2),
        later=await add(s, km=1, categories=["career_fair"], days_ahead=40),
    )  # fmt: skip
    await s.flush()
    return sc


@pytest.fixture
async def api(db_session):
    async def session_override():
        yield db_session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def get(api, **params) -> dict:
    resp = await api.get("/events", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def ids(body: dict, only: set[int] | None = None) -> list[int]:
    return [e["id"] for e in body["items"] if only is None or e["id"] in only]


async def test_radius_search_is_sorted_by_distance(api, scenario):
    body = await get(api, lat=P_LAT, lng=P_LNG, radius_km=5)
    assert ids(body) == [scenario.near, scenario.workshop]  # not far, online, restricted, cancelled, ended, later
    first = body["items"][0]
    assert first["distance_m"] == pytest.approx(500, rel=0.02)
    assert first["location"]["lat"] == pytest.approx(P_LAT + 0.5 * KM_LAT)
    assert first["sources"] == [{"source_id": "test", "url": first["sources"][0]["url"]}]
    assert body["total"] == 2 and body["next_offset"] is None
    assert body["filters"]["sort"] == "distance" and body["filters"]["online"] == "exclude"


async def test_category_filter(api, scenario):
    body = await get(api, lat=P_LAT, lng=P_LNG, radius_km=10, category="career_fair")
    assert ids(body) == [scenario.near, scenario.far]


async def test_restricted_audiences_are_opt_in(api, scenario):
    body = await get(api, lat=P_LAT, lng=P_LNG, radius_km=5, include_restricted="true")
    assert ids(body) == [scenario.near, scenario.students, scenario.workshop]


async def test_online_events_can_be_added_to_a_radius_search(api, scenario):
    body = await get(api, lat=P_LAT, lng=P_LNG, radius_km=5, online="include")
    assert ids(body) == [scenario.near, scenario.workshop, scenario.online]  # no distance: last
    assert body["items"][-1]["distance_m"] is None and body["items"][-1]["location"] is None


async def test_online_only(api, scenario):
    body = await get(api, online="only", limit=100)
    assert ids(body, scenario.ids) == [scenario.online]


async def test_default_window_is_30_days_and_can_be_widened(api, scenario):
    assert scenario.later not in ids(await get(api, lat=P_LAT, lng=P_LNG, radius_km=2))
    far_date = (await api.get(f"/events/{scenario.later}")).json()["starts_at"][:10]
    body = await get(api, lat=P_LAT, lng=P_LNG, radius_km=2, date_to=far_date)
    assert scenario.later in ids(body)


async def test_free_filter(api, scenario):
    assert ids(await get(api, lat=P_LAT, lng=P_LNG, radius_km=10, free="true")) == [scenario.workshop]


async def test_soonest_without_a_location(api, scenario):
    body = await get(api, category="career_fair", limit=100)
    assert ids(body, scenario.ids) == [scenario.online, scenario.near, scenario.far]
    assert body["filters"]["sort"] == "soonest"


async def test_blend_prefers_close_and_soon(api, scenario):
    body = await get(api, lat=P_LAT, lng=P_LNG, radius_km=5, sort="blend")
    assert ids(body) == [scenario.near, scenario.workshop]


async def test_pagination(api, scenario):
    page1 = await get(api, lat=P_LAT, lng=P_LNG, radius_km=5, limit=1)
    page2 = await get(api, lat=P_LAT, lng=P_LNG, radius_km=5, limit=1, offset=page1["next_offset"])
    assert (ids(page1), ids(page2)) == ([scenario.near], [scenario.workshop])
    assert page2["next_offset"] is None


async def test_planning_area_filter(api, db_session, scenario):
    near = await db_session.get(Event, scenario.near)
    await db_session.execute(
        update(Venue).where(Venue.id == near.venue_id).values(planning_area="JURONG WEST", region="WEST")
    )
    assert ids(await get(api, area="jurong west", limit=100), scenario.ids) == [scenario.near]
    assert scenario.near in ids(await get(api, region="west", limit=100))


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"lat": 1.3}, "both lat and lng"),
        ({"category": "raves"}, "unknown categories"),
        ({"sort": "distance"}, "needs lat and lng"),
        ({"when": "weekend", "date_from": "2026-10-01"}, "either when"),
        ({"area": "Atlantis"}, "unknown planning area"),
        ({"region": "SOUTH"}, "region must be"),
        ({"date_from": "next tuesday"}, "ISO date"),
        ({"date_from": "2026-12-01", "date_to": "2026-11-01"}, "after date_from"),
    ],
)
async def test_bad_parameters(api, params, message):
    resp = await api.get("/events", params=params)
    assert resp.status_code == 422 and message in resp.text


async def test_event_detail(api, scenario):
    resp = await api.get(f"/events/{scenario.students}")
    body = resp.json()
    assert resp.status_code == 200
    assert body["description"] == "Open to NTU students only." and body["audience"] == "students_only"
    assert body["sessions"] == [] and body["venue"]["name"].startswith("Test Hall")
    assert (await api.get("/events/999999999")).status_code == 404

"""Admin console API against the local Postgres (rolled back)."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.admin.router import get_fetcher
from app.clients import AppClients, get_clients
from app.main import app
from db.models import Event, EventSource
from db.session import get_session
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from scrapers.http import PoliteClient
from tests.factories import make_raw

pytestmark = pytest.mark.anyio

PAGE_HTML = (Path(__file__).parent / "fixtures" / "admin" / "eventbrite_style_page.html").read_text("utf-8")
HEADERS = {"X-Admin": "1"}


async def no_sleep(_s: float) -> None:
    return None


def fake_fetcher(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.url.path.startswith("/e/"):
            return httpx.Response(200, text=PAGE_HTML, headers={"Content-Type": "text/html"})
        return httpx.Response(404)

    return lambda: PoliteClient(transport=httpx.MockTransport(handler), min_delay_s=0, sleep=no_sleep, cache_dir=tmp_path)


@pytest.fixture
async def admin(db_session, tmp_path):
    async def session_override():
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_clients] = lambda: AppClients()
    app.dependency_overrides[get_fetcher] = lambda: fake_fetcher(tmp_path)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000") as client:
        yield client
    app.dependency_overrides.clear()


# --- access ----------------------------------------------------------------------------------

async def test_only_this_computer_can_use_the_console(db_session):
    transport = httpx.ASGITransport(app=app, client=("192.168.0.23", 50000))  # a phone on the LAN
    async with httpx.AsyncClient(transport=transport, base_url="http://192.168.0.4:9000") as lan:
        assert (await lan.get("/admin")).status_code == 403
        assert (await lan.post("/admin/api/drafts", json={"url": "https://x"}, headers=HEADERS)).status_code == 403
        assert (await lan.get("/events", params={"limit": 1})).status_code != 403  # the public API still works


async def test_api_calls_need_the_admin_header(admin):
    assert (await admin.post("/admin/api/drafts", json={"url": "https://x"})).status_code == 403


async def test_console_page_and_assets_are_served(admin):
    page = await admin.get("/admin")
    assert page.status_code == 200 and "Add to SG Events" in page.text
    assert (await admin.get("/admin/static/admin.js")).status_code == 200
    assert (await admin.get("/admin/static/..%2F..%2Fmain.py")).status_code == 404


# --- drafts ----------------------------------------------------------------------------------

async def test_bookmarklet_payload_becomes_a_draft_in_sgt(admin):
    import json
    import re

    jsonld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', PAGE_HTML, re.S).group(1))
    body = (await admin.post("/admin/api/drafts", headers=HEADERS, json={"url": "https://tickets.example.sg/e/tampines-career-fair-123", "jsonld": [jsonld]})).json()
    (draft,) = body["drafts"]
    assert body["note"] is None
    assert draft["title"] == "Tampines Career Fair 2026"
    assert (draft["start"], draft["end"]) == ("2026-10-03T10:00", "2026-10-03T16:00")
    assert (draft["venue"], draft["postal_code"], draft["price"]) == ("Our Tampines Hub", "528523", "Free")
    assert draft["news"] is False and "40 employers" in draft["description"]


async def test_page_without_event_data_gives_a_blank_draft_with_the_selection(admin):
    body = (await admin.post("/admin/api/drafts", headers=HEADERS, json={
        "url": "https://www.straitstimes.com/singapore/jobs-fair", "title": "Jobs fair this Saturday",
        "selection": "The fair runs 10am to 4pm at Jurong Point.",
    })).json()  # fmt: skip
    (draft,) = body["drafts"]
    assert "fill in the date" in body["note"]
    assert draft["title"] == "Jobs fair this Saturday" and draft["start"] is None
    assert draft["news"] is True  # a news site: facts and link only


async def test_paste_a_url_fetches_politely_and_extracts(admin):
    body = (await admin.post("/admin/api/extract", headers=HEADERS, json={"url": "https://tickets.example.sg/e/tampines-career-fair-123"})).json()
    assert body["drafts"][0]["title"] == "Tampines Career Fair 2026"
    assert body["drafts"][0]["url"] == "https://tickets.example.sg/e/tampines-career-fair-123"


async def test_robots_disallow_suggests_the_bookmarklet(admin):
    resp = await admin.post("/admin/api/extract", headers=HEADERS, json={"url": "https://tickets.example.sg/private/e/1"})
    assert resp.status_code == 422 and "bookmark" in resp.json()["detail"]


# --- save / list / delete ---------------------------------------------------------------------

def draft(**kwargs) -> dict:
    return {
        "url": "https://tickets.example.sg/e/admin-test-1", "title": "Admin Test Fair", "start": "2030-10-03T10:00",
        "end": "2030-10-03T16:00", "venue": "Admin Test Hall", "postal_code": "528523", "lat": 1.3531, "lng": 103.9404,
        "price": "Free", "organizer": "e2i", "description": "Meet employers.", **kwargs,
    }  # fmt: skip


async def test_save_creates_an_event_with_chosen_categories(admin, db_session):
    r = (await admin.post("/admin/api/events", headers=HEADERS, json={"draft": draft(), "categories": ["career_fair"]})).json()
    event = await db_session.get(Event, r["id"], populate_existing=True)
    assert (event.title, event.categories, event.enrichment_hash, event.is_free) == ("Admin Test Fair", ["career_fair"], "manual", True)
    assert event.starts_at.isoformat() == "2030-10-03T02:00:00+00:00"  # the form's time is SGT
    source = await db_session.scalar(select(EventSource).where(EventSource.event_id == r["id"]))
    assert source.source_id == "manual"
    listed = (await admin.get("/admin/api/events", headers=HEADERS)).json()
    assert listed[0]["id"] == r["id"]


async def test_news_articles_keep_facts_and_link_only(admin, db_session):
    r = (await admin.post("/admin/api/events", headers=HEADERS, json={"draft": draft(url="https://www.straitstimes.com/x", news=True)})).json()
    event = await db_session.get(Event, r["id"], populate_existing=True)
    assert event.description is None and event.confidence == "low"


async def test_bad_dates_are_explained(admin):
    resp = await admin.post("/admin/api/events", headers=HEADERS, json={"draft": draft(start="sometime soon")})
    assert resp.status_code == 422 and "start date" in resp.json()["detail"]


async def test_manual_entry_merges_with_the_same_crawled_event_and_wins(admin, db_session):
    crawled, _ = await upsert_event(db_session, normalize(make_raw(
        source_id="luma", title="Admin Test Fair 2030", start_raw="20301003T020000Z", end_raw="20301003T080000Z",
        postal_code="528523", venue_raw="Admin Test Hall", description=None,
    )))  # fmt: skip
    await db_session.commit()
    r = (await admin.post("/admin/api/events", headers=HEADERS, json={"draft": draft(title="Admin Test Fair (Walk-in)")})).json()
    assert r["merged_into"] is not None
    sources = set(await db_session.scalars(select(EventSource.source_id).where(EventSource.event_id == r["id"])))
    assert sources == {"luma", "manual"}
    assert (await db_session.get(Event, r["id"], populate_existing=True)).title == "Admin Test Fair (Walk-in)"


async def test_delete_only_hand_added_events(admin, db_session):
    r = (await admin.post("/admin/api/events", headers=HEADERS, json={"draft": draft()})).json()
    assert (await admin.delete(f"/admin/api/events/{r['id']}", headers=HEADERS)).status_code == 200
    crawled, _ = await upsert_event(db_session, normalize(make_raw(source_id="luma")))
    resp = await admin.delete(f"/admin/api/events/{crawled}", headers=HEADERS)
    assert resp.status_code == 409 and "luma" in resp.json()["detail"]


async def test_status(admin):
    body = (await admin.get("/admin/api/status", headers=HEADERS)).json()
    assert {"upcoming_events", "manual_events", "keys", "last_crawls"} <= set(body)

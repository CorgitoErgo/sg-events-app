"""The discovery agent end to end: fake Tavily + Eventbrite, mocked web, the rolled-back DB."""

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.admin.router import _autosearch, get_session_factory
from app.clients import AppClients, get_clients
from app.main import app
from db.models import AutosearchDecision, CrawlRun, Event, EventSource
from db.session import get_session
from pipeline.autosearch.agent import Limits
from pipeline.autosearch.runner import run_autosearch
from pipeline.autosearch.tavily import SearchHit, TavilyError
from scrapers.http import PoliteClient
from tests.test_autosearch_parts import EVENTBRITE_EVENT

pytestmark = pytest.mark.anyio

NOW = datetime.now(UTC)
SOON = (NOW + timedelta(days=9)).strftime("%Y-%m-%dT10:00:00+08:00")
PAST = (NOW - timedelta(days=9)).strftime("%Y-%m-%dT10:00:00+08:00")


def event_page(name: str, start: str, place: dict) -> str:
    data = {"@context": "https://schema.org", "@type": "Event", "name": name, "startDate": start, "location": place}
    return f'<html><head><script type="application/ld+json">{json.dumps(data)}</script></head><body>{name}</body></html>'


SG_PLACE = {"@type": "Place", "name": "Autosearch Test Hall",
            "address": {"@type": "PostalAddress", "streetAddress": "1 Tampines Walk", "postalCode": "528523"}}  # fmt: skip
LONDON = {"@type": "Place", "name": "ExCeL", "geo": {"latitude": 51.508, "longitude": 0.029}}

PAGES = {
    "https://events.example.sg/fair": event_page("Autosearch Test Career Fair", SOON, SG_PLACE),
    "https://events.example.com/london": event_page("London Tech Week", SOON, LONDON),
    "https://events.example.sg/old": event_page("Autosearch Past Bazaar", PAST, SG_PLACE),
    "https://blog.example.sg/post": "<html><body><p>Our thoughts on careers.</p></body></html>",
}
HITS = [
    "https://www.facebook.com/events/1",
    "https://events.example.sg/fair",
    "https://events.example.com/london",
    "https://events.example.sg/old",
    "https://blog.example.sg/post",
    "https://www.eventbrite.sg/d/singapore--singapore/career-fair/",
    "https://www.eventbrite.sg/e/fun-run-tickets-1234567890123",
    "https://private.example.sg/e/1",
]


class FakeTavily:
    def __init__(self, hits=HITS, error: Exception | None = None):
        self.hits, self.error, self.queries = hits, error, []

    async def search(self, query, *, max_results=10, exclude_domains=None):
        self.queries.append(query)
        if self.error:
            raise self.error
        return [SearchHit(url=u, title=u.rsplit("/", 1)[-1], content="", score=1.0) for u in self.hits]


class FakeEventbrite:
    async def event(self, event_id):
        start = (NOW + timedelta(days=12)).strftime("%Y-%m-%dT23:30:00Z")
        return {**EVENTBRITE_EVENT, "id": event_id, "name": {"text": "Autosearch Fun Run"}, "start": {"utc": start}, "end": {"utc": None}}


async def no_sleep(_s):
    return None


def fetcher(tmp_path):
    def handler(request):
        url = str(request.url)
        if request.url.path == "/robots.txt":
            body = "User-agent: *\nDisallow: /\n" if request.url.host == "private.example.sg" else ""
            return httpx.Response(200, text=body)
        if url in PAGES:
            return httpx.Response(200, text=PAGES[url], headers={"Content-Type": "text/html; charset=utf-8"})
        return httpx.Response(404)

    return lambda: PoliteClient(transport=httpx.MockTransport(handler), min_delay_s=0, sleep=no_sleep, cache_dir=tmp_path)


async def run(db_session, tmp_path, *, limits=Limits(max_searches=2), dry_run=False, tavily=None, queries=("q1", "q2")):
    return await run_autosearch(
        session_factory=lambda: nullcontext(db_session), clients=AppClients(), tavily=tavily or FakeTavily(),
        eventbrite=FakeEventbrite(), fetcher_factory=fetcher(tmp_path), limits=limits, queries=list(queries),
        dry_run=dry_run, now=NOW,
    )  # fmt: skip


def by_url(result) -> dict[str, tuple[str, str]]:
    return {d.url: (d.decision, d.reason) for d in result.decisions}


async def test_every_result_gets_a_decision_and_a_reason(db_session, tmp_path):
    result = await run(db_session, tmp_path)
    got = by_url(result)
    assert got["https://www.facebook.com/events/1"] == ("skipped", "facebook.com: prohibits automated access")
    assert got["https://events.example.sg/fair"][0] == "saved"
    assert "Singapore postal code 528523" in got["https://events.example.sg/fair"][1]
    assert got["https://events.example.com/london"] == ("skipped", "no sign it's in Singapore")
    assert got["https://events.example.sg/old"] == ("skipped", "already over")
    assert got["https://blog.example.sg/post"] == ("skipped", "no event details on the page (plain-text pages need ANTHROPIC_API_KEY)")
    assert "listing page" in got["https://www.eventbrite.sg/d/singapore--singapore/career-fair/"][1]
    assert got["https://www.eventbrite.sg/e/fun-run-1234567890123"][0] == "saved"  # the API's canonical URL
    assert "official Eventbrite API" in got["https://www.eventbrite.sg/e/fun-run-1234567890123"][1]
    assert "doesn't allow automated access" in got["https://private.example.sg/e/1"][1]
    # the second query returns the same links: nothing is opened or logged twice
    assert len(result.decisions) == 8 and result.counts["searches"] == 2

    saved = {d.url: d.event_id for d in result.decisions if d.decision == "saved"}
    sources = {
        url: set(await db_session.scalars(select(EventSource.source_id).where(EventSource.event_id == event_id)))
        for url, event_id in saved.items()
    }
    assert sources == {"https://events.example.sg/fair": {"web"}, "https://www.eventbrite.sg/e/fun-run-1234567890123": {"eventbrite"}}
    fun_run = await db_session.get(Event, saved["https://www.eventbrite.sg/e/fun-run-1234567890123"])
    assert (fun_run.price_min_sgd, fun_run.categories) == (15, ["sports_fitness"])  # classified by the rules

    run_row = await db_session.get(CrawlRun, result.run_id)
    assert (run_row.source_id, run_row.status, run_row.events_stored) == ("autosearch", "ok", 2)
    logged = await db_session.scalars(select(AutosearchDecision).where(AutosearchDecision.run_id == result.run_id))
    assert len(logged.all()) == 8
    assert result.stop_reason == "used 2 searches (limit 2)"


async def test_stops_at_the_event_limit(db_session, tmp_path):
    result = await run(db_session, tmp_path, limits=Limits(max_events=1))
    assert result.counts["saved"] == 1 and result.stop_reason == "saved 1 events (limit 1)"
    assert not any("eventbrite.sg/e/" in url for url in by_url(result))  # never reached


async def test_stops_at_the_page_limit(db_session, tmp_path):
    result = await run(db_session, tmp_path, limits=Limits(max_pages=2))
    assert result.counts["pages"] == 2 and result.stop_reason == "opened 2 pages (limit 2)"


async def test_known_events_are_not_saved_twice(db_session, tmp_path):
    await run(db_session, tmp_path)
    again = await run(db_session, tmp_path)
    assert by_url(again)["https://events.example.sg/fair"] == ("known", "already listed")
    assert again.counts["saved"] == 0


async def test_dry_run_saves_nothing(db_session, tmp_path):
    result = await run(db_session, tmp_path, dry_run=True)
    assert result.run_id is None and result.counts["would_save"] == 2 and result.counts["saved"] == 0
    assert await db_session.scalar(select(Event.id).where(Event.title == "Autosearch Test Career Fair")) is None


async def test_search_failure_is_recorded(db_session, tmp_path):
    result = await run(db_session, tmp_path, tavily=FakeTavily(error=TavilyError("Tavily credit or plan limit reached")))
    assert result.status == "failed" and "credit" in result.error
    assert (await db_session.get(CrawlRun, result.run_id)).status == "failed"


# --- admin console ------------------------------------------------------------------------------

@pytest.fixture
async def admin(db_session, tmp_path):
    async def session_override():
        yield db_session

    state = {"clients": AppClients()}
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_clients] = lambda: state["clients"]
    app.dependency_overrides[get_session_factory] = lambda: (lambda: nullcontext(db_session))
    _autosearch.update(task=None, result=None)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:9000", headers={"X-Admin": "1"}) as c:
        c.state = state
        yield c
    app.dependency_overrides.clear()
    _autosearch.update(task=None, result=None)


async def test_console_explains_a_missing_tavily_key(admin):
    resp = await admin.post("/admin/api/autosearch", json={})
    assert resp.status_code == 503 and "TAVILY_API_KEY" in resp.json()["detail"]


async def test_console_runs_a_dry_run_and_shows_decisions(admin):
    admin.state["clients"] = AppClients(tavily=FakeTavily(hits=["https://www.facebook.com/events/9"]))
    started = await admin.post("/admin/api/autosearch", json={"queries": ["career fair"], "max_searches": 1, "dry_run": True})
    assert started.status_code == 200 and started.json()["limits"]["max_searches"] == 1
    await _autosearch["task"]
    status = (await admin.get("/admin/api/autosearch")).json()
    assert status["running"] is False and status["dry_run"] is True
    assert status["decisions"][0]["decision"] == "skipped" and status["stop_reason"] == "used 1 searches (limit 1)"

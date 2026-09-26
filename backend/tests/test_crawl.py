"""crawl_source end to end with a fake adapter, a mocked transport and the rolled-back DB."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from db.models import CrawlRun
from pipeline.crawl import crawl_source
from scrapers.base import SourceBlocked
from scrapers.http import PoliteClient
from tests.factories import make_raw

pytestmark = pytest.mark.anyio

SITE = "https://fake.example.sg"


class FakeAdapter:
    source_id = "fake"
    base_urls = [SITE]
    schedule = "0 */6 * * *"
    min_delay_s = 0.0

    def __init__(self, pages: dict[str, int], *, blocked_on: str | None = None, explode: bool = False) -> None:
        self.pages = pages  # path -> number of events on that page
        self.blocked_on = blocked_on
        self.explode = explode

    async def discover(self, client):
        if self.explode:
            raise RuntimeError("boom")
        for path in self.pages:
            yield f"{SITE}{path}"

    async def fetch_and_parse(self, client, url):
        path = url.removeprefix(SITE)
        if path == self.blocked_on:
            raise SourceBlocked(url, "HTTP 403")
        res = await client.get(url, source_id=self.source_id)
        if not res.ok:
            return []
        return [make_raw(source_id=self.source_id) for _ in range(self.pages[path])]


async def no_sleep(_seconds: float) -> None:
    return None


def client_factory():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/broken":
            return httpx.Response(500)
        return httpx.Response(200, text="ok")

    return lambda: PoliteClient(transport=httpx.MockTransport(handler), min_delay_s=0, sleep=no_sleep, cache_dir=_TMP)


_TMP = None


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path):
    global _TMP
    _TMP = tmp_path


async def crawl(db_session, adapter, alerts: list[str]):
    async def alerter(message: str) -> None:
        alerts.append(message)

    return await crawl_source(
        adapter, session_factory=lambda: nullcontext(db_session), client_factory=client_factory(), alerter=alerter
    )


async def test_successful_run_is_recorded(db_session):
    alerts: list[str] = []
    result = await crawl(db_session, FakeAdapter({"/a": 2, "/b": 3}), alerts)
    run = await db_session.get(CrawlRun, result.run_id)
    assert (run.status, run.urls_fetched, run.urls_failed, run.events_found, run.events_stored) == ("ok", 2, 0, 5, 5)
    assert run.finished_at is not None and alerts == []


async def test_a_failing_page_does_not_stop_the_run(db_session):
    alerts: list[str] = []
    result = await crawl(db_session, FakeAdapter({"/a": 2, "/broken": 0, "/b": 1}), alerts)
    run = await db_session.get(CrawlRun, result.run_id)
    assert (run.status, run.urls_fetched, run.urls_failed, run.events_stored) == ("ok", 2, 1, 3)
    assert any("1 of 3 pages failed" in a for a in alerts)


async def test_blocked_source_stops_and_alerts(db_session):
    alerts: list[str] = []
    result = await crawl(db_session, FakeAdapter({"/a": 2, "/b": 5, "/c": 1}, blocked_on="/b"), alerts)
    run = await db_session.get(CrawlRun, result.run_id)
    assert (run.status, run.error, run.events_stored) == ("blocked", "HTTP 403", 2)  # /c never fetched
    assert "blocked (HTTP 403)" in alerts[0]


async def test_crashing_adapter_is_recorded_as_failed(db_session):
    alerts: list[str] = []
    result = await crawl(db_session, FakeAdapter({}, explode=True), alerts)
    run = await db_session.get(CrawlRun, result.run_id)
    assert run.status == "failed" and "boom" in run.error and "run failed" in alerts[0]


async def test_sudden_drop_alerts(db_session):
    now = datetime.now(UTC)
    for hours in (6, 12, 18):
        db_session.add(CrawlRun(source_id="fake", status="ok", events_found=20, started_at=now - timedelta(hours=hours)))
    await db_session.flush()
    alerts: list[str] = []
    await crawl(db_session, FakeAdapter({"/a": 2}), alerts)
    assert len(alerts) == 1 and "90% below its 7-day median of 20" in alerts[0]
    runs = (await db_session.scalars(select(CrawlRun).where(CrawlRun.source_id == "fake"))).all()
    assert len(runs) == 4

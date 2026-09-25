"""PoliteClient behaviour with a mocked transport and a fake clock: no network, no real sleeps."""

from collections.abc import Callable

import httpx
import pytest

from scrapers.base import SourceBlocked
from scrapers.http import PoliteClient

pytestmark = pytest.mark.anyio

SITE = "https://events.example.sg"


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], tmp_path, robots: str | int = ""
) -> tuple[PoliteClient, FakeTime]:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            if isinstance(robots, int):
                return httpx.Response(robots)
            return httpx.Response(200, text=robots)
        return handler(request)

    ft = FakeTime()
    client = PoliteClient(
        transport=httpx.MockTransport(route), cache_dir=tmp_path, sleep=ft.sleep, clock=ft.clock
    )
    return client, ft


def ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="hello")


async def test_robots_disallow_raises_and_allowed_paths_work(tmp_path):
    client, _ = make_client(ok, tmp_path, robots="User-agent: *\nDisallow: /private\n")
    async with client:
        with pytest.raises(SourceBlocked, match="robots"):
            await client.get(f"{SITE}/private/page", source_id="t")
        assert (await client.get(f"{SITE}/public", source_id="t")).content == b"hello"


async def test_requests_to_a_domain_are_spaced_by_crawl_delay(tmp_path):
    client, ft = make_client(ok, tmp_path, robots="User-agent: *\nCrawl-delay: 5\n")
    async with client:
        await client.get(f"{SITE}/a", source_id="t")
        await client.get(f"{SITE}/b", source_id="t")
    assert ft.sleeps == [5.0, 5.0]  # robots.txt fetch -> /a -> /b


async def test_default_delay_is_two_seconds(tmp_path):
    client, ft = make_client(ok, tmp_path)
    async with client:
        await client.get(f"{SITE}/a", source_id="t")
    assert ft.sleeps == [2.0]


async def test_429_honours_retry_after_then_succeeds(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, text="done")

    client, ft = make_client(handler, tmp_path)
    async with client:
        res = await client.get(f"{SITE}/a", source_id="t")
    assert res.ok and res.content == b"done"
    assert 7.0 in ft.sleeps


async def test_very_long_retry_after_gives_up_without_waiting(tmp_path):
    client, ft = make_client(lambda r: httpx.Response(429, headers={"Retry-After": "3600"}), tmp_path)
    async with client:
        res = await client.get(f"{SITE}/a", source_id="t")
    assert res.status == 429
    assert max(ft.sleeps) < 60


async def test_5xx_retries_with_backoff_then_returns_last_response(tmp_path):
    client, ft = make_client(lambda r: httpx.Response(503), tmp_path)
    async with client:
        res = await client.get(f"{SITE}/a", source_id="t")
    assert res.status == 503
    assert [s for s in ft.sleeps if s > 2] == [4.0, 8.0]  # 2 s backoff merges with the 2 s gap


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_errors_stop_the_source(tmp_path, status):
    client, _ = make_client(lambda r: httpx.Response(status), tmp_path)
    async with client:
        with pytest.raises(SourceBlocked, match=str(status)):
            await client.get(f"{SITE}/a", source_id="t")


async def test_challenge_page_stops_the_source(tmp_path):
    page = "<html><head><title>Just a moment...</title></head></html>"
    client, _ = make_client(
        lambda r: httpx.Response(200, text=page, headers={"Content-Type": "text/html"}), tmp_path
    )
    async with client:
        with pytest.raises(SourceBlocked, match="CAPTCHA"):
            await client.get(f"{SITE}/a", source_id="t")


async def test_recaptcha_widget_in_a_normal_page_is_fine(tmp_path):
    page = '<html><head><title>Beach Clean-up</title></head><div class="g-recaptcha"></div></html>'
    client, _ = make_client(
        lambda r: httpx.Response(200, text=page, headers={"Content-Type": "text/html"}), tmp_path
    )
    async with client:
        assert (await client.get(f"{SITE}/a", source_id="t")).ok


async def test_404_is_returned_for_the_adapter_to_handle(tmp_path):
    client, _ = make_client(lambda r: httpx.Response(404), tmp_path)
    async with client:
        assert (await client.get(f"{SITE}/gone", source_id="t")).status == 404


async def test_conditional_get_serves_cache_on_304(tmp_path):
    seen_headers = []

    def handler(request):
        seen_headers.append(request.headers.get("If-None-Match"))
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"body-v1", headers={"ETag": '"v1"'})

    client, _ = make_client(handler, tmp_path)
    async with client:
        first = await client.get(f"{SITE}/feed.ics", source_id="t")
        second = await client.get(f"{SITE}/feed.ics", source_id="t")
    assert seen_headers == [None, '"v1"']
    assert (first.from_cache, second.from_cache) == (False, True)
    assert second.status == 200 and second.content == b"body-v1"
    assert len(list((tmp_path / "t").glob("*.body"))) == 1


async def test_robots_404_means_no_restrictions(tmp_path):
    client, _ = make_client(ok, tmp_path, robots=404)
    async with client:
        assert (await client.get(f"{SITE}/anything", source_id="t")).ok


async def test_robots_5xx_means_assume_disallowed(tmp_path):
    client, _ = make_client(ok, tmp_path, robots=503)
    async with client:
        with pytest.raises(SourceBlocked, match="robots"):
            await client.get(f"{SITE}/anything", source_id="t")


async def test_sends_bot_user_agent(tmp_path):
    agents = []

    def handler(request):
        agents.append(request.headers["User-Agent"])
        return httpx.Response(200)

    client, _ = make_client(handler, tmp_path)
    async with client:
        await client.get(f"{SITE}/a", source_id="t")
    assert agents[0].startswith("SGEventsBot/")

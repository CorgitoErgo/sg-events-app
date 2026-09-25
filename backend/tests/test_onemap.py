"""OneMap client and venue matching, using saved OneMap responses (2026-09-26)."""

import json
from pathlib import Path

import httpx
import pytest

from pipeline.geo.geocode import VenueQuery, geocode, normalize_place, pick_by_postal, pick_by_text
from pipeline.geo.onemap import OneMapClient, OneMapError, SearchResult, _parse_result

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures" / "onemap"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))


def results(name: str) -> list[SearchResult]:
    return [r for r in map(_parse_result, fixture(name)["results"]) if r]


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler, *, credentials: bool = True) -> tuple[OneMapClient, FakeTime]:
    ft = FakeTime()
    client = OneMapClient(
        "me@example.com" if credentials else None,
        "secret" if credentials else None,
        transport=httpx.MockTransport(handler),
        sleep=ft.sleep,
        clock=ft.clock,
        wall_clock=lambda: 1_800_000_000.0,
    )
    return client, ft


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "tok123", "expiry_timestamp": "1800259200"})


# --- client ------------------------------------------------------------------------------------

async def test_search_parses_real_response():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=fixture("search_postal_528523.json"))

    client, _ = make_client(handler, credentials=False)
    async with client:
        hits = await client.search("528523")
    assert hits[0].name == "OUR TAMPINES HUB" and hits[0].postal == "528523"
    assert hits[0].lat == pytest.approx(1.35313, abs=1e-4) and hits[0].lng == pytest.approx(103.94041, abs=1e-4)
    assert "Authorization" not in seen[0].headers  # no credentials, no token
    assert seen[0].url.params["searchVal"] == "528523"


async def test_no_result():
    client, _ = make_client(lambda r: httpx.Response(200, json=fixture("search_no_result.json")), credentials=False)
    async with client:
        assert await client.search("nowhere") == []


def test_misspelt_longitude_key_is_accepted():
    row = {"SEARCHVAL": "X", "LATITUDE": "1.3", "LONGTITUDE": "103.8", "POSTAL": "NIL"}
    parsed = _parse_result(row)
    assert (parsed.lng, parsed.postal) == (103.8, None)


async def test_token_is_fetched_once_and_sent_as_bearer():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("getToken"):
            return token_response()
        return httpx.Response(200, json=fixture("search_postal_528523.json"))

    client, _ = make_client(handler)
    async with client:
        await client.search("528523")
        await client.search("528523")
    assert [c.url.path.rsplit("/", 1)[1] for c in calls] == ["getToken", "search", "search"]
    assert calls[1].headers["Authorization"] == "Bearer tok123"


async def test_falls_back_to_bare_token_when_bearer_is_rejected():
    auth_headers = []

    def handler(request):
        if request.url.path.endswith("getToken"):
            return token_response()
        auth_headers.append(request.headers.get("Authorization"))
        if request.headers.get("Authorization", "").startswith("Bearer "):
            return httpx.Response(401)
        return httpx.Response(200, json=fixture("planning_area_synthetic.json"))

    client, _ = make_client(handler)
    async with client:
        assert await client.planning_area(1.3533, 103.9404) == "TAMPINES"
        assert await client.planning_area(1.3533, 103.9404) == "TAMPINES"
    assert auth_headers == ["Bearer tok123", "tok123", "tok123"]  # remembered


async def test_planning_area_needs_credentials():
    client, _ = make_client(lambda r: pytest.fail("no request expected"), credentials=False)
    async with client:
        assert await client.planning_area(1.35, 103.94) is None


async def test_retries_then_raises():
    client, ft = make_client(lambda r: httpx.Response(503), credentials=False)
    async with client:
        with pytest.raises(OneMapError):
            await client.search("x")
    assert [s for s in ft.sleeps if s >= 1] == [2.0, 4.0]


async def test_bad_credentials_raise_clearly():
    client, _ = make_client(lambda r: httpx.Response(401))
    async with client:
        with pytest.raises(OneMapError, match="ONEMAP_EMAIL"):
            await client.search("x")


async def test_requests_are_spaced_to_five_per_second():
    client, ft = make_client(lambda r: httpx.Response(200, json={"results": []}), credentials=False)
    async with client:
        for _ in range(3):
            await client.search("x")
    assert ft.sleeps == [pytest.approx(0.2), pytest.approx(0.2)]


# --- matching ----------------------------------------------------------------------------------

def test_postal_match_requires_the_same_postal_code():
    hits = results("search_postal_528523.json")
    assert pick_by_postal("528523", hits).name == "OUR TAMPINES HUB"
    assert pick_by_postal("123456", hits) is None


def test_name_match_prefers_the_exact_building_over_a_subset_match():
    # OneMap lists "NIE (BLK 4) (LIBRARY)" first; token_set_ratio alone scores it 100 too.
    hit = pick_by_text("National Library", results("search_multi_national_library.json"), field="name")
    assert hit.name == "NATIONAL LIBRARY"


def test_singapore_abbreviations_are_expanded():
    assert normalize_place("Sengkang CC") == "sengkang community club"
    hit = pick_by_text("Sengkang CC", results("search_name_sengkang_cc.json"), field="name")
    assert hit.name == "SENGKANG COMMUNITY CLUB"


def test_weak_name_match_is_rejected():
    assert pick_by_text("Esplanade Concert Hall", results("search_name_sengkang_cc.json"), field="name") is None


class FakeOneMap:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses  # query -> fixture file
        self.queries: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.queries.append(query)
        name = self.responses.get(query)
        return results(name) if name else []


async def test_geocode_tries_postal_then_address_then_name():
    onemap = FakeOneMap({"Sengkang CC": "search_name_sengkang_cc.json"})
    hit = await geocode(onemap, VenueQuery("Sengkang CC", "2 Sengkang Square", None))
    assert hit.name == "SENGKANG COMMUNITY CLUB"
    assert onemap.queries == ["2 Sengkang Square", "Sengkang CC"]

    onemap = FakeOneMap({"528523": "search_postal_528523.json"})
    hit = await geocode(onemap, VenueQuery("Hub", "1 Tampines Walk, Singapore 528523", None))
    assert hit.postal == "528523" and onemap.queries == ["528523"]  # postal found in the address

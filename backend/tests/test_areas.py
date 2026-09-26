import httpx
import pytest

from app.clients import AppClients, get_clients
from app.main import app
from app.privacy import round_coords
from pipeline.geo.onemap import SearchResult

pytestmark = pytest.mark.anyio


class AreaOneMap:
    has_credentials = True

    async def search(self, query):
        if query == "528523":
            return [SearchResult("OUR TAMPINES HUB", None, None, "528523", 1.3531, 103.9404)]
        return []

    async def planning_area(self, lat, lng):
        return "TAMPINES"


@pytest.fixture
async def api():
    state = {"clients": AppClients(onemap=AreaOneMap())}
    app.dependency_overrides[get_clients] = lambda: state["clients"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        client.state = state
        yield client
    app.dependency_overrides.clear()


async def test_list_areas(api):
    body = (await api.get("/areas")).json()
    assert len(body) == 55
    assert {"planning_area": "SENGKANG", "region": "NORTH-EAST"} in body


async def test_resolve_postal(api):
    resp = await api.get("/areas/resolve", params={"postal": "528523"})
    assert resp.json() == {"planning_area": "TAMPINES", "region": "EAST"}
    assert (await api.get("/areas/resolve", params={"postal": "999999"})).status_code == 404
    assert (await api.get("/areas/resolve", params={"postal": "12ab"})).status_code == 422


async def test_resolve_without_onemap(api):
    api.state["clients"] = AppClients()
    assert (await api.get("/areas/resolve", params={"postal": "528523"})).status_code == 503


def test_postal_codes_are_cut_to_the_sector_in_logs():
    assert round_coords("/areas/resolve?postal=528523") == "/areas/resolve?postal=52xxxx"

"""Hybrid retrieval and POST /ask against the local Postgres (rolled back), with stub clients.

Scenario events sit around Jurong West, far from the real (central) events in the dev DB.
"""

from collections import Counter
from datetime import UTC, datetime

import anthropic
import httpx
import pytest
from sqlalchemy import update

from app.clients import AppClients, get_clients
from app.main import app
from app.services import query_parser
from app.services.answer import REFUSED, event_block, validate_citations
from app.services.query_parser import ParsedQuery, parse_rules
from app.services.retrieval import Overrides, retrieve
from db.models import Event, Venue
from db.session import get_session
from pipeline.embed import embed_events
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from tests.factories import make_raw
from tests.stubs import StubAnswerLLM, StubOneMap, StubVoyage

pytestmark = pytest.mark.anyio

P_LAT, P_LNG = 1.3404, 103.7090  # Jurong West
KM_LAT = 1 / 111.2


async def add(session, title: str, *, km: float, categories=(), days_ahead: int = 2, **kwargs) -> int:
    raw = make_raw(title=title, lat=P_LAT + km * KM_LAT, lng=P_LNG, postal_code=None,
                   address_raw="Jurong West", days_ahead=days_ahead, **kwargs)  # fmt: skip
    event_id, _ = await upsert_event(session, normalize(raw))
    await session.execute(update(Event).where(Event.id == event_id).values(categories=list(categories)))
    return event_id


@pytest.fixture
async def scenario(db_session):
    ids = {
        "pottery": await add(db_session, "Pottery Wheel Basics", km=1, categories=["workshop_class"]),
        "fair": await add(db_session, "Jurong Career Fair", km=2, categories=["career_fair"], days_ahead=1),
        "yoga": await add(db_session, "Sunrise Yoga in the Park", km=3, categories=["sports_fitness"], days_ahead=3),
    }
    await embed_events(db_session, StubVoyage(), Counter(), only_ids=list(ids.values()))
    await db_session.flush()
    return ids


def rules(question: str) -> ParsedQuery:
    return parse_rules(question, datetime.now(UTC))


async def run(db_session, question, *, voyage=None, onemap=None, overrides=None, near=True):
    return await retrieve(
        db_session, rules(question), overrides or Overrides(),
        lat=P_LAT if near else None, lng=P_LNG if near else None, now=datetime.now(UTC),
        voyage=voyage, onemap=onemap, top_k=5,
    )  # fmt: skip


# --- retrieval ---------------------------------------------------------------------------------

async def test_hybrid_ranks_the_matching_event_first(db_session, scenario):
    r = await run(db_session, "pottery classes near me", voyage=StubVoyage())
    assert r.events[0]["id"] == scenario["pottery"] and r.matched
    assert r.query.radius_m == 5000 and r.events[0]["distance_m"] == pytest.approx(1000, rel=0.02)


async def test_full_text_alone_still_ranks(db_session, scenario):
    r = await run(db_session, "yoga near me", voyage=None)
    assert r.events[0]["id"] == scenario["yoga"]


async def test_no_match_falls_back_to_filters_and_says_so(db_session, scenario):
    r = await run(db_session, "bouldering near me", voyage=StubVoyage())
    assert not r.matched and "no close matches" in r.notes[0]
    assert [e["id"] for e in r.events] == [scenario["pottery"], scenario["fair"], scenario["yoga"]]  # by distance


async def test_empty_semantic_query_orders_by_distance(db_session, scenario):
    r = await run(db_session, "anything near me")
    assert [e["id"] for e in r.events] == [scenario["pottery"], scenario["fair"], scenario["yoga"]]


async def test_rule_guessed_categories_rank_but_do_not_filter(db_session, scenario):
    r = await run(db_session, "career fairs near me", voyage=StubVoyage())
    assert r.query.categories == () and r.events[0]["id"] == scenario["fair"]


async def test_app_filters_override_the_question(db_session, scenario):
    r = await run(db_session, "pottery near me", voyage=StubVoyage(), overrides=Overrides(categories=["career_fair"]))
    assert [e["id"] for e in r.events] == [scenario["fair"]]


async def test_named_place_is_geocoded(db_session, scenario):
    onemap = StubOneMap({"Boon Lay MRT": (P_LAT + 3 * KM_LAT, P_LNG)})  # right next to the yoga
    r = await run(db_session, "yoga near Boon Lay MRT", voyage=StubVoyage(), onemap=onemap, near=False)
    assert onemap.searches == ["Boon Lay MRT"] and r.place == "Boon Lay MRT"
    assert r.events[0]["id"] == scenario["yoga"] and r.events[0]["distance_m"] < 100


async def test_in_planning_area_filters_by_area(db_session, scenario):
    pottery = await db_session.get(Event, scenario["pottery"])
    await db_session.execute(update(Venue).where(Venue.id == pottery.venue_id).values(planning_area="JURONG WEST"))
    r = await run(db_session, "events in Jurong West", near=False)
    assert r.query.area == "JURONG WEST" and [e["id"] for e in r.events] == [scenario["pottery"]]


async def test_near_me_without_a_location_asks_for_one(db_session, scenario):
    r = await run(db_session, "pottery near me", near=False, voyage=StubVoyage())
    assert any("share your location" in n for n in r.notes)


# --- answer helpers -----------------------------------------------------------------------------

def test_citations_outside_the_context_are_stripped():
    text, cited = validate_citations("Try Pottery [E5], or Yoga [E999]. Pottery again [E5].", {5, 7})
    assert cited == [5] and "[E999]" not in text and "Yoga." in text


def test_event_block_marks_news_and_distance():
    event = {
        "id": 42, "title": "Big Fair", "starts_at": datetime(2026, 10, 3, 2, tzinfo=UTC),
        "ends_at": None, "all_day": False, "venue": {"name": "Hall", "planning_area": "JURONG WEST"},
        "is_online": False, "distance_m": 1234.0, "is_free": True, "price_min_sgd": 0, "price_max_sgd": 0,
        "categories": ["career_fair"], "registration_url": "https://x", "sources": [],
        "summary": "A fair.", "confidence": "low",
    }  # fmt: skip
    block = event_block(event, "24h")
    assert 'id="E42"' in block and "Hall, Jurong West (1.2 km away)" in block
    assert "Price: Free | Categories: Career fairs & job fairs" in block and "news report" in block


# --- endpoint ------------------------------------------------------------------------------------

@pytest.fixture
async def api(db_session):
    query_parser._cache.clear()
    state = {"clients": AppClients(voyage=StubVoyage())}

    async def session_override():
        yield db_session

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_clients] = lambda: state["clients"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        client.state = state
        yield client
    app.dependency_overrides.clear()


def ask_body(q: str, **extra) -> dict:
    return {"q": q, "lat": P_LAT, "lng": P_LNG, **extra}


async def test_ask_json_without_an_llm_uses_the_template(api, scenario):
    resp = await api.post("/ask", json=ask_body("pottery near me"))
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert (body["parser"], body["answered_by"]) == ("rules", "template")
    assert body["cited_event_ids"][0] == scenario["pottery"]
    assert f"[E{scenario['pottery']}]" in body["answer_text"]
    assert body["events"][0]["title"] == "Pottery Wheel Basics"
    assert body["applied_filters"]["radius_km"] == 5.0 and body["applied_filters"]["semantic_query"] == "pottery"


async def test_ask_with_claude_validates_citations(api, scenario):
    llm = StubAnswerLLM(f"Try Pottery Wheel Basics [E{scenario['pottery']}] or the moon party [E123456789].")
    api.state["clients"] = AppClients(llm=llm, voyage=StubVoyage())
    body = (await api.post("/ask", json=ask_body("pottery near me", time_format="12h"))).json()
    assert body["answered_by"] == "claude" and body["cited_event_ids"] == [scenario["pottery"]]
    assert "E123456789" not in body["answer_text"]
    call = llm.stream_calls[0]
    assert call["model"] == "claude-sonnet-5" and "temperature" not in call
    assert "12-hour" in call["system"] and "<events>" in call["messages"][0]["content"]


async def test_ask_streams_server_sent_events(api, scenario):
    api.state["clients"] = AppClients(llm=StubAnswerLLM(f"Pottery [E{scenario['pottery']}] is closest."), voyage=StubVoyage())
    resp = await api.post("/ask", json=ask_body("pottery near me", stream=True))
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = [line.split(": ", 1)[1] for line in resp.text.splitlines() if line.startswith("event: ")]
    assert events[0] == "meta" and events[-1] == "done" and "delta" in events
    assert f'"cited_event_ids": [{scenario["pottery"]}]' in resp.text


async def test_refusal_and_api_errors_are_handled(api, scenario):
    api.state["clients"] = AppClients(llm=StubAnswerLLM("", stop_reason="refusal"), voyage=StubVoyage())
    assert (await api.post("/ask", json=ask_body("pottery near me"))).json()["answer_text"] == REFUSED

    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    api.state["clients"] = AppClients(llm=StubAnswerLLM(error=error), voyage=StubVoyage())
    body = (await api.post("/ask", json=ask_body("pottery near me"))).json()
    assert body["answered_by"] == "template" and body["cited_event_ids"][0] == scenario["pottery"]


async def test_no_results_skip_the_llm(api, scenario):
    llm = StubAnswerLLM("should not be used")
    api.state["clients"] = AppClients(llm=llm, voyage=StubVoyage())
    body = (await api.post("/ask", json=ask_body("pottery near me", filters={"categories": ["seniors"]}))).json()
    assert body["events"] == [] and "couldn't find any events" in body["answer_text"] and not llm.stream_calls


@pytest.mark.parametrize(
    "payload",
    [{"q": "x"}, {"q": "pottery", "filters": {"categories": ["raves"]}}, {"q": "pottery", "filters": {"area": "Atlantis"}}],
)
async def test_bad_requests(api, payload):
    assert (await api.post("/ask", json=payload)).status_code == 422

from datetime import UTC, datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.services import query_parser
from app.services.query_parser import parse_llm, parse_query, parse_rules

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # Fri 25 Sep, 23:00 SGT
MODEL = "claude-haiku-4-5-20251001"


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- rules ----------------------------------------------------------------------------------------

def test_skill_example_query():
    p = parse_rules("any free career fairs near Sengkang this weekend?", NOW)
    assert p.semantic_query == "career fairs"
    assert p.categories == ("career_fair",)
    assert (p.date_from, p.date_to) == (utc(2026, 9, 25, 16), utc(2026, 9, 27, 16))
    assert (p.place_text, p.place_mode, p.is_free, p.parser) == ("SENGKANG", "near", True, "rules")


@pytest.mark.parametrize(
    ("question", "window"),
    [
        ("AI meetups tonight", (utc(2026, 9, 25, 9), utc(2026, 9, 25, 16))),
        ("kids activities tomorrow", (utc(2026, 9, 25, 16), utc(2026, 9, 26, 16))),
        ("free things for kids this Saturday", (utc(2026, 9, 25, 16), utc(2026, 9, 26, 16))),
        ("talks on Monday", (utc(2026, 9, 27, 16), utc(2026, 9, 28, 16))),
        ("volunteering on weekends", (utc(2026, 9, 25, 16), utc(2026, 9, 27, 16))),
        ("webinars next week", (utc(2026, 9, 27, 16), utc(2026, 10, 4, 16))),
        ("concerts", (None, None)),
    ],
)
def test_dates(question, window):
    p = parse_rules(question, NOW)
    assert (p.date_from, p.date_to) == window


def test_places_areas_regions_and_postal_codes():
    assert parse_rules("events in Tampines", NOW).place_mode == "in"
    assert parse_rules("events in Tampines", NOW).semantic_query == ""
    p = parse_rules("workshops near 540123 within 3 km", NOW)
    assert (p.place_text, p.radius_km, p.semantic_query) == ("540123", 3.0, "workshops")
    assert parse_rules("what's on near Esplanade MRT", NOW).place_text == "Esplanade MRT"
    assert parse_rules("yoga near Boon Lay MRT", NOW).place_text == "Boon Lay MRT"  # not just the area
    assert parse_rules("yoga near Boon Lay", NOW).place_text == "BOON LAY"
    assert parse_rules("volunteering in the west", NOW).region == "WEST"
    assert parse_rules("markets in the north-east", NOW).region == "NORTH-EAST"
    assert parse_rules("walks along the east coast", NOW).region is None


def test_near_me_free_and_online():
    p = parse_rules("something fun near me", NOW)
    assert (p.near_me, p.semantic_query) == (True, "fun")
    assert parse_rules("free time activities", NOW).is_free is None
    assert parse_rules("online webinars about startups", NOW).online_only is True


def test_rule_parser_eval_does_not_regress():
    """The eval set (tests/rag_eval/queries.jsonl) scored 33/40 fully right on 2026-09-26."""
    from evals.parser_eval import NOW as EVAL_NOW
    from evals.parser_eval import load, score

    results = [score(case, parse_rules(case["q"], EVAL_NOW)) for case in load()]
    assert len(results) == 40
    assert sum(all(r.values()) for r in results) >= 33
    for field in ("dates", "place", "region", "free", "online_only"):
        assert all(r[field] for r in results), field


# --- LLM parser -----------------------------------------------------------------------------------

class StubParserLLM:
    def __init__(self, tool_input=None, error: Exception | None = None) -> None:
        self.tool_input, self.error, self.calls = tool_input, error, []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[SimpleNamespace(type="tool_use", name="search_events", input=self.tool_input)],
        )


LLM_OUTPUT = {
    "semantic_query": "career fairs",
    "categories": ["career_fair", "not_a_category"],
    "date_from": "2026-09-26T00:00:00+08:00",
    "date_to": "2026-09-28T00:00:00",
    "place_text": "Sengkang",
    "place_mode": "near",
    "region": None,
    "near_me": False,
    "radius_km": 500,
    "is_free": True,
    "include_online": True,
    "online_only": False,
}


@pytest.mark.anyio
async def test_llm_parser_request_and_result():
    llm = StubParserLLM(LLM_OUTPUT)
    p = await parse_llm(llm, "free career fairs near Sengkang this weekend", NOW, model=MODEL, has_location=True)
    call = llm.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "search_events"}
    assert "Friday 25 September 2026, 23:00" in call["messages"][0]["content"]
    assert "User location shared: yes" in call["messages"][0]["content"]
    assert p.parser == "llm" and p.categories == ("career_fair",)
    assert (p.date_from, p.date_to) == (utc(2026, 9, 25, 16), utc(2026, 9, 27, 16))  # naive = SGT
    assert (p.place_text, p.radius_km, p.is_free) == ("Sengkang", 50.0, True)  # radius clamped


@pytest.fixture(autouse=True)
def clear_cache():
    query_parser._cache.clear()


@pytest.mark.anyio
async def test_parse_query_falls_back_to_rules_on_api_errors():
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    p = await parse_query("career fairs this weekend", NOW, llm=StubParserLLM(error=error), model=MODEL, has_location=False)
    assert p.parser == "rules" and p.categories == ("career_fair",)


@pytest.mark.anyio
async def test_parse_query_is_cached():
    llm = StubParserLLM(LLM_OUTPUT)
    for _ in range(2):
        await parse_query("Career fairs  this weekend", NOW, llm=llm, model=MODEL, has_location=False)
    await parse_query("career fairs this weekend", NOW, llm=llm, model=MODEL, has_location=False)
    assert len(llm.calls) == 1  # same normalized question, same SGT day

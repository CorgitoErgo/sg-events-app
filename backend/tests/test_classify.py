"""The classifier against a stub Anthropic client: no network, no API key."""

from types import SimpleNamespace

import pytest

from pipeline.classify import (
    SUMMARY_MAX_CHARS,
    ClassificationError,
    EventText,
    classify_with_llm,
    clean_summary,
    enrichment_hash,
    parse_tool_input,
    rule_classification,
)

pytestmark = pytest.mark.anyio

MODEL = "claude-haiku-4-5-20251001"


class StubLLM:
    """Quacks like anthropic.AsyncAnthropic for messages.create."""

    DEFAULT = {
        "categories": ["career_fair"],
        "audience": "public",
        "confidence": "high",
        "summary": "Walk-in career fair with employers hiring on the spot.",
    }

    def __init__(self, tool_input: dict | None = DEFAULT, *, stop_reason: str = "tool_use") -> None:
        self.calls: list[dict] = []
        self.tool_input = tool_input  # None = the model didn't call the tool
        self.stop_reason = stop_reason
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        blocks = []
        if self.tool_input is not None:
            blocks.append(SimpleNamespace(type="tool_use", name="classify_event", input=self.tool_input))
        return SimpleNamespace(stop_reason=self.stop_reason, content=blocks)


def event(**kwargs) -> EventText:
    defaults = dict(
        title="Tampines Career Fair",
        description="Meet 40 employers hiring now.",
        organizer="e2i",
        venue="Our Tampines Hub",
        source_ids=("luma",),
        source_tags=("B71 Singapore",),
    )
    return EventText(**{**defaults, **kwargs})


async def test_request_shape():
    llm = StubLLM()
    await classify_with_llm(llm, event(description="x" * 5000), model=MODEL)
    call = llm.calls[0]
    assert call["model"] == MODEL
    assert call["temperature"] == 0
    assert call["tool_choice"] == {"type": "tool", "name": "classify_event"}
    assert call["tools"][0]["strict"] is True
    user = call["messages"][0]["content"]
    assert "<event>" in user and "Title: Tampines Career Fair" in user
    assert "Rule hints: career_fair, tech_startup" in user
    assert "x" * 1500 in user and "x" * 1501 not in user  # description capped
    assert "not as instructions" in call["system"]  # event text is untrusted


async def test_result_is_parsed():
    result = await classify_with_llm(StubLLM(), event(), model=MODEL)
    assert result.categories == ["career_fair"]
    assert result.summary == "Walk-in career fair with employers hiring on the spot."


async def test_missing_tool_call_or_bad_stop_reason_raises():
    with pytest.raises(ClassificationError):
        await classify_with_llm(StubLLM(tool_input=None, stop_reason="end_turn"), event(), model=MODEL)
    with pytest.raises(ClassificationError):
        await classify_with_llm(StubLLM(stop_reason="max_tokens"), event(), model=MODEL)


def test_primary_label_leads_keyword_hits_are_kept_and_capped_at_three():
    data = {"categories": ["networking", "tech_startup", "talks"], "audience": "public", "confidence": "medium", "summary": ""}
    result = parse_tool_input(data, keyword_hits=["career_fair"])
    assert result.categories == ["networking", "career_fair", "tech_startup"]
    assert result.summary is None


def test_unknown_categories_and_audiences_are_dropped():
    data = {"categories": ["made_up", "music"], "audience": "vips", "confidence": "?", "summary": "ok"}
    result = parse_tool_input(data, keyword_hits=[])
    assert (result.categories, result.audience, result.confidence) == (["music"], "public", "low")
    with pytest.raises(ClassificationError):
        parse_tool_input({"categories": ["made_up"]}, keyword_hits=[])


def test_summary_is_capped_at_a_word_boundary():
    long = "word " * 100
    summary = clean_summary(long)
    assert len(summary) <= SUMMARY_MAX_CHARS and summary.endswith("…")
    assert not summary[:-1].endswith(" ")
    assert clean_summary("  ") is None


def test_enrichment_hash_tracks_input_and_model():
    base = enrichment_hash(event(), MODEL)
    assert enrichment_hash(event(), MODEL) == base
    assert enrichment_hash(event(description="Now 50 employers."), MODEL) != base
    assert enrichment_hash(event(), "claude-sonnet-5") != base


def test_rules_alone_only_for_category_specific_sources():
    assert rule_classification(event()) is None  # Luma isn't category-specific

"""Discovery agent building blocks: domain rules, judge, budget, queries, Tavily, Eventbrite, extraction."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from pipeline.autosearch.agent import Budget, Limits, judge, repair_times, singapore_evidence
from pipeline.autosearch.eventbrite import event_id_from_url, to_raw_event
from pipeline.autosearch.extract import llm_events, page_text, structured_events
from pipeline.autosearch.runner import QUERY_PHRASES, plan_queries
from pipeline.autosearch.tavily import TavilyClient, TavilyError
from pipeline.domains import blocked_reason, host_of, is_news
from pipeline.normalize import normalize
from scrapers.base import RawEvent

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)


def raw(**kwargs) -> RawEvent:
    defaults = dict(source_id="web", source_url="https://events.example.sg/e/1", title="Career Fair",
                    fetched_at=NOW, start_raw="2026-10-03T10:00:00+08:00")  # fmt: skip
    return RawEvent(**{**defaults, **kwargs})


# --- domains ---------------------------------------------------------------------------------

def test_domain_rules():
    assert "prohibits" in blocked_reason("https://m.facebook.com/events/123")
    assert "ICS" in blocked_reason("https://luma.com/abc")
    assert "API-only" in blocked_reason("https://www.meetup.com/x")
    assert blocked_reason("https://www.eventbrite.sg/e/x-123456789") is None
    assert host_of("https://WWW.Straitstimes.com/a") == "straitstimes.com" and is_news("https://www.straitstimes.com/a")
    assert not is_news("https://www.nus.edu.sg/events")


# --- judge -------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("kwargs", "page", "expected"),
    [
        ({"lat": 1.3531, "lng": 103.9404}, "https://x.com/e", "map point in Singapore"),
        ({"lat": 51.5, "lng": -0.12, "address_raw": "Singapore Road, London"}, "https://x.sg/e", None),
        ({"postal_code": "528523", "address_raw": "1 Tampines Walk"}, "https://tickets.sg/e", "Singapore postal code 528523"),
        ({"postal_code": "560001", "address_raw": "MG Road"}, "https://tickets.in/e", None),  # a 6-digit Indian PIN
        ({"venue_raw": "Hall 2", "address_raw": "Expo, Singapore"}, "https://x.com/e", "address says Singapore"),
        ({"venue_raw": "Sengkang Community Club"}, "https://x.com/e", "venue in Sengkang"),
        ({"venue_raw": "National Museum"}, "https://x.com/e", None),  # "Museum" alone isn't evidence
        ({"venue_raw": "Zoom", "title": "Webinar for Singapore job seekers"}, "https://x.com/e", "online, for Singapore"),
        ({"venue_raw": "Zoom", "title": "Global webinar"}, "https://x.com/e", None),
    ],
)
def test_singapore_evidence(kwargs, page, expected):
    r = raw(**kwargs)
    assert singapore_evidence(r, normalize(r), page) == expected


def test_judge_rejects_past_far_cancelled_restricted_and_foreign():
    limits = Limits()
    sg = {"postal_code": "528523", "address_raw": "Singapore"}
    cases = {
        "already over": raw(start_raw="2026-09-01T10:00:00+08:00", **sg),
        "starts more than 120 days away": raw(start_raw="2027-06-01T10:00:00+08:00", **sg),
        "cancelled or postponed": raw(raw_payload={"eventStatus": "https://schema.org/EventCancelled"}, **sg),
        "not open to the public (students only)": raw(description="For NUS students only.", **sg),
        "no sign it's in Singapore": raw(venue_raw="Somewhere", source_url="https://events.example.com/e/1"),
    }
    for expected, r in cases.items():
        verdict = judge(r, normalize(r), r.source_url, now=NOW, limits=limits)
        assert (verdict.accept, verdict.reason) == (False, expected)
    ok = raw(**sg)
    verdict = judge(ok, normalize(ok), ok.source_url, now=NOW, limits=limits)
    assert verdict.accept and "Singapore postal code" in verdict.reason


# Real cases from the first live run (2026-09-26).
@pytest.mark.parametrize(
    ("start", "end", "expected_start", "expected_end", "note"),
    [
        # TechWeek: Singapore times labelled "Z" -> the literal reading ends at 01:00 SGT
        ("2026-09-29T09:00:00Z", "2026-09-30T17:00:00Z", "2026-09-29T09:00:00+08:00", "2026-09-30T17:00:00+08:00", "labelled Singapore times as UTC"),
        # OTR Listens: UTC written without a zone -> the literal reading starts at 01:30 SGT
        ("2026-10-10 01:30:00", None, "2026-10-10T09:30:00+08:00", None, "UTC times with no time zone"),
        # correct UTC ("Z") meaning a morning start in Singapore: untouched
        ("2026-10-10T23:30:00Z", None, "2026-10-10T23:30:00Z", None, None),
        # explicit +08:00 is always trusted, even early (a sunrise run)
        ("2026-10-10T05:30:00+08:00", None, "2026-10-10T05:30:00+08:00", None, None),
        # all-day dates are left alone
        ("2026-10-10", "2026-10-11", "2026-10-10", "2026-10-11", None),
    ],
)
def test_repair_times(start, end, expected_start, expected_end, note):
    fixed, got_note = repair_times(raw(start_raw=start, end_raw=end))
    assert (fixed.start_raw, fixed.end_raw) == (expected_start, expected_end)
    assert (got_note is None) == (note is None) and (note is None or note in got_note)


def test_times_from_apis_are_never_repaired():
    eb = raw(source_id="eventbrite", start_raw="2026-10-10T01:30:00Z")
    assert repair_times(eb) == (eb, None)


def test_foreign_time_zone_is_rejected_even_if_the_address_says_singapore():
    # World Bank page: "address": "Singapore" but the event is in Washington (-05:00)
    r = raw(start_raw="2026-12-14T21:18:00.000-05:00", venue_raw="World Bank Headquarters", address_raw="Singapore")
    verdict = judge(r, normalize(r), r.source_url, now=NOW, limits=Limits())
    assert (verdict.accept, verdict.reason) == (False, "published in another time zone (UTC-05:00)")


def test_budget_stops_at_the_first_limit():
    budget = Budget(Limits(max_searches=2, max_pages=3, max_events=2))
    assert budget.can_search() and budget.stop_reason() is None
    budget.pages = 3
    assert budget.stop_reason() == "opened 3 pages (limit 3)" and not budget.can_search()
    budget = Budget(Limits(max_events=2))
    budget.events = 2
    assert budget.stop_reason() == "saved 2 events (limit 2)"
    budget = Budget(Limits(max_seconds=0))
    assert "time limit" in budget.stop_reason()


def test_queries_rotate_least_recently_used_first():
    recent = {f"{QUERY_PHRASES[0]} Singapore September 2026": NOW, f"{QUERY_PHRASES[1]} Singapore September 2026": NOW}
    planned = plan_queries(NOW, recent=recent, count=3)
    assert planned == [f"{p} Singapore September 2026" for p in QUERY_PHRASES[2:5]]
    assert plan_queries(NOW, recent={}, count=5, custom=[" free talks ", ""]) == ["free talks"]


# --- Tavily ------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tavily_request_and_results():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": [
            {"url": "https://a.sg/e", "title": "A", "content": "x", "score": 0.9},
            {"url": "ftp://bad", "title": "B"},
        ]})  # fmt: skip

    async with TavilyClient("tvly-key", transport=httpx.MockTransport(handler)) as t:
        hits = await t.search("career fair Singapore", max_results=50, exclude_domains=["facebook.com"])
    body = json.loads(seen[0].content)
    assert seen[0].headers["Authorization"] == "Bearer tvly-key"
    assert (body["country"], body["search_depth"], body["max_results"], body["exclude_domains"]) == ("singapore", "basic", 20, ["facebook.com"])
    assert [h.url for h in hits] == ["https://a.sg/e"]


@pytest.mark.anyio
async def test_tavily_errors_are_explained():
    async with TavilyClient("bad", transport=httpx.MockTransport(lambda r: httpx.Response(401))) as t:
        with pytest.raises(TavilyError, match="TAVILY_API_KEY"):
            await t.search("x")
    responses = [httpx.Response(429, headers={"Retry-After": "0"}), httpx.Response(200, json={"results": []})]
    async with TavilyClient("k", transport=httpx.MockTransport(lambda r: responses.pop(0))) as t:
        assert await t.search("x") == []


# --- Eventbrite --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.eventbrite.sg/e/tampines-career-fair-tickets-1234567890123", "1234567890123"),
        ("https://www.eventbrite.com/e/123456789012?aff=ebdssbdestsearch", "123456789012"),
        ("https://www.eventbrite.sg/d/singapore--singapore/career-fair/", None),
        ("https://www.eventbrite.sg/o/organiser-12345678901", None),
    ],
)
def test_eventbrite_event_ids(url, expected):
    assert event_id_from_url(url) == expected


EVENTBRITE_EVENT = {  # synthetic, in the shape of GET /v3/events/{id}/?expand=venue,organizer,ticket_availability
    "id": "1234567890123", "name": {"text": "Fun Run @ East Coast"}, "summary": "5 km run for all.",
    "url": "https://www.eventbrite.sg/e/fun-run-1234567890123", "start": {"utc": "2026-10-10T23:30:00Z"},
    "end": {"utc": "2026-10-11T02:00:00Z"}, "status": "live", "online_event": False, "is_free": False,
    "venue": {"name": "East Coast Park", "latitude": "1.3008", "longitude": "103.9122",
              "address": {"localized_address_display": "East Coast Park Service Rd, Singapore 449876", "postal_code": "449876", "country": "SG"}},
    "organizer": {"name": "Run Club SG"}, "logo": {"original": {"url": "https://img.evbuc.com/x.jpg"}},
    "ticket_availability": {"minimum_ticket_price": {"major_value": "15.00", "currency": "SGD"},
                            "maximum_ticket_price": {"major_value": "25.00", "currency": "SGD"}},
}  # fmt: skip


def test_eventbrite_api_event_to_raw():
    r = to_raw_event(EVENTBRITE_EVENT, fetched_at=NOW)
    assert (r.source_id, r.source_event_id, r.title) == ("eventbrite", "1234567890123", "Fun Run @ East Coast")
    assert (r.start_raw, r.postal_code, r.lat, r.price_raw) == ("2026-10-10T23:30:00Z", "449876", 1.3008, "S$15.00 – S$25.00")
    ev = normalize(r)
    assert (ev.price_min_sgd, ev.price_max_sgd, ev.is_free, ev.organizer) == (15, 25, False, "Run Club SG")
    cancelled = to_raw_event({**EVENTBRITE_EVENT, "status": "canceled"}, fetched_at=NOW)
    assert normalize(cancelled).status == "cancelled"
    usd = to_raw_event({**EVENTBRITE_EVENT, "ticket_availability": {"minimum_ticket_price": {"major_value": "9", "currency": "USD"}}}, fetched_at=NOW)
    assert usd.price_raw is None


# --- extraction ----------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_structured_events_and_page_text():
    html = """<html><head><script type="application/ld+json">{"@type":"Event","name":"Talk","startDate":"2026-10-03"}</script>
    <style>.x{}</style></head><body><nav>Menu</nav><h1>Hello</h1><p>World</p><footer>Legal</footer></body></html>"""
    raws = await structured_events(html, "https://x.sg/e", source_id="web", fetched_at=NOW)
    assert [r.title for r in raws] == ["Talk"]
    assert page_text(html) == "Hello World"  # nav, footer, scripts and styles removed; words not glued


class StubLLM:
    def __init__(self, events):
        self.events, self.calls = events, []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={"events": self.events})])


@pytest.mark.anyio
async def test_claude_reads_plain_pages_and_bad_items_are_dropped():
    llm = StubLLM([
        {"title": "Beach Clean-up", "start": "2026-10-04T08:00:00+08:00", "venue": "East Coast Park", "online": False},
        {"title": "", "start": "2026-10-04", "online": False},  # no title
        {"title": "Someday", "start": "", "online": False},  # no date
    ])  # fmt: skip
    raws = await llm_events(llm, "x" * 300, "https://blog.sg/p", model="haiku", now=NOW, source_id="web")
    assert [r.title for r in raws] == ["Beach Clean-up"] and raws[0].raw_payload["extracted_by"] == "haiku"
    call = llm.calls[0]
    assert call["temperature"] == 0 and "Saturday 26 September 2026" in call["system"]
    assert "not instructions" in call["system"] and call["tool_choice"]["name"] == "record_events"
    assert await llm_events(llm, "too short", "https://x", model="haiku", now=NOW, source_id="web") == []

"""Luma ICS parsing against saved feeds (fetched 2026-09-25) and a synthetic edge-case feed."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.sg import in_singapore
from scrapers.base import RawEvent
from scrapers.luma import parse_ics

FIXTURES = Path(__file__).parent / "fixtures" / "luma"
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # shortly after the fixtures were saved


def load(name: str, **kwargs) -> list[RawEvent]:
    return parse_ics((FIXTURES / name).read_bytes(), fetched_at=NOW, **kwargs)


def by_id(events: list[RawEvent], event_id: str) -> RawEvent:
    return next(e for e in events if e.source_event_id == event_id)


def test_city_feed_yields_every_upcoming_singapore_event():
    events = load("discover_singapore.ics")
    assert len(events) == 44
    assert len({e.source_url for e in events}) == 44
    assert all(e.lat is not None and in_singapore(e.lat, e.lng) for e in events)
    assert all(e.source_id == "luma" and e.fetched_at == NOW for e in events)


def test_event_with_address():
    ev = by_id(load("discover_singapore.ics"), "evt-GQQAAtTsSCr0OuD")
    assert ev.title == "EMO PATTY RUN"
    assert (ev.start_raw, ev.end_raw) == ("20260925T223000Z", "20260926T030000Z")
    assert (ev.venue_raw, ev.address_raw) == ("Burgs Singapore", "Singapore")
    assert ev.source_url == "https://luma.com/event/evt-GQQAAtTsSCr0OuD"
    assert ev.registration_url == "https://luma.com/vv56ss6f"
    assert ev.organizer == "Plussantai"
    assert ev.lat == pytest.approx(1.3012, abs=1e-3) and ev.lng == pytest.approx(103.8599, abs=1e-3)
    assert ev.description.startswith("EMO PATTY RUN")
    assert "Hosted by" not in ev.description and "Address:" not in ev.description
    assert ev.raw_payload["ics_status"] == "TENTATIVE"
    assert "BEGIN:VEVENT" in ev.raw_payload["ics"]


def test_hidden_address_all_day_event_keeps_geo():
    ev = by_id(load("discover_singapore.ics"), "evt-GEzYcX1i5FjiSGT")
    assert (ev.start_raw, ev.end_raw) == ("20260925", "20260928")
    assert ev.venue_raw is None and ev.address_raw is None
    assert ev.lat is not None
    assert ev.description.startswith("Co-organized with NUS Enterprise")


def test_calendar_feed_drops_past_and_overseas_events_and_tags_calendar():
    events = load("calendar_b71_singapore.ics", feed_name="B71 Singapore")
    # 73 events in the feed; only two are upcoming, and three past ones are overseas anyway.
    assert [e.source_event_id for e in events] == ["evt-fqp1NSy7zCSmYCn", "evt-M5zvnKZB2G6AOlc"]
    assert all(e.source_categories == ["B71 Singapore"] for e in events)


def test_edge_cases():
    events = {e.source_event_id: e for e in load("edge_cases.ics")}
    assert "evt-sanfrancisco01" not in events  # outside Singapore
    assert "evt-pastevent0001" not in events  # over

    postal = events["evt-postal0000001"]
    assert postal.venue_raw == "Our Tampines Hub"
    assert postal.address_raw == "1 Tampines Walk, Singapore 528523"
    assert postal.postal_code == "528523"
    assert postal.description == "Meet 40 employers hiring now."

    assert events["evt-cancelled00001"].raw_payload["ics_status"] == "CANCELLED"

    external = events["evt-external00001"]
    assert external.registration_url == "https://bit.ly/example-event"
    assert external.source_url == "https://luma.com/event/evt-external00001"
    assert external.description is None

    online = events["evt-online000001"]
    assert online.lat is None and online.venue_raw is None
    assert "zoom.us" in online.description

    tz = events["evt-tzid00000001"]
    assert tz.start_raw == "2026-10-10T19:00:00+08:00"
    assert tz.postal_code == "018953"


def test_calendar_feeds_need_singapore_evidence_when_there_is_no_geo():
    city = {e.source_event_id for e in load("edge_cases.ics")}
    calendar = {e.source_event_id for e in load("edge_cases.ics", require_sg_evidence=True)}
    assert city - calendar == {"evt-online000001"}  # Zoom call, never mentions Singapore
    assert "evt-nogeosg00001" in calendar  # no GEO, but a Singapore address

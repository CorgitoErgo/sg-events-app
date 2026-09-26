from datetime import UTC, datetime

from scrapers.jsonld import find_events, price_text, to_raw_event

NOW = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
PAGE = "https://tickets.example.sg/e/1"


def raw(obj, page=PAGE):
    return to_raw_event(obj, page_url=page, source_id="manual", fetched_at=NOW)


def test_find_events_walks_graphs_lists_and_subtypes():
    data = [
        {"@graph": [{"@type": "Organization"}, {"@type": "EducationEvent", "name": "A"}]},
        {"@type": ["Event", "Thing"], "name": "B", "subEvent": [{"@type": "Event", "name": "C"}]},
        {"@type": "http://schema.org/MusicEvent", "name": "D"},
        {"@type": "Eventually", "name": "not an event"},
    ]
    assert [e["name"] for e in find_events(data)] == ["A", "B", "C", "D"]


def test_place_with_postal_address_and_geo():
    ev = raw({
        "@type": "Event", "name": "Fair", "startDate": "2026-10-03T10:00:00+08:00",
        "location": {"@type": "Place", "name": "Our Tampines Hub",
                     "address": {"@type": "PostalAddress", "streetAddress": "1 Tampines Walk", "postalCode": "528523"},
                     "geo": {"latitude": "1.3531", "longitude": 103.9404}},
        "organizer": [{"@type": "Organization", "name": "e2i"}],
        "url": "/e/fair",
    })  # fmt: skip
    assert (ev.venue_raw, ev.address_raw, ev.postal_code) == ("Our Tampines Hub", "1 Tampines Walk, 528523", "528523")
    assert (ev.lat, ev.lng, ev.organizer) == (1.3531, 103.9404, "e2i")
    assert ev.source_url == "https://tickets.example.sg/e/fair" and ev.start_raw == "2026-10-03T10:00:00+08:00"


def test_virtual_location_marks_online_and_hybrid_prefers_the_place():
    online = raw({"@type": "Event", "name": "Webinar", "location": {"@type": "VirtualLocation", "url": "https://zoom.us/j/1"}})
    assert online.venue_raw is None and "Online" in online.raw_payload["eventAttendanceMode"]
    hybrid = raw({
        "@type": "Event", "name": "Talk", "eventAttendanceMode": "https://schema.org/MixedEventAttendanceMode",
        "location": [{"@type": "VirtualLocation"}, {"@type": "Place", "name": "NLB Central", "address": "100 Victoria St, 188064"}],
    })  # fmt: skip
    assert hybrid.venue_raw == "NLB Central" and hybrid.postal_code == "188064"
    assert "Mixed" in hybrid.raw_payload["eventAttendanceMode"]


def test_description_html_is_cleaned_and_status_kept():
    ev = raw({"@type": "Event", "name": "X &amp; Y", "description": "<p>Line <b>one</b></p><p>Line two</p>",
              "eventStatus": "https://schema.org/EventCancelled"})  # fmt: skip
    assert ev.title == "X & Y"
    assert ev.description == "Line one\nLine two" or ev.description == "Line one Line two"
    assert ev.raw_payload["eventStatus"].endswith("EventCancelled")


def test_prices():
    assert price_text([{"price": "0", "priceCurrency": "SGD"}]) == "Free"
    assert price_text([{"lowPrice": 10, "highPrice": "25.00", "priceCurrency": "SGD"}]) == "S$10 – S$25"
    assert price_text([{"price": "15"}, {"price": "15"}]) == "S$15"
    assert price_text([{"price": "100"}, {"price": "12.50"}]) == "S$12.5 – S$100"
    assert price_text([{"price": "20", "priceCurrency": "USD"}]) is None  # not SGD: unknown, not wrong
    assert price_text([], free_flag=True) == "Free"
    assert price_text([]) is None


def test_unnamed_objects_are_skipped():
    assert raw({"@type": "Event", "startDate": "2026-10-03"}) is None

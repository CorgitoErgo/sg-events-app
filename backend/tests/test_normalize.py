from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from pipeline.normalize import (
    NormalizationError,
    fingerprint,
    geohash,
    normalize,
    normalize_title,
    parse_price,
    parse_when,
)
from scrapers.base import RawEvent
from scrapers.luma import parse_ics

BASE = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # Fri 25 Sep 2026, 23:00 SGT


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def raw(**kwargs) -> RawEvent:
    defaults = dict(
        source_id="test",
        source_url="https://example.com/e/1",
        title="Some Event",
        fetched_at=BASE,
        start_raw="20261003T020000Z",
    )
    return RawEvent(**{**defaults, **kwargs})


# --- dates -----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        # ICS UTC datetimes
        ("20260925T223000Z", "20260926T030000Z", (utc(2026, 9, 25, 22, 30), utc(2026, 9, 26, 3), False)),
        # ICS all-day: DTEND is exclusive
        ("20260925", "20260928", (utc(2026, 9, 24, 16), utc(2026, 9, 27, 16), True)),
        # ICS floating time = SGT
        ("20261010T190000", None, (utc(2026, 10, 10, 11), None, False)),
        # ISO with offset
        ("2026-10-03T10:00:00+08:00", None, (utc(2026, 10, 3, 2), None, False)),
        # ISO dates (JSON-LD): end date is inclusive
        ("2026-10-03", "2026-10-05", (utc(2026, 10, 2, 16), utc(2026, 10, 5, 16), True)),
        # Human text, from the event-schema skill
        ("Sat 3 Oct, 10am–4pm", None, (utc(2026, 10, 3, 2), utc(2026, 10, 3, 8), False)),
        ("3–5 Oct", None, (utc(2026, 10, 2, 16), utc(2026, 10, 5, 16), True)),
        ("3 Oct – 2 Nov", None, (utc(2026, 10, 2, 16), utc(2026, 11, 2, 16), True)),
        ("Sat, 3 Oct", None, (utc(2026, 10, 2, 16), None, True)),
        ("3 Oct, 3–5pm", None, (utc(2026, 10, 3, 7), utc(2026, 10, 3, 9), False)),
        ("Sat 3 Oct, 10pm–2am", None, (utc(2026, 10, 3, 14), utc(2026, 10, 3, 18), False)),
        ("28 Dec – 2 Jan", None, (utc(2026, 12, 27, 16), utc(2027, 1, 2, 16), True)),
        ("3 Oct 2026 7pm", "3 Oct 2026 9.30pm", (utc(2026, 10, 3, 11), utc(2026, 10, 3, 13, 30), False)),
    ],
)
def test_parse_when(start, end, expected):
    assert parse_when(start, end, relative_base=BASE) == expected


def test_recently_past_date_without_year_is_not_pushed_to_next_year():
    starts_at, _, _ = parse_when("20 Sep", None, relative_base=BASE)
    assert starts_at == utc(2026, 9, 19, 16)


def test_date_without_year_over_30_days_past_means_next_year():
    starts_at, _, _ = parse_when("1 Aug", None, relative_base=BASE)
    assert starts_at == utc(2027, 7, 31, 16)


def test_end_before_start_is_dropped():
    assert parse_when("20261003T100000Z", "20261003T090000Z", relative_base=BASE)[1] is None


@pytest.mark.parametrize("start", [None, "", "TBC", "Date to be announced"])
def test_unparseable_start_raises(start):
    with pytest.raises(NormalizationError):
        parse_when(start, None, relative_base=BASE)


# --- price -----------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Free", (Decimal(0), Decimal(0), True)),
        ("Free admission", (Decimal(0), Decimal(0), True)),
        ("$0", (Decimal(0), Decimal(0), True)),
        ("$10 – $25", (Decimal(10), Decimal(25), False)),
        ("S$15", (Decimal(15), Decimal(15), False)),
        ("From $8", (Decimal(8), None, False)),
        ("Members $5 / Public $8", (Decimal(5), Decimal(8), False)),
        ("Free for members, $10 for public", (Decimal(0), Decimal(10), False)),
        ("SGD 1,200", (Decimal(1200), Decimal(1200), False)),
        ("25 SGD", (Decimal(25), Decimal(25), False)),
        ("Tickets at the door", (None, None, None)),
        (None, (None, None, None)),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


# --- online / status / audience ----------------------------------------------------------

@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"venue_raw": "Zoom"}, True),
        ({"title": "Webinar: Careers in AI"}, True),
        ({"title": "Resume Clinic (Online)"}, True),
        ({"description": "Join at https://zoom.us/j/1"}, True),
        ({"raw_payload": {"eventAttendanceMode": "https://schema.org/MixedEventAttendanceMode"}}, True),
        ({"description": "Register online before 1 Oct.", "venue_raw": "Sengkang CC"}, False),
        ({"title": "Online Marketing Masterclass", "venue_raw": "NLB Central"}, False),
    ],
)
def test_detect_online(kwargs, expected):
    assert normalize(raw(**kwargs)).is_online is expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"raw_payload": {"ics_status": "CANCELLED"}}, "cancelled"),
        ({"raw_payload": {"eventStatus": "https://schema.org/EventPostponed"}}, "cancelled"),
        ({"title": "[CANCELLED] Beach Clean-up"}, "cancelled"),
        ({"description": "Due to weather, this event has been cancelled."}, "cancelled"),
        ({"title": "Cancel Culture: A Panel"}, "active"),
        ({"raw_payload": {"ics_status": "TENTATIVE"}}, "active"),
    ],
)
def test_detect_status(kwargs, expected):
    assert normalize(raw(**kwargs)).status == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Career Fair (for NUS students only)", "students_only"),
        ("Open to SMU students only.", "students_only"),
        ("Members-only mixer", "members_only"),
        ("This session is exclusively for NTU alumni.", "alumni"),
        ("Hear from alumni speakers about their careers.", "public"),
        ("Students and working adults welcome!", "public"),
    ],
)
def test_detect_audience(text, expected):
    assert normalize(raw(description=text)).audience == expected


# --- fingerprint -----------------------------------------------------------------------------

def test_normalize_title_drops_years_punctuation_and_filler():
    assert normalize_title("NTUC Career Fair 2026 @ Singapore!") == "ntuc career fair"
    assert normalize_title("职业博览会 2026") == "职业博览会"


def test_fingerprint_matches_title_variants_but_not_other_days():
    start = utc(2026, 10, 3, 2)
    a = fingerprint("NTUC Career Fair 2026 @ Singapore", start, "545025", None, None)
    b = fingerprint("ntuc career fair", start, "545025", None, None)
    c = fingerprint("NTUC Career Fair", utc(2026, 10, 4, 2), "545025", None, None)
    assert a == b != c


def test_fingerprint_uses_sgt_date_not_utc_date():
    # 23:30 UTC on 2 Oct is 07:30 SGT on 3 Oct: same SGT day as a 10am start.
    assert fingerprint("x", utc(2026, 10, 2, 23, 30), None, 1.3, 103.8) == fingerprint(
        "x", utc(2026, 10, 3, 2), None, 1.3, 103.8
    )


def test_geohash_reference_value():
    assert geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"  # Wikipedia's worked example


# --- end to end over the edge-case feed ---------------------------------------------------

def test_normalize_luma_edge_cases():
    raws = parse_ics(
        (Path(__file__).parent / "fixtures" / "luma" / "edge_cases.ics").read_bytes(), fetched_at=BASE
    )
    events = {e.source_url.rsplit("/", 1)[1]: normalize(e) for e in raws}

    fair = events["evt-postal0000001"]
    assert fair.starts_at == utc(2026, 10, 3, 2) and fair.ends_at == utc(2026, 10, 3, 8)
    assert fair.venue.name == "Our Tampines Hub" and fair.venue.postal_code == "528523"
    assert (fair.status, fair.is_online, fair.audience) == ("active", False, "public")
    assert fair.organizer == "e2i"

    assert events["evt-cancelled00001"].status == "cancelled"
    assert events["evt-cancelled00001"].venue is None  # address hidden

    online = events["evt-online000001"]
    assert online.is_online and online.lat is None and online.venue is None

    lantern = events["evt-tzid00000001"]
    assert lantern.starts_at == utc(2026, 10, 10, 11)
    assert lantern.venue.name == "Gardens by the Bay"


def test_point_outside_singapore_is_dropped():
    ev = normalize(raw(lat=103.85, lng=1.29, venue_raw="Swapped Hall"))  # lat/lng swapped
    assert ev.lat is None and ev.lng is None and ev.venue.lat is None

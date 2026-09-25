from datetime import UTC, date, datetime

import pytest

from app.privacy import CoordinateRoundingFilter, round_coords
from app.timewindows import explicit_window, preset_window


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


FRIDAY_NIGHT = utc(2026, 9, 25, 15, 0)  # Fri 25 Sep, 23:00 SGT


def test_today_is_the_sgt_calendar_day():
    assert preset_window("today", FRIDAY_NIGHT) == (utc(2026, 9, 24, 16), utc(2026, 9, 25, 16))


@pytest.mark.parametrize(
    "now",
    [
        FRIDAY_NIGHT,
        utc(2026, 9, 26, 3),  # Saturday 11:00 SGT
        utc(2026, 9, 27, 12),  # Sunday 20:00 SGT: the weekend in progress
        utc(2026, 9, 21, 1),  # Monday: the coming weekend
    ],
)
def test_this_weekend_is_saturday_to_monday_sgt(now):
    assert preset_window("weekend", now) == (utc(2026, 9, 25, 16), utc(2026, 9, 27, 16))


def test_week_and_month_run_from_now():
    assert preset_window("week", FRIDAY_NIGHT) == (FRIDAY_NIGHT, utc(2026, 10, 2, 15))
    assert preset_window("month", FRIDAY_NIGHT)[1] == utc(2026, 10, 25, 15)


def test_explicit_dates_are_sgt_days_and_date_to_is_inclusive():
    start, end = explicit_window(date(2026, 10, 3), date(2026, 10, 5), FRIDAY_NIGHT)
    assert (start, end) == (utc(2026, 10, 2, 16), utc(2026, 10, 5, 16))


def test_explicit_defaults_to_the_next_30_days():
    assert explicit_window(None, None, FRIDAY_NIGHT) == (FRIDAY_NIGHT, utc(2026, 10, 25, 15))


def test_naive_datetimes_are_sgt():
    start, _ = explicit_window(datetime(2026, 10, 3, 10, 0), None, FRIDAY_NIGHT)
    assert start == utc(2026, 10, 3, 2)


# --- privacy -------------------------------------------------------------------------------------

def test_coordinates_are_rounded_in_logged_paths():
    path = "/events?lat=1.352083&lng=103.819836&radius_km=5&category=music"
    assert round_coords(path) == "/events?lat=1.35&lng=103.82&radius_km=5&category=music"
    assert round_coords("/events?flat=1.23456") == "/events?flat=1.23456"  # only lat/lng params


def test_access_log_filter_rewrites_uvicorn_record():
    import logging

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/events?lat=1.3521&lng=103.8198", "1.1", 200), None,
    )  # fmt: skip
    assert CoordinateRoundingFilter().filter(record)
    assert "lat=1.35&lng=103.82" in record.getMessage()

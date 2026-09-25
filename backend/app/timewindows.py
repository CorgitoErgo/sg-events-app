"""Date windows in Singapore time. Shared by /events presets and (later) the /ask query parser.

All windows are [start, end) in UTC. "This weekend" is the coming Saturday 00:00 to
Monday 00:00 SGT, or the current weekend when it's already Saturday or Sunday.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

from pipeline.sg import SGT

Preset = Literal["today", "weekend", "week", "month"]
DEFAULT_WINDOW_DAYS = 30


def sgt_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=SGT)


def preset_window(preset: Preset, now: datetime) -> tuple[datetime, datetime]:
    today = now.astimezone(SGT).date()
    if preset == "today":
        start, end = sgt_midnight(today), sgt_midnight(today + timedelta(days=1))
    elif preset == "weekend":
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        if today.weekday() == 6:  # Sunday: the weekend that's in progress
            saturday = today - timedelta(days=1)
        start, end = sgt_midnight(saturday), sgt_midnight(saturday + timedelta(days=2))
    elif preset == "week":
        start, end = now, now + timedelta(days=7)
    else:  # month
        start, end = now, now + timedelta(days=DEFAULT_WINDOW_DAYS)
    return start.astimezone(UTC), end.astimezone(UTC)


def explicit_window(
    date_from: date | datetime | None, date_to: date | datetime | None, now: datetime
) -> tuple[datetime, datetime]:
    """Plain dates are SGT calendar days, and date_to is inclusive ("to 5 Oct" = through 5 Oct)."""
    start = _as_utc(date_from, end=False) if date_from is not None else now
    if date_to is not None:
        end = _as_utc(date_to, end=True)
    else:
        end = max(start, now) + timedelta(days=DEFAULT_WINDOW_DAYS)
    return start, end


def _as_utc(value: date | datetime, *, end: bool) -> datetime:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=SGT)).astimezone(UTC)
    return sgt_midnight(value + timedelta(days=1) if end else value).astimezone(UTC)

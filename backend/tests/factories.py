"""Test data builders."""

import uuid
from datetime import UTC, datetime, timedelta

from scrapers.base import RawEvent


def ics_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def make_raw(*, days_ahead: int = 7, hour_utc: int = 2, **kwargs) -> RawEvent:
    """A RawEvent a week from now (so it counts as upcoming) with a unique title and URL."""
    tag = uuid.uuid4().hex[:8]
    start = (datetime.now(UTC) + timedelta(days=days_ahead)).replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    defaults = dict(
        source_id="test",
        source_url=f"https://example.com/e/{tag}",
        source_event_id=f"evt-{tag}",
        title=f"Career Fair {tag}",
        fetched_at=datetime.now(UTC),
        start_raw=ics_utc(start),
        end_raw=ics_utc(start + timedelta(hours=6)),
        venue_raw=f"Test Hall {tag}",
        address_raw="1 Tampines Walk, Singapore 528523",
        postal_code="528523",
        lat=1.3533,
        lng=103.9404,
        organizer="e2i",
    )
    return RawEvent(**{**defaults, **kwargs})

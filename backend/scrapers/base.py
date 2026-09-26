"""Adapter contract shared by every source (see the sg-event-scraping skill).

Adapters fetch and parse only. They never write to the DB, classify or geocode.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from scrapers.http import PoliteClient


@dataclass(slots=True)
class RawEvent:
    """Deliberately loose: strings as found, parsing belongs in pipeline.normalize."""

    source_id: str
    source_url: str
    title: str
    fetched_at: datetime
    source_event_id: str | None = None
    description: str | None = None
    start_raw: str | None = None  # exactly as found, e.g. "Sat, 3 Oct, 10am–4pm"
    end_raw: str | None = None
    venue_raw: str | None = None  # "Our Tampines Hub"
    address_raw: str | None = None  # "1 Tampines Walk, Singapore 528523"
    postal_code: str | None = None  # 6 digits if found anywhere on the page
    lat: float | None = None
    lng: float | None = None
    price_raw: str | None = None
    organizer: str | None = None
    image_url: str | None = None
    registration_url: str | None = None
    language: str | None = None  # "en", "zh", "ms", "ta"
    source_categories: list[str] = field(default_factory=list)  # site's own tags
    raw_payload: dict[str, Any] = field(default_factory=dict)  # for debugging


class SourceBlocked(Exception):
    """The source refused us (401/403/CAPTCHA/robots.txt). Stop and report; never evade."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{reason}: {url}")
        self.url = url
        self.reason = reason


class SourceAdapter(Protocol):
    source_id: str  # "luma", "onepa", "nus_events" ...
    base_urls: list[str]
    schedule: str  # cron, e.g. "0 */6 * * *"
    min_delay_s: float

    def discover(self, client: "PoliteClient") -> AsyncIterator[str]:
        """Yield event detail URLs (or feed / API page URLs)."""
        ...

    async def fetch_and_parse(self, client: "PoliteClient", url: str) -> list[RawEvent]:
        """Fetch one URL and return zero or more RawEvents."""
        ...

    # Optional: `async def check_gone(self, client, source_url) -> bool`, True when the
    # event's page is definitely gone (404/410). Adapters define it only when re-fetching
    # an event page is allowed and meaningful; pipeline.freshness then cancels events the
    # source stopped listing. Without it, such events simply expire when they end.

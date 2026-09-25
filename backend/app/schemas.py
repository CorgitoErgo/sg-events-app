"""API response models. Times are UTC ISO 8601; the app renders them in Asia/Singapore."""

from datetime import datetime

from pydantic import BaseModel


class VenueOut(BaseModel):
    name: str
    address: str | None
    postal_code: str | None
    planning_area: str | None
    region: str | None


class Location(BaseModel):
    lat: float
    lng: float


class SourceOut(BaseModel):
    source_id: str
    url: str


class EventOut(BaseModel):
    id: int
    title: str
    summary: str | None
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    is_online: bool
    is_free: bool | None
    price_min_sgd: float | None
    price_max_sgd: float | None
    categories: list[str]
    audience: str
    organizer: str | None
    image_url: str | None
    registration_url: str | None
    confidence: str  # "low" = news/poster-derived: the app suggests checking the source
    status: str
    venue: VenueOut | None
    location: Location | None
    distance_m: float | None  # only when searching around a point
    sources: list[SourceOut]


class SessionOut(BaseModel):
    starts_at: datetime
    ends_at: datetime | None


class EventDetail(EventOut):
    title_alt: str | None
    description: str | None
    language: list[str] | None
    sessions: list[SessionOut]
    first_seen_at: datetime
    last_seen_at: datetime


class AppliedFilters(BaseModel):
    """Echoed back so the app can show the filters as editable chips."""

    categories: list[str]
    date_from: datetime
    date_to: datetime
    when: str | None
    lat: float | None
    lng: float | None
    radius_km: float | None
    area: str | None
    region: str | None
    free: bool | None
    online: str
    include_restricted: bool
    sort: str


class EventsPage(BaseModel):
    items: list[EventOut]
    total: int
    limit: int
    offset: int
    next_offset: int | None
    filters: AppliedFilters

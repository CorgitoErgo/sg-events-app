"""Eventbrite event links -> the official API (sg-event-scraping skill: API before HTML).

GET /v3/events/{id}/?expand=venue,organizer,ticket_availability with the private token.
The public search endpoint was removed in 2020, but event-by-id still works, so an
Eventbrite link found by web search is read through the API instead of its page.
"""

import re
from datetime import datetime
from typing import Any

import httpx

from scrapers.base import RawEvent

API = "https://www.eventbriteapi.com/v3/events/{id}/"
_EVENT_URL = re.compile(r"eventbrite\.[a-z.]+/e/(?:[^/?#]*?-)?(\d{9,})(?:[/?#]|$)", re.IGNORECASE)


class EventbriteError(Exception):
    pass


def event_id_from_url(url: str) -> str | None:
    m = _EVENT_URL.search(url)
    return m.group(1) if m else None


class EventbriteClient:
    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30.0, transport=transport)

    async def __aenter__(self) -> "EventbriteClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def event(self, event_id: str) -> dict[str, Any] | None:
        resp = await self._http.get(
            API.format(id=event_id), params={"expand": "venue,organizer,ticket_availability"}
        )
        if resp.status_code == 404:
            return None
        if resp.status_code in (401, 403):
            raise EventbriteError("Eventbrite rejected the token (check EVENTBRITE_TOKEN: the private token).")
        if resp.status_code != 200:
            raise EventbriteError(f"Eventbrite API -> HTTP {resp.status_code}")
        return resp.json()


def to_raw_event(data: dict[str, Any], *, fetched_at: datetime) -> RawEvent:
    venue = data.get("venue") or {}
    address = venue.get("address") or {}
    tickets = data.get("ticket_availability") or {}
    return RawEvent(
        source_id="eventbrite",
        source_url=data.get("url") or f"https://www.eventbrite.sg/e/{data['id']}",
        source_event_id=str(data["id"]),
        title=((data.get("name") or {}).get("text") or "").strip(),
        fetched_at=fetched_at,
        description=data.get("summary") or (data.get("description") or {}).get("text"),
        start_raw=(data.get("start") or {}).get("utc"),
        end_raw=(data.get("end") or {}).get("utc"),
        venue_raw=venue.get("name"),
        address_raw=address.get("localized_address_display"),
        postal_code=address.get("postal_code") if str(address.get("country", "SG")).upper() == "SG" else None,
        lat=_float(venue.get("latitude") or address.get("latitude")),
        lng=_float(venue.get("longitude") or address.get("longitude")),
        price_raw=_price(data, tickets),
        organizer=(data.get("organizer") or {}).get("name"),
        image_url=((data.get("logo") or {}).get("original") or {}).get("url") or (data.get("logo") or {}).get("url"),
        registration_url=data.get("url"),
        raw_payload={
            "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode" if data.get("online_event") else "",
            "eventStatus": "cancelled" if data.get("status") in ("canceled", "cancelled") else str(data.get("status") or ""),
            "eventbrite": {k: data.get(k) for k in ("id", "status", "is_free", "online_event", "currency")},
        },
    )


def _price(data: dict[str, Any], tickets: dict[str, Any]) -> str | None:
    if data.get("is_free") or tickets.get("is_free"):
        return "Free"
    low, high = tickets.get("minimum_ticket_price") or {}, tickets.get("maximum_ticket_price") or {}
    if (low.get("currency") or "SGD").upper() != "SGD" or low.get("major_value") is None:
        return None  # not Singapore dollars: unknown rather than wrong
    lo, hi = low["major_value"], high.get("major_value") or low["major_value"]
    return f"S${lo}" if str(lo) == str(hi) else f"S${lo} – S${hi}"


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

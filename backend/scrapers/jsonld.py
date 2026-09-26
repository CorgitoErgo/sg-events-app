"""schema.org Event (JSON-LD / microdata) -> RawEvent (sg-event-scraping skill, "JSON-LD first").

Maps startDate, endDate, location (Place / PostalAddress / geo / VirtualLocation), offers,
eventAttendanceMode, eventStatus and organizer. Used by the admin console today and by
HTML-page adapters (Peatix, Eventbrite pages, Esplanade) later.
"""

import html
import re
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

from scrapers.base import RawEvent

_TAGS = re.compile(r"<[^>]+>")
_POSTAL = re.compile(r"\b(?:Singapore\s*)?(\d{6})\b", re.IGNORECASE)


def find_events(data: Any) -> list[dict]:
    """Every object whose @type is an Event (Event, BusinessEvent, EducationEvent...), incl. @graph."""
    return [obj for obj in _walk(data) if _is_event(obj)]


def _walk(data: Any) -> Iterator[dict]:
    if isinstance(data, list):
        for item in data:
            yield from _walk(item)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "subEvent", "itemListElement", "item"):
            if key in data:
                yield from _walk(data[key])


def _is_event(obj: dict) -> bool:
    types = obj.get("@type") or obj.get("type") or []
    types = types if isinstance(types, list) else [types]
    return any(str(t).rsplit("/", 1)[-1].endswith("Event") for t in types)


def to_raw_event(obj: dict, *, page_url: str, source_id: str, fetched_at: datetime) -> RawEvent | None:
    title = clean(_first(obj.get("name")))
    if not title:
        return None
    location = _first_location(obj.get("location"))
    venue, address, postal, lat, lng, virtual = _place(location)
    attendance = str(_first(obj.get("eventAttendanceMode")) or "")
    if virtual and "Mixed" not in attendance:
        attendance = attendance or "https://schema.org/OnlineEventAttendanceMode"
    event_url = _url(obj.get("url"), page_url)
    offers = _as_list(obj.get("offers"))
    return RawEvent(
        source_id=source_id,
        source_url=event_url or page_url,
        source_event_id=str(obj["@id"]) if obj.get("@id") else None,
        title=title,
        fetched_at=fetched_at,
        description=clean(_first(obj.get("description"))),
        start_raw=_text(obj.get("startDate")),
        end_raw=_text(obj.get("endDate")),
        venue_raw=venue,
        address_raw=address,
        postal_code=postal,
        lat=lat,
        lng=lng,
        price_raw=price_text(offers, obj.get("isAccessibleForFree")),
        organizer=clean(_name(_first(obj.get("organizer")))),
        image_url=_image(obj.get("image"), page_url),
        registration_url=next((u for o in offers if isinstance(o, dict) and (u := _url(o.get("url"), page_url))), None)
        or event_url,
        raw_payload={
            "eventAttendanceMode": attendance,
            "eventStatus": str(_first(obj.get("eventStatus")) or ""),
            "jsonld": obj,
        },
    )


def price_text(offers: list, free_flag: Any = None) -> str | None:
    """A price string pipeline.normalize.parse_price understands: "Free", "S$10 – S$25"."""
    if free_flag is True or str(free_flag).lower() == "true":
        return "Free"
    amounts: list[Decimal] = []
    currencies: set[str] = set()
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        currencies.add(str(offer.get("priceCurrency") or "SGD").upper())
        for key in ("price", "lowPrice", "highPrice"):
            value = offer.get(key)
            if value in (None, ""):
                continue
            try:
                amounts.append(Decimal(str(value).replace(",", "").replace("$", "").strip()))
            except InvalidOperation:
                if "free" in str(value).lower():
                    amounts.append(Decimal(0))
    if not amounts:
        return None
    if currencies - {"SGD"}:
        return None  # not Singapore dollars: leave the price unknown rather than wrong
    low, high = min(amounts), max(amounts)
    if high == 0:
        return "Free"
    return f"S${_money(low)}" if low == high else f"S${_money(low)} – S${_money(high)}"


def _money(value: Decimal) -> str:
    """25.00 -> "25", 12.50 -> "12.5", 100 -> "100" (Decimal.normalize would give 1E+2)."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _place(location: Any) -> tuple[str | None, str | None, str | None, float | None, float | None, bool]:
    if isinstance(location, str):
        return clean(location), None, _postal(location), None, None, False
    if not isinstance(location, dict):
        return None, None, None, None, None, False
    types = str(location.get("@type", ""))
    if "VirtualLocation" in types:
        return None, None, None, None, None, True
    address = location.get("address")
    if isinstance(address, dict):
        parts = [address.get(k) for k in ("streetAddress", "addressLocality", "postalCode")]
        address_text = ", ".join(clean(str(p)) for p in parts if p) or None
        postal = _postal(str(address.get("postalCode") or "")) or _postal(address_text)
    else:
        address_text = clean(_text(address))
        postal = _postal(address_text)
    geo = location.get("geo") if isinstance(location.get("geo"), dict) else {}
    lat, lng = _float(geo.get("latitude")), _float(geo.get("longitude"))
    return clean(_text(location.get("name"))), address_text, postal, lat, lng, False


def _first_location(value: Any) -> Any:
    """Prefer a physical Place when an event lists several locations (hybrid events)."""
    items = _as_list(value)
    physical = [i for i in items if isinstance(i, dict) and "VirtualLocation" not in str(i.get("@type", ""))]
    return (physical or items or [None])[0]


def clean(text: str | None) -> str | None:
    if not text:
        return None
    text = html.unescape(_TAGS.sub(" ", str(text)))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text or None


def _postal(text: str | None) -> str | None:
    m = _POSTAL.search(text or "")
    return m.group(1) if m else None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first(value: Any) -> Any:
    items = _as_list(value)
    return items[0] if items else None


def _text(value: Any) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("@value") or value.get("name")
    return str(value).strip() if value not in (None, "") else None


def _name(value: Any) -> str | None:
    return _text(value.get("name")) if isinstance(value, dict) else _text(value)


def _url(value: Any, base: str) -> str | None:
    value = _text(value)
    if not value:
        return None
    absolute = urljoin(base, value)
    return absolute if absolute.startswith(("http://", "https://")) else None


def _image(value: Any, base: str) -> str | None:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return _url(value, base)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None

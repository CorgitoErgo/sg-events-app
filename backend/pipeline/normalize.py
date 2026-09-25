"""RawEvent -> NormalizedEvent. Pure functions, no I/O (see the event-schema skill).

Later steps add: Haiku summary, categories, OneMap geocoding, fuzzy dedup (step 3).
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import dateparser

from pipeline.sg import SGT, in_singapore
from scrapers.base import RawEvent

logger = logging.getLogger(__name__)


class NormalizationError(ValueError):
    """The RawEvent can't become an Event (e.g. no parseable start). Log it and skip."""


@dataclass(slots=True)
class NormalizedVenue:
    name: str
    address: str | None
    postal_code: str | None
    lat: float | None
    lng: float | None


@dataclass(slots=True)
class NormalizedEvent:
    fingerprint: str
    title: str
    starts_at: datetime  # UTC
    ends_at: datetime | None  # UTC, exclusive
    all_day: bool
    is_online: bool
    status: str  # active | cancelled
    audience: str  # public | students_only | members_only | alumni
    source_id: str
    source_url: str
    fetched_at: datetime
    source_event_id: str | None = None
    description: str | None = None
    venue: NormalizedVenue | None = None
    lat: float | None = None
    lng: float | None = None
    price_min_sgd: Decimal | None = None
    price_max_sgd: Decimal | None = None
    is_free: bool | None = None
    language: list[str] | None = None
    organizer: str | None = None
    image_url: str | None = None
    registration_url: str | None = None
    confidence: str = "high"
    source_tags: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)


def normalize(raw: RawEvent) -> NormalizedEvent:
    title = " ".join(raw.title.split())
    if not title:
        raise NormalizationError("empty title")
    starts_at, ends_at, all_day = parse_when(raw.start_raw, raw.end_raw, relative_base=raw.fetched_at)
    price_min, price_max, is_free = parse_price(raw.price_raw)

    lat, lng = raw.lat, raw.lng
    if lat is not None and lng is not None and not in_singapore(lat, lng):
        logger.warning("%s: point (%s, %s) outside Singapore; dropped", raw.source_url, lat, lng)
        lat = lng = None
    elif lat is None or lng is None:
        lat = lng = None

    is_online = detect_online(raw)
    venue = None
    if raw.venue_raw and not (is_online and _ONLINE_PLACE.search(raw.venue_raw)):
        venue = NormalizedVenue(
            name=" ".join(raw.venue_raw.split()),
            address=raw.address_raw,
            postal_code=raw.postal_code,
            lat=lat,
            lng=lng,
        )

    return NormalizedEvent(
        fingerprint=fingerprint(title, starts_at, raw.postal_code, lat, lng),
        title=title,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        is_online=is_online,
        status=detect_status(raw),
        audience=detect_audience(raw),
        source_id=raw.source_id,
        source_url=raw.source_url,
        fetched_at=raw.fetched_at,
        source_event_id=raw.source_event_id,
        description=(raw.description or "").strip() or None,
        venue=venue,
        lat=lat,
        lng=lng,
        price_min_sgd=price_min,
        price_max_sgd=price_max,
        is_free=is_free,
        language=[raw.language] if raw.language else None,
        organizer=(raw.organizer or "").strip() or None,
        image_url=raw.image_url,
        registration_url=raw.registration_url,
        source_tags=list(raw.source_categories),
        raw_payload=raw.raw_payload,
    )


# --- dates ---------------------------------------------------------------------------------

_ICS_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_ICS_DATETIME = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HAS_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_HAS_TIME = re.compile(
    r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s*(?:am|pm)\b|\bnoon\b|\bmidnight\b", re.IGNORECASE
)
_TIME_ONLY = re.compile(r"^\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*$", re.IGNORECASE)
_RANGE_SPLIT = re.compile(
    r"\s*(?:–|—|\s-\s|(?<=\d)-(?=\d)|(?<=[ap]m)-|\bto\b|\buntil\b|\btill\b)\s*", re.IGNORECASE
)
_BARE_DAY = re.compile(r"^\s*\d{1,2}\s*$")
_LEADING_DAY = re.compile(r"^\s*\d{1,2}\b")
_TRAILING_BARE_HOUR = re.compile(r"[,@]\s*\d{1,2}(?::\d{2})?\s*$")
_MERIDIEM_END = re.compile(r"(am|pm)\s*$", re.IGNORECASE)

_DATEPARSER_SETTINGS = {
    "TIMEZONE": "Asia/Singapore",
    "RETURN_AS_TIMEZONE_AWARE": True,
    "DATE_ORDER": "DMY",  # Singapore writes 3/10/2026 for 3 Oct
    # Not PREFER_DATES_FROM="future": it turns a date a few days past into next year's.
    # The 30-day rule in _parse_text handles missing years instead.
}


def parse_when(
    start_raw: str | None, end_raw: str | None, *, relative_base: datetime
) -> tuple[datetime, datetime | None, bool]:
    """Return (starts_at UTC, ends_at UTC exclusive or None, all_day). Never invents times."""
    if not start_raw or not start_raw.strip():
        raise NormalizationError("no start date")

    start = _parse_exact(start_raw)
    end = _parse_exact(end_raw) if end_raw else None
    if start is not None and (not end_raw or end is not None):
        starts_at, all_day, _ = start
        ends_at = None
        if end is not None:
            value, date_only, exclusive = end
            # ICS all-day DTEND is exclusive already; an ISO end *date* means "through that day".
            ends_at = value + timedelta(days=1) if date_only and not exclusive else value
        return starts_at, _sane_end(starts_at, ends_at), all_day

    return _parse_fuzzy(start_raw, end_raw, relative_base)


def _parse_exact(raw: str) -> tuple[datetime, bool, bool] | None:
    """Machine formats (ICS, ISO 8601) -> (utc value, date_only, ics_exclusive_date)."""
    s = raw.strip()
    if m := _ICS_DATE.match(s):
        return _midnight_sgt(date(*map(int, m.groups()))), True, True
    if m := _ICS_DATETIME.match(s):
        y, mo, d, h, mi, sec = map(int, m.groups()[:6])
        tz = UTC if m.group(7) else SGT  # floating ICS times are local
        return datetime(y, mo, d, h, mi, sec, tzinfo=tz).astimezone(UTC), False, False
    try:
        value = datetime.fromisoformat(s)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=SGT)
    return value.astimezone(UTC), bool(_ISO_DATE.match(s)), False


def _parse_fuzzy(
    start_raw: str, end_raw: str | None, base: datetime
) -> tuple[datetime, datetime | None, bool]:
    """Human text: "Sat 3 Oct, 10am–4pm", "3–5 Oct", "3 Oct – 2 Nov"."""
    left, right = start_raw.strip(), (end_raw or "").strip() or None
    if right is None:
        parts = _RANGE_SPLIT.split(left, maxsplit=1)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            left, right = parts[0].strip(), parts[1].strip()

    if right:
        if _BARE_DAY.match(left):  # "3–5 Oct": borrow month/year from the right side
            left = f"{left} {_LEADING_DAY.sub('', right).strip()}"
        elif _TRAILING_BARE_HOUR.search(left) and not _MERIDIEM_END.search(left):
            if m := _MERIDIEM_END.search(right):  # "3 Oct, 3–5pm"
                left = f"{left}{m.group(1)}"

    start_local, start_has_time = _parse_text(left, base)
    if start_local is None:
        raise NormalizationError(f"unparseable start: {start_raw!r}")

    end_local = None
    if right:
        t = _TIME_ONLY.match(right)
        if t and (t.group(2) or t.group(3)):  # "4pm", "16:00": same day as the start
            end_local = datetime.combine(start_local.date(), _to_time(t), tzinfo=SGT)
            if end_local <= start_local:
                end_local += timedelta(days=1)  # overnight, "10pm–2am"
        else:
            end_local, end_has_time = _parse_text(right, base)
            if end_local is not None and not end_has_time:
                end_local += timedelta(days=1)  # end date is inclusive
            if end_local is not None and end_local < start_local and not _HAS_YEAR.search(right):
                end_local = end_local.replace(year=end_local.year + 1)  # "28 Dec – 2 Jan"

    starts_at = start_local.astimezone(UTC)
    ends_at = end_local.astimezone(UTC) if end_local else None
    return starts_at, _sane_end(starts_at, ends_at), not start_has_time


def _parse_text(text: str, base: datetime) -> tuple[datetime | None, bool]:
    base_local = base.astimezone(SGT).replace(tzinfo=None)
    value = dateparser.parse(
        text, languages=["en"], settings={**_DATEPARSER_SETTINGS, "RELATIVE_BASE": base_local}
    )
    if value is None:
        return None, False
    value = value.astimezone(SGT)
    has_time = bool(_HAS_TIME.search(text))
    if not has_time:
        value = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if not _HAS_YEAR.search(text) and value < base.astimezone(SGT) - timedelta(days=30):
        value = value.replace(year=value.year + 1)
    return value, has_time


def _to_time(m: re.Match[str]) -> time:
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return time(hour % 24, minute)


def _midnight_sgt(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=SGT).astimezone(UTC)


def _sane_end(starts_at: datetime, ends_at: datetime | None) -> datetime | None:
    if ends_at is not None and ends_at < starts_at:
        logger.warning("end %s before start %s; dropping end", ends_at, starts_at)
        return None
    return ends_at


# --- price ---------------------------------------------------------------------------------

_PRICE_PREFIXED = re.compile(r"(?:S\$|SGD\s?|\$)\s?(\d{1,5}(?:,\d{3})*(?:\.\d{1,2})?)", re.IGNORECASE)
_PRICE_SUFFIXED = re.compile(r"\b(\d{1,5}(?:\.\d{1,2})?)\s?(?:SGD|dollars)\b", re.IGNORECASE)
_FREE = re.compile(r"\bfree\b", re.IGNORECASE)
_FROM = re.compile(r"\bfrom\b", re.IGNORECASE)


def parse_price(text: str | None) -> tuple[Decimal | None, Decimal | None, bool | None]:
    """(min, max, is_free) in SGD. Unknown -> all None."""
    if not text or not text.strip():
        return None, None, None
    amounts = [
        Decimal(n.replace(",", ""))
        for n in _PRICE_PREFIXED.findall(text) + _PRICE_SUFFIXED.findall(text)
    ]
    has_free = bool(_FREE.search(text))
    if not amounts:
        return (Decimal(0), Decimal(0), True) if has_free else (None, None, None)
    low, high = min(amounts), max(amounts)
    if has_free:
        low = Decimal(0)  # "Free for members, $10 public"
    if len(amounts) == 1 and _FROM.search(text):
        return low, None, False  # "From $8": no known maximum
    return low, high, high == 0


# --- online / status / audience ------------------------------------------------------------

_ONLINE_PLACE = re.compile(
    r"\b(online|virtual|webinar|zoom|microsoft teams|ms teams|google meet|livestream|live stream)\b",
    re.IGNORECASE,
)
_ONLINE_TITLE = re.compile(
    r"\bwebinar\b|\blivestream\b|[(\[](?:online|virtual|zoom)[)\]]"
    r"|\b(?:online|virtual) (?:event|session|talk|workshop|meetup|webinar)\b",
    re.IGNORECASE,
)
_ONLINE_LINK = re.compile(
    r"zoom\.us/|teams\.microsoft\.com/|meet\.google\.com/|webex\.com/", re.IGNORECASE
)


def detect_online(raw: RawEvent) -> bool:
    mode = str(raw.raw_payload.get("eventAttendanceMode", ""))
    if "Online" in mode or "Mixed" in mode:
        return True
    place = " ".join(filter(None, (raw.venue_raw, raw.address_raw)))
    return bool(
        _ONLINE_PLACE.search(place)
        or _ONLINE_TITLE.search(raw.title)
        or _ONLINE_LINK.search(raw.description or "")
    )


_CANCELLED_TITLE = re.compile(r"^\W*(cancel+ed|postponed)\b", re.IGNORECASE)
_CANCELLED_TEXT = re.compile(
    r"\b(?:event|session|programme|program) (?:is|has been) (?:cancel+ed|postponed)\b", re.IGNORECASE
)


def detect_status(raw: RawEvent) -> str:
    for key in ("eventStatus", "ics_status", "status"):
        value = str(raw.raw_payload.get(key, "")).lower()
        if "cancel" in value or "postpone" in value:
            return "cancelled"
    if _CANCELLED_TITLE.search(raw.title) or _CANCELLED_TEXT.search(raw.description or ""):
        return "cancelled"
    return "active"


_AUDIENCE_RULES = [
    (
        "students_only",
        re.compile(
            r"\bstudents?[- ]only\b|\bonly (?:for|open to) (?:\w+\s+){0,3}students\b"
            r"|\bexclusive(?:ly)? (?:to|for) (?:\w+\s+){0,3}students\b",
            re.IGNORECASE,
        ),
    ),
    (
        "members_only",
        re.compile(
            r"\bmembers?[- ]only\b|\bonly (?:for|open to) (?:\w+\s+){0,2}members\b"
            r"|\bexclusive(?:ly)? (?:to|for) (?:\w+\s+){0,2}members\b",
            re.IGNORECASE,
        ),
    ),
    (
        "alumni",
        re.compile(
            r"\balumni[- ]only\b|\bonly (?:for|open to) (?:\w+\s+){0,2}alumni\b"
            r"|\bexclusive(?:ly)? (?:to|for) (?:\w+\s+){0,2}alumni\b",
            re.IGNORECASE,
        ),
    ),
]


def detect_audience(raw: RawEvent) -> str:
    text = f"{raw.title}\n{(raw.description or '')[:2000]}"
    for audience, pattern in _AUDIENCE_RULES:
        if pattern.search(text):
            return audience
    return "public"


# --- fingerprint -----------------------------------------------------------------------------

_TITLE_STOPWORDS = frozenset({"singapore", "sg", "event", "events"})
_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def normalize_title(title: str) -> str:
    text = _HAS_YEAR.sub(" ", title.lower())
    text = re.sub(r"[^\w\s]|_", " ", text)
    return " ".join(w for w in text.split() if w not in _TITLE_STOPWORDS)


def fingerprint(
    title: str, starts_at: datetime, postal_code: str | None, lat: float | None, lng: float | None
) -> str:
    """sha1(normalized_title | start_date_sgt | postal code or geohash7)."""
    start_date = starts_at.astimezone(SGT).date().isoformat()
    place = postal_code or (geohash(lat, lng, 7) if lat is not None and lng is not None else "")
    return hashlib.sha1(f"{normalize_title(title)}|{start_date}|{place}".encode()).hexdigest()


def geohash(lat: float, lng: float, precision: int = 7) -> str:
    lat_range, lng_range = [-90.0, 90.0], [-180.0, 180.0]
    chars: list[str] = []
    bits = bit_count = 0
    use_lng = True
    while len(chars) < precision:
        rng, value = (lng_range, lng) if use_lng else (lat_range, lat)
        mid = (rng[0] + rng[1]) / 2
        if value >= mid:
            bits, rng[0] = bits * 2 + 1, mid
        else:
            bits, rng[1] = bits * 2, mid
        use_lng = not use_lng
        bit_count += 1
        if bit_count == 5:
            chars.append(_GEOHASH_BASE32[bits])
            bits = bit_count = 0
    return "".join(chars)

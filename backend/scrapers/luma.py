"""Luma (luma.com) adapter: public iCal feeds only.

Verified 2026-09-25:
- robots.txt: luma.com and api.lu.ma allow SGEventsBot for the city page and the ICS
  endpoint; no Crawl-delay (we use 2 s).
- Terms (luma.com/terms, Acceptable Use): "You must not access the Service by any means
  other than our publicly supported interfaces." iCal subscription is a documented
  feature (help.luma.com/p/ical-syncing), so this adapter reads ICS feeds and never
  crawls Luma's HTML.
- Feeds: the Singapore city feed ("What's Happening in Singapore", ~45 upcoming events)
  plus organisation calendars. Calendar feeds include past events and events in other
  cities; both are dropped here.
- Quirks: STATUS is always TENTATIVE (meaningless). Times are UTC, all-day events use
  DATE values with an exclusive DTEND. About half of events hide the address ("Check
  event page for more details.") but still carry GEO. LOCATION is the Luma event URL,
  or an external link for events that are only listed on a calendar.
"""

import logging
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import parse_qs, urlsplit

from icalendar import Calendar, Component

from pipeline.sg import SGT, in_singapore
from scrapers.base import RawEvent
from scrapers.http import PoliteClient

logger = logging.getLogger(__name__)

ICS_URL = "https://api.lu.ma/ics/get?entity={entity}&id={id}"
EVENT_URL = "https://luma.com/event/{event_id}"
SINGAPORE_PLACE_ID = "discplace-mUbtdfNjfWaLQ72"

# Organisation calendars featured on luma.com/singapore (2026-09-25). Individuals'
# "Personal" calendars are deliberately excluded (no tracking of private people).
# Edit freely; events outside Singapore are filtered out anyway.
CALENDARS: dict[str, str] = {
    "cal-6LuzdQ7EQHmBAbG": "B71 Singapore",
    "cal-ErtKDxR9i1z0JfZ": "MAGES Institute",
    "cal-pTBiL583BiKQTKL": "TEDxSingapore",
    "cal-QCQbg4FodJDriFO": "European Defense Tech Hub",
    "cal-iRYNvJr9vGv1QhH": "Monad Foundation Events",
    "cal-L8EuVHDQtkwVTxa": "GMI Cloud",
    "cal-RuuSzXkxVJ8ncA1": "Utila",
    "cal-GKXRU5P52agPvpP": "0G's Event Calendar",
    "cal-Nrz4EsmLDjXvjPp": "NEAR Events",
    "cal-ADPORHlkaP5S4ix": "Mantle Events Calendar",
    "cal-RCkgP87CRFLSX8i": "Solana Summit",
}

_INFO_LINE = re.compile(
    r"^(?:Get up-to-date information at|Find more information on):?\s*(\S+)\s*", re.IGNORECASE
)
_HIDDEN_ADDRESS = "check event page for more details"
_HOSTED_BY = re.compile(r"\s*Hosted by [^\n]*\Z")
_POSTAL = re.compile(r"\b(?:Singapore\s*)?(\d{6})\b")
_MENTIONS_SINGAPORE = re.compile(r"\bsingapore\b|\bs'pore\b", re.IGNORECASE)


def feed_url(entity: str, feed_id: str) -> str:
    return ICS_URL.format(entity=entity, id=feed_id)


class LumaAdapter:
    source_id = "luma"
    base_urls = ["https://api.lu.ma"]
    schedule = "0 */6 * * *"
    min_delay_s = 2.0

    def __init__(self, calendars: Mapping[str, str] | None = None) -> None:
        self.calendars = dict(CALENDARS if calendars is None else calendars)

    async def discover(self, client: PoliteClient) -> AsyncIterator[str]:
        yield feed_url("discover", SINGAPORE_PLACE_ID)
        for cal_id in self.calendars:
            yield feed_url("calendar", cal_id)

    async def fetch_and_parse(self, client: PoliteClient, url: str) -> list[RawEvent]:
        res = await client.get(url, source_id=self.source_id, min_delay_s=self.min_delay_s)
        if not res.ok:
            logger.warning("luma feed %s -> HTTP %s", url, res.status)
            return []
        feed_id = parse_qs(urlsplit(url).query).get("id", [""])[0]
        return parse_ics(
            res.content,
            fetched_at=datetime.now(UTC),
            feed_name=self.calendars.get(feed_id),
            # The city feed is Singapore by definition; calendars can be global.
            require_sg_evidence=feed_id != SINGAPORE_PLACE_ID,
        )


def parse_ics(
    data: bytes,
    *,
    fetched_at: datetime,
    now: datetime | None = None,
    feed_name: str | None = None,
    require_sg_evidence: bool = False,
) -> list[RawEvent]:
    """Parse a Luma ICS feed into RawEvents, dropping ended and non-Singapore events.

    With require_sg_evidence, an event without GEO is kept only if its title, address or
    description mentions Singapore (global calendars list London meetups and Zoom calls).
    """
    now = now or fetched_at
    events: list[RawEvent] = []
    for comp in Calendar.from_ical(data).walk("VEVENT"):
        raw = _parse_vevent(comp, fetched_at=fetched_at, now=now, feed_name=feed_name)
        if raw is None:
            continue
        if require_sg_evidence and raw.lat is None and not _mentions_singapore(raw):
            continue
        events.append(raw)
    return events


def _mentions_singapore(raw: RawEvent) -> bool:
    text = " ".join(filter(None, (raw.title, raw.venue_raw, raw.address_raw, raw.description)))
    return bool(_MENTIONS_SINGAPORE.search(text))


def _parse_vevent(
    comp: Component, *, fetched_at: datetime, now: datetime, feed_name: str | None
) -> RawEvent | None:
    title = str(comp.get("SUMMARY", "")).strip()
    uid = str(comp.get("UID", "")).strip()
    if not title or not uid or "DTSTART" not in comp:
        return None
    if _ends_at(comp) <= now:
        return None

    lat = lng = None
    if (geo := comp.get("GEO")) is not None:
        lat, lng = float(geo.latitude), float(geo.longitude)
        if not in_singapore(lat, lng):
            return None

    event_id = uid.split("@", 1)[0]  # "evt-GEzYcX1i5FjiSGT@events.lu.ma"
    page_url, address_lines, body = _split_description(str(comp.get("DESCRIPTION", "")))

    location = str(comp.get("LOCATION", "")).strip()
    external_url = None
    if location.startswith(("http://", "https://")):
        if "/event/" not in location:  # calendar-listed event hosted elsewhere
            external_url = location
    elif location and not address_lines:
        address_lines = [location]

    postal = _POSTAL.search(" ".join(address_lines))
    organizer = comp.get("ORGANIZER")
    organizer_name = str(organizer.params.get("CN", "")).strip() if organizer else ""

    return RawEvent(
        source_id="luma",
        source_url=EVENT_URL.format(event_id=event_id),
        source_event_id=event_id,
        title=title,
        fetched_at=fetched_at,
        description=body,
        start_raw=_raw_dt(comp, "DTSTART"),
        end_raw=_raw_dt(comp, "DTEND"),
        venue_raw=address_lines[0] if address_lines else None,
        address_raw=", ".join(address_lines[1:]) or None,
        postal_code=postal.group(1) if postal else None,
        lat=lat,
        lng=lng,
        organizer=organizer_name or None,
        registration_url=external_url or page_url or EVENT_URL.format(event_id=event_id),
        source_categories=[feed_name] if feed_name else [],
        raw_payload={
            "ics_status": str(comp.get("STATUS", "")),
            "ics": comp.to_ical().decode("utf-8"),
        },
    )


def _split_description(desc: str) -> tuple[str | None, list[str], str | None]:
    """Luma's DESCRIPTION: info-link line, optional "Address:" block, body, "Hosted by" line."""
    text = desc.strip()
    page_url = None
    if m := _INFO_LINE.match(text):
        page_url = m.group(1)
        text = text[m.end() :].lstrip()

    address_lines: list[str] = []
    if text.startswith("Address:"):
        block, _, text = text[len("Address:") :].lstrip("\n").partition("\n\n")
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not (lines and lines[0].lower().rstrip(".") == _HIDDEN_ADDRESS):
            address_lines = lines

    body = _HOSTED_BY.sub("", text).strip()
    return page_url, address_lines, body or None


def _raw_dt(comp: Component, name: str) -> str | None:
    """The value as written in the feed; ISO with offset when it carried a TZID."""
    prop = comp.get(name)
    if prop is None:
        return None
    if prop.params.get("TZID"):
        return comp.decoded(name).isoformat()
    return prop.to_ical().decode("ascii")


def _ends_at(comp: Component) -> datetime:
    """Event end as an aware datetime, for dropping events that are over."""
    if "DTEND" in comp:
        end = comp.decoded("DTEND")
        exclusive = True
    else:
        end = comp.decoded("DTSTART")
        exclusive = False
    if isinstance(end, datetime):
        return end if end.tzinfo else end.replace(tzinfo=SGT)
    if isinstance(end, date):
        day = end if exclusive else end + timedelta(days=1)
        return datetime.combine(day, time.min, tzinfo=SGT)
    raise ValueError(f"unexpected DTSTART/DTEND value: {end!r}")

"""The discovery agent's rules: run limits, which results to open, which events to keep.

Deterministic on purpose (no LLM needed): every decision carries a plain-English reason
that is logged and shown in the admin console.
"""

import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from pipeline.domains import host_of
from pipeline.normalize import NormalizedEvent
from pipeline.sg import PLANNING_AREA_REGION, SGT, in_singapore
from scrapers.base import RawEvent


@dataclass(frozen=True)
class Limits:
    """Hard caps so a run always ends. The first one reached stops the run."""

    max_searches: int = 10  # Tavily credits used
    max_pages: int = 40  # pages / API calls opened
    max_events: int = 25  # events saved or merged
    max_per_domain: int = 5  # pages opened per site per run
    max_seconds: int = 600
    results_per_search: int = 10
    horizon_days: int = 120  # ignore events further out than this


@dataclass
class Budget:
    limits: Limits
    started: float = field(default_factory=time.monotonic)
    searches: int = 0
    pages: int = 0
    events: int = 0

    def stop_reason(self) -> str | None:
        lim = self.limits
        if self.events >= lim.max_events:
            return f"saved {self.events} events (limit {lim.max_events})"
        if self.pages >= lim.max_pages:
            return f"opened {self.pages} pages (limit {lim.max_pages})"
        if time.monotonic() - self.started >= lim.max_seconds:
            return f"ran for {lim.max_seconds // 60} minutes (time limit)"
        return None

    def can_search(self) -> bool:
        return self.searches < self.limits.max_searches and self.stop_reason() is None


@dataclass(frozen=True)
class Verdict:
    accept: bool
    reason: str


# Planning areas whose names are ordinary words elsewhere ("Orchard", "Museum", "Newton")
# don't count as evidence on their own.
_AMBIGUOUS_AREAS = {
    "MUSEUM", "ORCHARD", "NEWTON", "PIONEER", "RIVER VALLEY", "DOWNTOWN CORE", "STRAITS VIEW",
    "SOUTHERN ISLANDS", "WESTERN ISLANDS", "NORTH-EASTERN ISLANDS", "CENTRAL WATER CATCHMENT",
    "WESTERN WATER CATCHMENT", "MARINA EAST", "MARINA SOUTH", "SINGAPORE RIVER", "OUTRAM", "SIMPANG",
    "TENGAH", "CHANGI BAY", "MANDAI", "TANGLIN", "NOVENA", "ROCHOR", "KALLANG",
}  # fmt: skip
_AREA_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in sorted(set(PLANNING_AREA_REGION) - _AMBIGUOUS_AREAS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_SINGAPORE = re.compile(r"\bsingapore\b|\bS'pore\b", re.IGNORECASE)


def singapore_evidence(raw: RawEvent, ev: NormalizedEvent, page_url: str) -> str | None:
    """Why we believe the event is in Singapore, or None."""
    if raw.lat is not None and raw.lng is not None:
        return "map point in Singapore" if in_singapore(raw.lat, raw.lng) else None  # a point elsewhere settles it
    place = " ".join(filter(None, (raw.venue_raw, raw.address_raw)))
    sg_site = host_of(page_url).endswith(".sg")
    if raw.postal_code and (_SINGAPORE.search(place) or sg_site):
        return f"Singapore postal code {raw.postal_code}"
    if _SINGAPORE.search(place):
        return "address says Singapore"
    if m := _AREA_PATTERN.search(place):
        return f"venue in {m.group(1).title()}"
    if ev.is_online:
        about = " ".join(filter(None, (raw.title, raw.organizer, (raw.description or "")[:1000])))
        return "online, for Singapore" if _SINGAPORE.search(about) or sg_site else None
    if sg_site and place:
        return "venue on a .sg site"
    return None


# --- time sanity for event data published on ordinary web pages ------------------------------------

_ISO_TIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$", re.IGNORECASE
)
_SGT_OFFSETS = {"+08:00", "+0800"}
NIGHT_HOURS = range(0, 6)  # a public event starting or ending at 00:00-05:59 SGT is almost always a mislabelled zone


def _parts(value: str | None) -> tuple[datetime, str] | None:
    m = _ISO_TIME.match((value or "").strip())
    if not m:
        return None
    naive = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4) or '00'}")
    return naive, (m.group(5) or "").upper()


def foreign_offset(raw: RawEvent) -> str | None:
    """An explicit non-Singapore UTC offset on the start time, e.g. "-05:00"."""
    parts = _parts(raw.start_raw)
    if parts and parts[1] not in ("", "Z") and parts[1] not in _SGT_OFFSETS:
        return parts[1]
    return None


def repair_times(raw: RawEvent) -> tuple[RawEvent, str | None]:
    """Fix the two common publisher mistakes on web pages: Singapore times labelled "Z" (UTC),
    and UTC times written without a zone. Only when the literal reading puts the event in the
    middle of the night and the swapped reading doesn't. Explicit offsets are trusted."""
    if raw.source_id != "web":
        return raw, None  # APIs (Eventbrite) and feeds label zones correctly
    parsed = [(key, _parts(getattr(raw, key))) for key in ("start_raw", "end_raw") if getattr(raw, key)]
    parsed = [(key, p) for key, p in parsed if p is not None]
    if not parsed or any(marker not in ("", "Z") for _, (_, marker) in parsed):
        return raw, None

    def read(naive: datetime, marker: str, swap: bool) -> datetime:
        as_utc = (marker == "Z") != swap
        return naive.replace(tzinfo=UTC if as_utc else SGT).astimezone(SGT)

    literal = [read(n, m, False) for _, (n, m) in parsed]
    swapped = [read(n, m, True) for _, (n, m) in parsed]
    at_night = lambda times: any(t.hour in NIGHT_HOURS for t in times)  # noqa: E731
    if not at_night(literal) or at_night(swapped):
        return raw, None
    fixed = replace(raw, **{key: t.isoformat() for (key, _), t in zip(parsed, swapped, strict=True)})
    if parsed[0][1][1] == "Z":
        return fixed, "times corrected (the page labelled Singapore times as UTC)"
    return fixed, "times corrected (the page gave UTC times with no time zone)"


def judge(raw: RawEvent, ev: NormalizedEvent, page_url: str, *, now: datetime, limits: Limits) -> Verdict:
    if offset := foreign_offset(raw):
        return Verdict(False, f"published in another time zone (UTC{offset})")
    if ev.status != "active":
        return Verdict(False, "cancelled or postponed")
    ends = ev.ends_at or (ev.starts_at + timedelta(days=1) if ev.all_day else ev.starts_at)
    if ends < now:
        return Verdict(False, "already over")
    if ev.starts_at > now + timedelta(days=limits.horizon_days):
        return Verdict(False, f"starts more than {limits.horizon_days} days away")
    evidence = singapore_evidence(raw, ev, page_url)
    if evidence is None:
        return Verdict(False, "no sign it's in Singapore")
    if ev.audience != "public":
        return Verdict(False, f"not open to the public ({ev.audience.replace('_', ' ')})")
    return Verdict(True, f"upcoming public event in Singapore ({evidence})")

"""The discovery agent's rules: run limits, which results to open, which events to keep.

Deterministic on purpose (no LLM needed): every decision carries a plain-English reason
that is logged and shown in the admin console.
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pipeline.domains import host_of
from pipeline.normalize import NormalizedEvent
from pipeline.sg import PLANNING_AREA_REGION, in_singapore
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


def judge(raw: RawEvent, ev: NormalizedEvent, page_url: str, *, now: datetime, limits: Limits) -> Verdict:
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

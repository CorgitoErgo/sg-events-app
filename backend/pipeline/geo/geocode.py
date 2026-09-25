"""Venue -> point, in the geo-proximity-sg skill's order: postal code, full address, venue name.

Source coordinates and the venues-table cache are handled by the caller (pipeline.enrich)
before this runs; this module only decides which OneMap result, if any, to trust.
"""

import logging
import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from pipeline.geo.onemap import OneMapClient, SearchResult
from pipeline.sg import in_singapore

logger = logging.getLogger(__name__)

POSTAL_RE = re.compile(r"\b(?:Singapore\s*)?(\d{6})\b", re.IGNORECASE)
MATCH_MIN = 80  # token_set_ratio needed to accept an address or name match

# Singapore shorthand that OneMap spells out ("Sengkang CC" -> "SENGKANG COMMUNITY CLUB").
_ABBREVIATIONS = {
    "cc": "community club",
    "rc": "residents committee",
    "poly": "polytechnic",
    "blk": "block",
    "st": "street",
    "ave": "avenue",
    "rd": "road",
    "dr": "drive",
}


@dataclass(frozen=True, slots=True)
class VenueQuery:
    name: str | None
    address: str | None
    postal_code: str | None


async def geocode(onemap: OneMapClient, venue: VenueQuery) -> SearchResult | None:
    postal = venue.postal_code or _find_postal(venue.address) or _find_postal(venue.name)
    if postal:
        if hit := pick_by_postal(postal, await onemap.search(postal)):
            return hit
    if venue.address:
        if hit := pick_by_text(venue.address, await onemap.search(venue.address), field="address"):
            return hit
    if venue.name:
        if hit := pick_by_text(venue.name, await onemap.search(venue.name), field="name"):
            return hit
    return None


def pick_by_postal(postal: str, results: list[SearchResult]) -> SearchResult | None:
    return next((r for r in results if r.postal == postal and in_singapore(r.lat, r.lng)), None)


def pick_by_text(query: str, results: list[SearchResult], *, field: str) -> SearchResult | None:
    """Best fuzzy match on name/building (or address). token_set_ratio decides acceptance;
    plain ratio breaks ties, since a subset ("National Library" vs "NIE (Library)") scores 100."""
    wanted = normalize_place(query)
    best, best_key = None, (0.0, 0.0)
    for r in results:
        if not in_singapore(r.lat, r.lng):
            continue
        candidates = [r.address] if field == "address" else [r.name, r.building]
        for text in filter(None, candidates):
            have = normalize_place(text)
            key = (fuzz.token_set_ratio(wanted, have), fuzz.ratio(wanted, have))
            if key > best_key:
                best, best_key = r, key
    return best if best_key[0] >= MATCH_MIN else None


def normalize_place(text: str) -> str:
    words = re.sub(r"[^\w\s]", " ", text.lower()).split()
    words = [w for w in words if w not in {"singapore", "s"}]
    return " ".join(_ABBREVIATIONS.get(w, w) for w in words)


def _find_postal(text: str | None) -> str | None:
    m = POSTAL_RE.search(text or "")
    return m.group(1) if m else None

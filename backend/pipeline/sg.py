"""Singapore constants shared by adapters and pipeline steps."""

from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")  # UTC+8, no DST

# Rough bounding box; anything outside is not a Singapore point (often swapped lat/lng).
SG_LAT_MIN, SG_LAT_MAX = 1.15, 1.48
SG_LNG_MIN, SG_LNG_MAX = 103.59, 104.10


def in_singapore(lat: float, lng: float) -> bool:
    return SG_LAT_MIN <= lat <= SG_LAT_MAX and SG_LNG_MIN <= lng <= SG_LNG_MAX

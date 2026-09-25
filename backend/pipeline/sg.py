"""Singapore constants shared by adapters and pipeline steps."""

from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")  # UTC+8, no DST

# Rough bounding box; anything outside is not a Singapore point (often swapped lat/lng).
SG_LAT_MIN, SG_LAT_MAX = 1.15, 1.48
SG_LNG_MIN, SG_LNG_MAX = 103.59, 104.10


def in_singapore(lat: float, lng: float) -> bool:
    return SG_LAT_MIN <= lat <= SG_LAT_MAX and SG_LNG_MIN <= lng <= SG_LNG_MAX


# URA Master Plan 2019: the 55 planning areas and their regions. OneMap returns the
# planning area name in upper case; the region isn't in its response.
_REGIONS: dict[str, tuple[str, ...]] = {
    "CENTRAL": (
        "BISHAN", "BUKIT MERAH", "BUKIT TIMAH", "DOWNTOWN CORE", "GEYLANG", "KALLANG",
        "MARINA EAST", "MARINA SOUTH", "MARINE PARADE", "MUSEUM", "NEWTON", "NOVENA",
        "ORCHARD", "OUTRAM", "QUEENSTOWN", "RIVER VALLEY", "ROCHOR", "SINGAPORE RIVER",
        "SOUTHERN ISLANDS", "STRAITS VIEW", "TANGLIN", "TOA PAYOH",
    ),
    "EAST": ("BEDOK", "CHANGI", "CHANGI BAY", "PASIR RIS", "PAYA LEBAR", "TAMPINES"),
    "NORTH": (
        "CENTRAL WATER CATCHMENT", "LIM CHU KANG", "MANDAI", "SEMBAWANG", "SIMPANG",
        "SUNGEI KADUT", "WOODLANDS", "YISHUN",
    ),
    "NORTH-EAST": (
        "ANG MO KIO", "HOUGANG", "NORTH-EASTERN ISLANDS", "PUNGGOL", "SELETAR", "SENGKANG",
        "SERANGOON",
    ),
    "WEST": (
        "BOON LAY", "BUKIT BATOK", "BUKIT PANJANG", "CHOA CHU KANG", "CLEMENTI", "JURONG EAST",
        "JURONG WEST", "PIONEER", "TENGAH", "TUAS", "WESTERN ISLANDS", "WESTERN WATER CATCHMENT",
    ),
}  # fmt: skip
PLANNING_AREA_REGION: dict[str, str] = {
    area: region for region, areas in _REGIONS.items() for area in areas
}


def region_for(planning_area: str | None) -> str | None:
    return PLANNING_AREA_REGION.get((planning_area or "").strip().upper())

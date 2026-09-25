"""The category taxonomy: the single source of truth (see the event-categorization skill).

IDs are stable slugs. Labels here are defaults; the app owns display labels and icons.
Served by GET /categories so the app never hard-codes the list.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Category:
    id: str
    label: str
    includes: str


CATEGORIES: tuple[Category, ...] = (
    Category("career_fair", "Career fairs & job fairs", "job fairs, recruitment drives, career expos, hiring days"),
    Category("career_dev", "Career development", "resume clinics, career talks, mentoring, industry sharing"),
    Category("networking", "Networking", "mixers, meetups for professionals, founder nights"),
    Category("tech_startup", "Tech & startups", "hackathons, demo days, dev meetups, AI talks"),
    Category("community", "Community events", "CC events, block parties, neighbourhood carnivals, festive gatherings"),
    Category("volunteering", "Volunteering", "volunteer drives, donation drives, befriending, clean-ups"),
    Category("workshop_class", "Workshops & classes", "courses, hands-on classes, skill workshops"),
    Category("talks", "Talks & seminars", "public lectures, panels, book talks"),
    Category("arts_culture", "Arts & culture", "exhibitions, theatre, heritage walks, cultural festivals"),
    Category("music", "Music & performances", "concerts, gigs, free performances"),
    Category("family_kids", "Family & kids", "children's programmes, school-holiday activities"),
    Category("sports_fitness", "Sports & fitness", "runs, group workouts, tournaments"),
    Category("nature_outdoors", "Nature & outdoors", "guided walks, gardening, park events"),
    Category("food_markets", "Food & markets", "bazaars, flea markets, food festivals"),
    Category("health_wellness", "Health & wellness", "health screenings, mental wellness, mindfulness"),
    Category("faith_festivals", "Religious & festive", "festival celebrations, open houses, religious community programmes"),
    Category("education_open_house", "Open houses & education fairs", "uni/poly open houses, education fairs"),
    Category("youth", "Youth", "youth programmes, youth centres, student-led events"),
    Category("seniors", "Seniors", "active ageing programmes, senior-friendly classes"),
)  # fmt: skip

CATEGORY_IDS: tuple[str, ...] = tuple(c.id for c in CATEGORIES)
MAX_CATEGORIES = 3

# A source's own tags -> categories. Passed to the LLM as hints; used alone (no LLM)
# only for sources listed in CATEGORY_SPECIFIC_SOURCES.
SOURCE_TAG_MAP: dict[str, tuple[str, ...]] = {
    # Luma calendars (source_categories = calendar name)
    "B71 Singapore": ("tech_startup",),
    "TEDxSingapore": ("talks",),
    "European Defense Tech Hub": ("tech_startup",),
    "Monad Foundation Events": ("tech_startup",),
    "GMI Cloud": ("tech_startup",),
    "Utila": ("tech_startup",),
    "0G's Event Calendar": ("tech_startup",),
    "NEAR Events": ("tech_startup",),
    "Mantle Events Calendar": ("tech_startup",),
    "Solana Summit": ("tech_startup",),
}

# Sources whose every listing is one kind of event (e.g. e2i career fairs): when their
# tags map to a category, skip the LLM. None of the current sources qualify.
CATEGORY_SPECIFIC_SOURCES: frozenset[str] = frozenset()

# High-precision title keywords. Matches are always kept in the final categories.
KEYWORD_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), category)
    for pattern, category in (
        (r"\b(?:career|job|jobs|recruitment|hiring)\s+(?:fair|fest|expo|drive)s?\b|\bjob\s*fair\b|\bwalk-in interviews?\b", "career_fair"),
        (r"\bresume\b|\bcv clinic\b|\bmock interviews?\b|\bcareer (?:talk|coaching|clinic|mentoring)\b", "career_dev"),
        (r"\bnetworking\b|\bmixer\b", "networking"),
        (r"\bhackathon\b|\bdemo day\b|\bhacker house\b", "tech_startup"),
        (r"\bvolunteer(?:s|ing)?\b|\bdonation drive\b|\bbeach clean[- ]?up\b|\bblood drive\b", "volunteering"),
        (r"\bworkshop\b|\bmasterclass\b", "workshop_class"),
        (r"\bpanel\b|\bpublic lecture\b|\bbook talk\b|\bseminar\b|\bfireside chat\b|\bartist talk\b", "talks"),
        (r"\bexhibition\b|\bheritage walk\b", "arts_culture"),
        (r"\bconcert\b|\brecital\b|\blive music\b", "music"),
        (r"\bfun run\b|\bmarathon\b|\brun club\b|\b\d+\s?km (?:run|walk)\b", "sports_fitness"),
        (r"\bbazaar\b|\bflea market\b|\bfood festival\b|\bpasar malam\b|\bnight market\b", "food_markets"),
        (r"\bhealth screening\b|\bmindfulness\b|\bmental (?:health|wellness)\b", "health_wellness"),
        (r"\bopen house\b.*\b(?:poly(?:technic)?|university|school|college|campus|institute)\b"
         r"|\b(?:poly(?:technic)?|university|school|college|campus|institute)\b.*\bopen house\b"
         r"|\beducation fair\b", "education_open_house"),
        (r"\bactive ageing\b|\bseniors?\b", "seniors"),
    )
)  # fmt: skip


def keyword_categories(title: str) -> list[str]:
    return _unique(cat for pattern, cat in KEYWORD_RULES if pattern.search(title))


def tag_categories(source_tags: list[str]) -> list[str]:
    return _unique(cat for tag in source_tags for cat in SOURCE_TAG_MAP.get(tag, ()))


def _unique(items) -> list[str]:
    return list(dict.fromkeys(items))

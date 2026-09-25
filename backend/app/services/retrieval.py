"""Hybrid retrieval for /ask (rag-pipeline skill, section 3).

Structured filters in SQL pick up to 500 candidates; within them, vector similarity
(pgvector, Voyage query embedding) and full text (tsvector) each rank the top 50, fused
with reciprocal rank fusion. A gentle boost then favours sooner and nearer events.
An empty semantic query skips ranking and keeps date/distance order.
"""

import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.events import EventQuery, Online, candidate_ids, fetch_events
from app.services.query_parser import ParsedQuery
from app.timewindows import explicit_window, preset_window
from pipeline.embed import VoyageClient, VoyageError, vector_literal
from pipeline.geo.onemap import OneMapClient, OneMapError
from pipeline.sg import PLANNING_AREA_REGION, in_singapore

logger = logging.getLogger(__name__)

CANDIDATES = 500
PER_LIST = 50
FUSED = 30
RRF_K = 60
DEFAULT_RADIUS_KM = 5.0
BOOST = 0.15  # max relative lift each for soonness and nearness
# Vector-only hits must be this close (cosine distance) to count. Calibrated on voyage-4
# with the 2026-09-26 Luma data: relevant 0.36-0.78, unrelated tail 0.87-0.99. Distances
# aren't comparable across queries, so this trims noise; it can't prove relevance.
VEC_MAX_DISTANCE = 0.80
VEC_WINDOW = 0.15  # ... and within this much of the best match

_HYBRID_SQL = text(
    """
    WITH filtered AS (
      SELECT id, embedding, search_tsv FROM events WHERE id = ANY(CAST(:ids AS bigint[]))
    ),
    vec_all AS (
      SELECT id, embedding <=> CAST(:qvec AS vector) AS d
      FROM filtered
      WHERE embedding IS NOT NULL AND CAST(:qvec AS vector) IS NOT NULL
    ),
    vec AS (
      SELECT id, row_number() OVER (ORDER BY d) AS r
      FROM vec_all
      WHERE d <= :max_d AND d <= (SELECT min(d) FROM vec_all) + :window
      ORDER BY d
      LIMIT :per_list
    ),
    fts AS (
      SELECT id, row_number() OVER (ORDER BY ts_rank_cd(search_tsv, q) DESC) AS r
      FROM filtered, websearch_to_tsquery('english', :qtext) q
      WHERE search_tsv @@ q
      ORDER BY ts_rank_cd(search_tsv, q) DESC
      LIMIT :per_list
    )
    SELECT id, sum(1.0 / (:k + r)) AS rrf
    FROM (SELECT id, r FROM vec UNION ALL SELECT id, r FROM fts) ranked
    GROUP BY id
    ORDER BY rrf DESC, id
    LIMIT :fused
    """
)


@dataclass
class Overrides:
    """Explicit filters from the app's chips. They win over the parser (skill rule)."""

    categories: list[str] | None = None
    when: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    radius_km: float | None = None
    area: str | None = None
    region: str | None = None
    free: bool | None = None
    online: Online | None = None
    include_restricted: bool = False


@dataclass
class Retrieval:
    events: list[dict]
    query: EventQuery
    semantic_query: str
    place: str | None
    notes: list[str] = field(default_factory=list)
    matched: bool = True  # False: the semantic query matched nothing; events only fit the filters


async def retrieve(
    session: AsyncSession,
    parsed: ParsedQuery,
    overrides: Overrides,
    *,
    lat: float | None,
    lng: float | None,
    now: datetime,
    voyage: VoyageClient | None,
    onemap: OneMapClient | None,
    top_k: int,
) -> Retrieval:
    notes: list[str] = []
    q, place = await _build_query(parsed, overrides, lat=lat, lng=lng, now=now, onemap=onemap, notes=notes)

    semantic = parsed.semantic_query.strip()
    order_q = replace(q, sort="distance" if q.radius_m is not None else "soonest")
    ids = await candidate_ids(session, order_q, now, limit=CANDIDATES)
    if semantic and ids:
        ranked = await _hybrid_rank(session, ids, semantic, voyage, notes)
        if ranked:
            events = await fetch_events(session, [i for i, _ in ranked], q)
            scores = dict(ranked)
            events.sort(key=lambda e: -scores[e["id"]] * _boost(e, q, now))
            return Retrieval(events[:top_k], q, semantic, place, notes)
        notes.append(f"no close matches for {semantic!r}; showing events that fit the other filters")
        events = await fetch_events(session, ids[:top_k], q)
        return Retrieval(events, q, semantic, place, notes, matched=False)
    events = await fetch_events(session, ids[:top_k], q)
    return Retrieval(events, q, semantic, place, notes)


async def _build_query(
    parsed: ParsedQuery,
    o: Overrides,
    *,
    lat: float | None,
    lng: float | None,
    now: datetime,
    onemap: OneMapClient | None,
    notes: list[str],
) -> tuple[EventQuery, str | None]:
    if o.when:
        start, end = preset_window(o.when, now)
    elif o.date_from or o.date_to:
        start, end = explicit_window(o.date_from, o.date_to, now)
    else:
        start, end = explicit_window(parsed.date_from, parsed.date_to, now)

    area = o.area
    region = o.region or parsed.region
    point_lat = point_lng = radius_m = None
    place = None
    radius_km = o.radius_km or parsed.radius_km

    if parsed.place_text and not area:
        place = parsed.place_text
        as_area = parsed.place_text.strip().upper()
        if parsed.place_mode == "in" and as_area in PLANNING_AREA_REGION:
            area = as_area
        else:
            hit = await _geocode(onemap, parsed.place_text, notes)
            if hit:
                point_lat, point_lng = hit
                radius_m = (radius_km or DEFAULT_RADIUS_KM) * 1000
            elif as_area in PLANNING_AREA_REGION:
                area = as_area
            else:
                notes.append(f"couldn't locate {parsed.place_text!r}; searched all of Singapore")
    if point_lat is None and lat is not None and lng is not None:
        point_lat, point_lng = lat, lng  # distances always; a radius only when asked for
        if parsed.near_me or o.radius_km:
            radius_m = (radius_km or DEFAULT_RADIUS_KM) * 1000
    elif parsed.near_me and lat is None:
        notes.append("share your location (or name a place) to search near you")

    if o.online:
        online = o.online
    elif parsed.online_only:
        online = "only"
    else:
        online = "include" if parsed.include_online else "exclude"

    # Categories filter only when they're reliable: the app's chips, or the LLM parser.
    # The rule parser's guesses ("ai" -> tech_startup) would drop relevant events that
    # aren't classified yet; its words still count in ranking via the semantic query.
    if o.categories is not None:
        categories = tuple(o.categories)
    elif parsed.parser == "llm":
        categories = parsed.categories
    else:
        categories = ()

    q = EventQuery(
        start=start,
        end=end,
        categories=categories,
        lat=point_lat,
        lng=point_lng,
        radius_m=radius_m,
        area=area,
        region=region,
        free=o.free if o.free is not None else parsed.is_free,
        online=online,
        include_restricted=o.include_restricted,
        sort="soonest",
        limit=CANDIDATES,
    )
    return q, place


async def _geocode(onemap: OneMapClient | None, place: str, notes: list[str]) -> tuple[float, float] | None:
    if onemap is None:
        return None
    try:
        results = await onemap.search(place)
    except (OneMapError, httpx.HTTPError) as exc:
        logger.warning("geocoding %r failed: %s", place, exc)
        return None
    for r in results:
        if in_singapore(r.lat, r.lng):
            return r.lat, r.lng
    return None


async def _hybrid_rank(
    session: AsyncSession, ids: list[int], semantic: str, voyage: VoyageClient | None, notes: list[str]
) -> list[tuple[int, float]]:
    qvec = None
    if voyage is not None:
        try:
            qvec = vector_literal((await voyage.embed([semantic], input_type="query", max_attempts=1))[0])
        except (VoyageError, httpx.HTTPError) as exc:
            logger.warning("query embedding failed: %s", exc)
            notes.append("semantic search unavailable; used keyword matching")
    words = re.findall(r"[\w'-]+", semantic)
    rows = await session.execute(
        _HYBRID_SQL,
        {
            "ids": ids,
            "qvec": qvec,
            "qtext": " or ".join(words),  # any word may match; ts_rank_cd rewards more
            "per_list": PER_LIST,
            "k": RRF_K,
            "fused": FUSED,
            "max_d": VEC_MAX_DISTANCE,
            "window": VEC_WINDOW,
        },
    )
    return [(r.id, float(r.rrf)) for r in rows]


def _boost(event: dict, q: EventQuery, now: datetime) -> float:
    window_h = max((q.end - max(q.start, now)).total_seconds() / 3600, 1.0)
    hours = max((event["starts_at"] - now).total_seconds() / 3600, 0.0)
    soon = 1 - min(hours / window_h, 1.0)
    near = 0.0
    if q.radius_m and event["distance_m"] is not None:
        near = 1 - min(event["distance_m"] / q.radius_m, 1.0)
    return 1 + BOOST * soon + BOOST * near

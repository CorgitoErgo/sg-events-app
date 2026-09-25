"""Event search: structured filters in SQL (see the geo-proximity-sg and rag-pipeline skills).

Radius search uses ST_DWithin on geography (metres, GiST index). ST_MakePoint takes
(lng, lat). An event is listed until it ends: coalesce(ends_at, starts_at), with a day's
grace for all-day events that have no end.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from geoalchemy2 import Geography
from sqlalchemy import Float, and_, case, cast, func, literal, not_, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from db.models import Event, EventSession, EventSource, Venue

Online = Literal["include", "exclude", "only"]
Sort = Literal["soonest", "distance", "blend"]


@dataclass(frozen=True, slots=True)
class EventQuery:
    start: datetime
    end: datetime
    categories: tuple[str, ...] = ()
    lat: float | None = None
    lng: float | None = None
    radius_m: float | None = None
    area: str | None = None
    region: str | None = None
    free: bool | None = None
    online: Online = "include"
    include_restricted: bool = False
    sort: Sort = "soonest"
    limit: int = 20
    offset: int = 0

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None


_LIST_COLUMNS = (
    Event.id, Event.title, Event.summary, Event.starts_at, Event.ends_at, Event.all_day,
    Event.is_online, Event.is_free, Event.price_min_sgd, Event.price_max_sgd, Event.categories,
    Event.audience, Event.organizer, Event.image_url, Event.registration_url, Event.confidence,
    Event.status, Event.venue_id,
    Venue.name.label("venue_name"), Venue.address.label("venue_address"),
    Venue.postal_code.label("venue_postal_code"), Venue.planning_area.label("venue_planning_area"),
    Venue.region.label("venue_region"),
    func.ST_Y(func.geometry(Event.geom)).label("lat"),
    func.ST_X(func.geometry(Event.geom)).label("lng"),
)  # fmt: skip


def _ends():
    return func.coalesce(
        Event.ends_at,
        case((Event.all_day.is_(True), Event.starts_at + timedelta(days=1)), else_=Event.starts_at),
    )


def _point(q: EventQuery):
    return cast(func.ST_MakePoint(literal(q.lng, Float), literal(q.lat, Float)), Geography)


def _conditions(q: EventQuery, now: datetime) -> list[Any]:
    conds: list[Any] = [
        Event.status == "active",
        _ends() >= max(q.start, now),  # not over, and not over before the window opens
        Event.starts_at < q.end,
    ]
    if q.categories:
        conds.append(Event.categories.overlap(cast(list(q.categories), ARRAY(Text))))
    if q.free is not None:
        conds.append(Event.is_free.is_(q.free))
    if not q.include_restricted:
        conds.append(Event.audience == "public")
    if q.area:
        conds.append(Venue.planning_area == q.area)
    if q.region:
        conds.append(Venue.region == q.region)

    online_only = and_(Event.is_online.is_(True), Event.geom.is_(None))  # no physical place
    if q.online == "only":
        conds.append(Event.is_online.is_(True))
    elif q.has_location:
        near = func.ST_DWithin(Event.geom, _point(q), q.radius_m)
        conds.append(or_(near, online_only) if q.online == "include" else near)
    elif q.online == "exclude":
        conds.append(not_(online_only))
    return conds


def _order(q: EventQuery, distance, now: datetime) -> list[Any]:
    if q.sort == "distance":
        return [distance.asc().nulls_last(), Event.starts_at, Event.id]
    if q.sort == "blend":
        # 0.6 * nearness + 0.4 * soonness, both in [0, 1] (geo-proximity-sg skill)
        window_h = max((q.end - max(q.start, now)).total_seconds() / 3600, 1.0)
        hours_until = func.greatest(func.extract("epoch", Event.starts_at - now) / 3600, 0)
        nearness = func.coalesce(1 - func.least(distance / q.radius_m, 1), 0)
        soonness = 1 - func.least(hours_until / window_h, 1)
        return [(0.6 * nearness + 0.4 * soonness).desc(), Event.starts_at, Event.id]
    return [Event.starts_at, Event.id]


async def search_events(session: AsyncSession, q: EventQuery, now: datetime) -> tuple[list[dict], int]:
    distance = (
        func.ST_Distance(Event.geom, _point(q)) if q.has_location else literal(None, Float)
    ).label("distance_m")
    conds = _conditions(q, now)

    base = select(*_LIST_COLUMNS, distance).outerjoin(Venue, Venue.id == Event.venue_id).where(*conds)
    rows = (await session.execute(base.order_by(*_order(q, distance, now)).limit(q.limit).offset(q.offset))).all()
    total = await session.scalar(
        select(func.count()).select_from(Event).outerjoin(Venue, Venue.id == Event.venue_id).where(*conds)
    )
    sources = await _sources(session, [r.id for r in rows])
    return [_to_dict(r, sources[r.id]) for r in rows], total or 0


async def get_event(session: AsyncSession, event_id: int) -> dict | None:
    extra = (
        Event.title_alt, Event.description, Event.language, Event.first_seen_at, Event.last_seen_at,
        literal(None, Float).label("distance_m"),
    )  # fmt: skip
    row = (
        await session.execute(
            select(*_LIST_COLUMNS, *extra)
            .outerjoin(Venue, Venue.id == Event.venue_id)
            .where(Event.id == event_id)
        )
    ).first()
    if row is None:
        return None
    sessions = (
        await session.execute(
            select(EventSession.starts_at, EventSession.ends_at)
            .where(EventSession.event_id == event_id)
            .order_by(EventSession.starts_at)
        )
    ).all()
    out = _to_dict(row, (await _sources(session, [event_id]))[event_id])
    out.update(
        title_alt=row.title_alt,
        description=row.description,
        language=row.language,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        sessions=[{"starts_at": s.starts_at, "ends_at": s.ends_at} for s in sessions],
    )
    return out


async def _sources(session: AsyncSession, event_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = defaultdict(list)
    if event_ids:
        rows = await session.execute(
            select(EventSource.event_id, EventSource.source_id, EventSource.source_url)
            .where(EventSource.event_id.in_(event_ids))
            .order_by(EventSource.event_id, EventSource.source_id, EventSource.source_url)
        )
        for event_id, source_id, url in rows:
            out[event_id].append({"source_id": source_id, "url": url})
    return out


def _to_dict(r, sources: list[dict]) -> dict:
    return {
        "id": r.id,
        "title": r.title,
        "summary": r.summary,
        "starts_at": r.starts_at,
        "ends_at": r.ends_at,
        "all_day": bool(r.all_day),
        "is_online": bool(r.is_online),
        "is_free": r.is_free,
        "price_min_sgd": float(r.price_min_sgd) if r.price_min_sgd is not None else None,
        "price_max_sgd": float(r.price_max_sgd) if r.price_max_sgd is not None else None,
        "categories": list(r.categories or []),
        "audience": r.audience or "public",
        "organizer": r.organizer,
        "image_url": r.image_url,
        "registration_url": r.registration_url,
        "confidence": r.confidence or "high",
        "status": r.status or "active",
        "venue": {
            "name": r.venue_name,
            "address": r.venue_address,
            "postal_code": r.venue_postal_code,
            "planning_area": r.venue_planning_area,
            "region": r.venue_region,
        }
        if r.venue_id is not None
        else None,
        "location": {"lat": r.lat, "lng": r.lng} if r.lat is not None else None,
        "distance_m": round(r.distance_m, 1) if r.distance_m is not None else None,
        "sources": sources,
    }

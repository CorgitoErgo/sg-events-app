from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories import CATEGORY_IDS
from app.schemas import AppliedFilters, EventDetail, EventsPage
from app.services.events import EventQuery, get_event, search_events
from app.timewindows import Preset, explicit_window, preset_window
from db.session import get_session
from pipeline.sg import PLANNING_AREA_REGION

router = APIRouter(tags=["events"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
REGIONS = frozenset(PLANNING_AREA_REGION.values())


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def _parse_when(value: str | None, name: str) -> date | datetime | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
    except ValueError:
        raise _bad_request(f"{name} must be an ISO date (2026-10-03) or datetime") from None


@router.get("/events", response_model=EventsPage)
async def list_events(
    session: SessionDep,
    category: Annotated[list[str] | None, Query(description="Repeatable; matches any. See GET /categories.")] = None,
    when: Annotated[Preset | None, Query(description="Preset window in SGT; excludes date_from/date_to.")] = None,
    date_from: Annotated[str | None, Query(description="ISO date (SGT day) or datetime. Default: now.")] = None,
    date_to: Annotated[str | None, Query(description="Inclusive ISO date or datetime. Default: 30 days on.")] = None,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    radius_km: Annotated[float, Query(gt=0, le=50, description="Used with lat/lng.")] = 5.0,
    area: Annotated[str | None, Query(description="Planning area, e.g. SENGKANG ('events in').")] = None,
    region: Annotated[str | None, Query(description="CENTRAL, EAST, NORTH, NORTH-EAST or WEST.")] = None,
    free: bool | None = None,
    online: Annotated[
        Literal["include", "exclude", "only"] | None,
        Query(description="Default: exclude when searching around a point, include otherwise."),
    ] = None,
    include_restricted: Annotated[bool, Query(description="Also show students/members/alumni-only events.")] = False,
    sort: Annotated[
        Literal["soonest", "distance", "blend"] | None,
        Query(description="Default: distance with lat/lng, soonest otherwise."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> EventsPage:
    now = datetime.now(UTC)
    categories = tuple(dict.fromkeys(category or ()))
    if unknown := [c for c in categories if c not in CATEGORY_IDS]:
        raise _bad_request(f"unknown categories {unknown}; see GET /categories")
    if (lat is None) != (lng is None):
        raise _bad_request("give both lat and lng, or neither")
    has_location = lat is not None
    if sort in ("distance", "blend") and not has_location:
        raise _bad_request(f"sort={sort} needs lat and lng")
    if when and (date_from or date_to):
        raise _bad_request("use either when or date_from/date_to, not both")
    area_norm = area.strip().upper() if area else None
    if area_norm and area_norm not in PLANNING_AREA_REGION:
        raise _bad_request(f"unknown planning area {area!r}")
    region_norm = region.strip().upper() if region else None
    if region_norm and region_norm not in REGIONS:
        raise _bad_request(f"region must be one of {sorted(REGIONS)}")

    if when:
        start, end = preset_window(when, now)
    else:
        start, end = explicit_window(_parse_when(date_from, "date_from"), _parse_when(date_to, "date_to"), now)
    if end <= start:
        raise _bad_request("date_to must be after date_from")

    q = EventQuery(
        start=start,
        end=end,
        categories=categories,
        lat=lat,
        lng=lng,
        radius_m=radius_km * 1000 if has_location else None,
        area=area_norm,
        region=region_norm,
        free=free,
        online=online or ("exclude" if has_location else "include"),
        include_restricted=include_restricted,
        sort=sort or ("distance" if has_location else "soonest"),
        limit=limit,
        offset=offset,
    )
    items, total = await search_events(session, q, now)
    return EventsPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        next_offset=offset + limit if offset + limit < total else None,
        filters=AppliedFilters(
            categories=list(categories),
            date_from=start,
            date_to=end,
            when=when,
            lat=lat,
            lng=lng,
            radius_km=radius_km if has_location else None,
            area=area_norm,
            region=region_norm,
            free=free,
            online=q.online,
            include_restricted=include_restricted,
            sort=q.sort,
        ),
    )


@router.get("/events/{event_id}", response_model=EventDetail)
async def event_detail(session: SessionDep, event_id: int) -> EventDetail:
    event = await get_event(session, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event

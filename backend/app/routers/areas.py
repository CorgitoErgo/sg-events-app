"""Planning areas, for the app's "home area" fallback when location permission is denied.

The app stores only the planning area (geo-proximity-sg skill, PDPA): a postal code is
resolved here and not kept.
"""

import re
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.clients import ClientsDep
from pipeline.geo.onemap import OneMapError
from pipeline.sg import PLANNING_AREA_REGION, in_singapore, region_for

router = APIRouter(tags=["areas"])


class AreaOut(BaseModel):
    planning_area: str
    region: str


@router.get("/areas", response_model=list[AreaOut])
async def list_areas() -> list[AreaOut]:
    return [AreaOut(planning_area=a, region=r) for a, r in sorted(PLANNING_AREA_REGION.items())]


@router.get("/areas/resolve", response_model=AreaOut)
async def resolve_postal(
    clients: ClientsDep,
    postal: Annotated[str, Query(description="6-digit Singapore postal code")],
) -> AreaOut:
    if not re.fullmatch(r"\d{6}", postal):
        raise HTTPException(status_code=422, detail="postal must be a 6-digit Singapore postal code")
    onemap = clients.onemap
    if onemap is None or not onemap.has_credentials:
        raise HTTPException(status_code=503, detail="postal code lookup is unavailable")
    try:
        hit = next((r for r in await onemap.search(postal) if r.postal == postal and in_singapore(r.lat, r.lng)), None)
        area = await onemap.planning_area(hit.lat, hit.lng) if hit else None
    except (OneMapError, httpx.HTTPError):
        raise HTTPException(status_code=503, detail="postal code lookup is unavailable") from None
    if hit is None or area is None or region_for(area) is None:
        raise HTTPException(status_code=404, detail="postal code not found")
    return AreaOut(planning_area=area, region=region_for(area))

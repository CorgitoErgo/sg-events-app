"""POST /ask: natural-language event search with a grounded answer (rag-pipeline skill).

JSON by default; with "stream": true, Server-Sent Events:
    event: meta   data: {applied_filters, events, parser, notes}
    event: delta  data: {"text": "..."}          (repeated)
    event: done   data: {answer_text, cited_event_ids, answered_by}
All database work happens before streaming starts.
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories import CATEGORIES, CATEGORY_IDS
from app.clients import ClientsDep
from app.config import get_settings
from app.routers.events import _parse_when
from app.schemas import EventOut
from app.services.answer import Answer, answer_stream
from app.services.query_parser import parse_query
from app.services.retrieval import Overrides, Retrieval, retrieve
from app.timewindows import Preset
from db.session import get_session
from pipeline.sg import PLANNING_AREA_REGION, SGT

router = APIRouter(tags=["ask"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]
_LABELS = {c.id: c.label for c in CATEGORIES}


class AskFilters(BaseModel):
    """The app's filter chips. Anything set here overrides what the question implies."""

    categories: list[str] | None = None
    when: Preset | None = None
    date_from: str | None = None
    date_to: str | None = None
    radius_km: float | None = Field(default=None, gt=0, le=50)
    area: str | None = None
    region: Literal["CENTRAL", "EAST", "NORTH", "NORTH-EAST", "WEST"] | None = None
    free: bool | None = None
    online: Literal["include", "exclude", "only"] | None = None
    include_restricted: bool = False

    @field_validator("categories")
    @classmethod
    def _known_categories(cls, value: list[str] | None) -> list[str] | None:
        if value and (unknown := [c for c in value if c not in CATEGORY_IDS]):
            raise ValueError(f"unknown categories {unknown}")
        return value

    @field_validator("area")
    @classmethod
    def _known_area(cls, value: str | None) -> str | None:
        if value and value.strip().upper() not in PLANNING_AREA_REGION:
            raise ValueError(f"unknown planning area {value!r}")
        return value.strip().upper() if value else None


class AskRequest(BaseModel):
    q: str = Field(min_length=2, max_length=500)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    filters: AskFilters = AskFilters()
    stream: bool = False
    time_format: Literal["24h", "12h"] = "24h"
    top_k: int = Field(default=12, ge=1, le=15)


class AskAppliedFilters(BaseModel):
    semantic_query: str
    categories: list[str]
    date_from: datetime
    date_to: datetime
    place: str | None
    lat: float | None
    lng: float | None
    radius_km: float | None
    area: str | None
    region: str | None
    free: bool | None
    online: str
    include_restricted: bool


class AskResponse(BaseModel):
    answer_text: str
    cited_event_ids: list[int]
    events: list[EventOut]
    applied_filters: AskAppliedFilters
    parser: Literal["llm", "rules"]
    answered_by: Literal["claude", "template"]
    notes: list[str]


def _applied(r: Retrieval) -> AskAppliedFilters:
    q = r.query
    return AskAppliedFilters(
        semantic_query=r.semantic_query,
        categories=list(q.categories),
        date_from=q.start,
        date_to=q.end,
        place=r.place,
        lat=q.lat,
        lng=q.lng,
        radius_km=q.radius_m / 1000 if q.radius_m is not None else None,
        area=q.area,
        region=q.region,
        free=q.free,
        online=q.online,
        include_restricted=q.include_restricted,
    )


def filters_text(f: AskAppliedFilters) -> str:
    """"Career fairs & job fairs · within 5 km of Sengkang · Sat 26 Sep – Mon 28 Sep · free"."""
    parts = [" / ".join(_LABELS.get(c, c) for c in f.categories)] if f.categories else []
    if f.radius_km is not None:
        parts.append(f"within {f.radius_km:g} km of {f.place or 'the user'}")
    if f.area:
        parts.append(f"in {f.area.title()}")
    if f.region:
        parts.append(f"in the {f.region.lower()} region")
    start, end = f.date_from.astimezone(SGT), f.date_to.astimezone(SGT)
    parts.append(f"{start:%a} {start.day} {start:%b} – {end:%a} {end.day} {end:%b}")
    if f.free:
        parts.append("free only")
    if f.online == "only":
        parts.append("online only")
    return " · ".join(parts)


@router.post("/ask", response_model=AskResponse)
async def ask(body: AskRequest, session: SessionDep, clients: ClientsDep):
    settings = get_settings()
    now = datetime.now(UTC)
    has_location = body.lat is not None and body.lng is not None
    parsed = await parse_query(
        body.q, now, llm=clients.llm, model=settings.anthropic_fast_model, has_location=has_location
    )
    f = body.filters
    overrides = Overrides(
        categories=f.categories,
        when=f.when,
        date_from=_parse_when(f.date_from, "filters.date_from"),
        date_to=_parse_when(f.date_to, "filters.date_to"),
        radius_km=f.radius_km,
        area=f.area,
        region=f.region,
        free=f.free,
        online=f.online,
        include_restricted=f.include_restricted,
    )
    result = await retrieve(
        session,
        parsed,
        overrides,
        lat=body.lat if has_location else None,
        lng=body.lng if has_location else None,
        now=now,
        voyage=clients.voyage,
        onemap=clients.onemap,
        top_k=body.top_k,
    )
    applied = _applied(result)
    stream = answer_stream(
        clients.llm,
        body.q,
        result.events,
        filters_text(applied),
        model=settings.anthropic_answer_model,
        clock=body.time_format,
        unmatched_query=None if result.matched else result.semantic_query,
    )

    if not body.stream:
        answer: Answer | None = None
        async for piece in stream:
            if isinstance(piece, Answer):
                answer = piece
        assert answer is not None
        return AskResponse(
            answer_text=answer.text,
            cited_event_ids=answer.cited_event_ids,
            events=result.events,
            applied_filters=applied,
            parser=parsed.parser,
            answered_by=answer.answered_by,
            notes=result.notes,
        )

    meta = {
        "applied_filters": applied.model_dump(mode="json"),
        "events": [EventOut(**e).model_dump(mode="json") for e in result.events],
        "parser": parsed.parser,
        "notes": result.notes,
    }

    async def sse():
        yield _sse("meta", meta)
        async for piece in stream:
            if isinstance(piece, Answer):
                yield _sse("done", {
                    "answer_text": piece.text,
                    "cited_event_ids": piece.cited_event_ids,
                    "answered_by": piece.answered_by,
                })  # fmt: skip
            else:
                yield _sse("delta", {"text": piece})

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

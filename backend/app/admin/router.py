"""Admin console API: turn pages you find in your own browser into events, no LLM needed.

Only answers requests from this PC (loopback), even when the API also listens on the LAN
for the phone. API calls must carry an `X-Admin: 1` header, which other websites can't
make your browser send (so they can't post events here).
"""

import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import anyio
import extruct
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories import CATEGORY_IDS
from app.clients import ClientsDep
from app.config import get_settings
from db.models import CrawlRun, Event, EventSource
from db.session import get_session
from pipeline.domains import is_news
from pipeline.ingest import ingest
from pipeline.normalize import NormalizationError, parse_when
from pipeline.sg import SGT
from scrapers.base import RawEvent, SourceBlocked
from scrapers.http import PoliteClient
from scrapers.jsonld import clean, find_events, to_raw_event

SOURCE_ID = "manual"
STATIC = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOOPBACK:
        raise HTTPException(status_code=403, detail="The admin console is only available on this computer.")


def admin_header(x_admin: Annotated[str | None, Header()] = None) -> None:
    if x_admin != "1":
        raise HTTPException(status_code=403, detail="Missing X-Admin header.")


def get_fetcher():
    """Factory for the PoliteClient used by "paste a URL" (overridden in tests)."""
    return PoliteClient


def get_session_factory():
    """Sessions for background work such as auto-search runs (overridden in tests)."""
    from db.session import SessionLocal

    return SessionLocal


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(local_only)], include_in_schema=False)
api = APIRouter(prefix="/api", dependencies=[Depends(admin_header)])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


# --- models ------------------------------------------------------------------------------------

class Draft(BaseModel):
    url: str | None = None
    title: str = ""
    start: str | None = None  # "2026-10-03T10:00" (SGT) or "2026-10-03" for all-day
    end: str | None = None  # all-day: the last day, inclusive
    venue: str | None = None
    address: str | None = None
    postal_code: str | None = None
    lat: float | None = None
    lng: float | None = None
    price: str | None = None
    organizer: str | None = None
    description: str | None = None
    registration_url: str | None = None
    image_url: str | None = None
    is_online: bool = False
    event_status: str | None = None
    source_event_id: str | None = None
    news: bool = False


class PagePayload(BaseModel):
    """What the bookmarklet reads from the page you're viewing."""

    url: str
    title: str | None = None
    description: str | None = None
    selection: str | None = Field(default=None, max_length=10_000)
    jsonld: list[Any] = []


class ExtractRequest(BaseModel):
    url: str


class SaveRequest(BaseModel):
    draft: Draft
    categories: list[str] = []


# --- pages -------------------------------------------------------------------------------------

@router.get("")
async def admin_page() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/static/{name}")
async def admin_static(name: str) -> FileResponse:
    path = (STATIC / name).resolve()
    if path.parent != STATIC.resolve() or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "no-store"})


# --- drafts ------------------------------------------------------------------------------------

@api.post("/drafts")
async def drafts_from_page(page: PagePayload) -> dict:
    return make_drafts(page)


@api.post("/extract")
async def extract_from_url(body: ExtractRequest, fetcher=Depends(get_fetcher)) -> dict:
    url = body.url.strip()
    if not re.match(r"^https?://[^/\s]+", url):
        raise HTTPException(status_code=422, detail="Enter a full link starting with https://")
    try:
        async with fetcher() as client:
            res = await client.get(url, source_id=SOURCE_ID)
    except SourceBlocked as exc:
        raise HTTPException(
            status_code=422,
            detail=f"This site doesn't allow automated fetching ({exc.reason}). Open the page in your browser "
            "and use the “Add to SG Events” bookmark instead.",
        ) from None
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Couldn't fetch the page: {exc!r}") from None
    if not res.ok:
        raise HTTPException(status_code=422, detail=f"The page returned HTTP {res.status}.")
    page_html = res.content.decode("utf-8", errors="replace")
    data = await anyio.to_thread.run_sync(
        lambda: extruct.extract(page_html, base_url=url, syntaxes=["json-ld", "microdata", "opengraph"], uniform=True)
    )
    og = next((d for d in data.get("opengraph", []) if isinstance(d, dict)), {})
    title = og.get("og:title") or _html_title(page_html)
    return make_drafts(
        PagePayload(url=url, title=title, description=og.get("og:description"), jsonld=data["json-ld"] + data["microdata"])
    )


def make_drafts(page: PagePayload) -> dict:
    now = datetime.now(UTC)
    news = is_news(page.url)
    drafts = []
    for obj in find_events(page.jsonld):
        raw = to_raw_event(obj, page_url=page.url, source_id=SOURCE_ID, fetched_at=now)
        if raw is not None:
            drafts.append(_draft_from_raw(raw, news=news))
    if drafts:
        return {"drafts": [d.model_dump() for d in drafts], "note": None}
    blank = Draft(
        url=page.url,
        title=clean(page.title) or "",
        description=clean(page.selection) or clean(page.description),
        registration_url=page.url,
        news=news,
    )
    note = "No structured event data on this page: fill in the date and place yourself."
    if page.selection:
        note += " Your highlighted text is in the description."
    return {"drafts": [blank.model_dump()], "note": note}


def _draft_from_raw(raw: RawEvent, *, news: bool) -> Draft:
    start, end = raw.start_raw, raw.end_raw
    try:
        starts_at, ends_at, all_day = parse_when(raw.start_raw, raw.end_raw, relative_base=raw.fetched_at)
        start = _input_value(starts_at, all_day)
        if ends_at is not None:
            end = _input_value(ends_at - timedelta(days=1) if all_day else ends_at, all_day)
    except NormalizationError:
        pass  # leave as found; the form shows it for fixing
    return Draft(
        url=raw.source_url,
        title=raw.title,
        start=start,
        end=end,
        venue=raw.venue_raw,
        address=raw.address_raw,
        postal_code=raw.postal_code,
        lat=raw.lat,
        lng=raw.lng,
        price=raw.price_raw,
        organizer=raw.organizer,
        description=raw.description,
        registration_url=raw.registration_url,
        image_url=raw.image_url,
        is_online="Online" in str(raw.raw_payload.get("eventAttendanceMode", "")),
        event_status=raw.raw_payload.get("eventStatus") or None,
        source_event_id=raw.source_event_id,
        news=news,
    )


def _input_value(dt: datetime, all_day: bool) -> str:
    local = dt.astimezone(SGT)
    return local.strftime("%Y-%m-%d") if all_day else local.strftime("%Y-%m-%dT%H:%M")


def _html_title(page_html: str) -> str | None:
    m = re.search(r"<title[^>]*>(.*?)</title>", page_html, re.IGNORECASE | re.DOTALL)
    return clean(m.group(1)) if m else None


# --- save / list / delete ----------------------------------------------------------------------

@api.post("/events")
async def save_event(body: SaveRequest, session: SessionDep, clients: ClientsDep) -> dict:
    d = body.draft
    if unknown := [c for c in body.categories if c not in CATEGORY_IDS]:
        raise HTTPException(status_code=422, detail=f"Unknown categories {unknown}")
    if not d.title.strip():
        raise HTTPException(status_code=422, detail="The event needs a title.")
    raw = RawEvent(
        source_id=SOURCE_ID,
        source_url=d.url or f"manual:{uuid.uuid4()}",
        source_event_id=d.source_event_id,
        title=d.title,
        fetched_at=datetime.now(UTC),
        description=None if d.news else d.description,  # news: facts and link only
        start_raw=d.start,
        end_raw=d.end,
        venue_raw=d.venue,
        address_raw=d.address,
        postal_code=d.postal_code,
        lat=d.lat,
        lng=d.lng,
        price_raw=d.price,
        organizer=d.organizer,
        image_url=d.image_url,
        registration_url=d.registration_url or d.url,
        raw_payload={
            "added_via": "admin",
            "eventAttendanceMode": "https://schema.org/OnlineEventAttendanceMode" if d.is_online else "",
            "eventStatus": d.event_status or "",
        },
    )
    try:
        result = await ingest(session, raw, clients, categories=body.categories or None, news=d.news)
    except NormalizationError as exc:
        raise HTTPException(status_code=422, detail=f"Check the start date and time ({exc}).") from None
    return {
        "id": result.event_id,
        "action": result.action,
        "merged_into": result.merged_into,
        "enriched": dict(result.enriched),
    }


@api.get("/events")
async def recent_manual_events(session: SessionDep) -> list[dict]:
    rows = await session.execute(
        select(Event.id, Event.title, Event.starts_at, Event.status, Event.categories, EventSource.source_url)
        .join(EventSource, EventSource.event_id == Event.id)
        .where(EventSource.source_id == SOURCE_ID)
        .order_by(Event.first_seen_at.desc(), Event.id.desc())
        .limit(50)
    )
    return [
        {"id": r.id, "title": r.title, "starts_at": r.starts_at, "status": r.status, "categories": r.categories, "url": r.source_url}
        for r in rows
    ]


@api.delete("/events/{event_id}")
async def delete_manual_event(event_id: int, session: SessionDep) -> dict:
    sources = set(await session.scalars(select(EventSource.source_id).where(EventSource.event_id == event_id)))
    if not sources:
        raise HTTPException(status_code=404, detail="Event not found.")
    if sources - {SOURCE_ID}:
        others = ", ".join(sorted(sources - {SOURCE_ID}))
        raise HTTPException(status_code=409, detail=f"Also listed by {others}; it would come back on the next crawl.")
    await session.execute(delete(Event).where(Event.id == event_id))
    await session.commit()
    return {"deleted": event_id}


@api.get("/status")
async def status(session: SessionDep, clients: ClientsDep) -> dict:
    settings = get_settings()
    active = await session.scalar(
        select(func.count()).select_from(Event).where(
            Event.status == "active", func.coalesce(Event.ends_at, Event.starts_at) >= func.now()
        )
    )
    manual = await session.scalar(
        select(func.count(func.distinct(EventSource.event_id))).where(EventSource.source_id == SOURCE_ID)
    )
    last_runs = (
        await session.execute(
            select(CrawlRun.source_id, func.max(CrawlRun.started_at)).group_by(CrawlRun.source_id)
        )
    ).all()
    return {
        "upcoming_events": active,
        "manual_events": manual,
        "keys": {
            "anthropic": clients.llm is not None,
            "voyage": clients.voyage is not None,
            "onemap": clients.onemap is not None,
            "onemap_can_renew": bool(clients.onemap and clients.onemap.can_renew),
            "eventbrite": settings.eventbrite_token is not None,
            "tavily": clients.tavily is not None,
        },
        "last_crawls": {source: started for source, started in last_runs},
    }


# --- auto-search (discovery agent) ---------------------------------------------------------------

class AutosearchRequest(BaseModel):
    queries: list[str] = Field(default=[], max_length=20)
    max_searches: int | None = Field(default=None, ge=1, le=50)
    max_pages: int | None = Field(default=None, ge=1, le=200)
    max_events: int | None = Field(default=None, ge=1, le=200)
    dry_run: bool = False


_autosearch: dict[str, Any] = {"task": None, "result": None, "dry_run": False}


@api.post("/autosearch")
async def start_autosearch(body: AutosearchRequest, clients: ClientsDep, factory=Depends(get_session_factory)) -> dict:
    import asyncio

    from pipeline.autosearch.runner import configured_limits, run_autosearch

    if clients.tavily is None:
        raise HTTPException(
            status_code=503, detail="Auto-search needs TAVILY_API_KEY in .env (free at tavily.com); then restart the API."
        )
    task = _autosearch["task"]
    if task is not None and not task.done():
        raise HTTPException(status_code=409, detail="An auto-search is already running.")
    limits = configured_limits(
        get_settings(), max_searches=body.max_searches, max_pages=body.max_pages, max_events=body.max_events
    )
    _autosearch.update(result=None, dry_run=body.dry_run)
    _autosearch["task"] = asyncio.create_task(
        run_autosearch(
            session_factory=factory,
            clients=clients,
            tavily=clients.tavily,
            eventbrite=clients.eventbrite,
            limits=limits,
            queries=[q for q in body.queries if q.strip()] or None,
            dry_run=body.dry_run,
        )
    )
    return {"started": True, "limits": vars(limits)}


@api.get("/autosearch")
async def autosearch_status(session: SessionDep) -> dict:
    from db.models import AutosearchDecision

    task = _autosearch["task"]
    running = task is not None and not task.done()
    result = None
    if task is not None and task.done():
        try:
            result = task.result()
        except Exception as exc:  # noqa: BLE001
            return {"running": False, "error": repr(exc), "decisions": [], "counts": {}}

    if result is not None and result.run_id is None:  # dry run: nothing in the database
        decisions = [vars(d) for d in result.decisions]
        run = None
    else:
        run = await session.scalar(
            select(CrawlRun).where(CrawlRun.source_id == "autosearch").order_by(CrawlRun.id.desc()).limit(1)
        )
        rows = []
        if run is not None:
            rows = (
                await session.scalars(
                    select(AutosearchDecision).where(AutosearchDecision.run_id == run.id).order_by(AutosearchDecision.id)
                )
            ).all()
        decisions = [
            {"query": r.query, "url": r.url, "title": r.title, "decision": r.decision, "reason": r.reason, "event_id": r.event_id}
            for r in rows
        ]
    counts = Counter(d["decision"] for d in decisions)
    return {
        "running": running,
        "dry_run": _autosearch["dry_run"],
        "run": None if run is None else {
            "id": run.id, "status": run.status, "started_at": run.started_at, "finished_at": run.finished_at, "error": run.error,
        },
        "stop_reason": result.stop_reason if result else None,
        "error": result.error if result else None,
        "searches": result.counts["searches"] if result else None,
        "counts": counts,
        "decisions": decisions[-300:],
    }  # fmt: skip


router.include_router(api)

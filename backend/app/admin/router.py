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
from urllib.parse import urlsplit

import anyio
import extruct
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories import CATEGORY_IDS
from app.clients import ClientsDep
from app.config import get_settings
from db.models import CrawlRun, Event, EventSource
from db.session import get_session
from pipeline.classify import MANUAL_HASH
from pipeline.dedup import find_duplicate, merge_events
from pipeline.embed import embed_events
from pipeline.enrich import geocode_venues
from pipeline.normalize import NormalizationError, normalize, parse_when
from pipeline.sg import SGT
from pipeline.store import upsert_event
from scrapers.base import RawEvent, SourceBlocked
from scrapers.http import PoliteClient
from scrapers.jsonld import clean, find_events, to_raw_event

SOURCE_ID = "manual"
STATIC = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# News pages: keep facts and a link only, never the article text (CLAUDE.md).
NEWS_DOMAINS = (
    "straitstimes.com", "channelnewsasia.com", "todayonline.com", "mothership.sg", "zaobao.com.sg",
    "businesstimes.com.sg", "asiaone.com", "tnp.straitstimes.com", "beritaharian.sg", "tamilmurasu.com.sg",
)  # fmt: skip


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
    news = _is_news(page.url)
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


def _is_news(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == d or host.endswith("." + d) for d in NEWS_DOMAINS)


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
        ev = normalize(raw)
    except NormalizationError as exc:
        raise HTTPException(status_code=422, detail=f"Check the start date and time ({exc}).") from None
    if d.news:
        ev.confidence = "low"

    event_id, action = await upsert_event(session, ev)
    if body.categories:
        await session.execute(
            update(Event).where(Event.id == event_id).values(categories=body.categories[:3], enrichment_hash=MANUAL_HASH)
        )
    await session.commit()

    counts: Counter[str] = Counter()
    event = await session.get(Event, event_id)
    if clients.onemap is not None and event.venue_id is not None:
        await geocode_venues(session, clients.onemap, counts, only_ids=[event.venue_id])
        await session.commit()
    merged_into = None
    if (other := await find_duplicate(session, event_id)) is not None:
        merged_into = await merge_events(session, event_id, other)
        event_id = merged_into
        await session.commit()
    if clients.voyage is not None:
        await embed_events(session, clients.voyage, counts, only_ids=[event_id])
        await session.commit()
    return {"id": event_id, "action": action, "merged_into": merged_into, "enriched": dict(counts)}


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
        },
        "last_crawls": {source: started for source, started in last_runs},
    }


router.include_router(api)

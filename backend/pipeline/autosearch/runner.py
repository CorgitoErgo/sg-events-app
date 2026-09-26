"""run_autosearch: plan queries -> search -> decide what to open -> fetch -> extract -> judge ->
save, stopping at the first limit reached. Every decision is logged with its reason.
"""

import logging
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import anthropic
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import AppClients
from app.config import get_settings
from db.models import AutosearchDecision, CrawlRun
from pipeline.autosearch.agent import Budget, Limits, judge
from pipeline.autosearch.eventbrite import EventbriteClient, EventbriteError, event_id_from_url
from pipeline.autosearch.eventbrite import to_raw_event as eventbrite_raw
from pipeline.autosearch.extract import llm_events, page_text, structured_events
from pipeline.autosearch.tavily import TavilyClient, TavilyError
from pipeline.domains import BLOCKED_DOMAINS, blocked_reason, host_of, is_news
from pipeline.ingest import ingest
from pipeline.normalize import NormalizationError, normalize
from pipeline.sg import SGT
from scrapers.base import RawEvent, SourceBlocked
from scrapers.http import PoliteClient

logger = logging.getLogger(__name__)

SOURCE_ID = "autosearch"  # crawl_runs / decisions
WEB_SOURCE = "web"  # source_id of events found on ordinary pages
MAX_PAGE_BYTES = 5_000_000

# One search phrase per category; each run takes the least recently used ones first,
# so all categories get covered over successive runs.
QUERY_PHRASES: tuple[str, ...] = (
    "career fair", "job fair", "community event", "volunteer event", "workshop", "public talk",
    "kids activities", "tech meetup", "art exhibition", "free concert", "health screening",
    "open house", "bazaar", "nature walk", "fun run", "seniors activities", "youth programme",
    "networking event", "festival celebration", "career talk",
)  # fmt: skip


@dataclass
class Decision:
    query: str
    url: str
    title: str | None
    decision: str  # saved | merged | known | skipped | error | would_save (dry run)
    reason: str
    event_id: int | None = None


@dataclass
class AutosearchResult:
    run_id: int | None
    status: str
    stop_reason: str
    counts: Counter[str] = field(default_factory=Counter)
    decisions: list[Decision] = field(default_factory=list)
    error: str | None = None


def configured_limits(settings, **overrides: int | None) -> Limits:
    base = Limits(
        max_searches=settings.autosearch_max_searches,
        max_pages=settings.autosearch_max_pages,
        max_events=settings.autosearch_max_events,
        max_per_domain=settings.autosearch_max_per_domain,
        max_seconds=settings.autosearch_max_minutes * 60,
    )
    changes = {k: v for k, v in overrides.items() if v is not None}
    return Limits(**{**vars(base), **changes}) if changes else base


async def run_with_configured_clients(
    *, queries: list[str] | None = None, dry_run: bool = False, **limit_overrides: int | None
) -> AutosearchResult:
    """Build every client from .env and run once (CLI and worker)."""
    from contextlib import AsyncExitStack

    from db.session import SessionLocal
    from pipeline.clients import make_eventbrite, make_llm, make_onemap, make_tavily, make_voyage

    settings = get_settings()
    async with AsyncExitStack() as stack:
        tavily = make_tavily(settings)
        if tavily is None:
            raise RuntimeError("TAVILY_API_KEY isn't set: get a free key at tavily.com and add it to .env")
        await stack.enter_async_context(tavily)
        eventbrite = make_eventbrite(settings)
        onemap, voyage = make_onemap(settings), make_voyage(settings)
        for c in (eventbrite, onemap, voyage):
            if c is not None:
                await stack.enter_async_context(c)
        llm = make_llm(settings)
        if llm is not None:
            stack.push_async_callback(llm.close)
        return await run_autosearch(
            session_factory=SessionLocal,
            clients=AppClients(llm=llm, voyage=voyage, onemap=onemap),
            tavily=tavily,
            eventbrite=eventbrite,
            limits=configured_limits(settings, **limit_overrides),
            queries=queries,
            dry_run=dry_run,
        )


def plan_queries(now: datetime, *, recent: dict[str, datetime], count: int, custom: list[str] | None = None) -> list[str]:
    if custom:
        return [q.strip() for q in custom if q.strip()][:count]
    month = now.astimezone(SGT).strftime("%B %Y")
    queries = [f"{phrase} Singapore {month}" for phrase in QUERY_PHRASES]
    never = datetime.min.replace(tzinfo=UTC)
    return sorted(queries, key=lambda q: recent.get(q, never))[:count]  # stable: never-used first


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


async def run_autosearch(
    *,
    session_factory: SessionFactory,
    clients: AppClients,
    tavily: TavilyClient,
    eventbrite: EventbriteClient | None = None,
    fetcher_factory: Callable[[], AbstractAsyncContextManager[PoliteClient]] = PoliteClient,
    limits: Limits = Limits(),
    queries: list[str] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> AutosearchResult:
    from pipeline.monitor import send_alert

    now = now or datetime.now(UTC)
    settings = get_settings()
    async with session_factory() as session:
        run = None
        if not dry_run:
            run = CrawlRun(source_id=SOURCE_ID)
            session.add(run)
            await session.commit()
        result = AutosearchResult(run.id if run else None, "ok", "")
        budget = Budget(limits)
        seen: set[str] = set()
        per_domain: Counter[str] = Counter()
        refused_hosts: set[str] = set()

        async def record(query: str, url: str, title: str | None, decision: str, reason: str, event_id: int | None = None) -> None:
            result.decisions.append(Decision(query, url, title, decision, reason, event_id))
            result.counts[decision] += 1
            if run is not None:
                session.add(AutosearchDecision(
                    run_id=run.id, query=query, url=url, title=title, decision=decision, reason=reason, event_id=event_id,
                ))  # fmt: skip
                await session.commit()  # commit each one: the admin console shows progress live

        try:
            recent = dict((await session.execute(
                select(AutosearchDecision.query, text("max(created_at)")).group_by(AutosearchDecision.query)
            )).all())  # fmt: skip
            planned = plan_queries(now, recent=recent, count=limits.max_searches, custom=queries)
            async with fetcher_factory() as client:
                for query in planned:
                    if not budget.can_search():
                        break
                    hits = await tavily.search(query, max_results=limits.results_per_search, exclude_domains=list(BLOCKED_DOMAINS))
                    budget.searches += 1
                    for hit in hits:
                        if budget.stop_reason():
                            break
                        url = hit.url.split("#", 1)[0]
                        if url in seen:
                            continue
                        seen.add(url)
                        await _handle_hit(
                            session, client, clients, eventbrite, query, url, hit.title, budget, per_domain,
                            refused_hosts, record, limits=limits, now=now, dry_run=dry_run, model=settings.anthropic_fast_model,
                        )  # fmt: skip
        except TavilyError as exc:
            result.status, result.error = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - recorded and alerted
            logger.exception("auto-search failed")
            result.status, result.error = "failed", repr(exc)

        if budget.searches >= limits.max_searches and not budget.stop_reason():
            result.stop_reason = f"used {budget.searches} searches (limit {limits.max_searches})"
        else:
            result.stop_reason = budget.stop_reason() or "no more queries"
        result.counts["searches"], result.counts["pages"] = budget.searches, budget.pages

        if run is not None:
            run.finished_at = datetime.now(UTC)
            run.status = result.status
            run.error = result.error
            run.urls_fetched = budget.pages
            run.events_found = result.counts["saved"] + result.counts["merged"] + result.counts["known"]
            run.events_stored = result.counts["saved"] + result.counts["merged"]
            session.add(run)
            await session.commit()
        if result.status == "failed":
            await send_alert(f"autosearch: run failed: {result.error}")
        return result


async def _handle_hit(
    session, client, clients: AppClients, eventbrite, query, url, title, budget: Budget, per_domain, refused_hosts,
    record, *, limits: Limits, now: datetime, dry_run: bool, model: str,
) -> None:  # fmt: skip
    host = host_of(url)
    if reason := blocked_reason(url):
        return await record(query, url, title, "skipped", f"{host}: {reason}")
    if host in refused_hosts:
        return await record(query, url, title, "skipped", "this site refused automated access earlier in the run")
    if per_domain[host] >= limits.max_per_domain:
        return await record(query, url, title, "skipped", f"already opened {limits.max_per_domain} pages from {host} this run")
    if (known := await _known_url(session, url)) is not None:
        return await record(query, url, title, "known", "already listed", known)

    raws: list[RawEvent] = []
    how = "schema.org event data"
    if (eb_id := event_id_from_url(url)) is not None:
        if eventbrite is None:
            return await record(query, url, title, "skipped", "Eventbrite link, but EVENTBRITE_TOKEN isn't set")
        budget.pages += 1
        per_domain[host] += 1
        try:
            data = await eventbrite.event(eb_id)
        except EventbriteError as exc:
            return await record(query, url, title, "error", str(exc))
        if data is None:
            return await record(query, url, title, "skipped", "Eventbrite: event not found")
        raws, how = [eventbrite_raw(data, fetched_at=now)], "the official Eventbrite API"
    elif "eventbrite." in host:
        return await record(query, url, title, "skipped", "Eventbrite listing page: only /e/ event links are read, via the official API")
    else:
        budget.pages += 1
        per_domain[host] += 1
        try:
            res = await client.get(url, source_id=WEB_SOURCE)
        except SourceBlocked as exc:
            refused_hosts.add(host)
            return await record(query, url, title, "skipped", f"site doesn't allow automated access ({exc.reason})")
        except Exception as exc:  # noqa: BLE001
            return await record(query, url, title, "error", f"couldn't fetch the page ({type(exc).__name__})")
        if not res.ok:
            return await record(query, url, title, "skipped", f"page returned HTTP {res.status}")
        if "html" not in res.headers.get("Content-Type", "html") or len(res.content) > MAX_PAGE_BYTES:
            return await record(query, url, title, "skipped", "not an ordinary web page")
        page_html = res.content.decode("utf-8", errors="replace")
        raws = await structured_events(page_html, url, source_id=WEB_SOURCE, fetched_at=now)
        if not raws and clients.llm is not None:
            try:
                raws = await llm_events(clients.llm, page_text(page_html), url, model=model, now=now, source_id=WEB_SOURCE)
                how = "the page text, read by Claude"
            except anthropic.APIError as exc:
                logger.warning("Claude extraction failed for %s: %r", url, exc)
        if not raws:
            hint = "" if clients.llm is not None else " (plain-text pages need ANTHROPIC_API_KEY)"
            return await record(query, url, title, "skipped", f"no event details on the page{hint}")

    news = is_news(url)
    for raw in raws:
        if budget.stop_reason():
            return
        try:
            ev = normalize(raw)
        except NormalizationError:
            await record(query, raw.source_url, raw.title, "skipped", "no usable start date")
            continue
        if raw.raw_payload.get("extracted_by") and not news:
            ev.confidence = "medium"  # read from prose by an LLM, not structured data
        verdict = judge(raw, ev, url, now=now, limits=limits)
        if not verdict.accept:
            await record(query, raw.source_url, raw.title, "skipped", verdict.reason)
            continue
        if (known := await _known_event(session, raw.source_url, ev.fingerprint)) is not None:
            await record(query, raw.source_url, raw.title, "known", "already listed", known)
            continue
        if dry_run:
            await record(query, raw.source_url, raw.title, "would_save", f"{verdict.reason}; from {how}")
            budget.events += 1
            continue
        saved = await ingest(session, raw, clients, news=news, normalized=ev)
        merged = saved.merged_into is not None or saved.action != "inserted"
        reason = f"{verdict.reason}; from {how}" + ("; merged with an existing listing" if merged else "")
        await record(query, raw.source_url, raw.title, "merged" if merged else "saved", reason, saved.event_id)
        budget.events += 1


async def _known_url(session: AsyncSession, url: str) -> int | None:
    return await session.scalar(text("SELECT event_id FROM event_sources WHERE source_url = :u LIMIT 1"), {"u": url})


async def _known_event(session: AsyncSession, url: str, fingerprint: str) -> int | None:
    return await session.scalar(
        text(
            "SELECT id FROM events WHERE fingerprint = :fp "
            "UNION ALL SELECT event_id FROM event_sources WHERE source_url = :u LIMIT 1"
        ),
        {"fp": fingerprint, "u": url},
    )

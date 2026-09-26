"""Crawler breakage alerts (sg-event-scraping skill: "Monitoring breakage").

A scraper that silently returns zero events is the most common failure. Alert when a
source's event count drops > 60% versus its 7-day median, when parse errors exceed 10%,
and whenever a source is blocked or a run fails. Alerts are logged and, if configured,
posted to an incoming webhook (Slack or Discord).
"""

import logging
import statistics
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from db.models import CrawlRun

logger = logging.getLogger(__name__)

DROP_THRESHOLD = 0.60
MIN_HISTORY_RUNS = 3
MIN_MEDIAN_EVENTS = 5  # below this, day-to-day noise isn't breakage
PARSE_ERROR_RATE = 0.10
HISTORY = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class RunStats:
    source_id: str
    status: str
    urls_fetched: int
    urls_failed: int
    events_found: int
    normalize_errors: int
    error: str | None = None


def evaluate(run: RunStats, recent_counts: list[int]) -> list[str]:
    """Alerts for one finished run, given events_found of the source's recent ok runs."""
    s = run.source_id
    if run.status == "blocked":
        return [f"{s}: blocked ({run.error}). Stop and check the site's terms/robots; never work around it."]
    if run.status == "failed":
        return [f"{s}: run failed: {run.error}"]

    alerts = []
    if len(recent_counts) >= MIN_HISTORY_RUNS:
        median = statistics.median(recent_counts)
        if median >= MIN_MEDIAN_EVENTS and run.events_found < (1 - DROP_THRESHOLD) * median:
            drop = 1 - run.events_found / median
            alerts.append(
                f"{s}: found {run.events_found} events, {drop:.0%} below its 7-day median of {median:g}. "
                "The site or feed may have changed."
            )
    pages = run.urls_fetched + run.urls_failed
    if pages and run.urls_failed / pages > PARSE_ERROR_RATE:
        alerts.append(f"{s}: {run.urls_failed} of {pages} pages failed to fetch or parse.")
    parsed = run.events_found + run.normalize_errors
    if parsed and run.normalize_errors / parsed > PARSE_ERROR_RATE:
        alerts.append(f"{s}: {run.normalize_errors} of {parsed} events couldn't be normalized (dates?).")
    return alerts


async def check_run(session: AsyncSession, run: CrawlRun) -> list[str]:
    history = await session.scalars(
        select(CrawlRun.events_found).where(
            CrawlRun.source_id == run.source_id,
            CrawlRun.status == "ok",
            CrawlRun.id != run.id,
            CrawlRun.started_at >= run.started_at - HISTORY,
        )
    )
    stats = RunStats(
        source_id=run.source_id,
        status=run.status,
        urls_fetched=run.urls_fetched,
        urls_failed=run.urls_failed,
        events_found=run.events_found,
        normalize_errors=run.normalize_errors,
        error=run.error,
    )
    return evaluate(stats, list(history))


async def send_alert(message: str) -> None:
    logger.error("ALERT %s", message)
    webhook = get_settings().alert_webhook_url
    if webhook is None:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # "text" for Slack, "content" for Discord; each ignores the other.
            await client.post(webhook.get_secret_value(), json={"text": message, "content": message})
    except httpx.HTTPError as exc:
        logger.warning("alert webhook failed: %r", exc)

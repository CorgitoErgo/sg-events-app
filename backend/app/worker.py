"""Scheduler worker: crawls every source on its own cron (in SGT) plus the freshness jobs.

    uv run python -m app.worker            # run until Ctrl+C
    uv run python -m app.worker --now      # ... and crawl every source once at start-up
    uv run python -m app.worker status     # recent crawl runs per source

Jobs never overlap themselves (max_instances=1) and missed runs collapse into one
(coalesce), e.g. after the machine sleeps.
"""

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from db.models import CrawlRun
from pipeline.sg import SGT
from scrapers.registry import ADAPTERS

logger = logging.getLogger("app.worker")

EXPIRE_AT = {"hour": 3, "minute": 0}  # SGT, nightly
RECHECK_AT = {"hour": 4, "minute": 0}
AUTOSEARCH_AT = {"hour": 7, "minute": 30}  # daily; ~10 Tavily searches = ~300 of the free 1,000 a month


async def crawl_job(source_id: str) -> None:
    from db.session import SessionLocal
    from pipeline.crawl import crawl_source
    from pipeline.enrich import enrich_with_configured_clients

    result = await crawl_source(
        ADAPTERS[source_id](), session_factory=SessionLocal, enricher=enrich_with_configured_clients
    )
    logger.info(
        "%s: run %s %s, %d events (%s)",
        source_id, result.run_id, result.status, len(result.collected.raws), dict(result.stored),
    )  # fmt: skip


async def expire_job() -> None:
    from db.session import SessionLocal
    from pipeline.freshness import expire_ended

    async with SessionLocal() as session:
        await expire_ended(session)


async def recheck_job() -> None:
    from db.session import SessionLocal
    from pipeline.freshness import recheck_missing
    from scrapers.http import PoliteClient

    async with SessionLocal() as session, PoliteClient() as client:
        counts = await recheck_missing(session, {sid: cls() for sid, cls in ADAPTERS.items()}, client)
    if counts:
        logger.info("re-check: %s", dict(counts))


async def autosearch_job() -> None:
    from pipeline.autosearch.runner import run_with_configured_clients

    result = await run_with_configured_clients()
    c = result.counts
    logger.info(
        "autosearch: %s, stopped because %s; saved=%d merged=%d known=%d skipped=%d",
        result.status, result.stop_reason, c["saved"], c["merged"], c["known"], c["skipped"],
    )  # fmt: skip


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        timezone=SGT,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    for source_id, cls in ADAPTERS.items():
        scheduler.add_job(
            crawl_job,
            CronTrigger.from_crontab(cls.schedule, timezone=SGT),
            args=[source_id],
            id=f"crawl:{source_id}",
            name=f"crawl {source_id}",
        )
    scheduler.add_job(expire_job, CronTrigger(**EXPIRE_AT, timezone=SGT), id="expire", name="expire ended events")
    scheduler.add_job(recheck_job, CronTrigger(**RECHECK_AT, timezone=SGT), id="recheck", name="re-check missing events")
    from app.config import get_settings

    if get_settings().tavily_api_key is not None:
        scheduler.add_job(autosearch_job, CronTrigger(**AUTOSEARCH_AT, timezone=SGT), id="autosearch", name="auto-search the web")
    return scheduler


async def run_forever(crawl_now: bool) -> None:
    scheduler = build_scheduler()
    scheduler.start()
    for job in scheduler.get_jobs():
        logger.info("scheduled %-26s next run %s", job.name, job.next_run_time.astimezone(SGT).strftime("%a %d %b %H:%M SGT"))
    if crawl_now:
        for source_id in ADAPTERS:
            await crawl_job(source_id)
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)
        from db.session import engine

        await engine.dispose()


async def print_status(limit: int = 5) -> None:
    from db.session import SessionLocal, engine

    async with SessionLocal() as session:
        for source_id in sorted(ADAPTERS):
            runs = (
                await session.scalars(
                    select(CrawlRun).where(CrawlRun.source_id == source_id).order_by(CrawlRun.started_at.desc()).limit(limit)
                )
            ).all()
            print(f"{source_id}:")
            if not runs:
                print("  no runs yet")
            for r in runs:
                took = f"{(r.finished_at - r.started_at).total_seconds():.0f}s" if r.finished_at else "running"
                print(
                    f"  {r.started_at.astimezone(SGT):%a %d %b %H:%M} SGT  {r.status:<7} {took:>6}  "
                    f"pages {r.urls_fetched}/{r.urls_fetched + r.urls_failed}  events {r.events_found}  "
                    f"stored {r.events_stored}{f'  error: {r.error}' if r.error else ''}"
                )
    await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", choices=["run", "status"], default="run")
    parser.add_argument("--now", action="store_true", help="crawl every source once at start-up")
    args = parser.parse_args(argv)
    if args.command == "status":
        asyncio.run(print_status())
        return
    logger.info("worker starting at %s", datetime.now(UTC).astimezone(SGT).strftime("%H:%M SGT"))
    try:
        asyncio.run(run_forever(args.now))
    except KeyboardInterrupt:
        logger.info("worker stopped")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    main()

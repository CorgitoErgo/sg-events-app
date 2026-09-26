"""Run one source adapter by hand (the worker runs them on a schedule).

    uv run python -m scrapers.run luma --dry-run --limit 5   # fetch + parse + normalize, print
    uv run python -m scrapers.run luma                       # ... store, enrich, record in crawl_runs
"""

import argparse
import asyncio
import logging
import sys

from pipeline.crawl import collect, crawl_source, normalize_all
from pipeline.normalize import NormalizedEvent
from pipeline.sg import SGT
from scrapers.http import PoliteClient
from scrapers.registry import ADAPTERS


def print_events(events: list[NormalizedEvent]) -> None:
    for ev in events:
        start = ev.starts_at.astimezone(SGT)
        when = start.strftime("%a %d %b %Y") + ("" if ev.all_day else start.strftime(" %H:%M"))
        where = "online" if ev.is_online else (ev.venue.name if ev.venue else "(address hidden)")
        geo = f"{ev.lat:.4f},{ev.lng:.4f}" if ev.lat is not None else "no geo"
        flags = " ".join(f for f in (ev.status != "active" and ev.status, ev.audience != "public" and ev.audience) if f)
        print(f"- {ev.title[:70]}")
        print(f"    {when} SGT | {where} | {geo} {flags}".rstrip())
        print(f"    {ev.source_url}  (organizer: {ev.organizer})")


def fmt(counts) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to do"


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", choices=sorted(ADAPTERS))
    parser.add_argument("--dry-run", action="store_true", help="don't write to the database")
    parser.add_argument("--limit", type=int, default=None, help="stop after N events")
    parser.add_argument("--no-enrich", action="store_true", help="skip geocoding, dedup, classification, embedding")
    args = parser.parse_args(argv)
    adapter = ADAPTERS[args.source]()

    if args.dry_run:
        async with PoliteClient() as client:
            collected = await collect(adapter, client, args.limit)
        events, errors = normalize_all(collected.raws)
        print(f"{args.source}: {len(collected.raws)} events parsed, {len(events)} normalized, {errors} failed")
        print_events(events)
        return 0

    from db.session import SessionLocal, engine
    from pipeline.enrich import enrich_with_configured_clients

    try:
        result = await crawl_source(
            adapter,
            session_factory=SessionLocal,
            enricher=None if args.no_enrich else enrich_with_configured_clients,
            limit=args.limit,
        )
    finally:
        await engine.dispose()
    c = result.collected
    print(f"{args.source}: run {result.run_id} {result.status}; pages ok={c.urls_fetched} failed={c.urls_failed}; "
          f"events={len(c.raws)} normalize_errors={result.normalize_errors}")  # fmt: skip
    print("stored: " + fmt(result.stored))
    if not args.no_enrich:
        print("enriched: " + fmt(result.enriched))
    for alert in result.alerts:
        print(f"ALERT: {alert}")
    return 0 if result.status == "ok" and not result.stored["error"] else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emoji in titles on Windows consoles
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))

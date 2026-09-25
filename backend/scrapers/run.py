"""Run one source adapter end to end.

    uv run python -m scrapers.run luma --dry-run --limit 5   # fetch + parse + normalize, print
    uv run python -m scrapers.run luma                       # ... and write to the DB
"""

import argparse
import asyncio
import logging
import sys
from collections import Counter

from pipeline.normalize import NormalizationError, NormalizedEvent, normalize
from pipeline.sg import SGT
from scrapers.base import RawEvent, SourceAdapter, SourceBlocked
from scrapers.http import PoliteClient
from scrapers.luma import LumaAdapter

logger = logging.getLogger("scrapers.run")

ADAPTERS: dict[str, type[SourceAdapter]] = {"luma": LumaAdapter}


async def collect(adapter: SourceAdapter, client: PoliteClient, limit: int | None) -> list[RawEvent]:
    """Run discover + fetch_and_parse; one RawEvent per source_url (tags merged)."""
    by_url: dict[str, RawEvent] = {}
    async for url in adapter.discover(client):
        try:
            raws = await adapter.fetch_and_parse(client, url)
        except SourceBlocked as exc:
            logger.error("%s blocked (%s); stopping this source", adapter.source_id, exc.reason)
            break
        for raw in raws:
            seen = by_url.setdefault(raw.source_url, raw)
            if seen is not raw:
                seen.source_categories += [c for c in raw.source_categories if c not in seen.source_categories]
        if limit and len(by_url) >= limit:
            break
    events = list(by_url.values())
    return events[:limit] if limit else events


def normalize_all(raws: list[RawEvent]) -> list[NormalizedEvent]:
    out = []
    for raw in raws:
        try:
            out.append(normalize(raw))
        except NormalizationError as exc:
            logger.warning("skipped %s: %s", raw.source_url, exc)
    return out


async def store_all(events: list[NormalizedEvent]) -> Counter[str]:
    from db.session import SessionLocal, engine
    from pipeline.store import upsert_event

    counts: Counter[str] = Counter()
    try:
        async with SessionLocal() as session:
            for ev in events:
                try:
                    async with session.begin_nested():  # one bad event doesn't sink the batch
                        _, action = await upsert_event(session, ev)
                    counts[action] += 1
                except Exception:
                    logger.exception("failed to store %s", ev.source_url)
                    counts["error"] += 1
            await session.commit()
    finally:
        await engine.dispose()
    return counts


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


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", choices=sorted(ADAPTERS))
    parser.add_argument("--dry-run", action="store_true", help="don't write to the database")
    parser.add_argument("--limit", type=int, default=None, help="stop after N events")
    args = parser.parse_args(argv)

    adapter = ADAPTERS[args.source]()
    async with PoliteClient() as client:
        raws = await collect(adapter, client, args.limit)
    events = normalize_all(raws)
    print(f"{args.source}: {len(raws)} upcoming Singapore events parsed, {len(events)} normalized")

    if args.dry_run:
        print_events(events)
        return 0
    counts = await store_all(events)
    print("stored: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emoji in titles on Windows consoles
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))

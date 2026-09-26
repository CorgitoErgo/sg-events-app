"""Run the discovery agent once.

    uv run python -m pipeline.autosearch                      # limits from .env (AUTOSEARCH_*)
    uv run python -m pipeline.autosearch --dry-run            # decide, but save nothing
    uv run python -m pipeline.autosearch -q "career fair Singapore October 2026" --max-events 5
"""

import argparse
import asyncio
import logging
import sys

from pipeline.autosearch.runner import run_with_configured_clients


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-q", "--query", action="append", help="search this instead of the planned queries (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="decide what to save, but save nothing")
    parser.add_argument("--max-searches", type=int)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run_with_configured_clients(
                queries=args.query,
                dry_run=args.dry_run,
                max_searches=args.max_searches,
                max_pages=args.max_pages,
                max_events=args.max_events,
            )
        )
    except RuntimeError as exc:
        print(exc)
        return 2
    for d in result.decisions:
        print(f"{d.decision:<10} {(d.title or '')[:50]:<50}  {d.reason}\n{'':<11}{d.url}")
    c = result.counts
    print(
        f"\n{result.status}: stopped because {result.stop_reason}. searches={c['searches']} pages={c['pages']} "
        f"saved={c['saved']} merged={c['merged']} known={c['known']} skipped={c['skipped']} "
        f"errors={c['error']}{f' would_save={c['would_save']}' if args.dry_run else ''}"
    )
    if result.error:
        print(f"error: {result.error}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())

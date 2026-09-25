"""Query-parser eval (rag-pipeline skill, section 6): per-field accuracy on tests/rag_eval/queries.jsonl.

    uv run python -m evals.parser_eval            # the configured parser (Haiku if ANTHROPIC_API_KEY, else rules)
    uv run python -m evals.parser_eval --rules    # force the rule-based parser

Dates are scored against windows computed for a fixed "now" (Fri 25 Sep 2026, 23:00 SGT),
which is also what the parser is told. Each Haiku run costs roughly US$0.05.
Run on every change to the parser prompt or rules. (Retrieval recall@10 needs a frozen
fixture DB snapshot; not built yet.)
"""

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.services.query_parser import ParsedQuery, _date_window, parse_llm, parse_rules
from pipeline.clients import make_llm

QUERIES = Path(__file__).resolve().parents[1] / "tests" / "rag_eval" / "queries.jsonl"
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)
FIELDS = ("categories", "dates", "place", "place_mode", "region", "near_me", "free", "online_only", "radius")


def load() -> list[dict]:
    return [json.loads(line) for line in QUERIES.read_text("utf-8").splitlines() if line.strip()]


def score(case: dict, p: ParsedQuery) -> dict[str, bool]:
    expected_dates = _date_window(case["dates"], NOW) if case.get("dates") else (None, None)
    place = case.get("place")
    return {
        "categories": set(p.categories) == set(case.get("categories", [])),
        "dates": (p.date_from, p.date_to) == expected_dates,
        "place": (p.place_text or "").lower() == (place or ""),
        "place_mode": place is None or p.place_mode == case.get("place_mode", "near"),
        "region": p.region == case.get("region"),
        "near_me": p.near_me == case.get("near_me", False),
        "free": (p.is_free is True) == case.get("free", False),
        "online_only": p.online_only == case.get("online_only", False),
        "radius": p.radius_km == case.get("radius"),
    }


async def run(force_rules: bool) -> dict[str, float]:
    settings = get_settings()
    llm = None if force_rules else make_llm(settings)
    cases = load()
    totals: Counter[str] = Counter()
    misses: list[str] = []
    try:
        for case in cases:
            if llm is None:
                parsed = parse_rules(case["q"], NOW)
            else:
                parsed = await parse_llm(llm, case["q"], NOW, model=settings.anthropic_fast_model, has_location=True)
            result = score(case, parsed)
            totals.update(field for field, ok in result.items() if ok)
            if wrong := [f for f, ok in result.items() if not ok]:
                misses.append(f"  {case['q']!r}: {', '.join(wrong)}")
    finally:
        if llm is not None:
            await llm.close()
    accuracy = {f: totals[f] / len(cases) for f in FIELDS}
    print(f"parser: {'llm' if llm else 'rules'} | {len(cases)} queries")
    for f in FIELDS:
        print(f"  {f:<12} {accuracy[f]:6.1%}")
    print(f"  {'all fields':<12} {sum(1 for _ in cases) - len(misses)}/{len(cases)} queries fully right")
    if misses:
        print("misses:\n" + "\n".join(misses))
    return accuracy


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rules", action="store_true", help="evaluate the rule-based parser")
    asyncio.run(run(parser.parse_args().rules))

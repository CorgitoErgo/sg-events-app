#!/usr/bin/env python3
"""Check whether a URL may be crawled per robots.txt, and report crawl-delay and sitemaps.

Usage:
    python check_robots.py https://www.onepa.gov.sg/events [--ua SGEventsBot]

Exit code 0 = allowed, 1 = disallowed, 2 = robots.txt unreachable (treat as "ask the user").
Uses only the standard library so it runs anywhere.
"""
import argparse
import sys
import urllib.request
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

DEFAULT_UA = "SGEventsBot"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--ua", default=DEFAULT_UA, help="User-agent token to test")
    args = p.parse_args()

    parts = urlparse(args.url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"

    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": args.ua})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"robots.txt: {robots_url}\n  could not fetch ({exc}). Ask the user before crawling.")
        return 2

    rp = RobotFileParser()
    rp.parse(body.splitlines())

    allowed = rp.can_fetch(args.ua, args.url)
    delay = rp.crawl_delay(args.ua)
    sitemaps = rp.site_maps() or []

    print(f"robots.txt: {robots_url}")
    print(f"  user-agent tested: {args.ua}")
    print(f"  {args.url} -> {'ALLOWED' if allowed else 'DISALLOWED'}")
    print(f"  crawl-delay: {delay if delay is not None else 'not set (use >= 2s)'}")
    print("  sitemaps:" + ("" if sitemaps else " none listed"))
    for s in sitemaps:
        print(f"    - {s}")
    print("\nReminder: robots.txt is not the site's terms of use. Check those too.")
    return 0 if allowed else 1


if __name__ == "__main__":
    sys.exit(main())

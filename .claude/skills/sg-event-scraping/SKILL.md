---
name: sg-event-scraping
description: How to collect Singapore event listings from Eventbrite, Luma, onePA, visitsingapore, university sites (NUS, NTU, SMU, SUTD, SIT, SUSS, polytechnics), news outlets (CNA, Straits Times), and community/religious/non-profit sites like Tzu Chi. Use this skill whenever writing, fixing, or debugging a scraper or source adapter, adding a new event source, dealing with blocked requests, JS-rendered pages, robots.txt, rate limiting, RSS/ICS feeds, or scheduling crawls — even if the user just says "add X as a source" or "the Luma scraper broke".
---

# SG Event Scraping

Every source becomes an **adapter** that yields `RawEvent` objects. The pipeline
(`event-schema` skill) turns those into canonical `Event` rows. Adapters never write to the
DB directly and never classify or geocode; that separation keeps each adapter small and
makes breakage easy to localise.

Before writing an adapter, read `references/sources.md` for that source's recommended
access method and known quirks.

## Pick the access method in this order

Cheaper, more stable, and more polite methods first. Only drop down a level when the one
above doesn't exist for that source.

1. **Official API** (Eventbrite org/venue endpoints, OneMap for geo; STB TIH was
   discontinued on 31 Jul 2025)
2. **Structured feeds**: ICS calendars, RSS/Atom, sitemaps with `lastmod`
3. **Embedded structured data** in HTML: JSON-LD `@type: Event`, microdata, `__NEXT_DATA__`
   JSON. Extract with `extruct`; this survives redesigns far better than CSS selectors
4. **The site's own JSON endpoints** the front-end calls (find them in the browser
   Network tab). Only use public, unauthenticated ones
5. **HTML parsing** with `selectolax` selectors
6. **Playwright** only when content is rendered client-side and 3–4 aren't available
7. **LLM extraction** (Claude Haiku with a tool schema) for free-text pages such as news
   articles or community announcements where there's no structure at all

## Politeness and legal hygiene

Run `scripts/check_robots.py <url>` before adding any source and record the result in the
adapter's docstring. Then:

- Honour `robots.txt` disallows and `Crawl-delay`. Default to ≥ 2 s between requests per
  domain and a concurrency of 1 per domain.
- Identify the bot: `User-Agent: SGEventsBot/0.1 (+https://<your-site>/bot; contact@...)`.
- Use conditional requests (`If-None-Match`, `If-Modified-Since`) and cache raw responses
  so re-parsing never needs a re-fetch.
- Read the site's terms. If they prohibit automated access, don't scrape it: use their API,
  ask for permission/a feed, or drop the source. Flag this to the user rather than deciding
  silently.
- Never log in, bypass paywalls, solve CAPTCHAs, or rotate proxies to evade blocks. A 403
  or CAPTCHA means stop and report, not escalate.
- Store facts (title, time, venue, price, link) and a short summary written by the
  pipeline. Don't store full descriptions from news sites. Singapore's Copyright Act 2021
  has a computational data analysis exception, but it has conditions and doesn't cover
  republishing; suggest the user get legal advice before launch.
- PDPA: don't collect attendee lists, personal phone numbers, or emails beyond published
  organiser business contacts.

## Adapter contract

```python
# backend/scrapers/base.py
class SourceAdapter(Protocol):
    source_id: str            # "luma", "onepa", "nus_events" ...
    base_urls: list[str]
    schedule: str             # cron, e.g. "0 */6 * * *"
    min_delay_s: float = 2.0

    async def discover(self, client: PoliteClient) -> AsyncIterator[str]:
        """Yield event detail URLs (or API page cursors)."""

    async def fetch_and_parse(self, client: PoliteClient, url: str) -> list[RawEvent]:
        """Fetch one URL and return zero or more RawEvents."""
```

`RawEvent` is deliberately loose (strings, not parsed datetimes) because parsing belongs in
the normalizer:

```python
@dataclass
class RawEvent:
    source_id: str
    source_url: str
    source_event_id: str | None
    title: str
    description: str | None
    start_raw: str | None      # exactly as found, e.g. "Sat, 3 Oct, 10am–4pm"
    end_raw: str | None
    venue_raw: str | None      # "Our Tampines Hub, 1 Tampines Walk"
    address_raw: str | None
    postal_code: str | None    # 6 digits if found anywhere on the page
    lat: float | None
    lng: float | None
    price_raw: str | None
    organizer: str | None
    image_url: str | None
    registration_url: str | None
    language: str | None       # "en", "zh", "ms", "ta"
    source_categories: list[str]  # the site's own tags, used as classifier hints
    fetched_at: datetime
    raw_payload: dict          # the JSON-LD / API object, for debugging
```

## PoliteClient

Build one shared `httpx.AsyncClient` wrapper that every adapter uses. It owns: per-domain
rate limiting, robots.txt caching (24 h), retries with exponential backoff on 429/5xx
(honour `Retry-After`), conditional GET, raw response caching to `data/raw/<source>/<sha>`,
and a hard stop on 401/403/CAPTCHA pages with a logged `SourceBlocked` event. Adapters
should not create their own clients.

## JSON-LD first

Most event platforms embed schema.org Events. Try this before writing selectors:

```python
import extruct
data = extruct.extract(html, base_url=url, syntaxes=["json-ld", "microdata"], uniform=True)
events = [d for d in data["json-ld"] + data["microdata"]
          if "Event" in str(d.get("@type", ""))]
```

Map `startDate`, `endDate`, `location.name`, `location.address`, `location.geo`,
`offers.price`, `eventAttendanceMode` (online vs offline), and `organizer.name`.

## LLM extraction fallback

For unstructured pages, send cleaned text (strip nav/footer, cap ~8k tokens) to Claude
Haiku with a tool whose input schema matches `RawEvent` (as a list; a page can hold many
events or none). Tell the model today's date in SGT so it can resolve "this Saturday",
and to return an empty list rather than guess. Keep `temperature=0`. Validate the output
with Pydantic and drop events missing a title or any date.

## Scheduling

APScheduler in a worker process reads each adapter's `schedule`. Suggested cadence:
aggregators (Eventbrite, Luma, Peatix) every 6 h; onePA and visitsingapore daily;
university and community sites daily; news RSS hourly. Each run records
`crawl_runs(source_id, started_at, finished_at, urls_fetched, events_found, errors)`.

## Monitoring breakage

A scraper that silently returns zero events is the most common failure. Alert when a
source's event count drops > 60 % versus its 7-day median, or when parse errors exceed
10 % of fetched pages. Keep one saved fixture per source in `tests/fixtures/<source>/`
and a parser test for it, so selector changes are caught in CI.

## Adding a new source (checklist)

1. Read the site's terms and run `scripts/check_robots.py`.
2. Look for an API, ICS, RSS, or sitemap; then JSON-LD; only then selectors.
3. Save 2–3 real pages as fixtures (listing + detail).
4. Write the adapter and a fixture-based test.
5. Add the source to `references/sources.md` with method, cadence, and quirks.
6. Run once against live with `--dry-run --limit 5` and eyeball the RawEvents.

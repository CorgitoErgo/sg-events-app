---
name: event-schema
description: The canonical Event data model for the SG events app and how RawEvents from scrapers are normalized into it — date/time parsing in Singapore time, price parsing, online vs in-person, recurring sessions, deduplication across sources (the same event on Eventbrite, Luma and a news article), freshness and expiry. Use this skill whenever creating or migrating database tables, writing the normalizer or dedup step, parsing messy dates like "Sat 3 Oct, 10am–4pm", or debugging duplicate or stale events.
---

# Event Schema & Normalization

## Tables (Postgres + PostGIS + pgvector)

```sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE venues (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  address       TEXT,
  postal_code   CHAR(6),
  planning_area TEXT,           -- e.g. 'SENGKANG' (from OneMap)
  region        TEXT,           -- 'NORTH-EAST', 'CENTRAL', ...
  geom          GEOGRAPHY(POINT, 4326),
  geocode_source TEXT,          -- 'onemap' | 'source' | 'manual'
  UNIQUE (name, postal_code)
);
CREATE INDEX venues_geom_gix ON venues USING GIST (geom);

CREATE TABLE events (
  id              BIGSERIAL PRIMARY KEY,
  fingerprint     TEXT UNIQUE NOT NULL,     -- see Dedup
  title           TEXT NOT NULL,
  title_alt       TEXT,                     -- second-language title if present
  summary         TEXT,                     -- <= 300 chars, pipeline-written
  description     TEXT,                     -- from primary sources only, never news
  starts_at       TIMESTAMPTZ NOT NULL,
  ends_at         TIMESTAMPTZ,
  all_day         BOOLEAN DEFAULT FALSE,
  is_online       BOOLEAN DEFAULT FALSE,
  venue_id        BIGINT REFERENCES venues(id),
  geom            GEOGRAPHY(POINT, 4326),   -- denormalized from venue for fast queries
  price_min_sgd   NUMERIC(10,2),
  price_max_sgd   NUMERIC(10,2),
  is_free         BOOLEAN,
  categories      TEXT[] NOT NULL DEFAULT '{}',
  audience        TEXT DEFAULT 'public',    -- public | students_only | members_only | alumni
  language        TEXT[],                   -- {'en','zh'}
  organizer       TEXT,
  image_url       TEXT,
  registration_url TEXT,
  confidence      TEXT DEFAULT 'high',      -- high | medium | low (news/poster-derived)
  status          TEXT DEFAULT 'active',    -- active | cancelled | expired
  embedding       VECTOR(1024),             -- set dimension to the chosen Voyage model
  search_tsv      TSVECTOR,
  first_seen_at   TIMESTAMPTZ DEFAULT now(),
  last_seen_at    TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX events_geom_gix   ON events USING GIST (geom);
CREATE INDEX events_starts_idx ON events (starts_at);
CREATE INDEX events_cats_gin   ON events USING GIN (categories);
CREATE INDEX events_tsv_gin    ON events USING GIN (search_tsv);
CREATE INDEX events_title_trgm ON events USING GIN (title gin_trgm_ops);
CREATE INDEX events_emb_hnsw   ON events USING hnsw (embedding vector_cosine_ops);

CREATE TABLE event_sources (          -- one event, many places it was seen
  event_id        BIGINT REFERENCES events(id) ON DELETE CASCADE,
  source_id       TEXT NOT NULL,
  source_url      TEXT NOT NULL,
  source_event_id TEXT,
  raw_payload     JSONB,
  last_seen_at    TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (source_id, source_url)
);

CREATE TABLE event_sessions (         -- onePA courses, multi-day festivals
  event_id  BIGINT REFERENCES events(id) ON DELETE CASCADE,
  starts_at TIMESTAMPTZ NOT NULL,
  ends_at   TIMESTAMPTZ
);
```

Keep `events.starts_at` as the **next upcoming** session so list queries stay simple.

## Normalizing RawEvent → Event

Write each step as a pure function with unit tests; the normalizer is where most bugs hide.

**Dates.** Parse with `dateparser` using `settings={"TIMEZONE": "Asia/Singapore",
"RETURN_AS_TIMEZONE_AWARE": True, "PREFER_DATES_FROM": "future"}` and pass the crawl
time as `RELATIVE_BASE`. Handle ranges like `10am–4pm`, `3–5 Oct`, `3 Oct – 2 Nov`
by splitting on en dash/hyphen/"to" before parsing. If a year is missing and the parsed
date is > 30 days in the past, bump it a year. If only a date is known, set
`all_day=True`. Convert to UTC for storage. Reject events whose start can't be parsed and
log them; don't invent times.

**Price.** "Free", "Free admission", "$0" → `is_free=True`. "$10 – $25", "S$15",
"From $8" → min/max in SGD. "Members $5 / Public $8" → min 5, max 8. Unknown → nulls.

**Online.** JSON-LD `eventAttendanceMode`, or venue/text containing Zoom, Teams, "online",
"webinar", "virtual" → `is_online=True`, no geom. Hybrid events keep both.

**Audience.** "for NUS students only", "members only", "alumni" → set `audience`. The app
hides non-public events by default.

**Cancellation.** "Cancelled", "Postponed", JSON-LD `eventStatus` → `status`.

**Summary.** Generate a ≤ 300-char summary with Claude Haiku from title + description.
For news-derived events, the summary is the only text stored.

## Dedup

The same career fair can appear on e2i, a university page, Eventbrite and a CNA article.

1. **Exact:** same `(source_id, source_event_id)` or same `source_url` → update in place.
2. **Fingerprint:** `sha1(normalized_title | start_date_sgt | venue_postal_or_geohash7)`,
   where normalized title = lowercase, strip punctuation, drop years and words like
   "2026", "singapore", "event".
3. **Fuzzy:** candidates within ±1 day and 300 m (or same postal code), then
   `rapidfuzz.fuzz.token_set_ratio(title_a, title_b) >= 85` → merge. Borderline 70–85:
   ask Claude Haiku "same event? yes/no" with both records.

On merge, keep the richest record as primary with this source priority:
organiser site / official API > ticketing platform > aggregator > news.
Append to `event_sources`; never delete the other source links.

## Freshness

- Every crawl updates `last_seen_at` on both `events` and `event_sources`.
- Nightly job: `ends_at` (or `starts_at` + 1 day) in the past → `status='expired'`.
- Primary source not seen for 3 consecutive expected crawls → re-fetch the URL; 404/410 →
  `status='cancelled'` with reason.
- API and app queries always filter `status='active' AND coalesce(ends_at, starts_at) >= now()`.

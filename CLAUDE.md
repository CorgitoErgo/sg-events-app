# SG Events Tracker — Project Context

A mobile app that tracks Singapore events by user-selected categories (career fairs,
community events, workshops, volunteering, etc.), supports proximity search around the
user's location, and offers a RAG-powered "Ask" assistant ("any free career fairs near
Sengkang this weekend?").

## Architecture (keep to this unless the user changes it)

```
[Source adapters] → [Raw store] → [Extract & normalize] → [Classify] → [Geocode] → [Dedup]
       │                                                                      │
  scheduler (APScheduler)                                                     ▼
                                               Postgres + PostGIS + pgvector (single DB)
                                                                      │
                                         FastAPI  ──  /events  /ask  /categories  /me/*
                                                                      │
                                         Expo (React Native) mobile app
```

## Stack defaults

- Backend: Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, `uv` for deps
- Scraping: `httpx` (async), `selectolax` for HTML, `extruct` for JSON-LD/microdata,
  Playwright only for pages that need JS, `icalendar` for ICS feeds, `feedparser` for RSS
- LLM: Anthropic SDK. `claude-haiku-4-5-20251001` for extraction/classification/query parsing
  (cheap, high-volume); `claude-sonnet-5` for the user-facing Ask answers
- Embeddings: Voyage AI (Anthropic does not ship an embeddings model). Confirm the current
  model name and dimension in Voyage docs before creating the vector column
- DB: Postgres 16 with PostGIS and pgvector (Supabase or local Docker)
- Mobile: Expo + TypeScript + Expo Router, TanStack Query, `expo-location`,
  `react-native-maps`, `expo-notifications`
- Tests: pytest with saved HTML/JSON fixtures per source; Jest for the app

## Repo layout

```
backend/
  app/            # FastAPI app, routers, services
  scrapers/       # one module per source adapter (see sg-event-scraping skill)
  pipeline/       # normalize, classify, geocode, dedup, embed
  db/             # models, migrations
  tests/fixtures/ # saved pages per source, never live network in tests
mobile/           # Expo app
```

## Non-negotiables

- Time zone is `Asia/Singapore` (UTC+8, no DST). Store UTC in DB, render SGT.
- Respect robots.txt, site terms, and rate limits; prefer official APIs, ICS and RSS feeds
  over HTML scraping. Never scrape behind a login. See `sg-event-scraping`.
- Store event facts + a link back to the source. Do not store or redisplay full news
  article text; keep at most a short self-written summary.
- No personal data about attendees; organizer info only as published business contact.
- Every event row keeps `source_url` and `last_seen_at` so stale events can be expired.
- Secrets (`ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `ONEMAP_EMAIL`, `ONEMAP_PASSWORD`,
  `TIH_API_KEY`, `EVENTBRITE_TOKEN`) come from `.env`, never committed.

## Skills in this repo (`.claude/skills/`)

| Skill | Use it for |
|---|---|
| `sg-event-scraping` | Writing/fixing source adapters, per-source strategy, politeness |
| `event-schema` | The canonical Event model, normalization, dates, dedup |
| `event-categorization` | Category taxonomy and the LLM classifier |
| `geo-proximity-sg` | OneMap geocoding, PostGIS radius search, SG location quirks |
| `rag-pipeline` | Embeddings, hybrid retrieval, query parsing, the /ask endpoint |
| `mobile-app-expo` | The Expo app: screens, location permission, map, notifications |

## Build order

1. DB schema + migrations (event-schema, geo-proximity-sg)
2. Two easy adapters first: an ICS/JSON-LD source (Luma or a university calendar) and one API
   source (STB TIH), end to end into the DB
3. Classifier + geocoder + dedup
4. `/events` with category + radius filters
5. Embeddings + `/ask`
6. Mobile app against the live API
7. Remaining adapters, scheduler, monitoring

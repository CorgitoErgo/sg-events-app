# SG Events Tracker: Claude Code Skill Kit

Drop-in project context and skills for building a Singapore events app (scraping + RAG +
proximity search + Expo mobile app) with Claude Code in VS Code.

## Install

1. Install the Claude Code extension in VS Code and sign in.
2. Copy `CLAUDE.md` and the `.claude/` folder into the root of your (empty) project repo.
3. Open that folder in VS Code and start Claude Code there. Project skills in
   `.claude/skills/` load automatically; you can also call them by name, e.g.
   `/sg-event-scraping`.
4. Create `.env` (and add it to `.gitignore`) with:
   `ANTHROPIC_API_KEY, VOYAGE_API_KEY, ONEMAP_EMAIL, ONEMAP_PASSWORD, TIH_API_KEY,
   EVENTBRITE_TOKEN, DATABASE_URL`.

## Accounts to register first

- Anthropic API key (Claude)
- Voyage AI API key (embeddings)
- OneMap developer account (geocoding)
- STB Tourism Information & Services Hub (TIH) API access (official events data)
- Eventbrite API token (only for organiser/venue lookups)
- Postgres with PostGIS + pgvector (local Docker `postgis/postgis` + pgvector, or Supabase)

## Suggested first prompts in Claude Code

1. "Scaffold the backend per CLAUDE.md: FastAPI app, Docker Compose with Postgres +
   PostGIS + pgvector, and the migrations from the event-schema skill."
2. "Build the PoliteClient and the base adapter, then a Luma ICS adapter with fixture tests."
3. "Add the STB TIH adapter. Check the TIH docs first and tell me which endpoints you'll use."
4. "Implement the normalizer, classifier, OneMap geocoder and dedup, and run the Luma
   fixtures through end to end."
5. "Add GET /events with category, date and radius filters."
6. "Add embeddings and POST /ask per the rag-pipeline skill, with the eval set."
7. "Scaffold the Expo app per the mobile-app-expo skill and connect it to /events."

## Contents

```
CLAUDE.md                                   # always-loaded project context
.claude/skills/
  sg-event-scraping/SKILL.md                # adapters, politeness, access-method ladder
  sg-event-scraping/references/sources.md   # per-source notes (Eventbrite, Luma, onePA, ...)
  sg-event-scraping/scripts/check_robots.py # robots.txt checker
  event-schema/SKILL.md                     # DB schema, normalization, dedup, freshness
  event-categorization/SKILL.md             # taxonomy + classifier
  geo-proximity-sg/SKILL.md                 # OneMap + PostGIS radius search
  rag-pipeline/SKILL.md                     # embeddings, hybrid retrieval, /ask, evals
  mobile-app-expo/SKILL.md                  # Expo app screens, location, notifications
```

## Before launch

Source terms of use change. Re-verify each source in `references/sources.md`, and get
legal advice on scraping and content reuse (Singapore Copyright Act 2021, PDPA) before
you publish the app.

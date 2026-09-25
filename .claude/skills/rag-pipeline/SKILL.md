---
name: rag-pipeline
description: The retrieval-augmented generation layer for the SG events app — building embedding text, Voyage embeddings in pgvector, hybrid retrieval (structured filters + vector + full-text with reciprocal rank fusion), natural-language query parsing into filters ("free career fairs near Sengkang this weekend"), grounded answers from Claude with event citations, and RAG evaluation. Use this skill whenever working on search relevance, the /ask or /events/search endpoints, embeddings, reranking, prompt design for answers, hallucinated events, or retrieval evals.
---

# RAG Pipeline

The key design idea: **events are structured data first, text second.** Dates, categories,
price and location are hard constraints handled in SQL. Vectors only rank what survives
the filters. A pure vector search would happily return a great-sounding career fair
from last March, 20 km away.

```
user text ──► Query parser (Haiku, tool use) ──► filters + semantic query
                                                   │
             SQL filters (status, date window, categories, radius, is_free, audience)
                                                   │
                ┌──────────────── candidate set (≤ 500) ────────────────┐
                ▼                                                       ▼
        vector ANN (pgvector)                                 full-text (tsvector)
                └─────────────► Reciprocal Rank Fusion ◄───────────────┘
                                        │
                              optional rerank (Voyage)
                                        │
                     top 10–15 events ──► Claude Sonnet answer with citations
```

## 1. Embedding text

Build one string per event, deterministic, in `pipeline/embed.py`:

```
{title}
Categories: {labels}
When: {Sat 3 Oct 2026, 10:00–16:00 SGT}
Where: {venue}, {planning_area} ({online/in-person})
Price: {Free | S$10–25}
Organizer: {organizer}
{summary}
```

Including the human-readable date and area lets vague queries ("weekend stuff in the
east") match even before filters kick in. Re-embed only when this string's hash changes
(`embedding_hash` column). Use Voyage with `input_type="document"` for events and
`input_type="query"` for queries; batch 128 at a time. Confirm the current model name and
output dimension in Voyage's docs and make the `VECTOR(n)` column match.

`search_tsv` is `to_tsvector('english', title || ' ' || coalesce(summary,'') || ' ' || organizer || ' ' || venue_name)`,
maintained by trigger. For Chinese titles, also index `title_alt` with the `simple`
config, since English stemming doesn't help there.

## 2. Query parser

Claude Haiku with a forced tool call turns free text into filters. Pass the current
SGT date/time, the user's location if shared, and the category table.

```json
{
  "name": "search_events",
  "input_schema": {
    "type": "object",
    "properties": {
      "semantic_query": {"type": "string", "description": "What the user is looking for, stripped of dates/places"},
      "categories": {"type": "array", "items": {"enum": ["career_fair", "..."]}},
      "date_from": {"type": "string", "format": "date-time"},
      "date_to": {"type": "string", "format": "date-time"},
      "place_text": {"type": ["string", "null"], "description": "Place name or postal code to geocode, null if 'near me'"},
      "near_me": {"type": "boolean"},
      "radius_km": {"type": ["number", "null"]},
      "is_free": {"type": ["boolean", "null"]},
      "include_online": {"type": "boolean"}
    },
    "required": ["semantic_query", "near_me", "include_online"]
  }
}
```

Resolve relative dates in SGT: "this weekend" = coming Sat 00:00 → Sun 23:59;
"tonight" = today 17:00 → 23:59; no date mentioned = next 30 days. Merge parser output
with the app's explicit UI filters; **UI filters win** on conflict. If `place_text` is set,
geocode it with the `geo-proximity-sg` helper.

Cache parser results by normalized query + date for 10 minutes.

## 3. Hybrid retrieval

In one SQL round trip where possible:

```sql
WITH filtered AS (
  SELECT id, embedding, search_tsv FROM events
  WHERE status='active' AND coalesce(ends_at, starts_at) >= now()
    AND starts_at BETWEEN :from AND :to
    AND (:cats IS NULL OR categories && :cats)
    AND (:is_free IS NULL OR is_free = :is_free)
    AND audience = 'public'
    AND (:lat IS NULL OR ST_DWithin(geom, ST_MakePoint(:lng,:lat)::geography, :radius_m)
         OR (:include_online AND is_online))
),
vec AS (
  SELECT id, row_number() OVER (ORDER BY embedding <=> :qvec) AS r
  FROM filtered ORDER BY embedding <=> :qvec LIMIT 50
),
fts AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank_cd(search_tsv, q) DESC) AS r
  FROM filtered, websearch_to_tsquery('english', :qtext) q
  WHERE search_tsv @@ q ORDER BY ts_rank_cd(search_tsv, q) DESC LIMIT 50
)
SELECT id, sum(1.0 / (60 + r)) AS rrf
FROM (SELECT * FROM vec UNION ALL SELECT * FROM fts) s
GROUP BY id ORDER BY rrf DESC LIMIT 30;
```

When filters are very selective (few hundred rows), pgvector's HNSW index may return
fewer results than expected; set `hnsw.ef_search` higher or, for small filtered sets,
let Postgres do an exact scan. Add a gentle boost for sooner and closer events after RRF.

If `semantic_query` is empty (pure filter search like "career fairs this week"), skip
vectors and just sort by date or distance.

Optional: rerank the top 30 with Voyage's reranker and keep 10–15.

## 4. Answer generation

`POST /ask` streams a Claude Sonnet response. The prompt includes only retrieved events,
each as a compact block with an ID:

```
<event id="E1842">
Title: NTUC e2i Career Fair @ Sengkang
When: Sat 3 Oct 2026, 10:00–16:00 SGT
Where: Sengkang Community Club (1.2 km away)
Price: Free | Categories: career_fair
Link: https://...
Summary: ...
</event>
```

System prompt rules:
- Answer only from the provided events. If none fit, say so plainly and suggest widening
  the radius, dates, or categories. Never invent events, dates, venues, or prices.
- Cite every event mentioned with its id in square brackets, e.g. `[E1842]`; the app turns
  these into tappable cards.
- Lead with the best 3–5 matches, with when/where/distance/price in one line each.
- Mention when an event is news-derived (`confidence=low`) and suggest checking the link.
- Times in SGT, 12-hour or 24-hour consistently (match the app setting).

Return JSON alongside the stream: `{answer_text, cited_event_ids, applied_filters}` so
the app can show the filters it used as editable chips ("Career fairs · 5 km ·
This weekend"). Validate that every cited ID was in the context; strip any that weren't.

## 5. Tracking & alerts (reuses retrieval)

Users' category subscriptions and saved searches are stored as the same filter object.
A job every few hours runs each saved search for events with `first_seen_at` since the
last run and sends an Expo push notification ("3 new career fairs near you this month").
Rate-limit to one digest per user per day unless they opt into instant alerts.

## 6. Evaluation

`tests/rag_eval/queries.jsonl`: 40–60 realistic queries with the expected filters and
event IDs from a frozen fixture DB snapshot. Examples: "career fairs for fresh grads
this month", "free things for kids near Punggol this Saturday", "volunteering on weekends
in the west", "Chinese-language community events", "AI meetups tonight".

Measure: parser filter accuracy (exact match per field), recall@10 of expected events,
and for answers, a Claude-graded check that every cited event exists and every stated
fact (date, venue, price) matches the event record. Run on every change to prompts,
embedding text, or retrieval SQL.

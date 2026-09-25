"""Initial schema: venues, events, event_sources, event_sessions (event-schema skill).

Revision ID: 0001
Revises:
Create Date: 2026-09-25

Differences from the skill's reference SQL (agreed before implementation):
- events.embedding_hash, so embeddings are recomputed only when the embedding text changes
- event_sessions has a primary key (event_id, starts_at)
- event_sources.event_id is NOT NULL and indexed; (source_id, source_event_id) indexed
- venues unique key is NULLS NOT DISTINCT, so venues without a postal code can't duplicate
- CHECK constraints on events.status / audience / confidence
- triggers maintain events.search_tsv and events.updated_at
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One statement per op.execute: asyncpg can't run several commands in one call.
UPGRADE = [
    "CREATE EXTENSION IF NOT EXISTS postgis",
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    # --- venues -----------------------------------------------------------------------
    """
    CREATE TABLE venues (
      id             BIGSERIAL PRIMARY KEY,
      name           TEXT NOT NULL,
      address        TEXT,
      postal_code    CHAR(6),
      planning_area  TEXT,                    -- e.g. 'SENGKANG' (from OneMap)
      region         TEXT,                    -- 'NORTH-EAST', 'CENTRAL', ...
      geom           GEOGRAPHY(POINT, 4326),
      geocode_source TEXT,                    -- 'onemap' | 'source' | 'manual'
      CONSTRAINT venues_name_postal_code_key UNIQUE NULLS NOT DISTINCT (name, postal_code)
    )
    """,
    "CREATE INDEX venues_geom_gix ON venues USING GIST (geom)",
    # --- events -----------------------------------------------------------------------
    """
    CREATE TABLE events (
      id               BIGSERIAL PRIMARY KEY,
      fingerprint      TEXT NOT NULL,
      title            TEXT NOT NULL,
      title_alt        TEXT,                    -- second-language title if present
      summary          TEXT,                    -- <= 300 chars, pipeline-written
      description      TEXT,                    -- from primary sources only, never news
      starts_at        TIMESTAMPTZ NOT NULL,    -- next upcoming session
      ends_at          TIMESTAMPTZ,
      all_day          BOOLEAN DEFAULT FALSE,
      is_online        BOOLEAN DEFAULT FALSE,
      venue_id         BIGINT REFERENCES venues(id),
      geom             GEOGRAPHY(POINT, 4326),  -- denormalized from venue for fast queries
      price_min_sgd    NUMERIC(10,2),
      price_max_sgd    NUMERIC(10,2),
      is_free          BOOLEAN,
      categories       TEXT[] NOT NULL DEFAULT '{}',
      audience         TEXT DEFAULT 'public',
      language         TEXT[],                  -- {'en','zh'}
      organizer        TEXT,
      image_url        TEXT,
      registration_url TEXT,
      confidence       TEXT DEFAULT 'high',     -- low = news/poster-derived
      status           TEXT DEFAULT 'active',
      embedding        VECTOR(1024),            -- Voyage voyage-4 family default dimension
      embedding_hash   TEXT,
      search_tsv       TSVECTOR,                -- maintained by events_search_tsv_trg
      first_seen_at    TIMESTAMPTZ DEFAULT now(),
      last_seen_at     TIMESTAMPTZ DEFAULT now(),
      updated_at       TIMESTAMPTZ DEFAULT now(),
      CONSTRAINT events_fingerprint_key UNIQUE (fingerprint),
      CONSTRAINT events_audience_check
        CHECK (audience IN ('public', 'students_only', 'members_only', 'alumni')),
      CONSTRAINT events_confidence_check CHECK (confidence IN ('high', 'medium', 'low')),
      CONSTRAINT events_status_check CHECK (status IN ('active', 'cancelled', 'expired'))
    )
    """,
    "CREATE INDEX events_geom_gix   ON events USING GIST (geom)",
    "CREATE INDEX events_starts_idx ON events (starts_at)",
    "CREATE INDEX events_cats_gin   ON events USING GIN (categories)",
    "CREATE INDEX events_tsv_gin    ON events USING GIN (search_tsv)",
    "CREATE INDEX events_title_trgm ON events USING GIN (title gin_trgm_ops)",
    "CREATE INDEX events_emb_hnsw   ON events USING hnsw (embedding vector_cosine_ops)",
    # --- event_sources: one event, many places it was seen ------------------------------
    """
    CREATE TABLE event_sources (
      event_id        BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
      source_id       TEXT NOT NULL,
      source_url      TEXT NOT NULL,
      source_event_id TEXT,
      raw_payload     JSONB,
      last_seen_at    TIMESTAMPTZ DEFAULT now(),
      PRIMARY KEY (source_id, source_url)
    )
    """,
    "CREATE INDEX event_sources_event_id_idx ON event_sources (event_id)",
    """
    CREATE INDEX event_sources_source_event_idx
      ON event_sources (source_id, source_event_id)
    """,
    # --- event_sessions: onePA courses, multi-day festivals ----------------------------
    """
    CREATE TABLE event_sessions (
      event_id  BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
      starts_at TIMESTAMPTZ NOT NULL,
      ends_at   TIMESTAMPTZ,
      PRIMARY KEY (event_id, starts_at)
    )
    """,
    # --- search_tsv: English on title/summary/organizer/venue, 'simple' on title_alt ----
    # concat_ws skips NULLs, so a missing organizer or venue doesn't blank the vector.
    # Note: renaming a venue does not refresh existing events' search_tsv.
    """
    CREATE FUNCTION events_search_tsv_update() RETURNS trigger
    LANGUAGE plpgsql AS $$
    DECLARE
      venue_name TEXT;
    BEGIN
      IF NEW.venue_id IS NOT NULL THEN
        SELECT v.name INTO venue_name FROM venues v WHERE v.id = NEW.venue_id;
      END IF;
      NEW.search_tsv :=
        to_tsvector('english', concat_ws(' ', NEW.title, NEW.summary, NEW.organizer, venue_name))
        || to_tsvector('simple', coalesce(NEW.title_alt, ''));
      RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER events_search_tsv_trg
      BEFORE INSERT OR UPDATE OF title, title_alt, summary, organizer, venue_id ON events
      FOR EACH ROW EXECUTE FUNCTION events_search_tsv_update()
    """,
    # --- updated_at: content changes only ---------------------------------------------
    # Every crawl bumps last_seen_at; that alone shouldn't count as the event changing.
    """
    CREATE FUNCTION events_set_updated_at() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
      IF (to_jsonb(NEW) - 'last_seen_at' - 'updated_at' - 'search_tsv')
         IS DISTINCT FROM (to_jsonb(OLD) - 'last_seen_at' - 'updated_at' - 'search_tsv') THEN
        NEW.updated_at := now();
      END IF;
      RETURN NEW;
    END
    $$
    """,
    """
    CREATE TRIGGER events_set_updated_at_trg
      BEFORE UPDATE ON events
      FOR EACH ROW EXECUTE FUNCTION events_set_updated_at()
    """,
]

# Extensions are left installed: other schemas may use them, and the PostGIS image
# pre-creates postgis anyway.
DOWNGRADE = [
    "DROP TABLE IF EXISTS event_sessions",
    "DROP TABLE IF EXISTS event_sources",
    "DROP TABLE IF EXISTS events",
    "DROP TABLE IF EXISTS venues",
    "DROP FUNCTION IF EXISTS events_search_tsv_update()",
    "DROP FUNCTION IF EXISTS events_set_updated_at()",
]


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE:
        op.execute(stmt)

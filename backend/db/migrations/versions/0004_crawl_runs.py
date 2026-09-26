"""crawl_runs (sg-event-scraping skill: scheduling and monitoring) and events.status_reason.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    """
    CREATE TABLE crawl_runs (
      id               BIGSERIAL PRIMARY KEY,
      source_id        TEXT NOT NULL,
      started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
      finished_at      TIMESTAMPTZ,
      status           TEXT NOT NULL DEFAULT 'running',
      urls_fetched     INTEGER NOT NULL DEFAULT 0,
      urls_failed      INTEGER NOT NULL DEFAULT 0,
      events_found     INTEGER NOT NULL DEFAULT 0,
      normalize_errors INTEGER NOT NULL DEFAULT 0,
      events_stored    INTEGER NOT NULL DEFAULT 0,
      store_errors     INTEGER NOT NULL DEFAULT 0,
      error            TEXT,
      CONSTRAINT crawl_runs_status_check CHECK (status IN ('running', 'ok', 'blocked', 'failed'))
    )
    """,
    "CREATE INDEX crawl_runs_source_started_idx ON crawl_runs (source_id, started_at DESC)",
    # Why an event was cancelled or expired, e.g. "source returned 404".
    "ALTER TABLE events ADD COLUMN status_reason TEXT",
]

DOWNGRADE = [
    "ALTER TABLE events DROP COLUMN IF EXISTS status_reason",
    "DROP TABLE IF EXISTS crawl_runs",
]


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE:
        op.execute(stmt)

"""autosearch_decisions: what the discovery agent did with each search result, and why.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    """
    CREATE TABLE autosearch_decisions (
      id          BIGSERIAL PRIMARY KEY,
      run_id      BIGINT NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
      created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
      query       TEXT NOT NULL,
      url         TEXT NOT NULL,
      title       TEXT,
      decision    TEXT NOT NULL,
      reason      TEXT NOT NULL,
      event_id    BIGINT REFERENCES events(id) ON DELETE SET NULL,
      CONSTRAINT autosearch_decisions_decision_check
        CHECK (decision IN ('saved', 'merged', 'known', 'skipped', 'error'))
    )
    """,
    "CREATE INDEX autosearch_decisions_run_idx ON autosearch_decisions (run_id, id)",
    "CREATE INDEX autosearch_decisions_query_idx ON autosearch_decisions (query, created_at DESC)",
]

DOWNGRADE = ["DROP TABLE IF EXISTS autosearch_decisions"]


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE:
        op.execute(stmt)

"""Enrichment tracking: classifier input hash, source tags, venue geocode attempts.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    # Hash of the classifier/summary input; NULL = not yet classified by the LLM.
    "ALTER TABLE events ADD COLUMN enrichment_hash TEXT",
    # The source's own tags (e.g. the Luma calendar name): classifier hints.
    "ALTER TABLE event_sources ADD COLUMN source_tags TEXT[] NOT NULL DEFAULT '{}'",
    # Last completed OneMap attempt (success or not), so failures aren't retried every run.
    "ALTER TABLE venues ADD COLUMN geocoded_at TIMESTAMPTZ",
    # "Events in Sengkang" filters by planning area rather than radius.
    "CREATE INDEX venues_planning_area_idx ON venues (planning_area)",
]

DOWNGRADE = [
    "DROP INDEX IF EXISTS venues_planning_area_idx",
    "ALTER TABLE venues DROP COLUMN IF EXISTS geocoded_at",
    "ALTER TABLE event_sources DROP COLUMN IF EXISTS source_tags",
    "ALTER TABLE events DROP COLUMN IF EXISTS enrichment_hash",
]


def upgrade() -> None:
    for stmt in UPGRADE:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in DOWNGRADE:
        op.execute(stmt)

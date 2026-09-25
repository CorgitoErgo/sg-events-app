"""updated_at: ignore derived columns (embedding and the enrichment/embedding hashes).

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEMPLATE = """
CREATE OR REPLACE FUNCTION events_set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF (to_jsonb(NEW) {ignored})
     IS DISTINCT FROM (to_jsonb(OLD) {ignored}) THEN
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END
$$
"""

_BASE = ("last_seen_at", "updated_at", "search_tsv")
_DERIVED = ("embedding", "embedding_hash", "enrichment_hash")


def _function(keys: tuple[str, ...]) -> str:
    return _TEMPLATE.format(ignored=" ".join(f"- '{k}'" for k in keys))


def upgrade() -> None:
    op.execute(_function(_BASE + _DERIVED))


def downgrade() -> None:
    op.execute(_function(_BASE))

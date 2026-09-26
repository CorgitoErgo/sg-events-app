"""ORM models for the canonical event schema (see the event-schema skill).

The tables are created by hand-written SQL in db/migrations/versions/0001_initial_schema.py;
keep these models in step with it so `alembic check` stays clean.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

# Voyage voyage-4 family default output dimension (confirmed in Voyage docs 2026-09-25).
EMBEDDING_DIM = 1024

AUDIENCES = ("public", "students_only", "members_only", "alumni")
CONFIDENCES = ("high", "medium", "low")
STATUSES = ("active", "cancelled", "expired")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


# spatial_index=False: the GiST indexes are declared explicitly below with the
# names used in the migration.
GeoPoint = Geography(geometry_type="POINT", srid=4326, spatial_index=False)


class Venue(Base):
    __tablename__ = "venues"
    __table_args__ = (
        UniqueConstraint("name", "postal_code", postgresql_nulls_not_distinct=True),
        Index("venues_geom_gix", "geom", postgresql_using="gist"),
        Index("venues_planning_area_idx", "planning_area"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(CHAR(6))
    planning_area: Mapped[str | None] = mapped_column(Text)  # e.g. 'SENGKANG' (OneMap)
    region: Mapped[str | None] = mapped_column(Text)  # 'NORTH-EAST', 'CENTRAL', ...
    geom: Mapped[Any | None] = mapped_column(GeoPoint)
    geocode_source: Mapped[str | None] = mapped_column(Text)  # onemap | source | manual
    # Last completed OneMap attempt (success or not); NULL = still to do.
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["Event"]] = relationship(back_populates="venue")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(_in("audience", AUDIENCES), name="events_audience_check"),
        CheckConstraint(_in("confidence", CONFIDENCES), name="events_confidence_check"),
        CheckConstraint(_in("status", STATUSES), name="events_status_check"),
        Index("events_geom_gix", "geom", postgresql_using="gist"),
        Index("events_starts_idx", "starts_at"),
        Index("events_cats_gin", "categories", postgresql_using="gin"),
        Index("events_tsv_gin", "search_tsv", postgresql_using="gin"),
        Index(
            "events_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index(
            "events_emb_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    title_alt: Mapped[str | None] = mapped_column(Text)  # second-language title
    summary: Mapped[str | None] = mapped_column(Text)  # <= 300 chars, pipeline-written
    description: Mapped[str | None] = mapped_column(Text)  # primary sources only, never news
    # Stored in UTC; `starts_at` is the next upcoming session (see event_sessions).
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    is_online: Mapped[bool | None] = mapped_column(Boolean, server_default=text("false"))
    venue_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("venues.id"))
    geom: Mapped[Any | None] = mapped_column(GeoPoint)  # denormalized from venue
    price_min_sgd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_max_sgd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    is_free: Mapped[bool | None] = mapped_column(Boolean)
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    audience: Mapped[str | None] = mapped_column(Text, server_default=text("'public'"))
    language: Mapped[list[str] | None] = mapped_column(ARRAY(Text))  # {'en','zh'}
    organizer: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    registration_url: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(Text, server_default=text("'high'"))
    status: Mapped[str | None] = mapped_column(Text, server_default=text("'active'"))
    status_reason: Mapped[str | None] = mapped_column(Text)  # e.g. "source returned 404"
    # Deferred: read and written with explicit SQL (pipeline.embed, app.services.retrieval).
    embedding: Mapped[Any | None] = mapped_column(Vector(EMBEDDING_DIM), deferred=True)
    embedding_hash: Mapped[str | None] = mapped_column(Text)  # hash of the embedding text
    # Hash of the classifier/summary input; NULL = not yet classified by the LLM.
    enrichment_hash: Mapped[str | None] = mapped_column(Text)
    search_tsv: Mapped[Any | None] = mapped_column(TSVECTOR, deferred=True)  # maintained by trigger
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    # Maintained by trigger: bumped on content changes, not on last_seen_at-only updates.
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    venue: Mapped[Venue | None] = relationship(back_populates="events")
    sources: Mapped[list["EventSource"]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True
    )
    sessions: Mapped[list["EventSession"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EventSession.starts_at",
    )


class EventSource(Base):
    """One event, many places it was seen."""

    __tablename__ = "event_sources"
    __table_args__ = (
        Index("event_sources_event_id_idx", "event_id"),
        Index("event_sources_source_event_idx", "source_id", "source_event_id"),
    )

    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE")
    )
    source_id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, primary_key=True)
    source_event_id: Mapped[str | None] = mapped_column(Text)
    source_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    event: Mapped[Event] = relationship(back_populates="sources")


CRAWL_STATUSES = ("running", "ok", "blocked", "failed")


class CrawlRun(Base):
    """One run of one source adapter (sg-event-scraping skill: monitoring)."""

    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint(_in("status", CRAWL_STATUSES), name="crawl_runs_status_check"),
        Index("crawl_runs_source_started_idx", "source_id", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"))
    urls_fetched: Mapped[int] = mapped_column(server_default=text("0"))
    urls_failed: Mapped[int] = mapped_column(server_default=text("0"))
    events_found: Mapped[int] = mapped_column(server_default=text("0"))
    normalize_errors: Mapped[int] = mapped_column(server_default=text("0"))
    events_stored: Mapped[int] = mapped_column(server_default=text("0"))
    store_errors: Mapped[int] = mapped_column(server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)


class EventSession(Base):
    """Individual sessions of multi-session events (onePA courses, multi-day festivals)."""

    __tablename__ = "event_sessions"

    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event: Mapped[Event] = relationship(back_populates="sessions")

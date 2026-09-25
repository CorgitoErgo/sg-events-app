"""Event embeddings with Voyage AI (see the rag-pipeline skill).

One deterministic text per event (title, categories, human-readable SGT date, place,
price, organizer, summary). Re-embedded only when that text changes (embedding_hash).

Voyage embeddings API (docs.voyageai.com/reference/embeddings-api, checked 2026-09-26):
POST https://api.voyageai.com/v1/embeddings, Bearer auth, up to 1,000 inputs per call
(320K tokens for voyage-4); input_type "document" for events, "query" for questions.
"""

import asyncio
import hashlib
import logging
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories import CATEGORIES
from db.models import EMBEDDING_DIM
from pipeline.sg import SGT

logger = logging.getLogger(__name__)

VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
BATCH_SIZE = 128
# Accounts without a payment method are limited to 3 requests and 10K tokens per minute
# (Voyage 429 message, 2026-09-26); keep each batch under that so backfills still work.
BATCH_TOKEN_BUDGET = 8_000
DESCRIPTION_FALLBACK_CHARS = 400
MAX_ATTEMPTS = 4

_LABELS = {c.id: c.label for c in CATEGORIES}


class VoyageError(Exception):
    pass


class VoyageClient:
    def __init__(
        self, api_key: str, *, model: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.model = model
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=60.0, transport=transport
        )

    async def __aenter__(self) -> "VoyageClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: Literal["document", "query"],
        max_attempts: int = MAX_ATTEMPTS,
        backoff_s: float = 5.0,
    ) -> list[list[float]]:
        """Request-time callers pass max_attempts=1: a user shouldn't wait on retries."""
        body = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": EMBEDDING_DIM,
        }
        for attempt in range(max_attempts):
            resp = await self._http.post(VOYAGE_URL, json=body)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                retry_after = resp.headers.get("Retry-After", "")
                await asyncio.sleep(float(retry_after) if retry_after.isdigit() else backoff_s * 2**attempt)
                continue
            if resp.status_code != 200:
                raise VoyageError(f"Voyage embeddings -> HTTP {resp.status_code}: {resp.text[:200]}")
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            vectors = [d["embedding"] for d in data]
            if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIM for v in vectors):
                raise VoyageError("unexpected embedding count or dimension")
            return vectors
        raise VoyageError("Voyage embeddings: gave up after retries")


def vector_literal(vector: list[float]) -> str:
    """pgvector's text format, for CAST(:v AS vector)."""
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


# --- embedding text ------------------------------------------------------------------------------

def embedding_text(
    *,
    title: str,
    categories: list[str],
    starts_at: datetime,
    ends_at: datetime | None,
    all_day: bool,
    venue: str | None,
    planning_area: str | None,
    is_online: bool,
    has_place: bool,
    is_free: bool | None,
    price_min: float | None,
    price_max: float | None,
    organizer: str | None,
    summary: str | None,
    description: str | None,
) -> str:
    lines = [title]
    if categories:
        lines.append("Categories: " + ", ".join(_LABELS.get(c, c) for c in categories))
    lines.append("When: " + format_when(starts_at, ends_at, all_day))
    place = ", ".join(p for p in (venue, planning_area.title() if planning_area else None) if p)
    mode = "online and in-person" if is_online and has_place else "online" if is_online else "in-person"
    lines.append(f"Where: {place} ({mode})" if place else f"Where: {mode}")
    if price := format_price(is_free, price_min, price_max):
        lines.append(f"Price: {price}")
    if organizer:
        lines.append(f"Organizer: {organizer}")
    blurb = summary or (description or "")[:DESCRIPTION_FALLBACK_CHARS].strip()
    if blurb:
        lines.append(blurb)
    return "\n".join(lines)


def format_when(
    starts_at: datetime, ends_at: datetime | None, all_day: bool, *, clock: Literal["24h", "12h"] = "24h"
) -> str:
    """"Sat 3 Oct 2026, 10:00–16:00 SGT" / "Fri 25 Sep – Sun 27 Sep 2026" (all-day)."""
    start = starts_at.astimezone(SGT)
    day = f"{start:%a} {start.day} {start:%b %Y}"
    if all_day:
        if ends_at is None:
            return day
        last = (ends_at.astimezone(SGT) - timedelta(days=1)).date()  # stored end is exclusive
        if last <= start.date():
            return day
        return f"{start:%a} {start.day} {start:%b} – {last:%a} {last.day} {last:%b %Y}"
    if ends_at is None:
        return f"{day}, {_clock(start, clock)} SGT"
    end = ends_at.astimezone(SGT)
    if end.date() == start.date():
        return f"{day}, {_clock(start, clock)}–{_clock(end, clock)} SGT"
    return f"{day}, {_clock(start, clock)} – {end:%a} {end.day} {end:%b}, {_clock(end, clock)} SGT"


def _clock(dt: datetime, clock: str) -> str:
    if clock == "24h":
        return f"{dt:%H:%M}"
    hour = dt.hour % 12 or 12
    suffix = "am" if dt.hour < 12 else "pm"
    return f"{hour}{suffix}" if dt.minute == 0 else f"{hour}:{dt:%M}{suffix}"


def format_price(is_free: bool | None, low: float | None, high: float | None) -> str | None:
    if is_free:
        return "Free"
    if low is None:
        return None
    if high is None:
        return f"from S${low:g}"
    return f"S${low:g}" if low == high else f"S${low:g}–{high:g}"


def text_hash(content: str, model: str) -> str:
    return hashlib.sha256(f"{model}\x1f{content}".encode()).hexdigest()


def estimate_tokens(content: str) -> int:
    return len(content) // 3 + 1  # conservative for English and mixed scripts


def batches(items: list, *, key) -> list[list]:
    """Split into batches of <= BATCH_SIZE items and <= BATCH_TOKEN_BUDGET estimated tokens."""
    out: list[list] = []
    current: list = []
    tokens = 0
    for item in items:
        cost = estimate_tokens(key(item))
        if current and (len(current) >= BATCH_SIZE or tokens + cost > BATCH_TOKEN_BUDGET):
            out.append(current)
            current, tokens = [], 0
        current.append(item)
        tokens += cost
    if current:
        out.append(current)
    return out


# --- pipeline step -------------------------------------------------------------------------------

_EVENTS_TO_EMBED_SQL = text(
    """
    SELECT e.id, e.title, e.categories, e.starts_at, e.ends_at, e.all_day, e.is_online,
           (e.geom IS NOT NULL OR e.venue_id IS NOT NULL) AS has_place,
           e.is_free, e.price_min_sgd, e.price_max_sgd, e.organizer, e.summary, e.description,
           e.embedding_hash, v.name AS venue, v.planning_area
    FROM events e
    LEFT JOIN venues v ON v.id = e.venue_id
    WHERE e.status = 'active' AND coalesce(e.ends_at, e.starts_at) >= now()
      AND (CAST(:only_ids AS bigint[]) IS NULL OR e.id = ANY(CAST(:only_ids AS bigint[])))
    ORDER BY e.id
    """
)


async def embed_events(
    session: AsyncSession,
    voyage: VoyageClient,
    counts: Counter[str],
    *,
    only_ids: Iterable[int] | None = None,
) -> None:
    ids = list(only_ids) if only_ids is not None else None
    pending: list[tuple[int, str, str]] = []
    for r in (await session.execute(_EVENTS_TO_EMBED_SQL, {"only_ids": ids})).all():
        content = embedding_text(
            title=r.title,
            categories=list(r.categories or []),
            starts_at=r.starts_at,
            ends_at=r.ends_at,
            all_day=bool(r.all_day),
            venue=r.venue,
            planning_area=r.planning_area,
            is_online=bool(r.is_online),
            has_place=bool(r.has_place),
            is_free=r.is_free,
            price_min=float(r.price_min_sgd) if r.price_min_sgd is not None else None,
            price_max=float(r.price_max_sgd) if r.price_max_sgd is not None else None,
            organizer=r.organizer,
            summary=r.summary,
            description=r.description,
        )
        digest = text_hash(content, voyage.model)
        if digest != r.embedding_hash:
            pending.append((r.id, content, digest))

    for batch in batches(pending, key=lambda item: item[1]):
        try:
            vectors = await voyage.embed([content for _, content, _ in batch], input_type="document")
        except (VoyageError, httpx.HTTPError) as exc:
            logger.warning("embedding %d events failed: %s", len(batch), exc)
            counts["embed_error"] += len(batch)
            continue
        for (event_id, _, digest), vector in zip(batch, vectors, strict=True):
            await session.execute(
                text("UPDATE events SET embedding = CAST(:v AS vector), embedding_hash = :h WHERE id = :id"),
                {"v": vector_literal(vector), "h": digest, "id": event_id},
            )
        counts["embedded"] += len(batch)

from collections import Counter
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text

from db.models import EMBEDDING_DIM, Event
from pipeline.embed import (
    BATCH_TOKEN_BUDGET,
    VoyageClient,
    VoyageError,
    batches,
    embed_events,
    embedding_text,
    format_price,
    format_when,
)
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from tests.factories import make_raw
from tests.stubs import StubVoyage


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- text -------------------------------------------------------------------------------------

def test_embedding_text_follows_the_skill_layout():
    content = embedding_text(
        title="NTUC e2i Career Fair @ Sengkang",
        categories=["career_fair"],
        starts_at=utc(2026, 10, 3, 2),
        ends_at=utc(2026, 10, 3, 8),
        all_day=False,
        venue="Sengkang Community Club",
        planning_area="SENGKANG",
        is_online=False,
        has_place=True,
        is_free=True,
        price_min=0,
        price_max=0,
        organizer="e2i",
        summary="Walk-in career fair with 40 employers.",
        description=None,
    )
    assert content == (
        "NTUC e2i Career Fair @ Sengkang\n"
        "Categories: Career fairs & job fairs\n"
        "When: Sat 3 Oct 2026, 10:00–16:00 SGT\n"
        "Where: Sengkang Community Club, Sengkang (in-person)\n"
        "Price: Free\n"
        "Organizer: e2i\n"
        "Walk-in career fair with 40 employers."
    )


@pytest.mark.parametrize(
    ("args", "clock", "expected"),
    [
        ((utc(2026, 10, 3, 2), utc(2026, 10, 3, 8), False), "24h", "Sat 3 Oct 2026, 10:00–16:00 SGT"),
        ((utc(2026, 10, 3, 2), utc(2026, 10, 3, 8, 30), False), "12h", "Sat 3 Oct 2026, 10am–4:30pm SGT"),
        ((utc(2026, 10, 3, 14), utc(2026, 10, 3, 18), False), "24h", "Sat 3 Oct 2026, 22:00 – Sun 4 Oct, 02:00 SGT"),
        ((utc(2026, 9, 24, 16), utc(2026, 9, 27, 16), True), "24h", "Fri 25 Sep – Sun 27 Sep 2026"),
        ((utc(2026, 9, 24, 16), utc(2026, 9, 25, 16), True), "24h", "Fri 25 Sep 2026"),
        ((utc(2026, 10, 3, 2), None, False), "12h", "Sat 3 Oct 2026, 10am SGT"),
    ],
)
def test_format_when(args, clock, expected):
    assert format_when(*args, clock=clock) == expected


def test_format_price():
    assert format_price(True, 0, 0) == "Free"
    assert format_price(False, 10, 25) == "S$10–25"
    assert format_price(False, 15, 15) == "S$15"
    assert format_price(False, 8, None) == "from S$8"
    assert format_price(None, None, None) is None


def test_batches_respect_count_and_token_budget():
    big = "x" * (BATCH_TOKEN_BUDGET * 3 // 2 - 30)  # just under half the budget each
    assert [len(b) for b in batches([big] * 5, key=str)] == [2, 2, 1]
    assert [len(b) for b in batches(["hi"] * 300, key=str)] == [128, 128, 44]


# --- client ------------------------------------------------------------------------------------

def voyage_response(n: int, dim: int = EMBEDDING_DIM) -> httpx.Response:
    return httpx.Response(200, json={
        "object": "list",
        "data": [{"object": "embedding", "embedding": [0.1] * dim, "index": i} for i in reversed(range(n))],
        "model": "voyage-4",
        "usage": {"total_tokens": 3},
    })  # fmt: skip


@pytest.mark.anyio
async def test_client_request_shape():
    seen = []

    def handler(request):
        seen.append(request)
        return voyage_response(2)

    async with VoyageClient("key", model="voyage-4", transport=httpx.MockTransport(handler)) as client:
        vectors = await client.embed(["a", "b"], input_type="document")
    body = __import__("json").loads(seen[0].content)
    assert body == {"input": ["a", "b"], "model": "voyage-4", "input_type": "document", "output_dimension": 1024}
    assert seen[0].headers["Authorization"] == "Bearer key"
    assert len(vectors) == 2


@pytest.mark.anyio
async def test_single_attempt_fails_fast_on_rate_limit():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={"detail": "3 RPM"})

    async with VoyageClient("key", model="voyage-4", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(VoyageError, match="429"):
            await client.embed(["q"], input_type="query", max_attempts=1)
    assert len(calls) == 1


@pytest.mark.anyio
async def test_rate_limit_is_retried_for_batches():
    responses = [httpx.Response(429, headers={"Retry-After": "0"}), voyage_response(1)]
    async with VoyageClient("key", model="voyage-4", transport=httpx.MockTransport(lambda r: responses.pop(0))) as client:
        assert len(await client.embed(["doc"], input_type="document")) == 1


@pytest.mark.anyio
async def test_wrong_dimension_is_rejected():
    async with VoyageClient("key", model="voyage-4", transport=httpx.MockTransport(lambda r: voyage_response(1, 512))) as client:
        with pytest.raises(VoyageError, match="dimension"):
            await client.embed(["doc"], input_type="document")


# --- pipeline step -----------------------------------------------------------------------------

@pytest.mark.anyio
async def test_embed_events_writes_vectors_once_and_keeps_updated_at(db_session):
    event_id, _ = await upsert_event(db_session, normalize(make_raw(title="Pottery Wheel Basics")))
    await db_session.commit()
    before = (await db_session.get(Event, event_id)).updated_at
    voyage, counts = StubVoyage(), Counter()

    await embed_events(db_session, voyage, counts, only_ids=[event_id])
    await embed_events(db_session, voyage, counts, only_ids=[event_id])

    assert counts["embedded"] == 1 and len(voyage.calls) == 1 and voyage.calls[0][1] == "document"
    row = (await db_session.execute(
        text("SELECT vector_dims(embedding) AS dims, embedding_hash, updated_at FROM events WHERE id = :id"),
        {"id": event_id},
    )).one()  # fmt: skip
    assert row.dims == EMBEDDING_DIM and row.embedding_hash
    assert row.updated_at == before  # derived columns don't count as a content change

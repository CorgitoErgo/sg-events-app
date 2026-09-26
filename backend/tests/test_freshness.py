from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from db.models import CrawlRun, Event, EventSource
from pipeline.freshness import expire_ended, recheck_missing
from pipeline.normalize import normalize
from pipeline.store import upsert_event
from tests.factories import make_raw

pytestmark = pytest.mark.anyio


async def add(session, **kwargs) -> int:
    event_id, _ = await upsert_event(session, normalize(make_raw(**kwargs)))
    return event_id


async def reload(session, event_id: int) -> Event:
    return await session.get(Event, event_id, populate_existing=True)


async def test_ended_events_expire(db_session):
    ended = await add(db_session, days_ahead=-3)
    upcoming = await add(db_session, days_ahead=3)
    assert await expire_ended(db_session) >= 1
    assert ((await reload(db_session, ended)).status, (await reload(db_session, ended)).status_reason) == ("expired", "ended")
    assert (await reload(db_session, upcoming)).status == "active"


class GoneAdapter:
    source_id = "fake"

    def __init__(self, gone_urls: set[str]) -> None:
        self.gone_urls = gone_urls
        self.checked: list[str] = []

    async def check_gone(self, client, url: str) -> bool:
        self.checked.append(url)
        return url in self.gone_urls


class NoCheckAdapter:
    source_id = "fake"


async def setup_history(session, *, runs: int = 3):
    now = datetime.now(UTC)
    for hours in range(1, runs + 1):
        session.add(CrawlRun(source_id="fake", status="ok", started_at=now - timedelta(hours=hours)))
    await session.flush()


async def age_source(session, event_id: int, source_id: str, days: int) -> None:
    await session.execute(
        update(EventSource)
        .where(EventSource.event_id == event_id, EventSource.source_id == source_id)
        .values(last_seen_at=datetime.now(UTC) - timedelta(days=days))
    )


async def test_events_missing_for_three_crawls_are_rechecked_and_cancelled_if_gone(db_session):
    missing = await add(db_session, source_id="fake", source_url="https://fake.example.sg/e/1")
    still_listed = await add(db_session, source_id="fake", source_url="https://fake.example.sg/e/2")
    await age_source(db_session, missing, "fake", days=2)
    await setup_history(db_session)

    adapter = GoneAdapter({"https://fake.example.sg/e/1"})
    counts = await recheck_missing(db_session, {"fake": adapter}, client=None)

    assert adapter.checked == ["https://fake.example.sg/e/1"]
    assert counts["cancelled"] == 1
    event = await reload(db_session, missing)
    assert (event.status, event.status_reason) == ("cancelled", "fake page gone (404/410)")
    assert (await reload(db_session, still_listed)).status == "active"


async def test_listed_elsewhere_is_not_missing(db_session):
    event_id = await add(db_session, source_id="fake", source_url="https://fake.example.sg/e/3", title="Shared Fair")
    await age_source(db_session, event_id, "fake", days=2)
    db_session.add(EventSource(event_id=event_id, source_id="other", source_url="https://other.example.sg/x"))
    await setup_history(db_session)
    adapter = GoneAdapter({"https://fake.example.sg/e/3"})
    await recheck_missing(db_session, {"fake": adapter}, client=None)
    assert adapter.checked == [] and (await reload(db_session, event_id)).status == "active"


async def test_needs_three_runs_of_history_and_an_opt_in(db_session):
    event_id = await add(db_session, source_id="fake", source_url="https://fake.example.sg/e/4")
    await age_source(db_session, event_id, "fake", days=2)
    await setup_history(db_session, runs=2)
    adapter = GoneAdapter({"https://fake.example.sg/e/4"})
    await recheck_missing(db_session, {"fake": adapter}, client=None)
    assert adapter.checked == []  # only 2 crawls: too early to call it missing

    await setup_history(db_session, runs=3)
    assert await recheck_missing(db_session, {"fake": NoCheckAdapter()}, client=None) == {}

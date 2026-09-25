"""/health with the DB session stubbed out: no database or network needed."""

import asyncio

import app.main as main
from db.session import get_session


class _StubSession:
    def __init__(self, error: Exception | None = None, delay_s: float = 0.0) -> None:
        self.error = error
        self.delay_s = delay_s

    async def execute(self, *_args, **_kwargs) -> None:
        await asyncio.sleep(self.delay_s)
        if self.error:
            raise self.error


def _use(session: _StubSession) -> None:
    async def override():
        yield session

    main.app.dependency_overrides[get_session] = override


def test_health_ok(client):
    _use(_StubSession())
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_db_error_returns_503(client):
    _use(_StubSession(error=OSError("connection refused")))
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "error", "database": "unreachable"}


def test_health_db_timeout_returns_503(client, monkeypatch):
    monkeypatch.setattr(main, "HEALTH_DB_TIMEOUT_S", 0.05)
    _use(_StubSession(delay_s=1.0))
    resp = client.get("/health")
    assert resp.status_code == 503

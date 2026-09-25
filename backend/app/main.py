import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.categories import CATEGORIES
from db.session import engine, get_session

logger = logging.getLogger(__name__)

HEALTH_DB_TIMEOUT_S = 2.0


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


app = FastAPI(title="SG Events API", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health(session: Annotated[AsyncSession, Depends(get_session)]) -> JSONResponse:
    """Liveness + database connectivity. 200 when the DB answers, 503 otherwise."""
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=HEALTH_DB_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - any DB failure means unhealthy
        logger.warning("Health check: database unreachable: %r", exc)
        return JSONResponse({"status": "error", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})


@app.get("/categories")
async def categories() -> list[dict[str, str]]:
    """The category taxonomy; the app renders its own labels and icons from the ids."""
    return [{"id": c.id, "label": c.label, "includes": c.includes} for c in CATEGORIES]

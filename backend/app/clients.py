"""External clients shared by request handlers, created once per process in the lifespan."""

from dataclasses import dataclass
from typing import Annotated

import anthropic
from fastapi import Depends, Request

from pipeline.embed import VoyageClient
from pipeline.geo.onemap import OneMapClient


@dataclass
class AppClients:
    llm: anthropic.AsyncAnthropic | None = None
    voyage: VoyageClient | None = None
    onemap: OneMapClient | None = None


def get_clients(request: Request) -> AppClients:
    return getattr(request.app.state, "clients", None) or AppClients()


ClientsDep = Annotated[AppClients, Depends(get_clients)]

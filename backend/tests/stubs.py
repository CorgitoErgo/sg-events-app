"""Deterministic stand-ins for Voyage, OneMap and Claude used across API tests."""

import hashlib
import math
import re
from types import SimpleNamespace

from db.models import EMBEDDING_DIM
from pipeline.geo.onemap import SearchResult


class StubVoyage:
    """Bag-of-words vectors over the first line (the title), so shared words mean similarity."""

    model = "stub-voyage"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str]] = []

    async def embed(self, texts, *, input_type, max_attempts=4, backoff_s=5.0):
        self.calls.append((list(texts), input_type))
        return [self.vector(t) for t in texts]

    @staticmethod
    def vector(text: str) -> list[float]:
        vec = [0.0] * EMBEDDING_DIM
        for word in re.findall(r"\w+", text.splitlines()[0].lower()):
            vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class StubOneMap:
    has_credentials = True

    def __init__(self, places: dict[str, tuple[float, float]] | None = None) -> None:
        self.places = {k.lower(): v for k, v in (places or {}).items()}
        self.searches: list[str] = []

    async def search(self, query: str) -> list[SearchResult]:
        self.searches.append(query)
        if (hit := self.places.get(query.lower())) is None:
            return []
        return [SearchResult(query.upper(), None, None, None, *hit)]

    async def planning_area(self, lat: float, lng: float) -> str | None:
        return None


class _Stream:
    def __init__(self, text: str, stop_reason: str) -> None:
        self._text, self._stop = text, stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    async def text_stream(self):
        for i in range(0, len(self._text), 12):
            yield self._text[i : i + 12]

    async def get_final_message(self):
        return SimpleNamespace(stop_reason=self._stop, content=[SimpleNamespace(type="text", text=self._text)])


class StubAnswerLLM:
    """messages.stream(...) for answers; messages.create(...) fails so parsing uses rules."""

    def __init__(self, text: str = "", *, stop_reason: str = "end_turn", error: Exception | None = None) -> None:
        self.text, self.stop_reason, self.error = text, stop_reason, error
        self.stream_calls: list[dict] = []
        self.messages = self

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        if self.error:
            raise self.error
        return _Stream(self.text, self.stop_reason)

    async def create(self, **kwargs):
        raise ValueError("parser disabled in this stub")

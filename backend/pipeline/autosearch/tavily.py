"""Tavily web search (docs.tavily.com/documentation/api-reference/endpoint/search, checked 2026-09-26).

POST https://api.tavily.com/search, Bearer tvly-... key; "basic" depth costs 1 credit.
The free plan has 1,000 credits a month.
"""

import asyncio
from dataclasses import dataclass

import httpx

SEARCH_URL = "https://api.tavily.com/search"
MAX_RETRY_AFTER_S = 30


class TavilyError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SearchHit:
    url: str
    title: str
    content: str
    score: float


class TavilyClient:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=30.0, transport=transport
        )

    async def __aenter__(self) -> "TavilyClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def search(
        self, query: str, *, max_results: int = 10, exclude_domains: list[str] | None = None
    ) -> list[SearchHit]:
        body = {
            "query": query,
            "search_depth": "basic",  # 1 credit
            "topic": "general",
            "country": "singapore",
            "max_results": max(1, min(max_results, 20)),
            "exclude_domains": (exclude_domains or [])[:150],
            "include_answer": False,
            "include_raw_content": False,
        }
        for attempt in range(2):
            resp = await self._http.post(SEARCH_URL, json=body)
            if resp.status_code == 429 and attempt == 0:
                wait = resp.headers.get("Retry-After", "5")
                await asyncio.sleep(min(float(wait) if wait.isdigit() else 5.0, MAX_RETRY_AFTER_S))
                continue
            break
        if resp.status_code == 401:
            raise TavilyError("Tavily rejected the API key (check TAVILY_API_KEY).")
        if resp.status_code in (402, 432, 433):
            raise TavilyError("Tavily credit or plan limit reached for this month.")
        if resp.status_code != 200:
            raise TavilyError(f"Tavily search -> HTTP {resp.status_code}: {resp.text[:200]}")
        return [
            SearchHit(url=r["url"], title=r.get("title") or "", content=r.get("content") or "", score=float(r.get("score") or 0))
            for r in resp.json().get("results", [])
            if r.get("url", "").startswith(("http://", "https://"))
        ]

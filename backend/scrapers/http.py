"""PoliteClient: the one HTTP client every adapter uses. Adapters never build their own.

It owns per-domain rate limiting (one request at a time, >= min_delay_s apart),
robots.txt (cached 24 h, Crawl-delay honoured), retries with backoff on 429/5xx
(honouring Retry-After), conditional GET against a raw-response cache in
data/raw/<source>/, and a hard stop on 401/403/CAPTCHA pages.
"""

import asyncio
import email.utils
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from scrapers.base import SourceBlocked

logger = logging.getLogger(__name__)

BOT_NAME = "SGEventsBot"
USER_AGENT = f"{BOT_NAME}/0.1 (+https://github.com/CorgitoErgo/sg-events-app)"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

ROBOTS_TTL_S = 24 * 3600
ROBOTS_UNREACHABLE_TTL_S = 10 * 60  # retry sooner when robots.txt itself failed
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER_S = 120.0  # a longer Retry-After means "not now": give up this run

# Challenge interstitials, not pages that merely embed a CAPTCHA widget in a form.
_CHALLENGE_TITLE = re.compile(
    rb"<title>[^<]*(captcha|just a moment|attention required|are you a robot)[^<]*</title>",
    re.IGNORECASE,
)
_CHALLENGE_MARKERS = (b"/cdn-cgi/challenge-platform/", b"cf-chl-")


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int  # 200 when served from cache after a 304
    content: bytes
    headers: httpx.Headers
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class PoliteClient:
    def __init__(
        self,
        *,
        user_agent: str = USER_AGENT,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        min_delay_s: float = 2.0,
        max_retries: int = 3,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self.cache_dir = cache_dir
        self.min_delay_s = min_delay_s
        self.max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, tuple[RobotFileParser, float]] = {}  # origin -> (parser, expiry)

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, *, source_id: str, min_delay_s: float | None = None) -> FetchResult:
        """GET politely. Raises SourceBlocked on robots disallow, 401/403 or a CAPTCHA page.

        Other non-2xx responses (404, 410, exhausted 429/5xx) are returned for the
        adapter to handle; freshness checks rely on seeing 404/410.
        """
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"

        robots = await self._robots_for(origin)
        if not robots.can_fetch(BOT_NAME, url):
            logger.error("SourceBlocked source=%s url=%s reason=robots.txt", source_id, url)
            raise SourceBlocked(url, "disallowed by robots.txt")
        delay = max(min_delay_s or self.min_delay_s, float(robots.crawl_delay(BOT_NAME) or 0))

        cached = self._cache_read(source_id, url)
        headers: dict[str, str] = {}
        if cached:
            meta, _ = cached
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]

        resp = await self._request(parts.netloc, url, headers, delay)

        if resp.status_code == 304 and cached:
            return FetchResult(url, 200, cached[1], resp.headers, from_cache=True)
        self._raise_if_blocked(source_id, url, resp)
        if resp.status_code == 200:
            self._cache_write(source_id, url, resp)
        return FetchResult(url, resp.status_code, resp.content, resp.headers)

    # --- throttling & retries ------------------------------------------------------------

    async def _request(
        self, domain: str, url: str, headers: dict[str, str], delay: float
    ) -> httpx.Response:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:  # concurrency 1 per domain
            for attempt in range(self.max_retries + 1):
                await self._wait_turn(domain, delay)
                try:
                    resp = await self._client.get(url, headers=headers)
                except httpx.TransportError as exc:
                    self._last_request[domain] = self._clock()
                    if attempt == self.max_retries:
                        raise
                    wait = self._backoff(attempt, None)
                    logger.warning("GET %s failed (%r); retrying in %.0fs", url, exc, wait)
                    await self._sleep(wait)
                    continue
                self._last_request[domain] = self._clock()

                if resp.status_code in RETRY_STATUSES and attempt < self.max_retries:
                    wait = self._backoff(attempt, resp.headers.get("Retry-After"))
                    if wait is None:
                        logger.warning("GET %s: Retry-After too long; giving up this run", url)
                        return resp
                    logger.warning("GET %s -> %s; retrying in %.0fs", url, resp.status_code, wait)
                    await self._sleep(wait)
                    continue
                return resp
        raise AssertionError("unreachable")

    async def _wait_turn(self, domain: str, delay: float) -> None:
        last = self._last_request.get(domain)
        if last is not None:
            wait = last + delay - self._clock()
            if wait > 0:
                await self._sleep(wait)

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None) -> float | None:
        wait = 2.0 * 2**attempt
        if retry_after:
            if retry_after.strip().isdigit():
                ra = float(retry_after)
            else:
                try:
                    when = email.utils.parsedate_to_datetime(retry_after)
                    ra = (when - datetime.now(UTC)).total_seconds()
                except (TypeError, ValueError):
                    ra = 0.0
            if ra > MAX_RETRY_AFTER_S:
                return None
            wait = max(wait, ra)
        return wait

    # --- robots.txt ------------------------------------------------------------------------

    async def _robots_for(self, origin: str) -> RobotFileParser:
        hit = self._robots.get(origin)
        if hit and hit[1] > self._clock():
            return hit[0]

        rp = RobotFileParser()
        ttl = ROBOTS_TTL_S
        robots_url = f"{origin}/robots.txt"
        try:
            resp = await self._request(urlsplit(origin).netloc, robots_url, {}, self.min_delay_s)
        except httpx.TransportError as exc:
            resp = None
            logger.warning("robots.txt unreachable at %s (%r): treating as disallow", origin, exc)

        # RFC 9309: 4xx = no restrictions; 5xx or unreachable = assume full disallow.
        if resp is not None and resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        elif resp is not None and 400 <= resp.status_code < 500:
            rp.parse([])
        else:
            rp.parse([])
            rp.disallow_all = True
            ttl = ROBOTS_UNREACHABLE_TTL_S
            if resp is not None:
                logger.warning("robots.txt at %s -> %s: treating as disallow", origin, resp.status_code)
        self._robots[origin] = (rp, self._clock() + ttl)
        return rp

    # --- blocking --------------------------------------------------------------------------

    @staticmethod
    def _raise_if_blocked(source_id: str, url: str, resp: httpx.Response) -> None:
        reason = None
        if resp.status_code in (401, 403):
            reason = f"HTTP {resp.status_code}"
        elif "html" in resp.headers.get("Content-Type", ""):
            head = resp.content[:20_000]
            if _CHALLENGE_TITLE.search(head) or any(m in head for m in _CHALLENGE_MARKERS):
                reason = "CAPTCHA/challenge page"
        if reason:
            logger.error("SourceBlocked source=%s url=%s reason=%s", source_id, url, reason)
            raise SourceBlocked(url, reason)

    # --- raw response cache ----------------------------------------------------------------

    def _cache_paths(self, source_id: str, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()
        base = self.cache_dir / source_id / key
        return base.with_suffix(".json"), base.with_suffix(".body")

    def _cache_read(self, source_id: str, url: str) -> tuple[dict, bytes] | None:
        meta_path, body_path = self._cache_paths(source_id, url)
        try:
            return json.loads(meta_path.read_text("utf-8")), body_path.read_bytes()
        except (OSError, ValueError):
            return None

    def _cache_write(self, source_id: str, url: str, resp: httpx.Response) -> None:
        meta_path, body_path = self._cache_paths(source_id, url)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(resp.content)
        meta = {
            "url": url,
            "fetched_at": datetime.now(UTC).isoformat(),
            "status": resp.status_code,
            "content_type": resp.headers.get("Content-Type"),
            "etag": resp.headers.get("ETag"),
            "last_modified": resp.headers.get("Last-Modified"),
        }
        meta_path.write_text(json.dumps(meta, indent=2), "utf-8")

"""OneMap (Singapore Land Authority) API client.

Verified 2026-09-26 by calling the API:
- Search `GET /api/common/elastic/search` still answers without a token but adds
  "error": "Authentication token missing...", so tokens will likely be enforced; we
  send one whenever credentials are configured.
- Planning area `GET /api/public/popapi/getPlanningarea` returns 401 without a token.
- Token: `POST /api/auth/post/getToken` {email, password} -> {access_token,
  expiry_timestamp}, valid ~3 days. The docs don't settle "Bearer <token>" vs the bare
  token in the Authorization header, so we try Bearer and fall back on a 401.
- Results have historically used both LONGITUDE and the misspelt LONGTITUDE.
"""

import asyncio
import base64
import json
import logging
import time
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from scrapers.http import USER_AGENT

logger = logging.getLogger(__name__)

BASE_URL = "https://www.onemap.gov.sg"
MIN_INTERVAL_S = 0.2  # 5 requests per second
TOKEN_REFRESH_MARGIN_S = 3600
MAX_ATTEMPTS = 3


class OneMapError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SearchResult:
    name: str  # SEARCHVAL
    building: str | None
    address: str | None
    postal: str | None
    lat: float
    lng: float


class OneMapClient:
    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        *,
        token: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._email = email
        self._password = password
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=20.0,
            transport=transport,
        )
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = asyncio.Lock()
        self._last_request: float | None = None
        self._token = token
        self._token_expiry = _jwt_expiry(token) if token else 0.0
        self._bearer = True  # flips to the bare token if OneMap rejects "Bearer"

    @property
    def has_credentials(self) -> bool:
        return bool((self._email and self._password) or self._token)

    @property
    def can_renew(self) -> bool:
        return bool(self._email and self._password)

    async def __aenter__(self) -> "OneMapClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def search(self, query: str) -> list[SearchResult]:
        """Geocode a postal code, address, building or place name (first page, best first)."""
        data = await self._get(
            "/api/common/elastic/search",
            {"searchVal": query, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1},
            auth=self.has_credentials,
        )
        return [r for r in map(_parse_result, data.get("results") or []) if r is not None]

    async def planning_area(self, lat: float, lng: float) -> str | None:
        """Planning area name (e.g. "SENGKANG") for a point; needs credentials."""
        if not self.has_credentials:
            return None
        data = await self._get(
            "/api/public/popapi/getPlanningarea", {"latitude": lat, "longitude": lng}, auth=True
        )
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            if isinstance(row, dict) and row.get("pln_area_n"):
                return str(row["pln_area_n"]).strip().upper()
        return None

    # --- plumbing --------------------------------------------------------------------------

    async def _get(self, path: str, params: dict[str, Any], *, auth: bool) -> Any:
        tried_bare = False
        for attempt in range(MAX_ATTEMPTS):
            headers = {}
            if auth:
                token = await self._get_token()
                headers["Authorization"] = f"Bearer {token}" if self._bearer else token
            resp = await self._request("GET", path, params=params, headers=headers)
            if resp.status_code == 401 and auth and self._bearer and not tried_bare:
                self._bearer, tried_bare = False, True
                logger.info("OneMap rejected 'Bearer <token>'; retrying with the bare token")
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS - 1:
                await self._sleep(2.0 * 2**attempt)
                continue
            if resp.status_code != 200:
                raise OneMapError(f"GET {path} -> HTTP {resp.status_code}")
            return resp.json()
        raise OneMapError(f"GET {path}: gave up after {MAX_ATTEMPTS} attempts")

    async def _get_token(self) -> str:
        margin = TOKEN_REFRESH_MARGIN_S if self.can_renew else 0
        if self._token and self._wall_clock() < self._token_expiry - margin:
            return self._token
        if not self.can_renew:
            expired = datetime.fromtimestamp(self._token_expiry, UTC).isoformat(timespec="minutes")
            raise OneMapError(
                f"ONEMAP_TOKEN expired at {expired}; paste a new one or set "
                "ONEMAP_EMAIL/ONEMAP_PASSWORD for automatic renewal"
            )
        resp = await self._request(
            "POST", "/api/auth/post/getToken", json={"email": self._email, "password": self._password}
        )
        if resp.status_code != 200:
            raise OneMapError(f"getToken -> HTTP {resp.status_code} (check ONEMAP_EMAIL/ONEMAP_PASSWORD)")
        body = resp.json()
        token = body.get("access_token")
        if not token:
            raise OneMapError("getToken response has no access_token")
        self._token = token
        try:
            self._token_expiry = float(body.get("expiry_timestamp"))
        except (TypeError, ValueError):
            self._token_expiry = self._wall_clock() + 3 * 24 * 3600
        return token

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async with self._lock:  # one request at a time, <= 5 per second
            if self._last_request is not None:
                wait = self._last_request + MIN_INTERVAL_S - self._clock()
                if wait > 0:
                    await self._sleep(wait)
            try:
                return await self._http.request(method, path, **kwargs)
            finally:
                self._last_request = self._clock()


def _jwt_expiry(token: str) -> float:
    """The `exp` claim of a JWT (unverified; only used to know when to stop using it)."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return float("inf")  # not a JWT we can read: use it until OneMap says otherwise


def _parse_result(row: dict[str, Any]) -> SearchResult | None:
    lng_raw = row.get("LONGITUDE", row.get("LONGTITUDE"))
    try:
        lat, lng = float(row["LATITUDE"]), float(lng_raw)
    except (KeyError, TypeError, ValueError):
        return None

    def clean(value: Any) -> str | None:
        text = str(value or "").strip()
        return None if not text or text.upper() == "NIL" else text

    return SearchResult(
        name=clean(row.get("SEARCHVAL")) or "",
        building=clean(row.get("BUILDING")),
        address=clean(row.get("ADDRESS")),
        postal=clean(row.get("POSTAL")),
        lat=lat,
        lng=lng,
    )

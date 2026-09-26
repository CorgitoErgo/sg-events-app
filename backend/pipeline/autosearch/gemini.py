"""Google Gemini reads plain-text event pages for the discovery agent (REST, checked 2026-09-26).

POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent with the
x-goog-api-key header. generationConfig.responseJsonSchema makes the reply JSON that matches
EVENTS_SCHEMA, so the output goes through the same validation as Claude's.
Free-tier requests may be used by Google to improve its products; the text sent here is
public web pages only.
"""

import asyncio
import json
import re
from datetime import datetime

import httpx

from pipeline.autosearch.extract import EVENTS_SCHEMA, MIN_TEXT_CHARS, system_prompt, to_raw_events, user_message
from scrapers.base import RawEvent

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
MAX_RETRY_S = 30


class GeminiError(Exception):
    pass


def _retry_delay(resp: httpx.Response) -> float:
    """Seconds from the RetryInfo detail Google sends with a 429 ("retryDelay": "17s")."""
    try:
        details = resp.json()["error"].get("details", [])
    except (ValueError, KeyError, AttributeError):
        details = []
    for d in details:
        if isinstance(d, dict) and (m := re.fullmatch(r"(\d+(?:\.\d+)?)s", str(d.get("retryDelay", "")))):
            return min(float(m.group(1)), MAX_RETRY_S)
    return 5.0


class GeminiClient:
    def __init__(self, api_key: str, *, model: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.model = model
        self._http = httpx.AsyncClient(headers={"x-goog-api-key": api_key}, timeout=60.0, transport=transport)

    async def __aenter__(self) -> "GeminiClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._http.aclose()

    async def extract_events(self, text: str, url: str, *, now: datetime, source_id: str) -> list[RawEvent]:
        if len(text) < MIN_TEXT_CHARS:
            return []
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt(now)}]},
            "contents": [{"role": "user", "parts": [{"text": user_message(text, url)}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
                "responseJsonSchema": EVENTS_SCHEMA,
            },
        }
        endpoint = f"{API_ROOT}/{self.model}:generateContent"
        for attempt in range(2):
            resp = await self._http.post(endpoint, json=body)
            if resp.status_code == 429 and attempt == 0:
                await asyncio.sleep(_retry_delay(resp))
                continue
            break
        if resp.status_code == 429:
            raise GeminiError("Gemini rate or quota limit reached (free tier: requests per minute/day).")
        if resp.status_code in (400, 401, 403) and "API_KEY" in resp.text:
            raise GeminiError("Gemini rejected the API key (check GEMINI_API_KEY).")
        if resp.status_code != 200:
            raise GeminiError(f"Gemini -> HTTP {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        candidates = data.get("candidates") or []
        if not candidates:  # prompt blocked (promptFeedback.blockReason): treat as no events
            return []
        finish = candidates[0].get("finishReason")
        reply = "".join(p.get("text", "") for p in (candidates[0].get("content") or {}).get("parts", []))
        if finish == "MAX_TOKENS":
            raise GeminiError("Gemini reply was cut off (too many events on one page)")
        if finish not in (None, "STOP") or not reply:
            return []  # SAFETY, RECITATION, ...: nothing usable
        try:
            parsed = json.loads(reply)
        except json.JSONDecodeError as exc:
            raise GeminiError(f"Gemini returned invalid JSON ({exc.msg})") from exc
        return to_raw_events(parsed, url, model=self.model, now=now, source_id=source_id)

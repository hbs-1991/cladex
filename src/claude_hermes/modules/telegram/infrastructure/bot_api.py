"""The raw Bot API client.

Moved from `main.py`'s `bot_call` closure in Wave 2. The token is a constructor
argument, never a module global and never read from the environment here: `run()`
reads it once and drops it from `os.environ` so nothing spawned afterwards can
inherit it.
"""
from __future__ import annotations


class BotApiClient:
    """Raw Bot API 10.x calls over httpx.

    aiogram does not type Rich Messages, and its make_request expects a
    TelegramMethod — so call the HTTP API directly.
    """

    def __init__(self, token: str, http, base: str = "https://api.telegram.org"):
        self._api = f"{base}/bot{token}"
        self._http = http

    async def call(self, method: str, payload: dict) -> dict:
        r = await self._http.post(f"{self._api}/{method}", json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"{method} failed: {data}")
        return data.get("result") or {}

    # `RichSender`, `TopicManager` and `TelegramQuestions` take the bare callable
    # (`BotApiCall`), so the client is usable as one without an extra lambda.
    async def __call__(self, method: str, payload: dict) -> dict:
        return await self.call(method, payload)

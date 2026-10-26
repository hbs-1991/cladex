"""The live progress UI for one turn.

Moved verbatim from `gateway/presenter.py` in Wave 2.
"""
from __future__ import annotations

import asyncio
import contextlib

import structlog

from ..application.ports import SenderPort
from .formatting import status_for

log = structlog.get_logger(__name__)


class Presenter:
    """The live progress UI for one turn.

    The draft is a compact status indicator — a single line — pushed only when the
    status changes. The full reply is sent once, as the final message, so the user
    never watches a wall of text stream in. Everything here is best-effort: the
    typing loop and the drafts must never abort the turn.
    """

    def __init__(self, sender: SenderPort, chat_id: int, thread_id: int | None,
                 chat_action, typing_interval: float = 4.0,
                 channel: str = "telegram"):
        self._sender = sender
        self._chat = chat_id
        self._thread = thread_id
        self._chat_action = chat_action
        self._interval = typing_interval
        self._channel = channel
        self._last: str | None = None
        self._task: asyncio.Task | None = None
        self._turn: str | None = None   # turn_id of the turn we present, else None

    async def on_event(self, tool_name: str, tool_input: dict | None = None) -> None:
        status = status_for(tool_name, tool_input)
        if status == self._last:
            return
        self._last = status
        await self._sender.send_draft(self._chat, self._thread, status)

    async def on_turn_event(self, ev) -> None:
        """TurnBus listener. Only turns started from our own channel drive the
        status line: a web turn on this lane must not post drafts into Telegram."""
        if ev.type == "turn.start":
            self._turn = ev.turn_id if ev.payload.get("channel") == self._channel else None
            return
        if self._turn is None or ev.turn_id != self._turn:
            return
        if ev.type == "tool.call":
            await self.on_event(ev.payload.get("name", ""), ev.payload.get("input"))
        elif ev.type == "text.delta":
            await self.on_event("")
        elif ev.type == "turn.end":
            self._turn = None

    async def finalize(self, text: str) -> None:
        await self._sender.send(self._chat, self._thread, text)

    async def _typing(self) -> None:
        # A Telegram chat action lasts ~5s, so refresh it a little sooner.
        while True:
            try:
                await self._chat_action()
            except Exception:
                log.debug("chat action failed", exc_info=True)
            await asyncio.sleep(self._interval)

    async def __aenter__(self) -> "Presenter":
        self._task = asyncio.create_task(self._typing())
        return self

    async def __aexit__(self, *exc) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

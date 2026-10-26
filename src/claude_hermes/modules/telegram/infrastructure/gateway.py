"""The transport seam: how updates reach the dispatcher.

New in Wave 2. `main.py` used to call `dp.start_polling(bot)` directly, which made
polling the only possible transport. Both ways in now sit behind one object, so a
webhook (`presentation/webhook.py`) is a composition choice rather than a rewrite.

The dispatcher's handlers hold no correctness-relevant state — the bus
subscription, the AskBroker and the titler tasks all live in objects passed in —
so a process that is fed updates by HTTP behaves exactly like one that polls.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class TelegramGateway:
    """One aiogram Dispatcher plus the Bot it dispatches for."""

    def __init__(self, bot, dispatcher, *, username: str = ""):
        self._bot = bot
        self._dp = dispatcher
        self.username = username

    @property
    def bot(self):
        return self._bot

    @property
    def dispatcher(self):
        return self._dp

    async def run_polling(self) -> None:
        log.info("claude-hermes polling as @%s", self.username)
        await self._dp.start_polling(self._bot)

    async def handle_update(self, payload: dict) -> None:
        """Feed one raw Update object (a webhook body) to the same handlers.

        aiogram validates the payload itself; a malformed body raises, and the
        webhook route turns that into a 400 rather than a 500.
        """
        from aiogram.types import Update
        await self._dp.feed_update(self._bot, Update.model_validate(
            payload, context={"bot": self._bot}))

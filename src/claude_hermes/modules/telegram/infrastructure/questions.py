"""The inline keyboard behind `ask_user`.

Moved from `main.py`'s `ask_user` closure in Wave 2 — the *rendering* half only.
Opening the question, waiting for it and reporting the outcome are broker
orchestration and belong to `turns`; this class only knows how a question looks
in a Telegram chat and how to take the buttons away afterwards. It satisfies
turns' `AskPresenterPort`.
"""
from __future__ import annotations

import structlog

from ..application.ports import BotApiCall
from ..domain.constants import CALLBACK_PREFIX, CANCEL_TOKEN

log = structlog.get_logger(__name__)

_HINT = "Tap a button — a typed reply won't reach me until this finishes."


class TelegramQuestions:
    def __init__(self, call: BotApiCall):
        self._call = call

    async def present(self, chat_id: int, thread_id: int | None, qid: str,
                      question: str, options: list[str]) -> dict | None:
        """Show the question with one button per option plus Cancel. Returns the
        sent message (so `dismiss` can clear its keyboard), or None for a lane
        that has no Telegram chat."""
        if not chat_id:
            return None
        keyboard = [[{"text": label, "callback_data": f"{CALLBACK_PREFIX}:{qid}:{i}"}]
                    for i, label in enumerate(options)]
        keyboard.append([{"text": "✖ Cancel",
                          "callback_data": f"{CALLBACK_PREFIX}:{qid}:{CANCEL_TOKEN}"}])
        payload = {
            "chat_id": chat_id,
            "text": f"{question}\n\n{_HINT}",
            "reply_markup": {"inline_keyboard": keyboard},
        }
        if thread_id:                   # same rule as RichSender._payload
            payload["message_thread_id"] = thread_id
        return await self._call("sendMessage", payload)

    async def dismiss(self, chat_id: int, sent: dict | None) -> None:
        """Take the buttons away once the answer is in hand."""
        if not sent:
            return
        try:
            await self._call("editMessageReplyMarkup", {
                "chat_id": chat_id,
                "message_id": sent.get("message_id"),
                "reply_markup": {"inline_keyboard": []},
            })
        except Exception:
            # Best-effort: the answer is already in hand, and failing to clear
            # a keyboard must never turn a delivered answer into an error.
            log.warning("could not clear the question keyboard", exc_info=True)

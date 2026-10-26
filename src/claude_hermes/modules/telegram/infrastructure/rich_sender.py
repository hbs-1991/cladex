"""The only caller of Bot API 10.x message methods.

Moved verbatim from `gateway/rich.py` in Wave 2; `render_html` and
`chunk_text` now come from the domain, which is what broke the old
`gateway/rich.py -> gateway/telegram.py` import.
"""
from __future__ import annotations

import structlog

from ..application.ports import BotApiCall
from ..domain.formatting import chunk_text, render_html

log = structlog.get_logger(__name__)


class RichSender:
    """The only caller of Bot API 10.x message methods.

    Sending a rich message uses rich_message = {"markdown": ...} | {"html": ...}
    (an object, not a block list). The model's markdown is passed through
    unescaped — that is the point of the feature. If Telegram rejects it, we
    retry as escaped HTML, and if that fails too we fall back to the plain
    sendMessage + chunk_text path the bot used before rich messages existed.
    """

    def __init__(self, call: BotApiCall, enabled: bool = True):
        self._call = call
        self._enabled = enabled

    def _payload(self, chat_id: int, thread_id: int | None, extra: dict) -> dict:
        payload = {"chat_id": chat_id, **extra}
        if thread_id:                       # 0/None → a chat without topics
            payload["message_thread_id"] = thread_id
        return payload

    async def send(self, chat_id: int, thread_id: int | None, text: str) -> None:
        if self._enabled:
            for body in ({"markdown": text}, {"html": render_html(text)}):
                try:
                    await self._call(
                        "sendRichMessage",
                        self._payload(chat_id, thread_id, {"rich_message": body}),
                    )
                    return
                except Exception:
                    log.warning("sendRichMessage(%s) failed", next(iter(body)),
                                exc_info=True)
        await self._send_plain(chat_id, thread_id, text)

    async def _send_plain(self, chat_id: int, thread_id: int | None, text: str) -> None:
        # The last rung of the chain: raising here would let a half-delivered reply be
        # followed by an error message, while the turn is already recorded as answered.
        for chunk in chunk_text(text):
            try:
                await self._call(
                    "sendMessage", self._payload(chat_id, thread_id, {"text": chunk})
                )
            except Exception:
                log.warning("sendMessage failed; dropped %d chars for chat=%s",
                            len(chunk), chat_id, exc_info=True)

    async def send_draft(self, chat_id: int, thread_id: int | None, status: str,
                         draft_id: int = 1) -> None:
        """Ephemeral ~30s streaming preview. Best-effort: a failed draft must never
        abort the turn — only the final send matters."""
        if not self._enabled:
            return
        try:
            await self._call("sendRichMessageDraft", self._payload(
                chat_id, thread_id,
                {"draft_id": draft_id, "rich_message": {"markdown": status}},
            ))
        except Exception:
            log.debug("sendRichMessageDraft failed", exc_info=True)

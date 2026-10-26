"""The Bot API half of `/model` — the keyboard, and the message it lives on.

Same shape as `questions.py`: `sendMessage` with an `inline_keyboard`, and an edit
afterwards. What is different is why this file exists at all — it is the layer
allowed to read an aiogram object, and reading one correctly is the whole job:
`thread_lane` is the rule that a `message_thread_id` on a supergroup reply chain
is NOT a lane, and the application layer may not know it.

Every decision — who may change what, what a button means, whether the menu is
still live — is in `application/services/model_menu.py`, which this only feeds.
"""
from __future__ import annotations

import structlog

from ..application.ports import BotApiCall
from ..application.services.model_menu import ModelMenuService
from ..domain.constants import SCOPE_THREAD
from ..domain.messages import _MODEL_FAILED
from ..domain.models import MessageEvent, MessageView, ModelMenu
from .aiogram_mapping import thread_lane

log = structlog.get_logger(__name__)


def message_view(message) -> MessageView | None:
    """One callback's message, reduced to the facts the service decides on.

    `None` when Telegram returned no message at all. An `InaccessibleMessage` —
    what a message too old to be fetched comes back as — has `date == 0`, and the
    service reads that as expired, which is exactly what it is.
    """
    if message is None:
        return None
    chat = getattr(message, "chat", None)
    if chat is None:
        return None
    return MessageView(
        chat_type=getattr(chat, "type", "") or "",
        chat_id=getattr(chat, "id", 0) or 0,
        thread_id=thread_lane(message),
        message_id=getattr(message, "message_id", 0) or 0,
        date=_stamp(getattr(message, "date", None)),
    )


def _stamp(date) -> int:
    """aiogram hands a `datetime` for a real message and a bare `0` for an
    inaccessible one; Telegram's wire value is unix seconds either way."""
    if hasattr(date, "timestamp"):
        return int(date.timestamp())
    try:
        return int(date or 0)
    except (TypeError, ValueError):
        return 0


def _keyboard(menu: ModelMenu) -> list[list[dict]]:
    return [[{"text": button.text, "callback_data": button.data} for button in row]
            for row in menu.rows]


class TelegramModelMenu:
    """Sends the `/model` keyboard and keeps it honest after a tap.

    Satisfies `ModelMenuPort` for `MessageRouter`, and is what the dispatcher
    hands a `mdl:` callback to.
    """

    def __init__(self, call: BotApiCall, service: ModelMenuService):
        self._call = call
        self._service = service

    async def open(self, event: MessageEvent, scope: str = SCOPE_THREAD) -> str | None:
        """`/model`. Returns the text the router should reply with, or None once
        the keyboard is in the chat — the router's contract for "handled"."""
        result = await self._service.open(event, scope)
        if result.menu is None:
            return result.reply
        payload = {
            "chat_id": event.chat_id,
            "text": result.menu.text,
            "reply_markup": {"inline_keyboard": _keyboard(result.menu)},
        }
        if event.thread_id:             # same rule as RichSender._payload
            payload["message_thread_id"] = event.thread_id
        await self._call("sendMessage", payload)
        return None

    async def on_callback(self, cb) -> str:
        """One `mdl:` press -> the popup text. Never raises: the dispatcher's only
        job left is to answer the callback, and a button that is never answered
        spins in the client forever."""
        view = message_view(getattr(cb, "message", None))
        user = getattr(cb, "from_user", None)
        try:
            result = await self._service.on_tap(getattr(cb, "data", None),
                                                getattr(user, "id", 0) or 0, view)
        except Exception:
            # A port that fell over (the database, mostly). Say so instead of
            # letting the exception reach aiogram, which answers nothing.
            log.exception("model menu tap failed for chat %s",
                          view.chat_id if view else "?")
            return _MODEL_FAILED
        if result.menu is not None and view is not None:
            # editMessageText, not editMessageReplyMarkup: the header names the
            # pair the lane runs, so redrawing only the ✓ would leave a line that
            # now says something false right above the buttons that changed it.
            try:
                await self._call("editMessageText", {
                    "chat_id": view.chat_id,
                    "message_id": view.message_id,
                    "text": result.menu.text,
                    "reply_markup": {"inline_keyboard": _keyboard(result.menu)},
                })
            except Exception:
                # Best-effort: the pair is already written, and a keyboard that
                # failed to redraw must not turn a done switch into an error.
                log.warning("could not redraw the model menu", exc_info=True)
        return result.answer

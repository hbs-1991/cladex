"""Content approval cards: the media, the text with its buttons, and the edits after.

Satisfies the content module's `ApprovalCards` port (`send`, `edit`,
`ensure_topic`) at composition; nothing here imports `content`. The card is two
things in the chat because Telegram forces it: a media group cannot carry an
inline keyboard, so the photos or the video go first and the buttons ride on the
text message that follows — whose id is the card's id, the one a reply is
matched against.

Nothing raises across the port. The content tick runs this from a worker and
keeps an outbox row per card, so every outcome has to come back as data it can
store: the ids, `{"topic_gone": True}` (re-create the topic once and retry), or
`{"error": reason}` (count an attempt, show the reason on the item).
"""
from __future__ import annotations

import re
from pathlib import Path

import structlog

from ..application.ports import BotApiCall
from ..domain.cards import card_callback
from ..domain.constants import (
    CONTENT_APPROVE,
    CONTENT_REDO,
    MEDIA_GROUP_LIMIT,
    MESSAGE_TEXT_LIMIT,
)
from ..domain.formatting import card_text
from ..domain.messages import (
    _CONTENT_APPROVE_BUTTON,
    _CONTENT_CUT,
    _CONTENT_OPEN_BUTTON,
    _CONTENT_REDO_BUTTON,
)

log = structlog.get_logger(__name__)

__all__ = ["TelegramApprovalCards"]

# What Telegram answers when the topic a send names is gone (deleted by the
# owner) or closed. Matched on the description, lower-cased: aiogram raises one
# `TelegramBadRequest` for every 400 and the description is the only signal.
_TOPIC_GONE = ("message thread not found", "topic_deleted", "topic_closed")
# A reason is stored on the item and shown in the web UI; a token in it would be
# a credential on a page. aiogram's own messages carry none, but a transport
# error's text is not ours to vouch for.
_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
_REASON_LIMIT = 300


class _TopicGone(Exception):
    """Internal: the send named a thread Telegram no longer has."""


def _is_topic_gone(exc: Exception) -> bool:
    text = str(getattr(exc, "message", "") or exc).lower()
    return any(marker in text for marker in _TOPIC_GONE)


def _reason(exc: Exception) -> str:
    """One line for the item's `error`: Telegram's description when there is one."""
    message = getattr(exc, "message", None)
    text = str(message) if message else f"{type(exc).__name__}: {exc}"
    return _TOKEN.sub("bot<token>", text).strip()[:_REASON_LIMIT] or type(exc).__name__


def _keyboard(item_id: int, revision: str, calendar_url: str | None) -> list[list]:
    """[Одобрить] [Переделать] / [Открыть в календаре] — the last one only for an
    https URL: Telegram refuses a URL button it cannot open (a localhost or
    plain-http origin), and that refusal would take the whole card down with it.
    Same rule as `ensure_menu_button`."""
    from aiogram.types import InlineKeyboardButton

    rows = [[
        InlineKeyboardButton(text=_CONTENT_APPROVE_BUTTON,
                             callback_data=card_callback(CONTENT_APPROVE, item_id,
                                                         revision)),
        InlineKeyboardButton(text=_CONTENT_REDO_BUTTON,
                             callback_data=card_callback(CONTENT_REDO, item_id,
                                                         revision)),
    ]]
    if calendar_url and str(calendar_url).startswith("https://"):
        rows.append([InlineKeyboardButton(text=_CONTENT_OPEN_BUTTON,
                                          url=str(calendar_url))])
    return rows


class TelegramApprovalCards:
    """Sends one approval card, edits it afterwards, and opens the cards' topic.

    `bot` is aiogram's `Bot` and does every send: the media are multipart (the
    same reason `TelegramDocuments` holds it), and the text message goes through
    it too because aiogram raises Telegram's own description — which is how a
    missing topic is told apart from a too-large photo. The raw client's
    `raise_for_status` fires before the body is read, so through it a 400 would
    say only "400 Bad Request". `api` (the raw `BotApiCall`) does the edits,
    which are best-effort and need no answer; `topics` is the `TopicManager`,
    fail-safe already.
    """

    def __init__(self, bot, api: BotApiCall, topics):
        self._bot = bot
        self._api = api
        self._topics = topics

    async def send(self, card: dict) -> dict:
        """The media, then the text with the buttons. Never raises.

        A crash or a failure between the two leaves media without a card; the
        outbox retries the whole card, so the owner may see the media twice. The
        buttons are only ever on the complete card, which is what matters.
        """
        try:
            return await self._send(card)
        except _TopicGone:
            return {"topic_gone": True}
        except Exception as exc:
            log.warning("content card %s not sent", card.get("item_id"),
                        exc_info=True)
            return {"error": _reason(exc)}

    async def edit(self, chat_id: int, message_id: int, text: str) -> None:
        """Replace the card's text and drop its buttons. Best-effort: the item's
        state is already written, and a card that failed to redraw is cosmetic —
        its buttons are bound to a revision the content side no longer accepts.
        `editMessageText` without `reply_markup` removes the keyboard."""
        try:
            await self._api("editMessageText", {
                "chat_id": chat_id, "message_id": message_id, "text": text,
            })
        except Exception:
            log.warning("could not edit content card %s in chat %s", message_id,
                        chat_id, exc_info=True)

    async def ensure_topic(self, chat_id: int, name: str) -> int | None:
        """A new forum topic's thread id, or None when the chat has no topics
        (Threaded Mode off) — the card then goes flat into the chat."""
        return await self._topics.create(chat_id, name)

    # --- the send ------------------------------------------------------------

    async def _send(self, card: dict) -> dict:
        from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions

        chat_id = int(card["chat_id"])
        thread_id = card.get("thread_id") or None     # 0/None → a flat chat
        paths = [str(p) for p in card.get("media") or []]
        # Checked before anything is sent: a card with half its media is worse
        # than a card that waits for the next tick with a reason on the item.
        for path in paths:
            if not Path(path).is_file():
                return {"error": f"media file missing: {Path(path).name}"}

        await self._send_media(chat_id, thread_id, card.get("media_kind") or "none",
                               paths)
        item_id = int(card["item_id"])
        text = card_text(title=card.get("title") or "",
                         channel=card.get("channel") or "",
                         fmt=card.get("format") or "",
                         publish_at_local=card.get("publish_at_local") or "",
                         body=card.get("body") or "", cut_note=_CONTENT_CUT,
                         limit=MESSAGE_TEXT_LIMIT)
        message = await self._guard(self._bot.send_message(
            chat_id, text, parse_mode="HTML", message_thread_id=thread_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=_keyboard(
                item_id, str(card["revision"]), card.get("calendar_url"))),
            link_preview_options=LinkPreviewOptions(is_disabled=True)))
        return {"message_id": int(message.message_id), "thread_id": thread_id}

    async def _send_media(self, chat_id: int, thread_id: int | None, kind: str,
                          paths: list[str]) -> None:
        from aiogram.types import FSInputFile, InputMediaPhoto

        if kind == "images":
            for start in range(0, len(paths), MEDIA_GROUP_LIMIT):
                chunk = paths[start:start + MEDIA_GROUP_LIMIT]
                if len(chunk) == 1:
                    send = self._bot.send_photo(chat_id, FSInputFile(chunk[0]),
                                                message_thread_id=thread_id)
                else:
                    send = self._bot.send_media_group(
                        chat_id, [InputMediaPhoto(media=FSInputFile(p)) for p in chunk],
                        message_thread_id=thread_id)
                await self._as_media(chat_id, thread_id, chunk, send)
        elif kind == "video":
            for path in paths:
                await self._as_media(chat_id, thread_id, [path], self._bot.send_video(
                    chat_id, FSInputFile(path), message_thread_id=thread_id,
                    supports_streaming=True))
        else:
            # "none" with files anyway, or a kind this sender has never heard
            # of: a document is the one shape Telegram takes for anything.
            for path in paths:
                await self._document(chat_id, thread_id, path)

    async def _as_media(self, chat_id: int, thread_id: int | None,
                        paths: list[str], send) -> None:
        """Send as media; when Telegram refuses the file AS media (dimensions, an
        aspect ratio, over the 10 MB photo limit), send the same files as
        documents, which it takes up to 50 MB and never re-encodes. Anything else
        — a missing topic, a block, a rate limit — is not the file's fault and
        goes up unchanged."""
        from aiogram.exceptions import TelegramBadRequest, TelegramEntityTooLarge

        try:
            await self._guard(send)
        except (TelegramBadRequest, TelegramEntityTooLarge) as exc:
            log.warning("Telegram refused %d file(s) as media (%s); sending as "
                        "documents", len(paths), _reason(exc))
            for path in paths:
                await self._document(chat_id, thread_id, path)

    async def _document(self, chat_id: int, thread_id: int | None, path: str) -> None:
        from aiogram.types import FSInputFile

        await self._guard(self._bot.send_document(chat_id, FSInputFile(path),
                                                  message_thread_id=thread_id))

    async def _guard(self, awaitable):
        """Await one Bot API call, turning "that topic is gone" into `_TopicGone`
        so no fallback path mistakes it for a bad file."""
        from aiogram.exceptions import TelegramAPIError

        try:
            return await awaitable
        except TelegramAPIError as exc:
            if _is_topic_gone(exc):
                raise _TopicGone from exc
            raise

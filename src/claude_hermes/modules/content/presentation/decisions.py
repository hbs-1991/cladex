"""The owner's decisions on an approval card, for the telegram module.

`ContentCardDecisions` is telegram's `ContentDecisions` port: the gateway calls it
for a `cnt:` button and for a reply in the card chat, and answers the owner with
the string it returns. Presentation, like the agent tool: it turns service calls
into the sentences a person reads — in Russian, like every other bot text the
owner sees — and never lets a refusal escape as an exception, because a callback
must always be answered.

Who may decide is checked twice: telegram checks the deploy owner before it calls
(the `/approve` pattern), and every lookup here is scoped to `user_id` as the
item's owner, so anybody else's tap finds no item and gets the stale answer.
"""
from __future__ import annotations

from typing import Any

import structlog

from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.modules.content.domain import cards as cd

log = structlog.get_logger(__name__)

STALE_TEXT = "Карточка устарела — открой пост в календаре."
TOO_LATE_TEXT = "⏰ Время публикации прошло — открой пост в календаре."
REDRAFTING_REPLY = "🔁 Переделываю — новая версия придёт сюда же."
NO_COMMENT_TEXT = "Напиши текстом, что переделать."


def _refused(message: str) -> str:
    return f"Нельзя одобрить: {message}"


class ContentCardDecisions:
    """`approve_from_card`, `redraft_from_reply`, `is_card_topic` over
    `ContentService` (`approve_card`, `live_card`/`redraft_card`,
    `is_card_topic`). Built once in `wiring.build` as `bundle.decisions`."""

    def __init__(self, service: Any):
        self._service = service

    async def approve_from_card(self, item_id: int, revision: str, user_id: int) -> str:
        """The popup text for an approve tap. Only the revision on the button can
        be approved, and only once; everything else is the stale answer."""
        if int(user_id or 0) <= 0:
            return STALE_TEXT
        try:
            item = await self._service.approve_card(int(user_id), int(item_id),
                                                    str(revision or ""))
        except ApplicationException as e:
            if e.code == "too_late":
                return TOO_LATE_TEXT
            if e.code in ("empty_body", "media_required"):
                return _refused(e.message)
            return STALE_TEXT
        except Exception:                             # noqa: BLE001 - always answer
            log.warning("content card approve failed", item=item_id, exc_info=True)
            return STALE_TEXT
        return cd.approved_text(self._service.local_time(item["publish_at"]))

    async def redraft_from_reply(self, chat_id: int, reply_to_message_id: int,
                                 user_id: int, comment: str) -> str | None:
        """The answer to a reply to a live card, or None when the message replies to
        anything else — it is then an ordinary message and becomes a turn.

        A failed card LOOKUP raises, deliberately: `None` would tell the gateway
        "not a card" and a redraft comment would become a turn. The gateway's
        `intercept_card_reply` answers any raise with its failure text — the one
        place that knows it cannot tell which replies are cards during an outage.
        """
        if int(user_id or 0) <= 0 or not reply_to_message_id:
            return None
        item = await self._service.live_card(int(user_id), int(chat_id),
                                             int(reply_to_message_id))
        if item is None:
            return None
        try:
            updated = await self._service.redraft_card(
                int(user_id), int(chat_id), int(reply_to_message_id), comment)
        except ApplicationException as e:
            if e.code == "no_comment":
                return NO_COMMENT_TEXT
            if e.code == "redraft_limit":
                url = self._service.calendar_url(item["id"])
                return (f"Лимит переделок исчерпан — поправь пост в календаре"
                        f"{': ' + url if url else '.'}")
            if e.code == "too_late":
                return TOO_LATE_TEXT
            return STALE_TEXT
        except Exception:                             # noqa: BLE001 - never a turn
            log.warning("content redraft failed", item=item["id"], exc_info=True)
            return STALE_TEXT
        return REDRAFTING_REPLY if updated is not None else STALE_TEXT

    async def is_card_topic(self, chat_id: int, thread_id: int | None) -> bool:
        """Whether this thread is the card topic, which the titler never renames."""
        try:
            return await self._service.is_card_topic(int(chat_id), thread_id)
        except Exception:                             # noqa: BLE001 - title as usual
            log.warning("content card topic check failed", exc_info=True)
            return False

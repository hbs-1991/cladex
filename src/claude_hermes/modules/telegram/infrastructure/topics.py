"""Forum topics over the raw Bot API.

Moved verbatim from `gateway/topics.py` in Wave 2.
"""
from __future__ import annotations

import structlog

from ..application.ports import BotApiCall
from ..domain.constants import TOPIC_NAME_LIMIT

log = structlog.get_logger(__name__)

__all__ = ["TOPIC_NAME_LIMIT", "TopicManager"]


class TopicManager:
    """Forum topics, including topics in private chats (Bot API 10.0).

    Every operation is fail-safe: a chat that is not threaded, a missing right,
    or a rate limit degrades to `None` / no-op, never to a raised exception.
    A `None` thread id means "deliver flat", which is what the bot did before
    threads existed.
    """

    def __init__(self, call: BotApiCall):
        self._call = call

    async def create(self, chat_id: int, name: str) -> int | None:
        try:
            result = await self._call(
                "createForumTopic", {"chat_id": chat_id, "name": name[:TOPIC_NAME_LIMIT]}
            )
            return int(result["message_thread_id"])
        except Exception:
            log.warning("createForumTopic failed for chat=%s; delivering flat",
                        chat_id, exc_info=True)
            return None

    async def rename(self, chat_id: int, thread_id: int, name: str) -> None:
        try:
            await self._call("editForumTopic", {
                "chat_id": chat_id,
                "message_thread_id": thread_id,
                "name": name[:TOPIC_NAME_LIMIT],
            })
        except Exception:
            log.warning("editForumTopic failed for chat=%s thread=%s",
                        chat_id, thread_id, exc_info=True)

    async def close(self, chat_id: int, thread_id: int) -> None:
        try:
            await self._call("closeForumTopic", {
                "chat_id": chat_id, "message_thread_id": thread_id,
            })
        except Exception:
            log.warning("closeForumTopic failed for chat=%s thread=%s",
                        chat_id, thread_id, exc_info=True)

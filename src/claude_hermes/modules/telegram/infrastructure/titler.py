"""Telegram topic naming: the one aiogram-bound provider of `ThreadTitlerPort`.

The model call, the prompt and the "did it answer instead of naming?" check are
sessions' (`SdkTitleGenerator`); writing the title to the threads row and telling
an open web tab about it is sessions' too (`ThreadTitleService`). What lives here
is the Telegram-shaped part: `spawn`, the fire-and-forget task set, and the
`topics.rename` call. The three collaborators arrive through the ports declared
beside `ThreadTitlerPort` — this module imports nothing from `sessions`.
"""
from __future__ import annotations

import asyncio

import structlog

from ..application.ports import (
    LaneBindingPort,
    ThreadTitleWriterPort,
    TitleGeneratorPort,
)

log = structlog.get_logger(__name__)

__all__ = ["Titler"]


class Titler:
    """Names a freshly opened thread from its first message.

    A thread is 'new' exactly when it has no session row yet — so spawn() must be
    called BEFORE the turn runs, or the turn will have created the row and the
    thread will no longer look new.

    `generator` produces the title (a stateless model-name holder; the composition
    root builds one per titler), `sessions` answers `is_unbound` and `titles`
    applies the name — and carries the bus that tells an open web tab about it.
    """

    def __init__(self, topics, *, generator: TitleGeneratorPort,
                 sessions: LaneBindingPort, titles: ThreadTitleWriterPort):
        self._topics = topics
        self._tasks: set[asyncio.Task] = set()
        self._generator = generator
        self._sessions = sessions
        self._titles = titles

    async def is_new_thread(self, session_key: str, thread_id: int | None) -> bool:
        if not thread_id:
            return False
        return await self._sessions.is_unbound(session_key)

    def spawn(self, chat_id: int, thread_id: int, text: str,
              session_key: str | None = None) -> None:
        task = asyncio.create_task(self._run(chat_id, thread_id, text, session_key))
        self._tasks.add(task)                     # keep a ref: the loop only weakly
        task.add_done_callback(self._tasks.discard)  # references tasks

    async def _run(self, chat_id: int, thread_id: int, text: str,
                   session_key: str | None) -> None:
        try:
            title = await self._generator.generate(text)
        except Exception:
            log.warning("titling failed for chat=%s thread=%s", chat_id, thread_id,
                        exc_info=True)
            return
        if not title:
            return
        await self._topics.rename(chat_id, thread_id, title)
        # The same name for the web: the topic and the sidebar row are one thread.
        # The turn's turn.start has long since created the threads row (this call
        # waited on a model), and a title the person typed is never overwritten.
        if session_key:
            await self._titles.apply(session_key, title)

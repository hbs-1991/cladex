"""`ask_user`, orchestrated: open a question, show it where the lane lives, wait
for exactly one answer, then clear the keyboard.

Everything that can race lives in `infrastructure/ask_broker.AskBroker`; this
service is the sequencing around it — and the part `main.py` carried as a closure
until the composition root existed, which is why it had no tests.
"""
from __future__ import annotations

import structlog

from claude_hermes.modules.turns.application.ports import AskBrokerPort
from claude_hermes.modules.turns.domain.value_objects import CANCELLED

log = structlog.get_logger(__name__)


class AskPresenter:
    """What a channel must offer to show a question. Telegram sends an inline
    keyboard; a web-only lane presents nothing and relies on the `ask.open` event."""

    async def present(self, chat_id: int, thread_id: int | None, qid: str,
                      question: str, options: list[str]):  # pragma: no cover - contract
        raise NotImplementedError

    async def dismiss(self, handle) -> None:  # pragma: no cover - contract
        raise NotImplementedError


class AskService:
    def __init__(self, broker: AskBrokerPort, presenter: AskPresenter | None = None):
        self._broker = broker
        self._presenter = presenter

    async def ask(self, chat_id: int, thread_id: int | None, question: str,
                  options: list[str], user_id: int, timeout: float,
                  emit=None) -> str | None:
        """The chosen option, CANCELLED, or None on timeout. The signature is the
        `AskPresenterPort` callable the tool handler expects."""
        # The remote broker keeps the question alive a minute past the caller's
        # timeout: a tap that lands as `wait` gives up must still find a hash to
        # compare-and-delete, or the answer is written into a key nobody reads.
        # The in-memory broker accepts and ignores it.
        qid, fut = await self._broker.open(options, user_id,
                                           ttl_seconds=timeout + 60)
        if emit is not None:
            await emit("ask.open", {"qid": qid, "question": question,
                                    "options": list(options)})
        handle = None
        try:
            if chat_id and self._presenter is not None:      # a channel lane
                handle = await self._presenter.present(chat_id, thread_id, qid,
                                                       question, options)
            answer = await self._broker.wait(qid, fut, timeout)
        finally:
            # Synchronous, and covers both awaits above: `except Exception` alone
            # does not catch asyncio.CancelledError (a BaseException since 3.8),
            # so a teardown that cancels this task mid-present skipped discard()
            # and leaked the entry forever. discard() is a pop-with-default, so
            # this is a harmless no-op on the normal path, where wait() has
            # already popped the entry itself.
            #
            # `discard` stays SYNC on the port for exactly this line: an `await`
            # here is skipped by the CancelledError that lands on it, and the
            # Redis broker would leak a hash a later tap could "answer" into the
            # void. Its remote DEL is a fire-and-forget task instead.
            self._broker.discard(qid)

        if emit is not None:
            source = ("timeout" if answer is None
                      else "cancelled" if answer == CANCELLED else "user")
            await emit("ask.answer", {"qid": qid, "source": source,
                                      "answer": answer if source == "user" else None})
        if handle is not None:
            try:
                await self._presenter.dismiss(handle)
            except Exception:
                # Best-effort: the answer is already in hand, and failing to clear
                # a keyboard must never turn a delivered answer into an error.
                log.warning("could not clear the question keyboard", exc_info=True)
        return answer

    # The tool handler is handed a plain callable, so the service is one too.
    async def __call__(self, chat_id: int, thread_id: int | None, question: str,
                       options: list[str], user_id: int, timeout: float,
                       emit=None) -> str | None:
        return await self.ask(chat_id, thread_id, question, options, user_id,
                              timeout, emit=emit)

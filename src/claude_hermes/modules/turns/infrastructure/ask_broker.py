"""Pending inline-keyboard questions and the wait that resolves them.

In-memory: one process owns every open question, which is what inline mode wants
and what `RedisAskBroker` replaces in redis mode. The `wait()` body below is moved
BYTE-IDENTICALLY from `gateway/questions.py`; both invariants in its docstring
were paid for with a live bug.

Four methods are `async` since Wave 3b — `open`, `wait`, `options_for`,
`resolve` — not because anything here awaits, but because the Redis broker that
satisfies the same port does, and one port with two calling conventions is a
caller that only ever worked in one mode. **`discard` and `deliver` stay
synchronous**: `discard` runs in a `finally` where `CancelledError` can land, and
an `await` there would be skipped and leak the entry; `deliver` is called from the
control channel's handler, which has an answer in hand and nothing to wait for.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass

from claude_hermes.modules.turns.domain.value_objects import CANCELLED

_CANCEL_TOKEN = "c"


@dataclass
class _Pending:
    options: list[str]
    user_id: int
    future: asyncio.Future


class AskBroker:
    """Pending inline-keyboard questions, and the wait that resolves them.

    The wait lives here rather than in the caller on purpose: it is the only
    race-critical code in this feature, and main.py — the natural home for the
    Telegram side — has no unit tests. Keeping it here makes it testable with
    no Bot API mock at all.
    """

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}

    async def open(self, options: list[str], user_id: int, *,
                   ttl_seconds: float | None = None) -> tuple[str, asyncio.Future]:
        """`ttl_seconds` is accepted and ignored: nothing here outlives the
        process, so the `wait` timeout already is the question's lifetime. The
        keyword exists because the Redis broker needs it and the caller must not
        have to know which one it got."""
        qid = secrets.token_hex(4)
        # get_running_loop, not get_event_loop: this is only ever called from a
        # tool call, which always has a running loop, and get_event_loop warns
        # (and in 3.12+ errors) when there is none.
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[qid] = _Pending(list(options), user_id, fut)
        return qid, fut

    async def wait(self, qid: str, fut: asyncio.Future, timeout: float) -> str | None:
        """The chosen option, CANCELLED, or None on timeout.

        Two things here are load-bearing and neither is obvious:

        1. `asyncio.wait` — NOT `asyncio.wait_for`. wait_for CANCELS the future
           on timeout, and this broker still holds that future until the finally
           below runs a loop step later. A click landing in that window would pop
           a cancelled future and raise InvalidStateError inside the aiogram task,
           where it is swallowed: the user's button spins, their answer is
           dropped, and the model is told nobody replied.

        2. Pop FIRST, read `fut.done()` SECOND. The pop is the arbiter — whoever
           removes the entry wins — and the future only carries the value. With
           the pop after the read, a click on the deadline is told "accepted"
           while this function returns None, and the user and the model disagree
           about what happened.
        """
        try:
            await asyncio.wait({fut}, timeout=timeout)
        finally:
            # Synchronous on purpose: the SDK cancels in-flight tool tasks on
            # teardown, so CancelledError can arrive AT this finally, and any
            # await here would be skipped, leaking the entry.
            self._pending.pop(qid, None)
        return fut.result() if fut.done() else None

    async def options_for(self, qid: str) -> list[str] | None:
        """The option labels of an open question, or None once it is gone."""
        p = self._pending.get(qid)
        return list(p.options) if p is not None else None

    async def resolve(self, qid: str, token: str, user_id: int) -> bool:
        """Deliver a click. False means "this question is no longer yours to answer"."""
        entry = self._pending.get(qid)          # PEEK — do not pop yet
        if entry is None or entry.user_id != user_id:
            # Popping before this check would let a stranger's click destroy a
            # question its owner is still looking at.
            return False

        if token == _CANCEL_TOKEN:
            value = CANCELLED
        else:
            try:
                index = int(token)
            except ValueError:
                return False
            if not 0 <= index < len(entry.options):
                return False
            value = entry.options[index]

        if self._pending.pop(qid, None) is None:   # someone won the race first
            return False
        if entry.future.done():                    # belt and braces
            return False
        entry.future.set_result(value)
        return True

    def deliver(self, qid: str, answer: str | None) -> bool:
        """Set a question's answer without re-checking who sent it.

        The control channel's entry point: the checks that matter — the owner, the
        token, the peek before the pop — were made by whichever process ran
        `resolve`, and this is the delivery of that decision to the process that
        is actually waiting. A qid that is not pending here is the normal case (the
        nudge is broadcast), so this reports rather than raises.

        Synchronous, like `discard`: it is called from the bus's reader task,
        which has the answer in hand and nothing to wait for.
        """
        entry = self._pending.pop(qid, None)
        if entry is None or entry.future.done():
            return False
        entry.future.set_result(answer)
        return True

    def discard(self, qid: str) -> None:
        """Drop a question that was never asked — e.g. sendMessage failed.

        Synchronous on purpose: `AskService.ask` calls it from a `finally` that a
        CancelledError can land on, and an `await` there is skipped.
        """
        self._pending.pop(qid, None)

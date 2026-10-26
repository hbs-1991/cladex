"""`ask_user` across processes: the question is a Redis key, the answer is a key,
and the nudge is only the latency.

In redis mode the worker that opened the question is parked inside a tool call in
one container while the tap arrives at an API instance in another. Nothing in
memory can bridge that, so the same three moves as everywhere else in Wave 3b:

* **The durable write is the arbiter.** `resolve` deletes the question hash and
  writes the answer key in ONE step; whoever wins that step owns the answer.
  `wait`'s deadline is the same step from the other side — its `DEL` returning 1
  means nobody answered, and returning 0 means a resolver got there first and its
  answer is already in the answer key.
* **Refusals never touch the hash.** A stranger's click, a bad token, an unknown
  qid: all read and return. Popping before the owner check would let a stranger's
  tap destroy a question its owner is still looking at.
* **The nudge is optional.** `hermes:ctl` carries `{"op": "ask", …}` so the parked
  worker wakes immediately; if that message is lost, `wait` polls the answer key
  every few seconds and the user waits a moment longer. The only reader of that
  payload is `application/control.py::TurnControl.handle`, which hands it to
  `deliver`.

**`discard` and `deliver` are synchronous**, and that is load-bearing.
`AskService.ask` calls `discard` from a `finally` where `CancelledError` can land:
an `await` there is skipped, and the hash would be left for a later tap to
"answer" into a void. So `discard` pops the local entry synchronously and leaves
the `DEL` to a fire-and-forget task held in a strong-reference set.

`CANCELLED` (`"\\x00cancelled"`) is JSON- and Redis-safe and travels as it is.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

import structlog
from redis.exceptions import ResponseError, WatchError

from claude_hermes.core.eventbus import CONTROL_CHANNEL, EventBus
from claude_hermes.modules.turns.domain.value_objects import CANCELLED

log = structlog.get_logger(__name__)

__all__ = ["RedisAskBroker", "ASK_PREFIX"]

# The key namespace. A wire value like a channel name: the worker that opens the
# question and the instance that answers it can come from two different deploys.
ASK_PREFIX = "hermes:ask:"

_CANCEL_TOKEN = "c"

# Compare and act in one server-side step: delete the question and publish the
# answer key only while the hash still names this user. The `user_id` re-check is
# not belt and braces — it is what makes this a compare-and-delete rather than a
# delete, so two taps racing produce exactly one answer.
_CLAIM_LUA = """
if redis.call('hget', KEYS[1], 'user_id') ~= ARGV[1] then
    return 0
end
redis.call('del', KEYS[1])
redis.call('set', KEYS[2], ARGV[2], 'EX', ARGV[3])
return 1
"""


class RedisAskBroker:
    """`AskBrokerPort` over Redis. One instance per process; the futures it hands
    out are local, everything that decides is not."""

    #: How long `wait` sleeps between answer-key reads. Short enough that a lost
    #: nudge is a pause rather than a timeout, long enough to be free.
    POLL_SECONDS = 5.0

    def __init__(self, client: Any, control: EventBus, *,
                 answer_ttl_seconds: float = 120.0) -> None:
        self._client = client
        self._control = control
        self._answer_ttl = max(int(answer_ttl_seconds), 1)
        self._pending: dict[str, asyncio.Future] = {}
        # Strong references to the fire-and-forget DELs `discard` schedules: the
        # loop keeps only a weak one, and a task nobody holds can be collected
        # mid-await and simply never run.
        self._cleanups: set[asyncio.Task] = set()

    # --- keys ---------------------------------------------------------------

    def _key(self, qid: str) -> str:
        return ASK_PREFIX + qid

    def _answer_key(self, qid: str) -> str:
        return f"{ASK_PREFIX}{qid}:answer"

    # --- the AskBrokerPort surface -----------------------------------------

    async def open(self, options: list[str], user_id: int, *,
                   ttl_seconds: float | None = None) -> tuple[str, asyncio.Future]:
        """Register the question where any process can find it, and hand back the
        local future `wait` parks on.

        `ttl_seconds` is the caller's timeout plus a margin (`AskService` passes
        timeout + 60): a tap that lands exactly as `wait` gives up must still find
        a hash to compare-and-delete, or the answer is written into a key nobody
        will ever read.
        """
        qid = secrets.token_hex(4)
        # get_running_loop, not get_event_loop: this is only ever called from a
        # tool call, which always has a running loop.
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        ttl = max(int(ttl_seconds or self._answer_ttl), 1)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.hset(self._key(qid), mapping={
                "options": json.dumps(list(options), ensure_ascii=False),
                "user_id": str(int(user_id)),
            })
            pipe.expire(self._key(qid), ttl)
            await pipe.execute()
        # Registered only once the question exists remotely. `AskService.ask`
        # awaits `open` OUTSIDE its try/finally, so a pipeline that raises never
        # reaches `discard` — an entry recorded before this line would leak for
        # the life of the process.
        self._pending[qid] = fut
        return qid, fut

    async def wait(self, qid: str, fut: asyncio.Future, timeout: float) -> str | None:
        """The chosen option, CANCELLED, or None on timeout.

        Three things are load-bearing here and none is obvious:

        1. `asyncio.wait`, never `wait_for`: `wait_for` CANCELS the future on
           timeout, and a tap landing in that window would set a cancelled future
           and raise `InvalidStateError` inside the resolver's task, where it is
           swallowed — the user's button spins and the model is told nobody
           replied.
        2. The answer key is read on every wake, because a pub/sub nudge can be
           lost and a question answered into a key nobody reads is the same bug
           with a longer fuse.
        3. The `DEL` at the deadline is the arbiter, not the clock: `1` means the
           question was still open and nobody answered it, `0` means a resolver
           won the race and the answer is one `GET` away.

        The local entry is popped in a **synchronous** `finally` — a cancel
        landing there must not leak it.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                await asyncio.wait({fut}, timeout=min(remaining, self.POLL_SECONDS))
                if fut.done():
                    return fut.result()
                answer = await self._client.get(self._answer_key(qid))
                if answer is not None:
                    return _text(answer)
            if await self._client.delete(self._key(qid)):
                return None                       # the question was still open
            if fut.done():
                return fut.result()
            answer = await self._client.get(self._answer_key(qid))
            return _text(answer) if answer is not None else None
        finally:
            self._pending.pop(qid, None)

    async def options_for(self, qid: str) -> list[str] | None:
        """The option labels of an open question, or None once it is gone."""
        raw = await self._client.hget(self._key(qid), "options")
        if raw is None:
            return None
        try:
            data = json.loads(_text(raw))
        except ValueError:
            log.warning("dropping a malformed ask hash", qid=qid)
            return None
        return [str(o) for o in data] if isinstance(data, list) else None

    async def resolve(self, qid: str, token: str, user_id: int) -> bool:
        """Deliver a click, from whichever process received it.

        False means "this question is no longer yours to answer" — and on every
        false path the hash is untouched.
        """
        data = await self._client.hgetall(self._key(qid))
        if not data:
            return False
        stored = {_text(k): _text(v) for k, v in data.items()}
        if stored.get("user_id") != str(int(user_id)):
            return False
        try:
            options = json.loads(stored.get("options") or "[]")
        except ValueError:
            return False
        value = _value(token, options if isinstance(options, list) else [])
        if value is None:
            return False
        if not await self._claim(qid, str(int(user_id)), value):
            return False                          # somebody won the race first
        try:
            await self._control.publish(CONTROL_CHANNEL,
                                        {"op": "ask", "qid": qid, "answer": value})
        except Exception:
            # The answer key is already written; the parked `wait` finds it on its
            # next poll. A lost nudge is seconds, not an answer.
            log.warning("ask_nudge_failed", qid=qid, exc_info=True)
        return True

    def deliver(self, qid: str, answer: str | None) -> bool:
        """Set a local future from the control channel. A qid that is not pending
        here is the normal case — the nudge is broadcast and exactly one process
        holds the question — so this reports rather than raises.

        Synchronous: it runs on the bus's reader task with the answer in hand.
        """
        fut = self._pending.pop(qid, None)
        if fut is None or fut.done():
            return False
        fut.set_result(answer)
        return True

    def discard(self, qid: str) -> None:
        """Drop a question that was never asked, or has already been answered.

        Synchronous on purpose (see the module docstring): the remote `DEL` is
        scheduled, never awaited.
        """
        self._pending.pop(qid, None)
        self._spawn(self._client.delete(self._key(qid), self._answer_key(qid)))

    # --- internals ----------------------------------------------------------

    async def _claim(self, qid: str, user_id: str, value: str) -> bool:
        keys = (self._key(qid), self._answer_key(qid))
        try:
            return bool(int(await self._client.eval(
                _CLAIM_LUA, 2, *keys, user_id, value, str(self._answer_ttl))))
        except ResponseError as e:
            # fakeredis only implements EVAL with `lupa` installed, and lupa is
            # not in the dev extra. The WATCH/MULTI fallback below is the same
            # compare-and-act in one transaction, so the unit tests exercise the
            # real semantics and the container takes the Lua path.
            if "unknown command" not in str(e).lower():
                raise
            return await self._claim_fallback(qid, user_id, value)

    async def _claim_fallback(self, qid: str, user_id: str, value: str) -> bool:
        key, answer_key = self._key(qid), self._answer_key(qid)
        async with self._client.pipeline(transaction=True) as pipe:
            await pipe.watch(key)
            current = _text(await pipe.hget(key, "user_id"))
            if current != user_id:
                await pipe.reset()
                return False
            pipe.multi()
            pipe.delete(key)
            pipe.set(answer_key, value, ex=self._answer_ttl)
            try:
                result = await pipe.execute()
            except WatchError:
                # The hash changed between the WATCH and the EXEC: another tap
                # claimed it. Same answer the Lua gives.
                return False
        return bool(result)

    def _spawn(self, coro) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:                      # no loop: nothing to clean up on
            coro.close()
            return
        self._cleanups.add(task)
        task.add_done_callback(self._cleanups.discard)


def _value(token: str, options: list) -> str | None:
    """The answer a token names, or None when it names nothing. Pure: the caller
    is what decides whether it may be written."""
    if token == _CANCEL_TOKEN:
        return CANCELLED
    try:
        index = int(token)
    except ValueError:
        return None
    if not 0 <= index < len(options):
        return None
    return str(options[index])


def _text(value: Any) -> str:
    """Values arrive as `str` with `decode_responses=True` (what
    `core.redis.create_client` builds) and as `bytes` without it."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)

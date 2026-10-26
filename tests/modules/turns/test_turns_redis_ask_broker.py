"""`ask_user` when the worker asking and the instance answering are not the same
process.

Two broker instances on one `fakeredis` server are what "two processes" means for
a unit test: one opens the question and parks in `wait`, the other resolves it.
The control channel between them is an `InProcessEventBus` — `RedisEventBus` has
its own tests (`tests/test_core_eventbus.py`), and what matters here is that
`resolve` publishes and `deliver` wakes the parked future.

Every case below is a claim about the durable half, because that is what makes a
lost nudge harmless: the hash is the question, the answer key is the answer, and
every refusal leaves the hash exactly as it found it.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from claude_hermes.core.eventbus import CONTROL_CHANNEL, InProcessEventBus
from claude_hermes.modules.turns.application.control import CancelRegistry, TurnControl
from claude_hermes.modules.turns.domain.value_objects import CANCELLED
from claude_hermes.modules.turns.infrastructure.redis_ask_broker import (
    ASK_PREFIX,
    RedisAskBroker,
)

fakeredis = pytest.importorskip("fakeredis")

USER = 7


async def gone(client, key: str, timeout: float = 3.0) -> bool:
    """Poll a key away. `discard` schedules its DEL rather than awaiting it, so a
    test cannot assert straight after the call."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not await client.exists(key):
            return True
        await asyncio.sleep(0.005)
    return not await client.exists(key)


def two_clients():
    """Two clients on one server: as close to two processes as a unit test gets."""
    server = fakeredis.FakeServer()
    return (fakeredis.FakeAsyncRedis(server=server, decode_responses=True),
            fakeredis.FakeAsyncRedis(server=server, decode_responses=True))


def one_client():
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def broker(client, control=None, **kw):
    b = RedisAskBroker(client, control or InProcessEventBus(), **kw)
    b.POLL_SECONDS = 0.02       # instance attribute: the answer-key fallback, fast
    return b


async def worker_and_api():
    """The pair the plan describes: a worker that opens the question and an API
    instance that resolves it, sharing Redis and the control channel."""
    worker_client, api_client = two_clients()
    control = InProcessEventBus()
    asking = broker(worker_client, control)
    answering = broker(api_client, control)
    # The worker's subscription to `hermes:ctl`, exactly as `main.run` builds it.
    turn_control = TurnControl(control, CancelRegistry(), asking)
    await turn_control.start()
    return asking, answering, turn_control


# --- the cross-process loop -------------------------------------------------


async def test_a_resolve_on_another_instance_wakes_the_parked_wait():
    asking, answering, _ = await worker_and_api()
    qid, fut = await asking.open(["yes", "no"], USER)

    waiter = asyncio.create_task(asking.wait(qid, fut, 5.0))
    assert await answering.resolve(qid, "1", USER) is True

    assert await asyncio.wait_for(waiter, 2) == "no"


async def test_the_cancel_token_travels_as_it_is():
    asking, answering, _ = await worker_and_api()
    qid, fut = await asking.open(["yes"], USER)

    waiter = asyncio.create_task(asking.wait(qid, fut, 5.0))
    assert await answering.resolve(qid, "c", USER) is True

    assert await asyncio.wait_for(waiter, 2) == CANCELLED


async def test_the_answer_key_is_the_fallback_when_the_nudge_is_lost():
    """Pub/sub is fire-and-forget. The key is what turns a lost message into a
    pause of at most one poll rather than a question answered into the void."""
    worker_client, api_client = two_clients()
    # No shared control bus: the nudge goes nowhere at all.
    asking = broker(worker_client)
    answering = broker(api_client)
    qid, fut = await asking.open(["yes", "no"], USER)

    waiter = asyncio.create_task(asking.wait(qid, fut, 5.0))
    assert await answering.resolve(qid, "0", USER) is True

    assert await asyncio.wait_for(waiter, 2) == "yes"


# --- refusals never touch the hash ------------------------------------------


async def test_a_stranger_is_refused_and_the_hash_is_untouched():
    """Popping before the owner check would let a stranger's tap destroy a
    question its owner is still looking at."""
    client = one_client()
    b = broker(client)
    qid, _ = await b.open(["yes", "no"], USER)

    assert await b.resolve(qid, "0", 999) is False
    assert await client.exists(ASK_PREFIX + qid) == 1
    assert await b.options_for(qid) == ["yes", "no"]
    assert await client.exists(f"{ASK_PREFIX}{qid}:answer") == 0


@pytest.mark.parametrize("token", ["", "abc", "-1", "9"])
async def test_a_token_that_names_nothing_is_refused_and_the_hash_survives(token):
    client = one_client()
    b = broker(client)
    qid, _ = await b.open(["yes", "no"], USER)

    assert await b.resolve(qid, token, USER) is False
    assert await client.exists(ASK_PREFIX + qid) == 1


async def test_an_unknown_qid_is_refused():
    assert await broker(one_client()).resolve("nope", "0", USER) is False


async def test_the_second_resolve_is_refused_and_options_are_gone():
    """The compare-and-delete is the arbiter: exactly one tap wins, and the
    question stops existing for everyone."""
    asking, answering, _ = await worker_and_api()
    qid, fut = await asking.open(["yes", "no"], USER)

    waiter = asyncio.create_task(asking.wait(qid, fut, 5.0))
    assert await answering.resolve(qid, "0", USER) is True
    assert await answering.resolve(qid, "1", USER) is False
    assert await answering.options_for(qid) is None

    assert await asyncio.wait_for(waiter, 2) == "yes"


# --- the deadline -----------------------------------------------------------


async def test_a_timeout_returns_none_and_takes_the_question_with_it():
    """The DEL at the deadline is what makes the timeout the arbiter: a tap that
    lands afterwards finds nothing to compare and is told the question is stale."""
    client = one_client()
    b = broker(client)
    qid, fut = await b.open(["yes"], USER)

    assert await b.wait(qid, fut, 0.05) is None
    assert await client.exists(ASK_PREFIX + qid) == 0
    assert await b.resolve(qid, "0", USER) is False


async def test_an_answer_that_lands_on_the_deadline_is_still_delivered():
    """`wait`'s DEL returning 0 means a resolver won the race — its answer is
    already in the answer key, and dropping it here would tell the user 'accepted'
    while the model is told nobody replied."""
    client = one_client()
    b = broker(client)
    qid, fut = await b.open(["yes", "no"], USER)
    # The resolver got there first; no nudge was ever delivered to this process.
    assert await broker(client).resolve(qid, "1", USER) is True

    assert await b.wait(qid, fut, 0.05) == "no"


async def test_wait_never_cancels_the_future():
    """`asyncio.wait`, not `wait_for`: a cancelled future set by a late tap raises
    `InvalidStateError` inside the resolver's task, where it is swallowed."""
    b = broker(one_client())
    qid, fut = await b.open(["yes"], USER)

    await b.wait(qid, fut, 0.05)

    assert not fut.cancelled()


# --- open, options_for, deliver, discard ------------------------------------


async def test_open_writes_the_question_with_a_ttl_past_the_callers_timeout():
    """`AskService` passes timeout + 60: a tap landing exactly as `wait` gives up
    must still find a hash, or the answer goes into a key nobody reads."""
    client = one_client()
    b = broker(client)
    qid, _ = await b.open(["yes"], USER, ttl_seconds=180)

    assert await client.hget(ASK_PREFIX + qid, "user_id") == str(USER)
    assert 170 < await client.ttl(ASK_PREFIX + qid) <= 180


async def test_options_for_reads_the_labels_and_then_nothing():
    b = broker(one_client())
    qid, _ = await b.open(["a", "b"], USER)
    assert await b.options_for(qid) == ["a", "b"]
    assert await b.options_for("nope") is None


async def test_deliver_is_synchronous_and_reports_a_qid_it_does_not_hold():
    """The control channel broadcasts, and exactly one process holds the question.
    Every other process's `deliver` is a no-op, which is the normal case."""
    b = broker(one_client())
    qid, fut = await b.open(["yes"], USER)

    assert b.deliver("someone-elses-qid", "yes") is False
    assert b.deliver(qid, "yes") is True
    assert fut.result() == "yes"
    assert b.deliver(qid, "yes") is False          # already delivered


async def test_discard_is_sync_and_the_del_still_lands():
    """`AskService` calls this from a `finally` a CancelledError can land on, so
    it cannot await — the DEL is a fire-and-forget task instead."""
    client = one_client()
    b = broker(client)
    qid, _ = await b.open(["yes"], USER)

    assert b.discard(qid) is None                  # synchronous, returns nothing
    assert await gone(client, ASK_PREFIX + qid)
    assert await client.exists(f"{ASK_PREFIX}{qid}:answer") == 0


async def test_a_discarded_question_can_no_longer_be_answered():
    client = one_client()
    b = broker(client)
    qid, _ = await b.open(["yes"], USER)
    b.discard(qid)
    assert await gone(client, ASK_PREFIX + qid)

    assert await b.resolve(qid, "0", USER) is False


async def test_the_nudge_carries_the_answer_on_the_control_channel():
    """One writer, one reader: `TurnControl.handle` is the only thing that reads
    this payload, and it hands it to `deliver`."""
    seen: list[dict] = []
    control = InProcessEventBus()

    async def handler(payload):
        seen.append(payload)

    control.subscribe(CONTROL_CHANNEL, handler)
    b = broker(one_client(), control)
    qid, _ = await b.open(["yes", "no"], USER)
    await b.resolve(qid, "1", USER)

    assert seen == [{"op": "ask", "qid": qid, "answer": "no"}]


async def test_a_nudge_that_cannot_be_published_still_leaves_the_answer_key():
    class Broken(InProcessEventBus):
        async def publish(self, channel, payload):
            raise RuntimeError("no redis")

    client = one_client()
    b = broker(client, Broken())
    qid, fut = await b.open(["yes"], USER)

    assert await b.resolve(qid, "0", USER) is True
    assert await b.wait(qid, fut, 0.2) == "yes"     # found through the answer key


async def test_a_failed_open_leaves_no_local_entry_behind():
    """`AskService.ask` awaits `open` OUTSIDE its try/finally, so a Redis write
    that raises never reaches `discard`. Registering the future before the write
    would leak the entry for the life of the process — and a later tap on a qid
    that was never published could be "delivered" into it."""
    class Broken:
        def pipeline(self, transaction=True):
            raise RuntimeError("redis is away")

    b = broker(Broken())
    with pytest.raises(RuntimeError):
        await b.open(["yes", "no"], USER)
    assert b._pending == {}

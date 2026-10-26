"""Two processes, one Redis: the acceptance line of Wave 3b.

Everything else in the suite proves a piece of this in isolation — `RedisSnapshots`
against fakeredis, `RedisEventBus` against two clients on one `FakeServer`, the SSE
watermark against a staged bus. This file is the only place where the pieces are
assembled the way a deployment assembles them and driven against the **real**
Redis: two `TurnBus` instances, each with its own client, its own `RedisEventBus`
and its own reader task, sharing nothing but the server.

What that buys over the fakes: real SUBSCRIBE confirmations (so `ready` is tested
as a round trip rather than as a local `Event`), real pub/sub ordering, and the
genuine interleaving of "the emitter mirrored the snapshot, then published" across
two connections. A bug that only shows up as a timing window between those two
steps cannot be reproduced anywhere else.

Skips cleanly without the stack: `redis_url` reports "run `make test-stack`"
rather than failing, because the suite has to stay green on a laptop with only the
database up.

The three cases are the three things that used to be process-local state:

1. the turn stream — instance B tails a turn instance A is running, including the
   mid-turn snapshot, with no doubled delta;
2. cancel — a click on B reaches the task running on A;
3. `ask_user` — a question opened on A is answered on B and wakes A's wait.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from claude_hermes.core.eventbus import RedisEventBus
from claude_hermes.core.redis import create_client
from claude_hermes.modules.turns.application.bus import TurnBus
from claude_hermes.modules.turns.application.control import CancelRegistry, TurnControl
from claude_hermes.modules.turns.infrastructure.publishers import ChannelPublisher
from claude_hermes.modules.turns.infrastructure.snapshots import RedisSnapshots
from claude_hermes.modules.turns.presentation.sse import event_stream

LANE = "web:1:two-processes"
TTL = 60
# Generous: every wait here is for a Redis round trip plus a reader poll, and a
# loaded CI box is allowed to be slow. Nothing in this file is a timing assertion.
TIMEOUT = 10.0


class SharedEvents:
    """`turn_events` as both processes see it: one table, two readers.

    In production that is PostgreSQL; here it is a list, because what this file is
    testing is the Redis half. The id it hands out is what the SSE cursor and the
    snapshot's `replayed` set de-duplicate against, so it has to be a real
    monotonic id and not `None`.
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def append(self, session_key, turn_id, seq, ts, type, payload) -> int:
        self.rows.append({"id": len(self.rows) + 1, "session_key": session_key,
                          "turn_id": turn_id, "seq": seq, "ts": ts, "type": type,
                          "payload": payload})
        return self.rows[-1]["id"]

    async def after(self, session_key, after_id=0, limit=1000):
        return [dict(r) for r in self.rows
                if r["session_key"] == session_key and r["id"] > after_id][:limit]


@pytest.fixture
async def process(redis_url):
    """A factory for one "process": its own client, event bus and turn bus.

    Separate clients on purpose. Two `TurnBus` objects over one connection would
    share a reader task and a pub/sub socket, which is precisely the thing that is
    NOT shared between an agent and a worker — the bug this file exists to catch
    would be invisible.
    """
    events = SharedEvents()
    built = []

    async def make():
        client = create_client(redis_url)
        bus = RedisEventBus(client)
        await bus.start()
        snapshots = RedisSnapshots(client, ttl_seconds=TTL)
        proc = SimpleNamespace(client=client, events=bus, snapshots=snapshots,
                               turn_events=events,
                               bus=TurnBus(events, ChannelPublisher(bus), snapshots))
        built.append(proc)
        return proc

    try:
        yield make
    finally:
        for proc in built:
            await proc.events.aclose()
            await proc.client.aclose()


def payload_of(frame: str) -> dict:
    data = [line[5:].strip() for line in frame.splitlines() if line.startswith("data:")]
    return json.loads(data[0])


async def next_payload(gen) -> dict:
    """The next real frame, skipping heartbeats."""
    while True:
        frame = await asyncio.wait_for(gen.__anext__(), TIMEOUT)
        if not frame.startswith(":"):
            return payload_of(frame)


# --- 1. the stream ----------------------------------------------------------


async def test_b_streams_a_turn_a_is_running_without_doubling_a_delta(process):
    """The acceptance line: "a turn started via instance A streams over SSE from
    instance B".

    The interesting moment is staged deliberately. The generator is lazy, so
    nothing runs until the first `__anext__`; at that point it subscribes (which
    only *schedules* the SUBSCRIBE) and replays the database. While it is
    suspended on that first frame, A emits another delta — which the emitter
    mirrors into the Redis snapshot BEFORE it publishes. So that delta is now in
    both places at once: in the snapshot B is about to read, and on its way to B's
    reader. That is the normal case in redis mode, and the seq watermark is the
    only thing standing between it and a browser showing the text twice.
    """
    a = await process()
    b = await process()

    await a.bus.emit(LANE, "t1", "turn.start", {"channel": "web", "user_id": 1})
    await a.bus.emit(LANE, "t1", "text.delta", {"delta": "Hel"})

    gen = event_stream(SimpleNamespace(bus=b.bus, turn_events=b.turn_events), LANE, 0)
    try:
        first = await next_payload(gen)                 # the DB replay
        assert first["type"] == "turn.start" and first["id"] == 1

        # Suspended past the replay, before the snapshot: the window.
        await a.bus.emit(LANE, "t1", "text.delta", {"delta": "lo"})

        second = await next_payload(gen)
        # The coalesced snapshot, read out of Redis on B, carrying A's seq.
        assert second["type"] == "text.delta"
        assert second["payload"] == {"delta": "Hello"} and second["seq"] == 3
        assert second["id"] is None

        # From here the subscription is live (snapshot awaited `ready`), so these
        # two reach B over pub/sub and not through the snapshot.
        await a.bus.emit(LANE, "t1", "text.block", {"text": "Hello"})
        await a.bus.emit(LANE, "t1", "turn.end", {"status": "ok", "cost_usd": None,
                                                  "sdk_session_id": None,
                                                  "error": None, "usage": None})
        third = await next_payload(gen)
        fourth = await next_payload(gen)
    finally:
        await gen.aclose()

    assert third["type"] == "text.block" and third["payload"] == {"text": "Hello"}
    assert fourth["type"] == "turn.end"
    # The delta that was in the snapshot AND on the wire was yielded exactly once,
    # coalesced. A raw {"delta": "lo"} frame anywhere above is the doubled text.
    assert {"delta": "lo"} not in [f.get("payload") for f in (first, second, third, fourth)]
    # A's turn is over, so its snapshot keys are gone from B's point of view too.
    assert await b.bus.is_running(LANE) is False
    assert await b.bus.snapshot(LANE) == []


async def test_b_sees_a_lane_a_is_running_and_then_sees_it_idle(process):
    """The sidebar's question, asked from the other process. `is_running` reads
    the snapshot store, which is the only reason it can answer at all here."""
    a = await process()
    b = await process()
    assert await b.bus.is_running(LANE) is False
    await a.bus.emit(LANE, "t1", "turn.start", {"channel": "web", "user_id": 1})
    assert await b.bus.is_running(LANE) is True
    snap = await b.bus.snapshot(LANE)
    assert [(e.turn_id, e.type) for e in snap] == [("t1", "turn.start")]
    await a.bus.emit(LANE, "t1", "turn.end", {"status": "ok"})
    assert await b.bus.is_running(LANE) is False


# --- 2. cancel --------------------------------------------------------------


async def test_a_cancel_nudge_published_on_b_cancels_the_task_running_on_a(process):
    """Cancel's latency half. The truth is the flag on the `turns` row (B1's
    `TurnLauncher.cancel` writes it first); this is the nudge that makes the
    cancellation immediate instead of one heartbeat late, and it has to cross a
    process boundary to be worth anything.
    """
    a = await process()
    b = await process()

    registry = CancelRegistry()
    worker = TurnControl(a.events, registry, asks=None)
    # `start` awaits `ready`, so after this line nothing published on the control
    # channel can be missed. Without it this test would be a race.
    await worker.start()

    api = TurnControl(b.events, CancelRegistry(), asks=None)
    await api.start()

    running = asyncio.Event()

    async def turn():
        running.set()
        await asyncio.sleep(TIMEOUT * 3)

    task = asyncio.create_task(turn())
    await running.wait()
    registry.register("t1", task)
    try:
        # A turn nobody here is running is a no-op, not an error: the nudge is
        # broadcast and every process but one ignores it.
        await api.nudge_cancel("not-a-turn-anybody-runs")
        await api.nudge_cancel("t1")
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, TIMEOUT)
        assert task.cancelled()
        # Idempotent across the wire as well: the heartbeat on the worker keeps
        # seeing `cancel_requested_at` and re-nudges, and a second `task.cancel()`
        # would land on the terminal write's await.
        await api.nudge_cancel("t1")
        await asyncio.sleep(0.2)
    finally:
        await worker.aclose()
        await api.aclose()


# --- 3. ask_user ------------------------------------------------------------


def _redis_ask_broker():
    """B1 owns `infrastructure/redis_ask_broker.py`. This file is written against
    the signature the plan freezes, so the case starts running the moment the
    module lands — rather than being forgotten in a follow-up."""
    try:
        from claude_hermes.modules.turns.infrastructure.redis_ask_broker import (
            RedisAskBroker,
        )
    except ImportError:
        return None
    return RedisAskBroker


async def test_an_ask_opened_on_a_and_answered_on_b_wakes_a(process):
    """The worker parks on a question; the browser answers it to whichever API
    instance served the request. The answer key is the truth and the nudge is the
    latency — this asserts the whole path, nudge included, by waiting first and
    resolving second."""
    broker_class = _redis_ask_broker()
    if broker_class is None:
        pytest.skip("RedisAskBroker (B1) is not implemented yet: "
                    "modules/turns/infrastructure/redis_ask_broker.py is absent")

    a = await process()
    b = await process()
    worker_broker = broker_class(a.client, a.events)
    api_broker = broker_class(b.client, b.events)
    worker = TurnControl(a.events, CancelRegistry(), worker_broker)
    await worker.start()
    try:
        qid, fut = await worker_broker.open(["yes", "no"], 7, ttl_seconds=TTL)
        waiting = asyncio.create_task(worker_broker.wait(qid, fut, TIMEOUT))
        await asyncio.sleep(0.1)                       # park the worker on the wait

        # The other process can see the question well enough to render it.
        assert await api_broker.options_for(qid) == ["yes", "no"]
        # A stranger's tap is refused and leaves the question open.
        assert await api_broker.resolve(qid, "1", 999) is False
        assert await api_broker.options_for(qid) == ["yes", "no"]

        assert await api_broker.resolve(qid, "1", 7) is True
        assert await asyncio.wait_for(waiting, TIMEOUT) == "no"
        # Answered once: the second tap has nothing left to resolve.
        assert await api_broker.resolve(qid, "0", 7) is False
    finally:
        await worker.aclose()

"""The channel bus, in both shapes.

`InProcessEventBus` is inline mode and the old listener list; `RedisEventBus` is
the same contract across processes, driven here against `fakeredis` with two bus
instances on one `FakeServer` — which is what "two processes" means for a unit
test. The container exercise is `tests/modules/turns/test_turns_two_processes.py`.

What is worth testing here is not "a message arrives". It is the four things the
rest of Wave 3b leans on: `ready` really is the server's subscription
confirmation and not merely the command having been written (the SSE snapshot
ordering depends on it), one slow handler holds only its own channel, a raising
handler costs nobody else their events, and a dead pub/sub socket does not
silently freeze every stream in the process.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from claude_hermes.core.eventbus import InProcessEventBus, RedisEventBus

fakeredis = pytest.importorskip("fakeredis")


async def until(predicate, timeout: float = 3.0) -> bool:
    """Poll until a predicate holds. The Redis bus reads on its own task, so a
    test cannot assert straight after a publish."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def collector():
    seen: list[dict] = []

    async def handler(payload: dict) -> None:
        seen.append(payload)

    return seen, handler


# --- in-process -------------------------------------------------------------


async def test_in_process_delivers_per_channel_and_unsubscribes():
    bus = InProcessEventBus()
    seen, handler = collector()
    unsubscribe = bus.subscribe("a", handler)
    await bus.ready("a")                       # immediate: there is nothing to wait for
    await bus.publish("a", {"n": 1})
    await bus.publish("b", {"n": 2})           # a different channel is a different lane
    assert seen == [{"n": 1}]
    unsubscribe()
    await bus.publish("a", {"n": 3})
    assert seen == [{"n": 1}]


async def test_in_process_a_raising_handler_never_costs_the_others():
    """One browser tab whose queue blew up must not take the turn down with it."""
    bus = InProcessEventBus()
    seen, good = collector()

    async def bad(payload):
        raise RuntimeError("boom")

    bus.subscribe("a", bad)
    bus.subscribe("a", good)
    await bus.publish("a", {"n": 1})
    assert seen == [{"n": 1}]


async def test_unsubscribing_twice_is_harmless():
    bus = InProcessEventBus()
    _, handler = collector()
    unsubscribe = bus.subscribe("a", handler)
    unsubscribe()
    unsubscribe()


# --- redis ------------------------------------------------------------------


def two_clients():
    """Two clients on one server: as close to two processes as a unit test gets."""
    server = fakeredis.FakeServer()
    return (fakeredis.FakeAsyncRedis(server=server, decode_responses=True),
            fakeredis.FakeAsyncRedis(server=server, decode_responses=True))


async def test_redis_carries_an_event_from_one_instance_to_another():
    publisher_client, subscriber_client = two_clients()
    publisher = RedisEventBus(publisher_client)
    subscriber = RedisEventBus(subscriber_client)
    await publisher.start()
    await subscriber.start()
    seen, handler = collector()
    try:
        subscriber.subscribe("hermes:turn:web:1:x", handler)
        # INVARIANT: after `ready` the SUBSCRIBE is confirmed, so nothing published
        # from here on can be missed. Without it this test would be a race.
        await subscriber.ready("hermes:turn:web:1:x")
        await publisher.publish("hermes:turn:web:1:x", {"type": "turn.start", "seq": 1})
        await publisher.publish("hermes:turn:web:1:y", {"type": "turn.start", "seq": 1})
        assert await until(lambda: len(seen) == 1)
        assert seen == [{"type": "turn.start", "seq": 1}]
        await asyncio.sleep(0.1)               # the other lane must stay out of it
        assert len(seen) == 1
    finally:
        await publisher.aclose()
        await subscriber.aclose()


async def test_redis_ready_resolves_only_once_the_subscription_is_live():
    """`subscribe` is synchronous and only queues the SUBSCRIBE. `ready` is the
    seam every caller that must not miss the first event awaits."""
    client, _ = two_clients()
    bus = RedisEventBus(client)
    await bus.start()
    try:
        _, handler = collector()
        bus.subscribe("hermes:ctl", handler)
        assert not bus._ready["hermes:ctl"].is_set()     # not yet: the reader has it
        await bus.ready("hermes:ctl")
        assert bus._ready["hermes:ctl"].is_set()
        # A channel nobody subscribed to is ready immediately rather than hanging.
        await asyncio.wait_for(bus.ready("hermes:turn:nobody"), 1.0)
    finally:
        await bus.aclose()


async def test_redis_the_last_unsubscribe_stops_the_delivery():
    publisher_client, subscriber_client = two_clients()
    publisher = RedisEventBus(publisher_client)
    subscriber = RedisEventBus(subscriber_client)
    await publisher.start()
    await subscriber.start()
    try:
        first, handler_a = collector()
        second, handler_b = collector()
        drop_a = subscriber.subscribe("ch", handler_a)
        drop_b = subscriber.subscribe("ch", handler_b)
        await subscriber.ready("ch")
        await publisher.publish("ch", {"n": 1})
        assert await until(lambda: first and second)

        drop_a()                       # one of two: the channel stays subscribed
        await publisher.publish("ch", {"n": 2})
        assert await until(lambda: len(second) == 2)
        assert len(first) == 1

        # The last one: UNSUBSCRIBE is scheduled, fire and forget. A live
        # subscription on another channel keeps the reader reading, so this is a
        # real test of "the server stopped sending" and not of an idle reader.
        drop_b()
        third, handler_c = collector()
        subscriber.subscribe("other", handler_c)
        await subscriber.ready("other")
        await publisher.publish("ch", {"n": 3})
        await publisher.publish("other", {"n": 4})
        assert await until(lambda: third == [{"n": 4}])
        assert len(second) == 2
    finally:
        await publisher.aclose()
        await subscriber.aclose()


async def test_redis_a_raising_handler_never_costs_the_others():
    publisher_client, subscriber_client = two_clients()
    publisher = RedisEventBus(publisher_client)
    subscriber = RedisEventBus(subscriber_client)
    await publisher.start()
    await subscriber.start()
    try:
        seen, good = collector()

        async def bad(payload):
            raise RuntimeError("boom")

        subscriber.subscribe("ch", bad)
        subscriber.subscribe("ch", good)
        await subscriber.ready("ch")
        await publisher.publish("ch", {"n": 1})
        await publisher.publish("ch", {"n": 2})
        assert await until(lambda: len(seen) == 2)
    finally:
        await publisher.aclose()
        await subscriber.aclose()


async def test_redis_drops_a_payload_it_cannot_parse_and_keeps_reading():
    """Anything can PUBLISH to a channel. One bad frame must not end the reader —
    that would freeze every SSE stream in the process."""
    publisher_client, subscriber_client = two_clients()
    subscriber = RedisEventBus(subscriber_client)
    await subscriber.start()
    try:
        seen, handler = collector()
        subscriber.subscribe("ch", handler)
        await subscriber.ready("ch")
        await publisher_client.publish("ch", "not json at all")
        await publisher_client.publish("ch", '"a bare string, not an object"')
        await publisher_client.publish("ch", '{"n": 1}')
        assert await until(lambda: seen == [{"n": 1}])
    finally:
        await subscriber.aclose()


class _DyingPubSub:
    """A PubSub whose socket dies after a few reads, then stays dead."""

    def __init__(self, inner, reads_before_death: int):
        self._inner = inner
        self._left = reads_before_death

    @property
    def subscribed(self):
        return self._inner.subscribed

    async def subscribe(self, channel):
        return await self._inner.subscribe(channel)

    async def unsubscribe(self, channel):
        return await self._inner.unsubscribe(channel)

    async def get_message(self, **kwargs):
        if self._left <= 0:
            raise ConnectionError("connection reset by peer")
        self._left -= 1
        return await self._inner.get_message(**kwargs)

    async def aclose(self):
        await self._inner.aclose()


class _FlakyClient:
    """A client whose FIRST pub/sub connection dies; later ones are healthy."""

    def __init__(self, inner, reads_before_death: int):
        self._inner = inner
        self._reads_before_death = reads_before_death
        self.pubsubs = 0

    def pubsub(self):
        self.pubsubs += 1
        inner = self._inner.pubsub()
        return (_DyingPubSub(inner, self._reads_before_death) if self.pubsubs == 1
                else inner)

    async def publish(self, channel, data):
        return await self._inner.publish(channel, data)


async def test_redis_the_reader_resubscribes_after_a_dropped_connection():
    """A pub/sub socket that dies silently freezes every SSE stream in the
    process, which looks like "the agent stopped answering" and not like an
    outage. The reader rebuilds the connection and re-subscribes every channel
    that still has a handler."""
    publisher_client, subscriber_client = two_clients()
    flaky = _FlakyClient(subscriber_client, reads_before_death=2)
    subscriber = RedisEventBus(flaky)
    subscriber.BACKOFF_SECONDS = 0.01           # no need to wait out a real backoff
    await subscriber.start()
    try:
        seen, handler = collector()
        subscriber.subscribe("ch", handler)
        await subscriber.ready("ch")
        assert await until(lambda: flaky.pubsubs >= 2)      # the first one died

        # The recovered connection re-subscribed, so a publish lands again. The
        # retry is for the window between the rebuild and the SUBSCRIBE landing:
        # this bus is at-least-once, not "no event is ever dropped".
        async def republish():
            for _ in range(50):
                if seen:
                    return
                await publisher_client.publish("ch", '{"n": 1}')
                await asyncio.sleep(0.02)

        await republish()
        assert seen and seen[0] == {"n": 1}
    finally:
        await subscriber.aclose()


async def test_redis_aclose_stops_the_reader():
    client, _ = two_clients()
    bus = RedisEventBus(client)
    await bus.start()
    reader = bus._reader
    await bus.aclose()
    assert reader.done() and bus._reader is None
    await bus.aclose()                          # idempotent


class _GatedPubSub:
    """A PubSub whose reads are held open until the test lets them through, so a
    SUBSCRIBE can be on the wire while its confirmation demonstrably is not."""

    def __init__(self, inner, gate: asyncio.Event, sent: asyncio.Event):
        self._inner = inner
        self._gate = gate
        self._sent = sent

    @property
    def subscribed(self):
        return self._inner.subscribed

    async def subscribe(self, channel):
        result = await self._inner.subscribe(channel)
        self._sent.set()                    # redis-py only WROTE the command here
        return result

    async def unsubscribe(self, channel):
        return await self._inner.unsubscribe(channel)

    async def get_message(self, **kwargs):
        await self._gate.wait()
        return await self._inner.get_message(**kwargs)

    async def aclose(self):
        await self._inner.aclose()


class _GatedClient:
    def __init__(self, inner, gate: asyncio.Event, sent: asyncio.Event):
        self._inner = inner
        self._gate = gate
        self._sent = sent

    def pubsub(self):
        return _GatedPubSub(self._inner.pubsub(), self._gate, self._sent)

    async def publish(self, channel, data):
        return await self._inner.publish(channel, data)


async def test_redis_ready_waits_for_the_confirmation_frame_not_the_command():
    """INVARIANT: `ready` is the SERVER's answer, not the client's send.

    redis-py's `PubSub.subscribe` writes the command and never parses a reply, so
    resolving `ready` there would promise an ordering the socket has not
    delivered — and the snapshot read that follows goes out on a different pooled
    connection, so an event published in between would be in neither the snapshot
    nor the subscription. Here the reader is held before its first read: the
    SUBSCRIBE is out, and `ready` must still be waiting.
    """
    client, _ = two_clients()
    gate, sent = asyncio.Event(), asyncio.Event()
    bus = RedisEventBus(_GatedClient(client, gate, sent))
    await bus.start()
    try:
        _, handler = collector()
        bus.subscribe("hermes:turn:web:1:x", handler)
        await asyncio.wait_for(sent.wait(), 2)          # the command is on the wire
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(bus.ready("hermes:turn:web:1:x"), 0.2)
        assert not bus._ready["hermes:turn:web:1:x"].is_set()

        gate.set()                                      # let the frame be read
        await asyncio.wait_for(bus.ready("hermes:turn:web:1:x"), 2)
    finally:
        gate.set()
        await bus.aclose()


async def test_redis_a_slow_handler_holds_only_its_own_channel():
    """Fan-out runs on a per-channel delivery task, never on the reader.

    The Telegram presenter is a subscriber and does a network call per status
    change; inline it only ever blocked its own turn. On the reader it would
    block every lane's SSE frames, every pending `ready()` confirmation and the
    control nudges that carry cancel and ask answers.
    """
    publisher_client, subscriber_client = two_clients()
    publisher = RedisEventBus(publisher_client)
    subscriber = RedisEventBus(subscriber_client)
    await publisher.start()
    await subscriber.start()
    release, entered = asyncio.Event(), asyncio.Event()
    try:
        async def slow(payload):
            entered.set()
            await release.wait()

        fast, fast_handler = collector()
        subscriber.subscribe("hermes:turn:slow", slow)
        subscriber.subscribe("hermes:turn:fast", fast_handler)
        await subscriber.ready("hermes:turn:slow")
        await subscriber.ready("hermes:turn:fast")
        await publisher.publish("hermes:turn:slow", {"n": 1})
        assert await until(entered.is_set)

        # The reader is not behind that handler: another lane is delivered...
        await publisher.publish("hermes:turn:fast", {"n": 2})
        assert await until(lambda: fast == [{"n": 2}])
        # ...and a new subscription is still confirmed while it hangs.
        _, control = collector()
        subscriber.subscribe("hermes:ctl", control)
        await asyncio.wait_for(subscriber.ready("hermes:ctl"), 2)
    finally:
        release.set()
        await publisher.aclose()
        await subscriber.aclose()


async def test_redis_keeps_one_channels_events_in_order():
    """Per-channel queues rather than one per process: the SSE watermark reads a
    per-lane `seq`, so order WITHIN a lane is a contract."""
    publisher_client, subscriber_client = two_clients()
    publisher = RedisEventBus(publisher_client)
    subscriber = RedisEventBus(subscriber_client)
    await publisher.start()
    await subscriber.start()
    try:
        seen: list[int] = []

        async def handler(payload):
            await asyncio.sleep(0.01)       # a handler that is not instant
            seen.append(payload["seq"])

        subscriber.subscribe("hermes:turn:web:1:x", handler)
        await subscriber.ready("hermes:turn:web:1:x")
        for seq in range(1, 6):
            await publisher.publish("hermes:turn:web:1:x", {"seq": seq})
        assert await until(lambda: len(seen) == 5)
        assert seen == [1, 2, 3, 4, 5]
    finally:
        await publisher.aclose()
        await subscriber.aclose()

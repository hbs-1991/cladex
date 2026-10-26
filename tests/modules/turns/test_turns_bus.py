"""The bus over its ports.

It runs on fakes here, which is what proves the repository and the publisher are
actually ports: the bus never sees an engine.
"""
from claude_hermes.core.eventbus import InProcessEventBus
from claude_hermes.modules.turns.application.bus import TurnBus
from claude_hermes.modules.turns.domain.events import TurnEvent
from claude_hermes.modules.turns.infrastructure.publishers import (
    ChannelPublisher,
    InProcessPublisher,
    turn_channel,
)
from claude_hermes.modules.turns.infrastructure.snapshots import MemorySnapshots


class FakeEvents:
    def __init__(self, fail=False):
        self.rows = []
        self._fail = fail

    async def append(self, session_key, turn_id, seq, ts, type, payload):
        if self._fail:
            raise RuntimeError("db down")
        self.rows.append((session_key, turn_id, seq, type))
        return len(self.rows)

    async def after(self, session_key, after_id=0, limit=1000):
        return []


def make():
    events = FakeEvents()
    return TurnBus(events, InProcessPublisher()), events


async def test_transient_deltas_never_reach_the_repository():
    bus, events = make()
    await bus.emit("web:1:x", "t1", "turn.start", {})
    await bus.emit("web:1:x", "t1", "text.delta", {"delta": "Hel"})
    await bus.emit("web:1:x", "t1", "text.block", {"text": "Hello"})
    assert [r[3] for r in events.rows] == ["turn.start", "text.block"]


async def test_a_persistence_failure_still_fans_the_event_out():
    bus = TurnBus(FakeEvents(fail=True), InProcessPublisher())
    seen = []

    async def listener(ev):
        seen.append(ev)

    bus.subscribe("web:1:x", listener)
    ev = await bus.emit("web:1:x", "t1", "turn.start", {})
    assert ev.id is None and [e.type for e in seen] == ["turn.start"]


async def test_two_turns_on_one_lane_keep_separate_sequences():
    """A cron job delivers into the owner's private lane while a live turn runs
    there. One slot per lane let the delivery reset the live turn's seq and empty
    its snapshot — the two-level keying is deliberate."""
    bus, _ = make()
    lane = "tg:private:1"
    await bus.emit(lane, "live", "turn.start", {})
    await bus.emit(lane, "live", "text.delta", {"delta": "Wor"})
    await bus.emit(lane, "cron1", "turn.start", {})
    await bus.emit(lane, "cron1", "turn.end", {"status": "ok"})
    assert await bus.is_running(lane) is True
    nxt = await bus.emit(lane, "live", "text.delta", {"delta": "king"})
    assert nxt.seq == 3
    assert [(e.turn_id, e.type) for e in await bus.snapshot(lane)] == [
        ("live", "turn.start"), ("live", "text.delta")]
    assert (await bus.snapshot(lane))[-1].payload == {"delta": "Working"}


async def test_unsubscribe_stops_the_fan_out():
    bus, _ = make()
    seen = []

    async def listener(ev):
        seen.append(ev)

    unsub = bus.subscribe("web:1:x", listener)
    await bus.emit("web:1:x", "t1", "turn.start", {})
    unsub()
    await bus.emit("web:1:x", "t1", "turn.end", {"status": "ok"})
    assert len(seen) == 1


async def test_a_bad_listener_does_not_break_the_others():
    bus, _ = make()
    seen = []

    async def bad(ev):
        raise RuntimeError("boom")

    async def good(ev):
        seen.append(ev)

    bus.subscribe("web:1:x", bad)
    bus.subscribe("web:1:x", good)
    await bus.emit("web:1:x", "t1", "turn.start", {})
    assert len(seen) == 1


# --- the channel seam -------------------------------------------------------


async def test_an_event_survives_the_trip_through_a_channel():
    """`TurnEvent.to_wire()` out, `TurnEvent(**wire)` in. The lane's channel is
    what a second process subscribes to, so the wire form has to round-trip every
    field — including the `id` that is None for a delta and the `seq` the SSE
    watermark is written against."""
    events_bus = InProcessEventBus()
    emitter = TurnBus(FakeEvents(), ChannelPublisher(events_bus))
    seen = []

    async def listener(ev):
        seen.append(ev)

    # A SECOND bus object over the same channel bus: as close to a second process
    # as an in-process test gets.
    reader = TurnBus(FakeEvents(), ChannelPublisher(events_bus))
    reader.subscribe("web:1:x", listener)
    await reader.ready("web:1:x")

    sent = await emitter.emit("web:1:x", "t1", "turn.start", {"channel": "web"})
    delta = await emitter.emit("web:1:x", "t1", "text.delta", {"delta": "Hi"})
    assert [e.to_wire() for e in seen] == [sent.to_wire(), delta.to_wire()]
    assert seen[1].id is None and seen[1].seq == 2
    assert turn_channel("web:1:x") == "hermes:turn:web:1:x"


async def test_the_snapshot_is_written_before_the_event_is_published():
    """INVARIANT. A listener that reads the snapshot the moment it is told about
    an event must already see that event — otherwise a subscriber arriving in the
    gap finds it in neither the snapshot nor the stream."""
    events_bus = InProcessEventBus()
    bus = TurnBus(FakeEvents(), ChannelPublisher(events_bus))
    seen = []

    async def listener(ev):
        seen.append([e.type for e in await bus.snapshot("web:1:x")])

    bus.subscribe("web:1:x", listener)
    await bus.emit("web:1:x", "t1", "turn.start", {})
    await bus.emit("web:1:x", "t1", "text.delta", {"delta": "Hi"})
    assert seen == [["turn.start"], ["turn.start", "text.delta"]]


async def test_snapshot_waits_for_the_subscription_before_it_reads_the_store():
    """INVARIANT. `subscribe` only SCHEDULES the SUBSCRIBE in redis mode, so
    `snapshot` awaits `ready` first — without it there is a window in which an
    event is in neither what the snapshot returned nor the live tail."""
    order = []

    class SlowPublisher(ChannelPublisher):
        async def ready(self, session_key):
            order.append("ready")
            await super().ready(session_key)

    class WatchingStore(MemorySnapshots):
        async def read(self, session_key):
            order.append("read")
            return await super().read(session_key)

    bus = TurnBus(FakeEvents(), SlowPublisher(InProcessEventBus()), WatchingStore())
    await bus.emit("web:1:x", "t1", "turn.start", {})
    await bus.snapshot("web:1:x")
    assert order == ["ready", "read"]


async def test_the_bus_takes_the_snapshot_store_it_is_given():
    """The two-argument constructor still builds its own, which is what every
    inline deployment relies on."""
    store = MemorySnapshots()
    bus = TurnBus(FakeEvents(), InProcessPublisher(), store)
    await bus.emit("web:1:x", "t1", "turn.start", {})
    assert await store.is_running("web:1:x") is True
    assert await store.read("web:1:x") == await bus.snapshot("web:1:x")


# --- the wire form and the shared lane ---------------------------------------


def test_to_wire_shape():
    ev = TurnEvent("web:1:x", "t1", 3, "2026-07-12T10:00:00+00:00", "text.block",
                   {"text": "x"}, id=7)
    assert ev.to_wire() == {
        "id": 7, "session_key": "web:1:x", "turn_id": "t1", "seq": 3,
        "ts": "2026-07-12T10:00:00+00:00", "type": "text.block", "payload": {"text": "x"},
    }


async def test_lanes_are_isolated():
    bus, _ = make()
    seen = []

    async def listener(ev):
        seen.append(ev.session_key)

    bus.subscribe("web:1:x", listener)
    await bus.emit("web:1:y", "t9", "turn.start", {"channel": "web", "user_id": 1})
    assert seen == []


async def test_a_cron_delivery_does_not_disturb_the_live_turn():
    """A scheduled job delivers into the owner's private lane while a turn is
    running there. Two turns, one lane: neither may reset the other's seq, empty
    its snapshot, or make the lane look idle."""
    bus, _ = make()
    lane = "tg:private:1"
    await bus.emit(lane, "live", "turn.start", {"channel": "telegram", "user_id": 1})
    await bus.emit(lane, "live", "text.delta", {"delta": "Wor"})

    await bus.emit(lane, "cron1", "turn.start", {"channel": "cron", "user_id": 0})
    await bus.emit(lane, "cron1", "text.block", {"text": "report"})
    await bus.emit(lane, "cron1", "turn.end", {"status": "ok"})

    assert await bus.is_running(lane) is True                    # the live turn is still up
    nxt = await bus.emit(lane, "live", "text.delta", {"delta": "king"})
    assert nxt.seq == 3                                    # continues 1, 2, 3
    snap = await bus.snapshot(lane)
    assert [(e.turn_id, e.type) for e in snap] == [("live", "turn.start"),
                                                   ("live", "text.delta")]
    assert snap[-1].payload == {"delta": "Working"}
    await bus.emit(lane, "live", "turn.end", {"status": "ok"})
    assert await bus.is_running(lane) is False and await bus.snapshot(lane) == []

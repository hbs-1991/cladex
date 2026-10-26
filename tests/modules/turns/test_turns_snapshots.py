"""The in-flight snapshot store, as a contract rather than an implementation.

A browser that reloads mid-turn is handed `read(lane)`, and what it shows depends
on this being byte-for-byte what the bus would have replayed: the persisted events
of every turn in flight, then at most one coalesced `thinking.delta` and one
`text.delta` per turn, each carrying **that turn's latest seq** — the SSE
watermark in `presentation/sse.py` drops a duplicate by comparing against exactly
that number, so a store that reported seq 0 there would let every delta through
twice.

Parametrized over both stores so neither can drift from the other: the whole
point of `RedisSnapshots` is that a browser attached to instance B sees exactly
what instance A would have shown it, and "exactly" is only true if one test file
decides it.

The Redis half runs on `fakeredis` — a FakeAsyncRedis per store instance, so each
test gets its own empty server. The cross-process exercise against the real
container is `tests/modules/turns/test_turns_two_processes.py`.
"""
from __future__ import annotations

import pytest

from claude_hermes.modules.turns.domain.events import TurnEvent
from claude_hermes.modules.turns.infrastructure.snapshots import (
    SNAPSHOT_PREFIX,
    MemorySnapshots,
    RedisSnapshots,
)

try:
    import fakeredis
except ImportError:                                        # pragma: no cover
    fakeredis = None

LANE = "web:1:x"
TS = "2026-07-12T10:00:00+00:00"
TTL = 60


def redis_store() -> RedisSnapshots:
    return RedisSnapshots(fakeredis.FakeAsyncRedis(decode_responses=True),
                          ttl_seconds=TTL)


STORES = [
    pytest.param(MemorySnapshots, id="memory"),
    pytest.param(redis_store, id="redis",
                 marks=pytest.mark.skipif(fakeredis is None,
                                          reason="fakeredis is not installed")),
]


@pytest.fixture(params=STORES)
def store(request):
    return request.param()


class Seq:
    """The emitter's side: a sequence per turn, exactly as `TurnBus.emit` keeps
    it, so the events these tests record look like real ones."""

    def __init__(self):
        self._seq: dict[str, int] = {}
        self._ids = 0

    def event(self, turn_id, type, payload=None, *, lane=LANE, persisted=None):
        self._seq[turn_id] = self._seq.get(turn_id, 0) + 1
        if persisted is None:
            persisted = type not in ("text.delta", "thinking.delta")
        eid = None
        if persisted:
            self._ids += 1
            eid = self._ids
        return TurnEvent(lane, turn_id, self._seq[turn_id], TS, type,
                         payload or {}, id=eid)


async def test_an_idle_lane_has_nothing_to_replay(store):
    assert await store.is_running(LANE) is False
    assert await store.read(LANE) == []


async def test_the_snapshot_is_the_persisted_events_plus_the_unflushed_buffers(store):
    seq = Seq()
    for ev in (seq.event("t1", "turn.start", {"channel": "web", "user_id": 1}),
               seq.event("t1", "tool.call", {"call_id": "c1", "name": "Read", "input": {}}),
               seq.event("t1", "thinking.delta", {"delta": "Hm"}),
               seq.event("t1", "thinking.delta", {"delta": "m."}),
               seq.event("t1", "text.delta", {"delta": "Hel"}),
               seq.event("t1", "text.delta", {"delta": "lo"})):
        await store.record(ev)

    assert await store.is_running(LANE) is True
    snap = await store.read(LANE)
    assert [e.type for e in snap] == ["turn.start", "tool.call",
                                      "thinking.delta", "text.delta"]
    assert snap[-2].payload == {"delta": "Hmm."}       # the deltas, coalesced
    assert snap[-1].payload == {"delta": "Hello"}
    # Every synthetic delta carries the turn's LATEST seq: the SSE watermark is
    # written against it, and a lower number would let a duplicate through.
    assert snap[-2].seq == 6 and snap[-1].seq == 6
    assert snap[-2].id is None and snap[-1].id is None
    assert [e.turn_id for e in snap] == ["t1"] * 4


async def test_a_flushed_block_clears_its_buffer_and_keeps_the_other(store):
    seq = Seq()
    for ev in (seq.event("t1", "turn.start", {}),
               seq.event("t1", "thinking.delta", {"delta": "Hmm."}),
               seq.event("t1", "text.delta", {"delta": "Hello"}),
               seq.event("t1", "text.block", {"text": "Hello"})):
        await store.record(ev)
    snap = await store.read(LANE)
    assert [e.type for e in snap] == ["turn.start", "text.block", "thinking.delta"]
    assert snap[-1].payload == {"delta": "Hmm."}       # the thinking buffer survives
    await store.record(seq.event("t1", "thinking", {"text": "Hmm."}))
    assert [e.type for e in await store.read(LANE)] == ["turn.start", "text.block",
                                                        "thinking"]


async def test_turn_end_drops_the_turn_and_the_lane_goes_idle(store):
    seq = Seq()
    await store.record(seq.event("t1", "turn.start", {}))
    await store.record(seq.event("t1", "text.delta", {"delta": "Hi"}))
    await store.record(seq.event("t1", "turn.end", {"status": "ok"}))
    assert await store.is_running(LANE) is False
    assert await store.read(LANE) == []


async def test_two_turns_on_one_lane_keep_separate_snapshots(store):
    """A cron job delivers its report into the owner's private lane while a live
    turn runs there. One slot per lane let the delivery empty the live turn's
    snapshot and report the lane idle."""
    seq = Seq()
    await store.record(seq.event("live", "turn.start", {}))
    await store.record(seq.event("live", "text.delta", {"delta": "Wor"}))
    await store.record(seq.event("cron1", "turn.start", {}))
    await store.record(seq.event("cron1", "text.block", {"text": "report"}))
    await store.record(seq.event("cron1", "turn.end", {"status": "ok"}))

    assert await store.is_running(LANE) is True
    await store.record(seq.event("live", "text.delta", {"delta": "king"}))
    snap = await store.read(LANE)
    assert [(e.turn_id, e.type) for e in snap] == [("live", "turn.start"),
                                                   ("live", "text.delta")]
    assert snap[-1].payload == {"delta": "Working"} and snap[-1].seq == 3


async def test_an_event_for_a_turn_that_never_started_is_ignored(store):
    """The emitter still sequences and publishes it — a cron delivery's `turn.end`
    after a restart, say — but there is nothing here for a late subscriber to
    catch up on, and inventing a turn would make the lane look busy forever."""
    seq = Seq()
    await store.record(seq.event("ghost", "text.block", {"text": "orphan"}))
    await store.record(seq.event("ghost", "turn.end", {"status": "ok"}))
    assert await store.is_running(LANE) is False
    assert await store.read(LANE) == []


async def test_clear_removes_one_turn_and_leaves_the_other(store):
    """What the reaper calls after it marks a turn lost."""
    seq = Seq()
    await store.record(seq.event("live", "turn.start", {}))
    await store.record(seq.event("cron1", "turn.start", {}))
    await store.clear(LANE, "cron1")
    assert [e.turn_id for e in await store.read(LANE)] == ["live"]
    await store.clear(LANE, "live")
    assert await store.is_running(LANE) is False
    await store.clear(LANE, "live")                    # idempotent
    await store.clear("web:1:never", "nothing")


async def test_lanes_do_not_see_each_other(store):
    seq = Seq()
    await store.record(seq.event("t1", "turn.start", {}, lane="web:1:a"))
    await store.record(seq.event("t2", "turn.start", {}, lane="web:1:b"))
    assert [e.turn_id for e in await store.read("web:1:a")] == ["t1"]
    assert await store.is_running("web:1:b") is True
    assert await store.is_running("web:1:c") is False


# --- what only the Redis store can get wrong --------------------------------

pytestmark_redis = pytest.mark.skipif(fakeredis is None,
                                      reason="fakeredis is not installed")


@pytestmark_redis
async def test_redis_expires_every_key_it_touches():
    """The TTL is this store's garbage collector. A worker killed between a
    heartbeat and the reaper leaves keys behind by definition — if one of them had
    no expiry the lane would report itself busy until somebody ran FLUSHDB."""
    store = redis_store()
    client = store._client
    seq = Seq()
    await store.record(seq.event("t1", "turn.start", {}))
    await store.record(seq.event("t1", "tool.call", {"name": "Read"}))
    await store.record(seq.event("t1", "text.delta", {"delta": "Hi"}))
    await store.record(seq.event("t1", "thinking.delta", {"delta": "Hm"}))

    keys = sorted(k for k in await client.keys(SNAPSHOT_PREFIX + "*"))
    assert keys == sorted([f"{SNAPSHOT_PREFIX}{LANE}",
                           f"{SNAPSHOT_PREFIX}{LANE}:t1",
                           f"{SNAPSHOT_PREFIX}{LANE}:t1:events",
                           f"{SNAPSHOT_PREFIX}{LANE}:t1:text",
                           f"{SNAPSHOT_PREFIX}{LANE}:t1:thinking"])
    for key in keys:
        assert 0 < await client.ttl(key) <= TTL, f"{key} has no expiry"


@pytestmark_redis
async def test_redis_turn_end_leaves_no_keys_behind():
    store = redis_store()
    seq = Seq()
    await store.record(seq.event("t1", "turn.start", {}))
    await store.record(seq.event("t1", "text.delta", {"delta": "Hi"}))
    await store.record(seq.event("t1", "turn.end", {"status": "ok"}))
    assert await store._client.keys(SNAPSHOT_PREFIX + "*") == []


@pytestmark_redis
async def test_redis_a_restarted_turn_does_not_keep_the_old_buffer():
    """A second `turn.start` for the same id restarts the turn — the same answer
    the emitter's seq counter gives. Without the LREM the id would be in the lane
    list twice and the snapshot would replay the turn twice."""
    store = redis_store()
    seq = Seq()
    await store.record(seq.event("t1", "turn.start", {}))
    await store.record(seq.event("t1", "text.delta", {"delta": "stale"}))
    await store.record(seq.event("t1", "turn.start", {}))
    snap = await store.read(LANE)
    assert [e.type for e in snap] == ["turn.start"]
    assert await store._client.lrange(f"{SNAPSHOT_PREFIX}{LANE}", 0, -1) == ["t1"]


@pytestmark_redis
async def test_redis_a_listed_turn_whose_keys_aged_out_is_not_running():
    """The killed-worker case: the lane list outlived the turn's own keys. Both
    `read` and `is_running` have to agree that there is nothing there."""
    store = redis_store()
    seq = Seq()
    await store.record(seq.event("t1", "turn.start", {}))
    await store._client.delete(f"{SNAPSHOT_PREFIX}{LANE}:t1",
                               f"{SNAPSHOT_PREFIX}{LANE}:t1:events")
    assert await store.read(LANE) == []
    assert await store.is_running(LANE) is False


@pytestmark_redis
async def test_redis_drops_a_snapshot_event_it_cannot_parse():
    """One corrupt key must not turn a browser reload into a 500."""
    store = redis_store()
    seq = Seq()
    await store.record(seq.event("t1", "turn.start", {}))
    await store._client.rpush(f"{SNAPSHOT_PREFIX}{LANE}:t1:events", "not json at all")
    await store.record(seq.event("t1", "text.delta", {"delta": "Hi"}))
    assert [e.type for e in await store.read(LANE)] == ["turn.start", "text.delta"]

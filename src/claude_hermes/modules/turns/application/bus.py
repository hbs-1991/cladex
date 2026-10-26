"""The turn-event bus: one typed stream per session lane.

The runner (and the cron ticker) emit events; the bus persists the durable ones
into `turn_events`, mirrors the turn in flight into a `SnapshotStore` so a late
subscriber (a browser that reloaded mid-turn) can catch up, and publishes every
event onto the lane's channel — in-process in inline mode, Redis pub/sub in redis
mode — where the SSE endpoint and the Telegram presenter pick it up.

Text and thinking deltas are transient — they reach listeners and the snapshot
buffer, never the database — so `turn_events` grows per block, not per token.

Three orderings in here are invariants, not preferences:

* **`emit` records into the snapshot store BEFORE it publishes.** Paired with
  `snapshot`'s `ready`-before-read below, that gives at-least-once delivery: after
  `snapshot()` returns, every event is either in what it returned or still on its
  way to a subscription that already exists. De-duplicating is the SSE stream's
  job (`presentation/sse.py` and its seq watermark).
* **`snapshot` awaits `publisher.ready(lane)` BEFORE reading the store.**
  `subscribe` is synchronous and only *schedules* the Redis SUBSCRIBE; reading the
  snapshot before the subscription is live leaves a window in which an event is in
  neither place.
* **The emitter owns `seq`.** `_Active` is kept here for the sequence number and
  nothing else — a turn has exactly one emitter, so the counter can stay local,
  while everything a *reader* needs lives in the store where another process can
  see it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import structlog

from claude_hermes.modules.turns.application.ports import (
    EventPublisher,
    SnapshotStore,
    TurnEventRepository,
)
from claude_hermes.modules.turns.domain.events import TRANSIENT, Listener, TurnEvent, _now

log = structlog.get_logger(__name__)

__all__ = ["TurnBus", "MemorySnapshots", "TRANSIENT", "TurnEvent", "Listener"]


@dataclass
class _Active:
    """The emitter's sequence counter for one turn. Nothing else: what a reader
    needs is in the `SnapshotStore`, which may be another process's Redis."""
    turn_id: str
    seq: int = 0


@dataclass
class _Snapshot:
    """One in-flight turn as a late subscriber needs to see it."""
    turn_id: str
    seq: int = 0
    events: list[TurnEvent] = field(default_factory=list)   # persisted, in order
    text: str = ""                                          # deltas since text.block
    thinking: str = ""                                      # deltas since thinking


class MemorySnapshots:
    """The in-flight mirror, in this process's memory. Satisfies `SnapshotStore`.

    Inline mode's store, and the default `TurnBus` builds when nobody injects one.
    `RedisSnapshots` is the same bookkeeping in Redis keys, which is what lets a
    browser attached to instance B catch up on a turn instance A is running.

    It lives in the application layer rather than next to `RedisSnapshots` in
    `infrastructure/` for one reason: `application` may not import
    `infrastructure` (import-linter, "turns layers"), and `TurnBus`'s two-argument
    constructor has to keep working. `infrastructure/snapshots.py` re-exports it,
    which is the name the composition root and the tests use.

    Keyed lane -> turn -> state, because a lane can carry two turns at once: a
    cron job delivers its report into the owner's private lane while a live turn
    runs there, and a single slot per lane let the delivery reset the live turn's
    seq, empty its snapshot and report the lane as idle.
    """

    def __init__(self) -> None:
        self._lanes: dict[str, dict[str, _Snapshot]] = {}

    async def record(self, ev: TurnEvent) -> None:
        lane = self._lanes.get(ev.session_key)
        if ev.type == "turn.start":
            lane = self._lanes.setdefault(ev.session_key, {})
            # A fresh state, not `setdefault`: a second `turn.start` for the same
            # id restarts the turn, exactly as the emitter's counter does.
            snap = lane[ev.turn_id] = _Snapshot(ev.turn_id)
        else:
            snap = lane.get(ev.turn_id) if lane is not None else None
        if snap is None:
            # An event for a turn whose `turn.start` this store never saw. The
            # emitter still sequences and publishes it; there is simply nothing
            # here for a late subscriber to catch up on.
            return
        # Every record, transient or not: the synthetic deltas `read` produces
        # carry this seq, and the SSE watermark is written against it.
        snap.seq = ev.seq
        if ev.type == "text.delta":
            snap.text += str(ev.payload.get("delta", ""))
        elif ev.type == "thinking.delta":
            snap.thinking += str(ev.payload.get("delta", ""))
        else:
            snap.events.append(ev)
            if ev.type == "text.block":
                snap.text = ""
            elif ev.type == "thinking":
                snap.thinking = ""
        if ev.type == "turn.end":
            await self.clear(ev.session_key, ev.turn_id)

    async def read(self, session_key: str) -> list[TurnEvent]:
        """Everything a late subscriber needs: the persisted events of every turn
        in flight, each followed by one synthetic thinking.delta and one text.delta
        carrying its not-yet-flushed reasoning and text, at the turn's latest seq.
        Turns come in the order they started; every event names its own turn."""
        out: list[TurnEvent] = []
        for snap in self._lanes.get(session_key, {}).values():
            out.extend(snap.events)
            if snap.thinking:
                out.append(TurnEvent(session_key, snap.turn_id, snap.seq, _now(),
                                     "thinking.delta", {"delta": snap.thinking}))
            if snap.text:
                out.append(TurnEvent(session_key, snap.turn_id, snap.seq, _now(),
                                     "text.delta", {"delta": snap.text}))
        return out

    async def is_running(self, session_key: str) -> bool:
        return bool(self._lanes.get(session_key))

    async def clear(self, session_key: str, turn_id: str) -> None:
        lane = self._lanes.get(session_key)
        if lane is None:
            return
        lane.pop(turn_id, None)
        if not lane:
            self._lanes.pop(session_key, None)


class TurnBus:
    def __init__(self, events: TurnEventRepository, publisher: EventPublisher,
                 snapshots: SnapshotStore | None = None):
        self._events = events
        self._publisher = publisher
        self._snapshots = MemorySnapshots() if snapshots is None else snapshots
        # lane -> turn_id -> sequence counter. Keyed by turn as well as lane
        # because a lane can carry two turns at once (see `MemorySnapshots`).
        self._active: dict[str, dict[str, _Active]] = {}

    # --- subscriptions ------------------------------------------------------

    def subscribe(self, session_key: str, listener: Listener):
        """Register a listener and return the unsubscribe callable.

        Synchronous, and only *schedules* the subscription in redis mode. A caller
        that must not miss the first event awaits `ready` (or `snapshot`, which
        does it for them) before reading any state.
        """
        return self._publisher.subscribe(session_key, listener)

    async def ready(self, session_key: str) -> None:
        """Resolve once this lane's subscription is live."""
        await self._publisher.ready(session_key)

    # --- active-turn state --------------------------------------------------

    async def is_running(self, session_key: str) -> bool:
        return await self._snapshots.is_running(session_key)

    async def snapshot(self, session_key: str) -> list[TurnEvent]:
        """The turn in flight, for a subscriber that arrived mid-turn.

        INVARIANT (ordering): `ready` first, store second. The emitter writes the
        snapshot before it publishes, so once the subscription is live every event
        is either in what this returns or will arrive on the subscription — never
        in neither.
        """
        await self._publisher.ready(session_key)
        return await self._snapshots.read(session_key)

    # --- emission -----------------------------------------------------------

    async def emit(self, session_key: str, turn_id: str, type: str,
                   payload: dict) -> TurnEvent:
        lane = self._active.get(session_key) or {}
        act = None if type == "turn.start" else lane.get(turn_id)
        if act is None:
            act = _Active(turn_id)
            if type == "turn.start":
                self._active.setdefault(session_key, lane)[turn_id] = act
        act.seq += 1
        ev = TurnEvent(session_key, turn_id, act.seq, _now(), type, payload)

        if type not in TRANSIENT:
            try:
                eid = await self._events.append(session_key, turn_id, act.seq, ev.ts,
                                                type, payload)
                ev = replace(ev, id=eid)
            except Exception:
                log.exception("turn event persistence failed (%s on %s)", type, session_key)

        if type == "turn.end":
            lane.pop(turn_id, None)
            if not lane:
                self._active.pop(session_key, None)

        # INVARIANT (ordering): mirror, then publish. A subscriber that is already
        # live gets the event; one that arrives a moment later reads it out of the
        # snapshot. Publishing first would leave a window where it is in neither.
        await self._snapshots.record(ev)
        await self._publisher.publish(ev)
        return ev

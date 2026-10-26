"""The launcher: every channel's entry to a turn that runs somewhere else.

The store is faked here on purpose — `tests/modules/turns/test_turns_repository.py`
is where the SQL claims are proved against a real PostgreSQL. What these tests pin
is the *sequencing* the launcher owns, and every case is one of the orderings the
plan calls an invariant: the row before the dispatch, the flag before the nudge,
`ready` before the row is read, and the bounded re-read after `turn.end` (which is
emitted from inside the turn body, before the row that carries the reply is
written).
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from claude_hermes.modules.turns.application.launcher import (
    LOST_ERROR,
    TurnLauncher,
)
from claude_hermes.modules.turns.domain.events import TurnEvent
from claude_hermes.modules.turns.domain.exceptions import LaneBusy, TurnFailed

LANE = "web:1:x"
T0 = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
ACTIVE = ("queued", "running")


async def until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return bool(predicate())


class FakeStore:
    """`TurnStore` in a dict, with the two rules the SQL encodes: one active row
    per lane, and a `queued` row that is cancelled is cancelled outright."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.calls: list[str] = []
        self.pruned: list[str] = []
        self.prune_result = 0

    async def create(self, turn_id, session_key, channel, user_id, text,
                     attachment_ids, now_iso, lease_until):
        self.calls.append("create")
        if any(r["session_key"] == session_key and r["status"] in ACTIVE
               for r in self.rows.values()):
            raise LaneBusy("the agent is busy in this thread")
        self.rows[turn_id] = {
            "id": turn_id, "session_key": session_key, "channel": channel,
            "user_id": user_id, "text": text, "attachment_ids": list(attachment_ids),
            "status": "queued", "created_at": now_iso, "started_at": None,
            "finished_at": None, "lease_until": lease_until, "worker_id": None,
            "cancel_requested_at": None, "reply": None, "error": None,
        }

    async def request_cancel(self, session_key, now_iso, turn_id=None):
        self.calls.append("request_cancel")
        for row in self.rows.values():
            if row["session_key"] != session_key or row["status"] not in ACTIVE:
                continue
            if turn_id is not None and row["id"] != turn_id:
                continue
            row["cancel_requested_at"] = now_iso
            if row["status"] == "queued":
                row["status"] = "cancelled"
                row["finished_at"] = now_iso
            return {"id": row["id"], "status": row["status"]}
        return None

    async def active(self, session_key):
        self.calls.append("active")
        for row in self.rows.values():
            if row["session_key"] == session_key and row["status"] in ACTIVE:
                return dict(row)
        return None

    async def expired(self, now_iso, limit=100):
        self.calls.append("expired")
        return [dict(r) for r in self.rows.values()
                if r["status"] in ACTIVE and r["lease_until"] < now_iso]

    async def fail_queued(self, turn_id, error, now_iso):
        self.calls.append("fail_queued")
        row = self.rows.get(turn_id)
        if row is None or row["status"] != "queued":
            return False
        row.update(status="error", finished_at=now_iso, error=error)
        return True

    async def mark_lost(self, turn_id, now_iso):
        self.calls.append("mark_lost")
        row = self.rows.get(turn_id)
        if row is None or row["status"] not in ACTIVE:
            return False
        row.update(status="lost", finished_at=now_iso, error=LOST_ERROR)
        return True

    async def prune(self, before_iso, limit=1000):
        self.calls.append("prune")
        self.pruned.append(before_iso)
        return self.prune_result

    async def get(self, turn_id):
        self.calls.append("get")
        row = self.rows.get(turn_id)
        return dict(row) if row else None

    # --- test helpers ----------------------------------------------------

    def put(self, turn_id, *, status="running", lane=LANE, reply=None, error=None,
            lease=300.0):
        self.rows[turn_id] = {
            "id": turn_id, "session_key": lane, "channel": "web", "user_id": 1,
            "text": "hi", "attachment_ids": [], "status": status,
            "created_at": T0.isoformat(), "started_at": T0.isoformat(),
            "finished_at": None,
            "lease_until": (T0 + timedelta(seconds=lease)).isoformat(),
            "worker_id": "w1", "cancel_requested_at": None, "reply": reply,
            "error": error,
        }
        return turn_id

    def settle(self, turn_id, status, *, reply=None, error=None):
        self.rows[turn_id].update(status=status, reply=reply, error=error,
                                  finished_at=T0.isoformat())


class FakeDispatcher:
    def __init__(self, error: Exception | None = None):
        self.dispatched: list[str] = []
        self._error = error

    async def dispatch(self, turn_id):
        if self._error is not None:
            raise self._error
        self.dispatched.append(turn_id)


class FakeBus:
    """Enough `TurnEventBusPort` for the launcher: subscribe, ready, emit."""

    def __init__(self):
        self.listeners: dict[str, list] = {}
        self.emitted: list[TurnEvent] = []
        self.order: list[str] = []
        # Unset means `ready` blocks — which is how a test proves the row is not
        # read before the subscription is live.
        self.gate = asyncio.Event()
        self.gate.set()

    def subscribe(self, session_key, listener):
        self.order.append("subscribe")
        self.listeners.setdefault(session_key, []).append(listener)

        def unsubscribe():
            self.order.append("unsubscribe")
            self.listeners[session_key].remove(listener)

        return unsubscribe

    async def ready(self, session_key):
        await self.gate.wait()
        self.order.append("ready")

    async def emit(self, session_key, turn_id, type, payload):
        event = TurnEvent(session_key, turn_id, len(self.emitted) + 1,
                          T0.isoformat(), type, payload)
        self.emitted.append(event)
        for listener in list(self.listeners.get(session_key, ())):
            await listener(event)
        return event


class FakeControl:
    def __init__(self, error: Exception | None = None):
        self.nudged: list[str] = []
        self._error = error

    async def nudge_cancel(self, turn_id):
        if self._error is not None:
            raise self._error
        self.nudged.append(turn_id)


def make(store=None, dispatcher=None, bus=None, control=None, **kw):
    launcher = TurnLauncher(store or FakeStore(), dispatcher or FakeDispatcher(),
                            bus or FakeBus(), control or FakeControl(), **kw)
    launcher.SETTLE_INTERVAL = 0.005          # instance attribute: tests only
    return launcher


class Uploaded:
    """Anything with a `file_id` is an attachment as far as the launcher cares."""

    def __init__(self, file_id=None, filename="a.png"):
        self.file_id = file_id
        self.filename = filename


# --- start ------------------------------------------------------------------


async def test_start_writes_the_row_then_dispatches_the_id():
    store, dispatcher = FakeStore(), FakeDispatcher()
    turns = make(store, dispatcher, queue_timeout_seconds=120.0)

    turn_id = await turns.start(LANE, "hello", user_id=7,
                                attachments=[Uploaded("f1")], channel="telegram")

    row = store.rows[turn_id]
    assert (row["status"], row["text"], row["channel"]) == ("queued", "hello",
                                                            "telegram")
    assert row["attachment_ids"] == ["f1"]
    # The lease on a queued row is the queue timeout: a task that vanished must
    # not hold the lane forever.
    assert (datetime.fromisoformat(row["lease_until"])
            - datetime.fromisoformat(row["created_at"])) == timedelta(seconds=120)
    assert dispatcher.dispatched == [turn_id]
    # The row exists before the queue is told about it — the other order is a
    # worker claiming a row that is not there yet.
    assert store.calls.index("create") == 0


async def test_an_attachment_with_no_id_is_dropped_rather_than_queued_as_none():
    """The legacy Telegram path writes bytes into the lane directory and
    registers nothing. There is no id for another process to resolve, so queueing
    a `None` would fail the whole turn on the worker."""
    store = FakeStore()
    turn_id = await make(store).start(LANE, "hi", attachments=[Uploaded(),
                                                              Uploaded("f2")])
    assert store.rows[turn_id]["attachment_ids"] == ["f2"]


async def test_a_busy_lane_refuses_the_second_start():
    """`LaneBusy` comes from the store (the unique index in production) and
    propagates untouched: the router maps it to the same 409 it always answered."""
    store = FakeStore()
    turns = make(store)
    await turns.start(LANE, "one")
    with pytest.raises(LaneBusy):
        await turns.start(LANE, "two")


async def test_a_dispatch_failure_ends_the_row_and_frees_the_lane():
    """A `queued` row nobody will ever claim holds the lane until its lease
    lapses — minutes of a dead thread for an error the caller already saw."""
    store = FakeStore()
    dispatcher = FakeDispatcher(RuntimeError("no broker"))
    turns = make(store, dispatcher)

    with pytest.raises(RuntimeError):
        await turns.start(LANE, "hi")

    [row] = list(store.rows.values())
    assert row["status"] not in ACTIVE and row["finished_at"] is not None
    # `error`, not `lost`: no worker was ever handed this turn, and the row is
    # the only place the real cause survives once the exception has been raised.
    assert row["status"] == "error"
    assert "no broker" in row["error"]
    assert "mark_lost" not in store.calls
    assert await turns.is_busy(LANE) is False
    # …and the lane really is reusable, which is the point of ending the row.
    dispatcher._error = None
    await turns.start(LANE, "again")


async def test_is_busy_reads_the_row():
    store = FakeStore()
    turns = make(store)
    assert await turns.is_busy(LANE) is False
    turn_id = await turns.start(LANE, "hi")
    assert await turns.is_busy(LANE) is True
    store.settle(turn_id, "ok", reply="done")
    assert await turns.is_busy(LANE) is False


# --- cancel -----------------------------------------------------------------


async def test_cancel_flags_the_row_before_it_nudges():
    """The flag is the truth and the nudge is the latency. The other order means a
    worker that acts on the nudge and then heartbeats a row with no flag on it."""
    store, control = FakeStore(), FakeControl()
    turns = make(store, control=control)
    turn_id = store.put("t1")

    assert await turns.cancel(LANE) is True
    assert store.calls == ["request_cancel"]           # before the nudge
    assert control.nudged == [turn_id]
    assert store.rows[turn_id]["cancel_requested_at"] is not None
    assert store.rows[turn_id]["status"] == "running"  # the worker owns the ending


async def test_cancelling_a_queued_turn_ends_it_outright_and_nudges_nobody():
    """`request_cancel`'s CASE is what makes this one statement: the turn never
    ran, so there is no task anywhere to interrupt."""
    store, control = FakeStore(), FakeControl()
    turns = make(store, control=control)
    turn_id = await turns.start(LANE, "hi")

    assert await turns.cancel(LANE) is True
    assert store.rows[turn_id]["status"] == "cancelled"
    assert control.nudged == []


async def test_cancel_reports_false_when_the_lane_is_idle():
    assert await make().cancel(LANE) is False


async def test_a_nudge_that_cannot_be_published_still_leaves_the_flag_set():
    """The heartbeat sees `cancel_requested_at` within a third of a lease, so a
    dead pub/sub connection is slow, not wrong."""
    store = FakeStore()
    turns = make(store, control=FakeControl(RuntimeError("no redis")))
    turn_id = store.put("t1")

    assert await turns.cancel(LANE) is True
    assert store.rows[turn_id]["cancel_requested_at"] is not None


async def test_cancel_can_name_the_turn_it_means():
    store, control = FakeStore(), FakeControl()
    turns = make(store, control=control)
    store.put("t1")

    assert await turns.cancel(LANE, turn_id="t-other") is False
    assert await turns.cancel(LANE, turn_id="t1") is True
    assert control.nudged == ["t1"]


# --- wait -------------------------------------------------------------------


async def test_wait_returns_immediately_for_a_row_that_is_already_finished():
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="ok", reply="done")
    assert await make(store, bus=bus).wait("t1", LANE) == "done"
    assert bus.order == ["subscribe", "ready", "unsubscribe"]


async def test_wait_awaits_ready_before_it_reads_the_row():
    """`subscribe` only SCHEDULES the subscription in redis mode. Reading the row
    first leaves a window in which the turn ends in neither the read nor the
    subscription — and `wait` would then sit out a whole poll."""
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="ok", reply="done")
    bus.gate.clear()
    turns = make(store, bus=bus)

    waiter = asyncio.create_task(turns.wait("t1", LANE))
    await asyncio.sleep(0.02)
    assert "get" not in store.calls, "the row was read before the subscription was live"
    bus.gate.set()
    assert await asyncio.wait_for(waiter, 2) == "done"


async def test_wait_rereads_after_turn_end_because_the_row_commits_later():
    """`turn.end` is emitted from inside the turn body; `TurnRunService` writes
    the row after that body returns. Without the bounded re-read the Telegram
    reply is one 15-second poll late about half the time."""
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="running")
    turns = make(store, bus=bus)

    waiter = asyncio.create_task(turns.wait("t1", LANE, poll_seconds=30.0))
    assert await until(lambda: bus.listeners.get(LANE))
    await bus.emit(LANE, "t1", "turn.end", {"status": "ok"})
    await asyncio.sleep(0.02)                  # still running: the re-read spins
    assert not waiter.done()
    store.settle("t1", "ok", reply="late but here")

    assert await asyncio.wait_for(waiter, 2) == "late but here"


async def test_wait_falls_back_to_the_poll_when_the_event_never_arrives():
    """The poll is the fallback for a lost pub/sub message, not the primary path
    — but it is the one that has to work when the message is gone."""
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="running")
    turns = make(store, bus=bus)

    waiter = asyncio.create_task(turns.wait("t1", LANE, poll_seconds=0.01))
    await asyncio.sleep(0.03)
    store.settle("t1", "ok", reply="found by polling")
    assert await asyncio.wait_for(waiter, 2) == "found by polling"


async def test_wait_ignores_another_turns_end_on_the_same_lane():
    """A cron delivery ends on the owner's private lane while a live turn runs
    there. Waking on it would make `wait` re-read a row that is still running."""
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="running")
    turns = make(store, bus=bus)

    waiter = asyncio.create_task(turns.wait("t1", LANE, poll_seconds=30.0))
    assert await until(lambda: bus.listeners.get(LANE))
    await bus.emit(LANE, "other", "turn.end", {"status": "ok"})
    await asyncio.sleep(0.05)
    assert not waiter.done()

    waiter.cancel()


async def test_wait_returns_none_for_a_cancelled_turn():
    """None, not an exception: the user stopped it on purpose and the Telegram
    dispatcher's `if reply:` is what skips the final message."""
    store = FakeStore()
    store.put("t1", status="cancelled")
    assert await make(store).wait("t1", LANE) is None


@pytest.mark.parametrize("status", ["error", "lost"])
async def test_wait_raises_turn_failed_with_the_rows_own_message(status):
    store = FakeStore()
    store.put("t1", status=status, error="boom")
    with pytest.raises(TurnFailed, match="boom"):
        await make(store).wait("t1", LANE)


async def test_wait_unsubscribes_even_when_it_raises():
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="error", error="boom")
    with pytest.raises(TurnFailed):
        await make(store, bus=bus).wait("t1", LANE)
    assert bus.listeners[LANE] == []


# --- reap -------------------------------------------------------------------


async def test_reap_marks_the_turn_lost_and_publishes_the_frozen_end_payload():
    """The lane comes back and every subscriber — an SSE stream on another
    instance included — learns the turn is over. The payload carries the same keys
    a real end does, so no client needs a special case — the model keys included,
    as nulls: nothing here knows what the lost worker was running."""
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="running", lease=10.0)
    turns = make(store, bus=bus)

    assert await turns.reap(T0 + timedelta(seconds=60)) == 1

    assert store.rows["t1"]["status"] == "lost"
    assert store.rows["t1"]["error"] == LOST_ERROR
    [event] = bus.emitted
    assert (event.type, event.turn_id, event.session_key) == ("turn.end", "t1", LANE)
    assert event.payload == {"status": "error", "cost_usd": None,
                             "sdk_session_id": None, "error": LOST_ERROR,
                             "usage": None, "model": None, "effort": None,
                             "fallback_from": None}
    assert await turns.is_busy(LANE) is False


async def test_a_queued_turn_nobody_claimed_is_reaped_the_same_way():
    """A queue message that vanished must not hold a lane forever."""
    store, bus = FakeStore(), FakeBus()
    turns = make(store, bus=bus, queue_timeout_seconds=30.0)
    turn_id = await turns.start(LANE, "hi")
    store.rows[turn_id]["created_at"] = T0.isoformat()
    store.rows[turn_id]["lease_until"] = (T0 + timedelta(seconds=30)).isoformat()

    assert await turns.reap(T0 + timedelta(seconds=31)) == 1
    assert store.rows[turn_id]["status"] == "lost"


async def test_reap_leaves_a_turn_whose_lease_is_still_good():
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="running", lease=300.0)
    assert await make(store, bus=bus).reap(T0 + timedelta(seconds=60)) == 0
    assert store.rows["t1"]["status"] == "running"
    assert bus.emitted == []


async def test_reap_skips_the_turn_a_worker_finished_between_the_read_and_the_write():
    """`mark_lost` matches an active row; losing that race means the lane is
    already free and nothing was ever wrong — so no synthetic end is published
    over a turn that really did finish."""
    store, bus = FakeStore(), FakeBus()
    store.put("t1", status="running", lease=10.0)

    async def finish_first(turn_id, now_iso):
        store.settle("t1", "ok", reply="made it")
        return await FakeStore.mark_lost(store, turn_id, now_iso)

    store.mark_lost = finish_first
    assert await make(store, bus=bus).reap(T0 + timedelta(seconds=60)) == 0
    assert bus.emitted == []


async def test_reap_prunes_the_retention_window_and_survives_a_prune_failure():
    store, bus = FakeStore(), FakeBus()
    turns = make(store, bus=bus, retention_days=7)

    await turns.reap(T0)
    assert store.pruned == [(T0 - timedelta(days=7)).isoformat()]

    async def boom(before_iso, limit=1000):
        raise RuntimeError("no")

    store.prune = boom
    store.put("t1", status="running", lease=1.0)
    # Pruning is housekeeping: failing it must not cost the reaping that shares
    # the pass.
    assert await turns.reap(T0 + timedelta(seconds=60)) == 1


async def test_wait_on_a_row_that_does_not_exist_fails_instead_of_polling_forever():
    """`start` committed the row before this call and retention is measured in
    days, so there is no legitimate way here — and an unbounded wait is a Telegram
    handler that never answers."""
    with pytest.raises(TurnFailed):
        await make().wait("t-missing", LANE, poll_seconds=0.01)

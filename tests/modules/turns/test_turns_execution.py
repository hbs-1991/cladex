"""The worker's half: claim a row, run the turn, write the end — and every way
that can go wrong while another process is watching the same row.

The store is faked (the SQL claims live in `test_turns_repository.py`); what is
pinned here is what the run service decides. Each case is one of the failures the
plan names: a second cancel that would abort the terminal write, a reaped row the
worker must not resurrect, a heartbeat that raises and then a lease that lapses,
a forged attachment id pointing at another lane — and the envelope a turn that
failed before its body ran still owes whoever is watching the lane over SSE.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from claude_hermes.modules.turns.application.control import CancelRegistry
from claude_hermes.modules.turns.application.execution import (
    TurnRunService,
    worker_identity,
)

LANE = "web:1:x"
T0 = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
ACTIVE = ("queued", "running")
WORKER = "worker-1"


async def until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return bool(predicate())


class FakeStore:
    """`TurnStore` with the two guards that matter here: `claim` matches `queued`
    only, and `heartbeat`/`finish` match this worker's own `running` row."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.heartbeats = 0
        self.heartbeat_error: Exception | None = None

    def put(self, turn_id="t1", *, lane=LANE, status="queued", text="hi",
            attachment_ids=(), channel="web", user_id=7):
        self.rows[turn_id] = {
            "id": turn_id, "session_key": lane, "channel": channel,
            "user_id": user_id, "text": text,
            "attachment_ids": list(attachment_ids), "status": status,
            "created_at": T0.isoformat(), "started_at": None, "finished_at": None,
            "lease_until": (T0 + timedelta(seconds=300)).isoformat(),
            "worker_id": None, "cancel_requested_at": None, "reply": None,
            "error": None,
        }
        return turn_id

    async def claim(self, turn_id, worker_id, now_iso, lease_until):
        row = self.rows.get(turn_id)
        if row is None or row["status"] != "queued":
            return None
        row.update(status="running", started_at=now_iso, worker_id=worker_id,
                   lease_until=lease_until)
        return dict(row)

    async def heartbeat(self, turn_id, worker_id, lease_until):
        self.heartbeats += 1
        if self.heartbeat_error is not None:
            raise self.heartbeat_error
        row = self.rows.get(turn_id)
        if row is None or row["worker_id"] != worker_id or row["status"] != "running":
            return None
        row["lease_until"] = lease_until
        return {"cancel_requested_at": row["cancel_requested_at"]}

    async def finish(self, turn_id, worker_id, status, *, reply=None, error=None,
                     now_iso):
        row = self.rows.get(turn_id)
        if row is None or row["worker_id"] != worker_id or row["status"] != "running":
            return False
        row.update(status=status, reply=reply, error=error, finished_at=now_iso)
        return True

    async def get(self, turn_id):
        row = self.rows.get(turn_id)
        return dict(row) if row else None

    # --- what another process does to the row while we run ---------------

    def flag_cancel(self, turn_id):
        self.rows[turn_id]["cancel_requested_at"] = T0.isoformat()

    def reap(self, turn_id):
        self.rows[turn_id].update(status="lost", finished_at=T0.isoformat(),
                                  error="worker lost: lease expired")


class FakeRunner:
    """`run_turn` as the run service sees it: a coroutine that can be cancelled."""

    def __init__(self, reply="done", error: Exception | None = None,
                 block: bool = False):
        self.calls: list[dict] = []
        self.cancelled = False
        self.started = asyncio.Event()
        self._reply = reply
        self._error = error
        self._block = block

    async def run_turn(self, session_key, text, *, user_id=0, attachments=None,
                       channel="telegram", turn_id=None):
        self.calls.append({"session_key": session_key, "text": text,
                           "user_id": user_id, "attachments": list(attachments or []),
                           "channel": channel, "turn_id": turn_id})
        self.started.set()
        if self._error is not None:
            raise self._error
        if self._block:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return self._reply


class FakeFiles:
    def __init__(self, records=()):
        self.records = {r["id"]: r for r in records}

    async def get(self, file_id):
        return self.records.get(file_id)

    async def resolve(self, record):
        return Path(record["path"])


def record(file_id="f1", lane=LANE, name="a.png", media_type="image/png"):
    return {"id": file_id, "session_key": lane, "name": name,
            "media_type": media_type, "path": f"/tmp/{name}"}


class FakeBus:
    """`TurnEventBusPort` reduced to what the run service uses: the envelope a
    turn that never reached its body would otherwise never get."""

    def __init__(self):
        self.events: list[tuple[str, str, str, dict]] = []

    async def emit(self, session_key, turn_id, type, payload):
        self.events.append((session_key, turn_id, type, payload))
        return None


def make(store, runner=None, files=None, registry=None, **kw):
    kw.setdefault("lease_seconds", 300.0)
    return TurnRunService(store, runner or FakeRunner(), files,
                          registry or CancelRegistry(), worker_id=WORKER, **kw)


# --- the happy path ---------------------------------------------------------


async def test_a_claimed_turn_runs_and_the_row_carries_the_reply():
    store, runner = FakeStore(), FakeRunner("the answer")
    store.put("t1", text="ping", channel="telegram", user_id=42)

    await make(store, runner).run("t1")

    assert runner.calls == [{"session_key": LANE, "text": "ping", "user_id": 42,
                             "attachments": [], "channel": "telegram",
                             "turn_id": "t1"}]
    row = store.rows["t1"]
    assert (row["status"], row["reply"], row["worker_id"]) == ("ok", "the answer",
                                                               WORKER)
    assert row["finished_at"] is not None


async def test_an_unclaimable_id_is_a_no_op():
    """Cancelled before pickup, reaped, or already claimed. The queue is
    at-least-once by design and the row is the arbiter — this is not an error."""
    store, runner = FakeStore(), FakeRunner()
    store.put("t1", status="cancelled")

    await make(store, runner).run("t1")

    assert runner.calls == []
    assert store.rows["t1"]["status"] == "cancelled"


async def test_an_id_that_is_not_a_row_at_all_is_a_no_op():
    store, runner = FakeStore(), FakeRunner()
    await make(store, runner).run("nope")
    assert runner.calls == []


async def test_a_turn_that_raises_is_recorded_as_error_with_its_message():
    store, runner = FakeStore(), FakeRunner(error=RuntimeError("the CLI died"))
    store.put("t1")

    await make(store, runner).run("t1")

    row = store.rows["t1"]
    assert row["status"] == "error" and "the CLI died" in row["error"]


# --- cancelling -------------------------------------------------------------


async def test_a_cancel_nudge_mid_run_ends_the_turn_cancelled():
    store, runner = FakeStore(), FakeRunner(block=True)
    registry = CancelRegistry()
    store.put("t1")

    task = asyncio.create_task(make(store, runner, registry=registry).run("t1"))
    await asyncio.wait_for(runner.started.wait(), 2)
    assert registry.cancel("t1") is True
    await asyncio.wait_for(task, 2)

    assert runner.cancelled is True
    assert store.rows["t1"]["status"] == "cancelled"


async def test_a_second_cancel_cannot_abort_the_terminal_write():
    """The registry is idempotent and the write is shielded, and both are needed:
    the heartbeat keeps seeing `cancel_requested_at` and would cancel again a
    third of a lease later, landing on the write's `await`."""
    store, runner = FakeStore(), FakeRunner(block=True)
    registry = CancelRegistry()
    store.put("t1")

    task = asyncio.create_task(make(store, runner, registry=registry).run("t1"))
    await asyncio.wait_for(runner.started.wait(), 2)
    registry.cancel("t1")
    assert registry.cancel("t1") is False          # the second one is a no-op
    await asyncio.wait_for(task, 2)

    assert store.rows["t1"]["status"] == "cancelled"
    assert store.rows["t1"]["finished_at"] is not None


async def test_the_heartbeat_sees_the_cancel_flag_when_the_nudge_was_lost():
    """The fallback path: no nudge ever arrives, and the same statement that
    extends the lease reports the flag."""
    store, runner = FakeStore(), FakeRunner(block=True)
    store.put("t1")
    service = make(store, runner, lease_seconds=0.06)

    task = asyncio.create_task(service.run("t1"))
    await asyncio.wait_for(runner.started.wait(), 2)
    store.flag_cancel("t1")
    await asyncio.wait_for(task, 3)

    assert runner.cancelled is True
    assert store.rows["t1"]["status"] == "cancelled"


# --- the row going away underneath ------------------------------------------


async def test_a_reaped_row_stops_the_run_and_the_refused_finish_leaves_it_alone():
    """`finish` is guarded by `worker_id` AND by `status='running'`, so a worker
    whose turn was reaped cannot flip the row back. The refusal is logged; what is
    asserted here is that the row keeps what the reaper wrote."""
    store, runner = FakeStore(), FakeRunner(block=True)
    store.put("t1")
    service = make(store, runner, lease_seconds=0.06)

    task = asyncio.create_task(service.run("t1"))
    await asyncio.wait_for(runner.started.wait(), 2)
    store.reap("t1")
    await asyncio.wait_for(task, 3)

    row = store.rows["t1"]
    assert row["status"] == "lost" and row["reply"] is None
    assert row["error"] == "worker lost: lease expired"


async def test_a_heartbeat_exception_is_retried_on_the_next_interval():
    """A database blip must not end a turn: the next interval tries again, and a
    successful extension pushes the lease back out."""
    store, runner = FakeStore(), FakeRunner(block=True)
    store.put("t1")
    store.heartbeat_error = RuntimeError("db is away")
    service = make(store, runner, lease_seconds=0.6)      # interval 0.2

    task = asyncio.create_task(service.run("t1"))
    assert await until(lambda: store.heartbeats >= 1)
    store.heartbeat_error = None                          # the blip passes
    assert await until(lambda: store.heartbeats >= 2)

    assert store.rows["t1"]["status"] == "running"        # the turn survived it
    assert runner.cancelled is False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_lease_that_lapses_locally_makes_the_worker_cancel_its_own_turn():
    """The worker kills itself before the reaper has to be wrong about it: a
    process that cannot prove it is alive is one whose lane is about to be freed
    by somebody else, and two SDK loops on one lane is the thing the whole lease
    exists to prevent."""
    store, runner = FakeStore(), FakeRunner(block=True)
    store.put("t1")
    store.heartbeat_error = RuntimeError("db is away")
    service = make(store, runner, lease_seconds=0.09)

    task = asyncio.create_task(service.run("t1"))
    await asyncio.wait_for(runner.started.wait(), 2)
    await asyncio.wait_for(task, 3)

    assert store.heartbeats >= 1
    assert runner.cancelled is True
    assert store.rows["t1"]["status"] == "cancelled"


async def test_a_long_turn_keeps_its_lease_pushed_out():
    store, runner = FakeStore(), FakeRunner(block=True)
    store.put("t1")
    service = make(store, runner, lease_seconds=0.06)

    task = asyncio.create_task(service.run("t1"))
    assert await until(lambda: store.heartbeats >= 2)
    assert store.rows["t1"]["status"] == "running"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


# --- attachments ------------------------------------------------------------


async def test_attachments_are_re_resolved_against_the_rows_lane():
    store = FakeStore()
    store.put("t1", attachment_ids=["f1"])
    runner = FakeRunner()

    await make(store, runner, files=FakeFiles([record("f1")])).run("t1")

    [attachment] = runner.calls[0]["attachments"]
    assert (attachment.file_id, attachment.filename) == ("f1", "a.png")
    assert attachment.is_image is True
    assert store.rows["t1"]["status"] == "ok"


@pytest.mark.parametrize("files", [
    pytest.param(FakeFiles([record("f1", lane="web:1:other")]), id="another lane"),
    pytest.param(FakeFiles(), id="no such file"),
])
async def test_an_attachment_from_another_lane_fails_the_turn(files):
    """The row is not an authorization: anything that can write the table can name
    a file id, so the lane check is re-applied on the worker — the same refusal
    the web router applies before the id ever reaches a row."""
    store, runner = FakeStore(), FakeRunner()
    store.put("t1", attachment_ids=["f1"])

    await make(store, runner, files=files).run("t1")

    assert runner.calls == []                     # the turn never ran
    assert store.rows["t1"]["status"] == "error"
    assert "f1" in store.rows["t1"]["error"]


# --- the envelope of a turn that never reached its body ----------------------


async def test_a_failure_before_the_body_still_emits_a_start_and_an_end():
    """`turn.start` is the first thing the turn body emits, so a setup failure
    (a forged attachment id, a blob store that is away) used to end the turn with
    a row and NO event: Telegram's `wait` reads the row, but a browser watching
    the lane over SSE reads only events and would wait forever.
    """
    store, runner, bus = FakeStore(), FakeRunner(), FakeBus()
    store.put("t1", attachment_ids=["f1"], channel="web", user_id=7)

    await make(store, runner, files=FakeFiles(), bus=bus).run("t1")

    assert [(type, payload) for _, _, type, payload in bus.events] == [
        ("turn.start", {"channel": "web", "user_id": 7,
                        "model": None, "effort": None}),
        # The model keys as nulls on both: this turn never reached the resolver,
        # and a consumer must not need a special case for a synthetic envelope.
        ("turn.end", {"status": "error", "cost_usd": None, "sdk_session_id": None,
                      "error": "unknown attachment f1", "usage": None,
                      "model": None, "effort": None, "fallback_from": None}),
    ]
    assert {turn_id for _, turn_id, _, _ in bus.events} == {"t1"}
    assert {lane for lane, _, _, _ in bus.events} == {LANE}
    assert store.rows["t1"]["status"] == "error"


async def test_the_synthetic_end_is_emitted_before_the_row_is_written():
    """Same order as every other end: the event, then the row. `TurnLauncher.wait`
    re-reads the row after a `turn.end` precisely because of it."""
    store, runner, bus = FakeStore(), FakeRunner(), FakeBus()
    store.put("t1", attachment_ids=["f1"])
    order: list[str] = []

    async def emit(session_key, turn_id, type, payload):
        order.append(type)
        return None

    bus.emit = emit
    original = store.finish

    async def finish(*a, **kw):
        order.append("row")
        return await original(*a, **kw)

    store.finish = finish

    await make(store, runner, files=FakeFiles(), bus=bus).run("t1")

    assert order == ["turn.start", "turn.end", "row"]


async def test_a_bus_that_is_down_never_changes_what_the_row_says():
    class Broken(FakeBus):
        async def emit(self, session_key, turn_id, type, payload):
            raise RuntimeError("the bus is away")

    store, runner = FakeStore(), FakeRunner()
    store.put("t1", attachment_ids=["f1"])

    await make(store, runner, files=FakeFiles(), bus=Broken()).run("t1")

    assert store.rows["t1"]["status"] == "error"


async def test_a_failure_inside_the_body_is_not_announced_twice():
    """The body emitted its own `turn.end`; a second one here would be a
    duplicated end on every lane a turn ever fails on."""
    store, bus = FakeStore(), FakeBus()
    runner = FakeRunner(error=RuntimeError("the CLI died"))
    store.put("t1")

    await make(store, runner, bus=bus).run("t1")

    assert bus.events == []
    assert store.rows["t1"]["status"] == "error"


async def test_a_swallowed_cancel_does_not_leave_the_task_marked_cancelling():
    """`run` swallows the cancel — it IS the end of the turn. Leaving
    `Task.cancelling()` raised would make the next `asyncio.timeout` in the
    taskiq frame above read our cancel as a cancel of its own wait."""
    store, runner = FakeStore(), FakeRunner(block=True)
    registry = CancelRegistry()
    store.put("t1")

    task = asyncio.create_task(make(store, runner, registry=registry).run("t1"))
    await asyncio.wait_for(runner.started.wait(), 2)
    registry.cancel("t1")
    await asyncio.wait_for(task, 2)

    assert task.cancelling() == 0
    assert store.rows["t1"]["status"] == "cancelled"


# --- identity ---------------------------------------------------------------


def test_the_worker_id_names_the_process():
    host, _, pid = worker_identity().rpartition(":")
    assert host and pid.isdigit()


# --- whose workspace (Wave 4a, D6) ------------------------------------------


class FakeResolver:
    """`WorkspaceResolverPort` reduced to what the run service asks of it. The
    real one applies `lane_owner` to the key; this one records what it was asked
    so the test can pin that the ROW — not a default — decided."""

    def __init__(self, answer="ws", error: Exception | None = None):
        self.asked: list[tuple] = []
        self._answer = answer
        self._error = error

    async def for_lane(self, session_key, channel, user_id):
        self.asked.append((session_key, channel, user_id))
        if self._error is not None:
            raise self._error
        return self._answer


class WorkspaceRunner(FakeRunner):
    """A runner on the Wave 4a head: it accepts `workspace=` and records it."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.workspaces: list = []

    async def run_turn(self, session_key, text, *, user_id=0, attachments=None,
                       channel="telegram", turn_id=None, workspace=None):
        self.workspaces.append(workspace)
        return await super().run_turn(
            session_key, text, user_id=user_id, attachments=attachments,
            channel=channel, turn_id=turn_id)


async def test_the_lane_owners_workspace_is_resolved_from_the_row():
    """The row is the only thing that knows the key, the channel and the sender
    at the same time, so this is where a turn's workspace is decided — once."""
    store, runner, resolver = FakeStore(), WorkspaceRunner(), FakeResolver()
    store.put("t1", lane="tg:private:9", channel="telegram", user_id=9)

    await make(store, runner, workspaces=resolver).run("t1")

    assert resolver.asked == [("tg:private:9", "telegram", 9)]
    assert runner.workspaces == ["ws"]


async def test_without_a_resolver_the_keyword_is_not_passed_at_all():
    """`gateway.telegram` and the five runner test modules drive a `run_turn`
    that has never heard of `workspace=`; passing it only when there IS a
    resolver is what keeps them working."""
    store, runner = FakeStore(), FakeRunner()
    store.put("t1")

    await make(store, runner).run("t1")

    assert runner.calls[0]["turn_id"] == "t1"     # the legacy head ran


async def test_an_unresolvable_owner_ends_the_turn_rather_than_guessing():
    """A lane whose owner has no `users` row must not run somewhere arbitrary.
    The failure surfaces as the turn's own error, which the user can see."""
    store = FakeStore()
    resolver = FakeResolver(error=RuntimeError("unknown user"))
    store.put("t1")

    await make(store, WorkspaceRunner(), workspaces=resolver).run("t1")

    assert store.rows["t1"]["status"] == "error"
    assert "unknown user" in store.rows["t1"]["error"]

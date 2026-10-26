"""The worker's half of a turn: claim the row, run the SDK loop, write the end.

`TurnLauncher` puts a turn on the queue; this is what the queued task calls. It is
the only place that owns a `turns` row while the turn runs, and everything in it
exists so that two processes can never disagree about who that is:

* **`claim` matches `queued` only.** A `running` row with a lapsed lease belongs
  to the reaper, never to a second execution — re-running an SDK loop that may
  have half-completed doubles its side effects and its cost.
* **Every terminal write carries `worker_id` and is shielded.** `worker_id`
  stops a worker whose turn was reaped from flipping the row back to `ok`;
  `asyncio.shield` stops a second cancel from aborting the write itself, which is
  how a cancelled turn used to end with no row state at all.
* **The heartbeat retries, and the worker kills its own turn before the reaper
  has to be wrong about it.** A heartbeat that raises is logged and tried again
  on the next interval; if the local clock passes the lease without a successful
  extension the turn is cancelled here (`turn_lease_lost`). The reaper is the
  backstop for a process that is gone, not for one that is merely slow.
* **The row is not an authorization.** Anything that can write the table can name
  a file id, so every attachment is re-resolved against the row's own lane —
  the same refusal the web router applies before an id ever reaches a row.
* **The workspace is resolved here, once.** Since Wave 4a the row's key, channel
  and sender decide whose tree the turn runs in (`WorkspaceResolverPort.for_lane`,
  the `lane_owner` rule), and the answer travels down on `TurnContext`. Deciding
  it anywhere lower would mean deciding it twice, and two answers to "whose files
  are these" is the whole failure mode this wave exists to close.
* **A failure BEFORE the turn body still gets an envelope.** `turn.start` is the
  first thing `TurnService._run_turn_locked` emits, so a blob fetch or a forged
  attachment id that fails here would end the turn with a row nobody watching the
  lane can see: Telegram's `wait` reads the row, but the web SSE stream reads
  only events. So this file synthesizes `turn.start` + `turn.end {error}` in that
  case, the same shape the reaper's synthetic end uses, and emits it BEFORE the
  terminal row write — the order every other end follows.

`run_turn` on the runner still means "run this turn here, now": the SDK loop, the
event envelope and the lane's in-process lock are unchanged, and the five runner
test modules drive it directly.
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from datetime import datetime, timedelta, timezone

import structlog

from claude_hermes.core.contracts import IMAGE_TYPES, Attachment
from claude_hermes.modules.turns.domain.events import NO_MODEL_END_KEYS

from .control import CancelRegistry
from .ports import (
    FilePublisher,
    TurnEventBusPort,
    TurnStore,
    WorkspaceResolverPort,
)

log = structlog.get_logger(__name__)

__all__ = ["TurnRunService", "worker_identity"]


def worker_identity() -> str:
    """Who this process is on the `turns` row. Host plus pid: unique per running
    process, stable for its lifetime, and readable in a log line next to the
    container it came from."""
    return f"{socket.gethostname()}:{os.getpid()}"


class TurnRunService:
    """Runs one claimed turn. One instance per process; `run(turn_id)` is what the
    `run_turn` task body calls."""

    def __init__(self, store: TurnStore, runner, files: FilePublisher | None,
                 registry: CancelRegistry, *, lease_seconds: float = 60.0,
                 worker_id: str | None = None,
                 bus: TurnEventBusPort | None = None,
                 workspaces: WorkspaceResolverPort | None = None) -> None:
        self._store = store
        self._runner = runner
        self._files = files
        self._registry = registry
        self._lease_seconds = float(lease_seconds)
        self._worker_id = worker_id or worker_identity()
        # Only for the envelope a pre-body failure would otherwise never get; the
        # turn's own events come from `TurnService`, which owns the sequence.
        self._bus = bus
        # Wave 4a: THE place a turn's workspace is decided. This object holds the
        # row, so it is the one thing that knows the key, the channel and the
        # sender at the same time; everything below it is handed the answer.
        # `None` leaves `run_turn` without a workspace, where the runner falls back
        # to the deploy owner's tree.
        self._workspaces = workspaces

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def run(self, turn_id: str) -> None:
        now = datetime.now(timezone.utc)
        row = await self._store.claim(turn_id, self._worker_id, now.isoformat(),
                                      self._lease_until(now))
        if row is None:
            # Cancelled before pickup, reaped, or already claimed. Not an error:
            # the queue is at-least-once by design and the row is the arbiter.
            log.info("turn_not_claimable", turn_id=turn_id, worker_id=self._worker_id)
            return

        session_key = row["session_key"]
        task = asyncio.current_task()
        if task is not None:
            self._registry.register(turn_id, task)
        heartbeat = asyncio.create_task(self._heartbeat(turn_id),
                                        name=f"turn-heartbeat:{turn_id}")
        try:
            try:
                attachments = await self._attachments(row)
            except asyncio.CancelledError:
                await asyncio.shield(self._announce(row, turn_id, None))
                raise
            except Exception as exc:
                await asyncio.shield(self._announce(row, turn_id, str(exc)))
                raise
            reply = await self._runner.run_turn(
                session_key, row["text"], user_id=int(row["user_id"] or 0),
                attachments=attachments, channel=row["channel"], turn_id=turn_id,
                **await self._workspace_kwarg(row))
        except asyncio.CancelledError:
            # This IS the end of the turn — there is nobody to re-raise to, the
            # task body is the whole task. `TurnService` has already emitted
            # `turn.end {cancelled}`; all that is left is the row.
            await asyncio.shield(self._finish(turn_id, "cancelled"))
            if task is not None:
                # Swallowing the cancel leaves `Task.cancelling()` raised (3.11+),
                # and the next `asyncio.timeout`/`wait_for` in the taskiq frame
                # above us would read that count as a cancel of ITS wait.
                uncancel = getattr(task, "uncancel", None)
                if uncancel is not None:
                    uncancel()
        except Exception as exc:
            log.exception("turn_failed", turn_id=turn_id, session_key=session_key)
            await asyncio.shield(self._finish(turn_id, "error", error=str(exc)))
        else:
            await asyncio.shield(self._finish(turn_id, "ok", reply=reply))
        finally:
            heartbeat.cancel()
            self._registry.unregister(turn_id)

    # --- whose tree this turn runs in -----------------------------------------

    async def _workspace_kwarg(self, row: dict) -> dict:
        """`{"workspace": ws}`, or nothing at all when no resolver is wired.

        Passing the keyword only when there IS a resolver is what keeps the
        bare `run_turn(session_key, text, ...)` call — and every fake runner
        written against it — working unchanged. Not swallowed: a lane whose owner
        has no `users` row is a turn that must not run somewhere arbitrary, and
        `UnknownUser` reaching `run`'s `except Exception` ends the turn with an
        error the user can see rather than in the wrong workspace.
        """
        if self._workspaces is None:
            return {}
        return {"workspace": await self._workspaces.for_lane(
            row["session_key"], row["channel"], int(row["user_id"] or 0))}

    # --- the envelope a turn that never started would not get -----------------

    async def _announce(self, row: dict, turn_id: str, error: str | None) -> None:
        """Synthesize `turn.start` + `turn.end` for a turn that failed before its
        body ran.

        `error is None` means the setup was cancelled rather than broken. Emitting
        `turn.start` first is not decoration: every consumer (the SSE snapshot
        store, the Telegram presenter) registers the turn on that event and would
        drop a bare end. Best-effort, like every other emit on this path — a bus
        that is down must not change what the row says.
        """
        if self._bus is None:
            return
        try:
            # Both payloads carry the model keys as null: this turn never reached
            # the resolver, so nothing here knows the lane's pair — and a consumer
            # of `turn.start`/`turn.end` must never need a special case for an
            # envelope the body did not produce.
            await self._bus.emit(row["session_key"], turn_id, "turn.start",
                                 {"channel": row["channel"],
                                  "user_id": int(row["user_id"] or 0),
                                  "model": None, "effort": None})
            await self._bus.emit(row["session_key"], turn_id, "turn.end", {
                "status": "cancelled" if error is None else "error",
                "cost_usd": None, "sdk_session_id": None, "error": error,
                "usage": None, **NO_MODEL_END_KEYS})
        except Exception:
            log.exception("turn_setup_event_failed", turn_id=turn_id)

    # --- the lease ----------------------------------------------------------

    def _lease_until(self, now: datetime) -> str:
        return (now + timedelta(seconds=self._lease_seconds)).isoformat()

    async def _heartbeat(self, turn_id: str) -> None:
        """Push the lease out every third of it, and read the cancel flag while
        we are there — the same statement does both.

        Returns (and stops heartbeating) on every path that ends the turn: the
        flag, a row that is no longer ours, and a lease this process could not
        extend in time. Cancellation goes through the registry rather than the
        task, so a second pass cannot cancel a turn twice.
        """
        interval = max(self._lease_seconds / 3.0, 0.01)
        deadline = time.monotonic() + self._lease_seconds
        while True:
            await asyncio.sleep(interval)
            now = datetime.now(timezone.utc)
            try:
                row = await self._store.heartbeat(turn_id, self._worker_id,
                                                  self._lease_until(now))
            except Exception:
                # A database blip must not end a turn. Retry on the next interval;
                # the lease below is the limit on how long we may keep running
                # without having proved we are alive.
                log.warning("turn_heartbeat_failed", turn_id=turn_id, exc_info=True)
                if time.monotonic() >= deadline:
                    log.error("turn_lease_lost", turn_id=turn_id,
                              worker_id=self._worker_id)
                    self._registry.cancel(turn_id)
                    return
                continue
            if row is None:
                # Reaped, or finished by somebody else: the row is not ours and
                # the only correct response is to stop running.
                log.warning("turn_reaped_while_running", turn_id=turn_id,
                            worker_id=self._worker_id)
                self._registry.cancel(turn_id)
                return
            deadline = time.monotonic() + self._lease_seconds
            if row.get("cancel_requested_at"):
                # The fallback path for a lost control nudge — same ending, up to
                # a third of a lease later.
                log.info("turn_cancel_flag_seen", turn_id=turn_id)
                self._registry.cancel(turn_id)
                return

    # --- the terminal write -------------------------------------------------

    async def _finish(self, turn_id: str, status: str, *, reply: str | None = None,
                      error: str | None = None) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            written = await self._store.finish(turn_id, self._worker_id, status,
                                               reply=reply, error=error,
                                               now_iso=now_iso)
        except Exception:
            log.exception("turn_finish_failed", turn_id=turn_id, status=status)
            return
        if written:
            return
        # The row was not ours any more. A completed turn recorded as `lost` has
        # to be visible somewhere, so the refusal is logged with what the row
        # actually says.
        row = await self._store.get(turn_id)
        log.warning("turn_finish_ignored", turn_id=turn_id, wanted=status,
                    actual=(row or {}).get("status"), worker_id=self._worker_id)

    # --- attachments --------------------------------------------------------

    async def _attachments(self, row: dict) -> list[Attachment]:
        """Re-resolve the row's file ids, refusing any that belongs to another
        lane. The check is the whole point of re-resolving here rather than
        trusting what the row says."""
        ids = list(row.get("attachment_ids") or [])
        if not ids:
            return []
        if self._files is None:
            raise ValueError("this process has no file store to resolve attachments")
        out: list[Attachment] = []
        for file_id in ids:
            record = await self._files.get(str(file_id))
            if record is None or record["session_key"] != row["session_key"]:
                raise ValueError(f"unknown attachment {file_id}")
            out.append(Attachment(
                path=await self._files.resolve(record), filename=record["name"],
                media_type=record["media_type"],
                is_image=record["media_type"] in IMAGE_TYPES, file_id=record["id"]))
        return out

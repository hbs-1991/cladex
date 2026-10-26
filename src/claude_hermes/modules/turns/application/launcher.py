"""Starting a turn, waiting for one, and reaping the turns nobody will finish.

This is the half of Wave 3b every *channel* touches. A web route, a Telegram
message and a cron delivery all end up here, and none of them runs a turn any
more: `start` writes a row and hands the queue an id, and whichever process holds
the other end claims it. What used to be `asyncio.create_task(run())` inside a
request handler is now a row, which is why an agent restart mid-turn stopped
killing the turn.

Four orderings in this file are invariants rather than preferences:

* **The row is created before the task is dispatched.** The unique partial index
  on `turns(session_key)` is the lane lock, so `create` is what refuses a second
  turn — never a `SELECT` above it. A dispatch that fails afterwards finishes the
  row, because a `queued` row nobody will claim holds the lane until its lease
  lapses.
* **Cancel is flag first, nudge second.** `store.request_cancel` is the truth (one
  statement, and it is also what makes a never-started turn `cancelled` outright);
  the control-channel nudge is only the latency. A lost nudge costs a third of a
  lease — the heartbeat sees the same flag — and never costs correctness.
* **`wait` subscribes, awaits `ready`, and only then reads the row.** `subscribe`
  in redis mode merely *schedules* the SUBSCRIBE; reading the row first leaves a
  window in which the turn ends in neither the subscription nor the read.
* **`wait` re-reads after `turn.end`.** The event is emitted from inside the turn
  body and the row is written by `TurnRunService` after that body returns, so the
  row is still `running` for a moment after the event. Without the bounded
  re-read below the Telegram reply arrives one 15-second poll late about half the
  time.

A lost turn is **never re-executed**: re-running an SDK loop that may have
half-completed doubles its side effects and its cost. "Reclaimable within the
lease period" is a claim about the lane, not about the work.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import structlog

from claude_hermes.modules.turns.domain.events import NO_MODEL_END_KEYS, TurnEvent
from claude_hermes.modules.turns.domain.exceptions import TurnFailed

from .control import TurnControl
from .ports import TurnDispatcher, TurnEventBusPort, TurnStore

log = structlog.get_logger(__name__)

__all__ = ["TurnLauncher", "LOST_ERROR", "LOST_END_PAYLOAD"]

# The sentence a reaped turn ends with, on the row and in the synthetic event.
# `TurnRepository.mark_lost` writes the same string onto the row — the row's copy
# is what `wait` raises with when it never saw the event.
LOST_ERROR = "worker lost: lease expired"

# The frozen `turn.end` payload of a reaped turn: the same keys a real end
# carries, so the SPA and the Telegram presenter need no special case. The model
# keys are null because nobody here knows what the lost worker was running — the
# lane's pair lives on its row, not on this event.
LOST_END_PAYLOAD = {"status": "error", "cost_usd": None, "sdk_session_id": None,
                    "error": LOST_ERROR, "usage": None, **NO_MODEL_END_KEYS}

_TERMINAL = frozenset({"ok", "error", "cancelled", "lost"})


class TurnLauncher:
    """Every channel's entry to the turn machinery: start, is_busy, cancel, wait,
    reap. Holds no per-turn state — the row does."""

    #: How long `wait` keeps re-reading the row after a `turn.end`, and how often.
    #: Class attributes so a test can shrink them without a constructor keyword
    #: nobody would ever set in production.
    SETTLE_SECONDS = 5.0
    SETTLE_INTERVAL = 0.05

    def __init__(self, store: TurnStore, dispatcher: TurnDispatcher,
                 bus: TurnEventBusPort, control: TurnControl, *,
                 queue_timeout_seconds: float = 300.0,
                 retention_days: int = 30) -> None:
        self._store = store
        self._dispatcher = dispatcher
        self._bus = bus
        # The control object, not the raw bus: the cancel nudge's wire format has
        # exactly one writer and one reader, and both are `TurnControl`.
        self._control = control
        self.queue_timeout_seconds = float(queue_timeout_seconds)
        self._retention_days = int(retention_days)

    # --- starting -----------------------------------------------------------

    async def start(self, session_key: str, text: str, *, user_id: int = 0,
                    attachments=None, channel: str = "web") -> str:
        """Create the turn and queue it. Returns the turn id.

        `LaneBusy` propagates: the index refused a second active turn on this
        lane, which is the same 409 the router used to answer from a check.
        """
        turn_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=self.queue_timeout_seconds)
        await self._store.create(
            turn_id, session_key, channel, int(user_id), text,
            _attachment_ids(attachments), now.isoformat(), lease_until.isoformat())
        try:
            await self._dispatcher.dispatch(turn_id)
        except Exception as exc:
            # A `queued` row nobody will ever claim still holds the lane, and the
            # caller is about to be told the turn failed — so end it here, with
            # the real reason rather than the reaper's sentence: the queue was
            # unreachable, no worker was ever lost. `fail_queued` is the one
            # terminal write with no `worker_id` (there is no worker yet); it
            # matches `queued` only, so it cannot touch a turn that somehow got
            # claimed between the dispatch and this line.
            log.exception("turn_dispatch_failed", turn_id=turn_id,
                          session_key=session_key)
            await self._store.fail_queued(
                turn_id, f"dispatch failed: {exc}",
                datetime.now(timezone.utc).isoformat())
            raise
        return turn_id

    async def is_busy(self, session_key: str) -> bool:
        """Is a turn holding this lane? The row is the one truth, in every
        process — and it ignores the lease, so this can never say "free" about a
        lane `create` would refuse."""
        return await self._store.active(session_key) is not None

    # --- cancelling ---------------------------------------------------------

    async def cancel(self, session_key: str, turn_id: str | None = None) -> bool:
        """Stop the turn on this lane. True when there was one.

        Flag first (one statement, and the arbiter), nudge second (the latency).
        A turn that was still `queued` comes back `cancelled` and never runs, so
        there is nothing to nudge — nobody is executing it anywhere.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        row = await self._store.request_cancel(session_key, now_iso, turn_id)
        if row is None:
            return False
        # The id is logged because cancelling by lane can hit a newer turn if the
        # previous one finished between the click and the UPDATE.
        log.info("turn_cancel_requested", turn_id=row["id"], session_key=session_key,
                 status=row["status"])
        if row["status"] != "cancelled":
            try:
                await self._control.nudge_cancel(row["id"])
            except Exception:
                # The heartbeat sees `cancel_requested_at` within a third of a
                # lease, so a nudge that cannot be published is slow, not wrong.
                log.warning("turn_cancel_nudge_failed", turn_id=row["id"],
                            exc_info=True)
        return True

    # --- waiting ------------------------------------------------------------

    async def wait(self, turn_id: str, session_key: str, *,
                   poll_seconds: float = 15.0) -> str | None:
        """Block until this turn ends, wherever it is running.

        Returns the reply on `ok` and `None` on `cancelled`; raises `TurnFailed`
        on `error` and on `lost`. The poll is the fallback for a lost pub/sub
        message, not the primary path.
        """
        ended = asyncio.Event()

        async def listener(event: TurnEvent) -> None:
            if event.turn_id == turn_id and event.type == "turn.end":
                ended.set()

        unsubscribe = self._bus.subscribe(session_key, listener)
        try:
            # INVARIANT (ordering): the subscription is live before the row is
            # read. The other way round, a turn that ends in between is in
            # neither the read nor the subscription.
            await self._bus.ready(session_key)
            while True:
                ended.clear()
                row = await self._store.get(turn_id)
                if row is None:
                    # The row was created before this call and retention is
                    # measured in days, so there is no legitimate way here. Fail
                    # loudly rather than poll a row that will never exist: an
                    # unbounded wait is a Telegram handler that never answers.
                    log.error("turn_row_vanished", turn_id=turn_id,
                              session_key=session_key)
                    raise TurnFailed("the turn is gone")
                if row["status"] in _TERMINAL:
                    return _outcome(row)
                try:
                    await asyncio.wait_for(ended.wait(), timeout=poll_seconds)
                except (asyncio.TimeoutError, TimeoutError):
                    continue                    # the fallback poll: read again
                # `turn.end` is emitted from inside the turn body; the row is
                # written after that body returns. So re-read briefly rather than
                # falling back to the poll, which would cost 15 seconds of
                # silence in a Telegram chat about half the time.
                settled = await self._settled(turn_id)
                if settled is not None:
                    return _outcome(settled)
        finally:
            unsubscribe()

    async def _settled(self, turn_id: str) -> dict | None:
        """Re-read the row until it is terminal, for at most `SETTLE_SECONDS`.
        `None` means it is still running — back to the poll."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.SETTLE_SECONDS
        while True:
            row = await self._store.get(turn_id)
            if row is not None and row["status"] in _TERMINAL:
                return row
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(self.SETTLE_INTERVAL)

    # --- reaping ------------------------------------------------------------

    async def reap(self, now: datetime) -> int:
        """Free the lanes of turns nobody will finish, then prune old rows.

        Driven by the same clock as the cron tick (`scheduler_tick` in redis mode,
        `CronTicker`'s `extra` inline) — a second clock would be a second thing to
        get wrong about time.

        NAMED LIMITATION (reap false positive): a worker that is alive but stalled
        past its lease loses its turn to this pass. The lane gets the synthetic
        end, the user is told the turn failed, and the real turn's own `finish` is
        refused by the `worker_id` guard and logged. The worker's self-cancel on a
        lapsed lease (`execution.py`) is what keeps that window one heartbeat wide.
        """
        now_iso = now.isoformat()
        reaped = 0
        for row in await self._store.expired(now_iso):
            if not await self._store.mark_lost(row["id"], now_iso):
                # The worker finished it between the read and this write; the lane
                # is already free and nothing was ever wrong.
                continue
            reaped += 1
            log.warning("turn_lost", turn_id=row["id"], session_key=row["session_key"],
                        worker_id=row["worker_id"], status=row["status"],
                        lease_until=row["lease_until"])
            try:
                # Through the bus, so the snapshot is cleared and every subscriber
                # — an SSE stream on another instance included — sees the end.
                await self._bus.emit(row["session_key"], row["id"], "turn.end",
                                     dict(LOST_END_PAYLOAD))
            except Exception:
                log.exception("turn_lost_event_failed", turn_id=row["id"])
        await self._prune(now)
        return reaped

    async def _prune(self, now: datetime) -> None:
        """Drop finished rows past the retention window. The row holds a third
        copy of the message text and the reply (after `messages` and the SDK
        transcript), so this table is not an archive."""
        before = (now - timedelta(days=self._retention_days)).isoformat()
        try:
            dropped = await self._store.prune(before)
        except Exception:
            # Pruning is housekeeping: failing it must not stop the reaping that
            # shares this pass.
            log.exception("turn_prune_failed")
            return
        if dropped:
            log.info("turns_pruned", count=dropped, before=before)


def _outcome(row: dict) -> str | None:
    """The row's terminal state as `wait`'s caller sees it."""
    status = row["status"]
    if status == "ok":
        return row["reply"] or ""
    if status == "cancelled":
        # The user stopped it on purpose and has nothing to be told; the Telegram
        # dispatcher's `if reply:` skips the final message.
        return None
    raise TurnFailed(row["error"] or "the turn failed")


def _attachment_ids(attachments) -> list[str]:
    """The file ids the worker will re-resolve against the row's lane.

    An attachment with no id was written into the lane directory without being
    registered. There is no id for
    another process to resolve, so it is dropped with a warning rather than
    becoming a `None` the worker would fail the whole turn on.
    """
    out: list[str] = []
    for attachment in attachments or ():
        file_id = getattr(attachment, "file_id", None)
        if file_id:
            out.append(str(file_id))
        else:
            log.warning("dropping an unregistered attachment from a queued turn",
                        filename=getattr(attachment, "filename", ""))
    return out

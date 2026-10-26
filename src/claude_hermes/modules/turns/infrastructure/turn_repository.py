"""The only place `turns` SQL lives.

Every method below is **one statement**, and that is not a style preference: this
table is what makes "exactly one active turn per lane" and "a dead worker's lane
comes back" true across processes, and both claims dissolve the moment a method
becomes read-then-write. Three rules follow from it:

* **The unique partial index is the lock.** `create` does not ask whether the lane
  is busy; it inserts and lets `turns_one_active_per_lane` answer. The
  `IntegrityError` is translated to `domain.exceptions.LaneBusy` here, so nothing
  above this file has to know a database raised.
* **`claim` matches `queued` only.** A `running` row whose lease expired belongs
  to the reaper, never to a second execution: re-running an SDK loop that may have
  half-completed doubles its side effects and its cost.
* **`worker_id` guards every terminal write.** `heartbeat` and `finish` both carry
  it, so a worker whose turn was reaped out from under it cannot flip the row back
  to `ok` — `finish` returns `False` instead, and the caller logs that a completed
  turn was recorded as lost.

Takes an `AsyncEngine`, never the `Database` facade, and each call is its own
short transaction: a caller never holds a session.
"""
from __future__ import annotations

from sqlalchemy import case, delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ..domain.exceptions import LaneBusy
from ..domain.models import ACTIVE_STATUSES, TurnRow

_TABLE = TurnRow.__table__
_COLUMNS = tuple(_TABLE.columns)


class TurnRepository:
    """`turns`: one row per turn. Satisfies `application.ports.TurnStore`."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def create(self, turn_id: str, session_key: str, channel: str, user_id: int,
                     text: str, attachment_ids: list[str], now_iso: str,
                     lease_until: str) -> None:
        """Insert a `queued` turn, or raise `LaneBusy`.

        No `SELECT` first: the index decides, in one statement, and two instances
        racing on the same lane get one winner and one `LaneBusy` rather than two
        turns.
        """
        stmt = insert(_TABLE).values(
            id=turn_id, session_key=session_key, channel=channel, user_id=user_id,
            text=text, attachment_ids=list(attachment_ids), status="queued",
            created_at=now_iso, lease_until=lease_until)
        try:
            async with self._engine.begin() as conn:
                await conn.execute(stmt)
        except IntegrityError as exc:
            raise LaneBusy("the agent is busy in this thread") from exc

    async def claim(self, turn_id: str, worker_id: str, now_iso: str,
                    lease_until: str) -> dict | None:
        """Take ownership of a queued turn. `None` means it is not ours to run —
        cancelled before pickup, reaped, or already claimed by another worker."""
        stmt = (update(_TABLE)
                .where(_TABLE.c.id == turn_id, _TABLE.c.status == "queued")
                .values(status="running", started_at=now_iso, worker_id=worker_id,
                        lease_until=lease_until)
                .returning(*_COLUMNS))
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def heartbeat(self, turn_id: str, worker_id: str,
                        lease_until: str) -> dict | None:
        """Push the lease out and read the cancel flag in the same statement.

        `None` means the row is no longer ours — reaped, or finished by somebody
        else — and the only correct response is to stop running.
        """
        stmt = (update(_TABLE)
                .where(_TABLE.c.id == turn_id, _TABLE.c.worker_id == worker_id,
                       _TABLE.c.status == "running")
                .values(lease_until=lease_until)
                .returning(_TABLE.c.cancel_requested_at))
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def finish(self, turn_id: str, worker_id: str, status: str, *,
                     reply: str | None = None, error: str | None = None,
                     now_iso: str) -> bool:
        """Write the terminal state. `False` means the row was not ours any more:
        the caller logs it, because a completed turn recorded as `lost` has to be
        visible somewhere."""
        stmt = (update(_TABLE)
                .where(_TABLE.c.id == turn_id, _TABLE.c.worker_id == worker_id,
                       _TABLE.c.status == "running")
                .values(status=status, reply=reply, error=error,
                        finished_at=now_iso))
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount > 0

    async def fail_queued(self, turn_id: str, error: str, now_iso: str) -> bool:
        """End a turn that was never claimed, with the real reason.

        `finish` cannot do this: it is guarded by `worker_id` and matches a
        `running` row, which is exactly what stops a reaped worker resurrecting a
        turn. A row whose dispatch raised has neither, and leaving it `queued`
        holds the lane until its lease lapses. So this is the plan's "direct
        terminal UPDATE without the `worker_id` guard" — narrowed to `queued`,
        so it can never touch a turn some worker is already executing.
        """
        stmt = (update(_TABLE)
                .where(_TABLE.c.id == turn_id, _TABLE.c.status == "queued")
                .values(status="error", error=error, finished_at=now_iso))
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount > 0

    async def request_cancel(self, session_key: str, now_iso: str,
                             turn_id: str | None = None) -> dict | None:
        """Flag the active turn as cancelled — and finish it outright if it never
        started.

        One statement with a CASE rather than "read the status, then decide":
        a `claim` racing this is settled by the `status` the UPDATE itself reads,
        so the turn is either cancelled before it ran or flagged while it runs.
        The returned `status` is the post-update one, so `cancelled` means nothing
        was ever executed.

        `turn_id` narrows it to one turn. Without it this cancels whatever is
        active on the lane, which can be a newer turn if the previous one finished
        between the click and the UPDATE — the race the optional id exists to
        close once a client can name the turn.
        """
        conditions = [_TABLE.c.session_key == session_key,
                      _TABLE.c.status.in_(ACTIVE_STATUSES)]
        if turn_id is not None:
            conditions.append(_TABLE.c.id == turn_id)
        queued = _TABLE.c.status == "queued"
        stmt = (update(_TABLE)
                .where(*conditions)
                .values(cancel_requested_at=now_iso,
                        status=case((queued, "cancelled"), else_=_TABLE.c.status),
                        finished_at=case((queued, now_iso),
                                         else_=_TABLE.c.finished_at))
                .returning(_TABLE.c.id, _TABLE.c.status))
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def active(self, session_key: str) -> dict | None:
        """The turn holding this lane, **lease ignored**.

        The index defines busy and the reaper is the only thing that frees a lane,
        so filtering on the lease here would let `is_busy` say "free" about a lane
        `create` will refuse.
        """
        stmt = (select(_TABLE)
                .where(_TABLE.c.session_key == session_key,
                       _TABLE.c.status.in_(ACTIVE_STATUSES))
                .limit(1))
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def active_lanes(self, session_keys: list[str]) -> set[str]:
        """Which of these lanes are busy — one query for a whole page of the
        sidebar, because one query per row is how a thread list becomes slow."""
        keys = list(session_keys)
        if not keys:
            return set()
        stmt = (select(_TABLE.c.session_key)
                .where(_TABLE.c.session_key.in_(keys),
                       _TABLE.c.status.in_(ACTIVE_STATUSES))
                .distinct())
        async with self._engine.connect() as conn:
            return {r[0] for r in (await conn.execute(stmt)).all()}

    async def expired(self, now_iso: str, limit: int = 100) -> list[dict]:
        """Active rows whose lease has lapsed — the reaper's work list. A `queued`
        row is in here too: a queue message that vanished must not hold a lane."""
        stmt = (select(_TABLE)
                .where(_TABLE.c.status.in_(ACTIVE_STATUSES),
                       _TABLE.c.lease_until < now_iso)
                .order_by(_TABLE.c.lease_until)
                .limit(limit))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def mark_lost(self, turn_id: str, now_iso: str) -> bool:
        """`False` when somebody got there first — the worker finished it between
        the `expired` read and this write, and the lane is already free."""
        stmt = (update(_TABLE)
                .where(_TABLE.c.id == turn_id,
                       _TABLE.c.status.in_(ACTIVE_STATUSES))
                .values(status="lost", finished_at=now_iso,
                        error="worker lost: lease expired"))
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount > 0

    async def prune(self, before_iso: str, limit: int = 1000) -> int:
        """Drop finished rows older than the retention window.

        Bounded by `limit` (through a subquery, since PostgreSQL has no
        `DELETE ... LIMIT`) so one pass of a long-neglected table is a short
        transaction and not a lock held over a million rows.
        """
        doomed = (select(_TABLE.c.id)
                  .where(_TABLE.c.finished_at.is_not(None),
                         _TABLE.c.finished_at < before_iso)
                  .limit(limit))
        stmt = delete(_TABLE).where(_TABLE.c.id.in_(doomed))
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount

    async def get(self, turn_id: str) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(_TABLE).where(_TABLE.c.id == turn_id))).mappings().first()
        return dict(row) if row else None

"""The only place jobs SQL lives."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from claude_hermes.modules.scheduler.application.ports import STAY_IDLE
from claude_hermes.modules.scheduler.domain.models import Job, JobRun

# How long a claim holds a job that declares no timeout of its own. The lease only
# has to cover the gap between the claim and a worker picking the task off the
# queue: a run that outlives its lease is not re-claimed, because `claim_due`
# already moved `next_run_at` past it.
LEASE_SECONDS = 300.0

# The most runs one calendar window returns. A routine every hour for 62 days is
# ~1500 rows; past this the calendar is a log, and the Routine page has the log.
RUNS_BETWEEN_LIMIT = 5000


class JobRepository:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def create(
        self, name: str, kind: str, value: str, prompt: str,
        deliver_chat_id: int, origin_session_key: str, next_run_at: str | None,
        deliver_thread_id: int | None = None, timeout_seconds: int | None = None,
        owner_id: int | None = None, model: str | None = None,
        effort: str | None = None,
    ) -> int:
        """Register a routine. `owner_id` (Wave 4a) is whose routine it is, and it
        is load-bearing twice over: it scopes every read below, and `SdkJobExecutor`
        resolves the job owner's workspace from it, so a routine created by an
        allowlisted user runs in that user's tree and reports their spend.

        `model`/`effort` (0008) are the routine's own pair as catalog ids, NULL
        meaning "inherit" — `cron_model`, then `model` — exactly as `timeout_seconds`
        means "inherit the global limit".

        `next_run_at` is None for an on-demand (`manual`) routine: it is active and
        waits for a wake, so `claim_due` never selects it until one arrives."""
        stmt = insert(Job).values(
            name=name, kind=kind, value=value, prompt=prompt,
            deliver_chat_id=deliver_chat_id, deliver_thread_id=deliver_thread_id,
            origin_session_key=origin_session_key, next_run_at=next_run_at,
            timeout_seconds=timeout_seconds, owner_id=owner_id,
            model=model, effort=effort,
            created_at=datetime.now(timezone.utc).isoformat(),
        ).returning(Job.id)
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one()

    async def get(self, job_id: int, owner_id: int | None = None) -> dict | None:
        """One routine, or None. With an `owner_id` the scoping is part of the
        SELECT, so somebody else's routine is indistinguishable from one that does
        not exist — a 404 rather than a 403, the same rule the thread guard
        follows. `owner_id=None` reads across owners and is what the ticker, the
        executor and the delivery path use: they run a job, they do not answer a
        request."""
        stmt = select(Job).where(Job.id == job_id)
        if owner_id is not None:
            stmt = stmt.where(Job.owner_id == owner_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def by_thread(self, chat_id: int, thread_id: int) -> dict | None:
        """The job whose reports are delivered to this topic. Newest wins if a
        thread id was ever reused; state is not filtered — a paused job's last
        report can still be replied to."""
        stmt = (
            select(Job)
            .where(Job.deliver_chat_id == chat_id, Job.deliver_thread_id == thread_id)
            .order_by(Job.id.desc())
            .limit(1)
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def list_jobs(self, owner_id: int | None = None) -> list[dict]:
        """Every routine, or every routine of one owner. `GET /api/routines` is open
        to any logged-in user, so the scoping has to happen here — before Wave 4a it
        listed the deploy's routines to everybody who could log in."""
        stmt = select(Job).order_by(Job.id)
        if owner_id is not None:
            stmt = stmt.where(Job.owner_id == owner_id)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def due(self, now_iso: str) -> list[dict]:
        stmt = (
            select(Job)
            .where(Job.state == "active", Job.next_run_at <= now_iso)
            .order_by(Job.next_run_at)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def claim_due(
        self,
        now_iso: str,
        next_of: Callable[[dict], str | None] | None = None,
        limit: int = 100,
        *,
        lease_seconds: float = LEASE_SECONDS,
    ) -> list[dict]:
        """The jobs this caller takes responsibility for on this tick.

        ONE transaction, and that transaction is the whole at-most-once guarantee
        across N worker processes: `SELECT ... FOR UPDATE SKIP LOCKED` takes the row
        locks, and the *same* transaction moves `next_run_at` forward (`next_of`)
        and stamps the lease. A second ticker running the identical statement at the
        identical moment skips the locked rows and gets a disjoint set; one that
        arrives after the commit sees a `next_run_at` already in the future. There
        is no window in which two processes see the same fire.

        `next_of` is the caller's clock arithmetic (the domain's `next_fire` over
        the row's schedule) — the repository must not import a schedule to know when
        a job fires next. `None` back from it means the job is spent, and the row
        goes `state='done'` exactly as `reschedule(id, None)` leaves it.
        `STAY_IDLE` means "fired, not spent" — an on-demand routine — and the row
        keeps `state='active'` with `next_run_at` NULL until its next wake. Passing no
        `next_of` at all claims without rescheduling, which only a caller that wants
        the lease alone should do.

        Each returned row carries an extra `fire_at`: the `next_run_at` this claim
        consumed. The row is the pre-update snapshot, so the catch-up window is read
        off `fire_at` and nothing downstream has to know the reschedule happened.
        """
        pick = (
            select(Job)
            .where(
                Job.state == "active",
                Job.next_run_at <= now_iso,
                # A live lease means another worker already owns this fire.
                or_(Job.lease_until.is_(None), Job.lease_until < now_iso),
            )
            .order_by(Job.next_run_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        now = datetime.fromisoformat(now_iso)
        claimed: list[dict] = []
        async with self._engine.begin() as conn:
            for row in (await conn.execute(pick)).mappings().all():
                job = dict(row)
                job["fire_at"] = job["next_run_at"]
                # A job that asked for a long timeout needs a lease that outlives
                # its own run; one that did not gets the floor.
                seconds = max(float(job["timeout_seconds"] or 0.0), lease_seconds)
                values: dict = {
                    "claimed_at": now_iso,
                    "lease_until": (now + timedelta(seconds=seconds)).isoformat(),
                }
                if next_of is not None:
                    nxt = next_of(job)
                    if nxt == STAY_IDLE:
                        values["next_run_at"] = None
                    else:
                        values["next_run_at"] = nxt
                        if nxt is None:
                            values["state"] = "done"
                await conn.execute(
                    update(Job).where(Job.id == job["id"]).values(**values))
                claimed.append(job)
        return claimed

    async def wake_if_idle(self, job_id: int, now_iso: str) -> bool:
        """Set a pending wake on an idle on-demand routine: ONE statement, the check
        and the write together.

        `UPDATE … WHERE state='active' AND kind='manual' AND next_run_at IS NULL AND
        (lease_until IS NULL OR lease_until < :now) RETURNING id`. Two callers at the
        same instant serialise on the row lock, and PostgreSQL re-checks the WHERE
        for the second one against the first one's committed row — whose
        `next_run_at` is no longer NULL — so exactly one of them gets `True`.

        Unlike `run_now` it never touches the lease: a routine that is running (a
        live lease) is not woken, and one whose wake is already pending is not
        woken twice. Both are what keeps the publisher to one run at a time.
        """
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.state == "active",
                Job.kind == "manual",
                Job.next_run_at.is_(None),
                or_(Job.lease_until.is_(None), Job.lease_until < now_iso),
            )
            .values(next_run_at=now_iso)
            .returning(Job.id)
        )
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).first() is not None

    async def clear_lease(self, job_id: int) -> None:
        """Hand the job back. Called when a run finishes or is skipped, and by
        `run_now` — whose entire point is that the next tick picks the job up, which
        a lease left over from the previous claim would otherwise prevent."""
        async with self._engine.begin() as conn:
            await conn.execute(update(Job).where(Job.id == job_id).values(
                claimed_at=None, lease_until=None))

    async def reschedule(self, job_id: int, next_run_at: str | None) -> None:
        if next_run_at is None:
            stmt = update(Job).where(Job.id == job_id).values(
                state="done", next_run_at=None)
        else:
            stmt = update(Job).where(Job.id == job_id).values(next_run_at=next_run_at)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def set_state(self, job_id: int, state: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(Job).where(Job.id == job_id).values(state=state))

    async def update(self, job_id: int, **fields) -> None:
        """Edit a job's definition. Whitelisted on purpose: `id`, `state` and the
        run bookkeeping have their own methods, so a caller handing us a request
        body cannot rewrite them by naming the column."""
        allowed = {"name", "prompt", "timeout_seconds", "kind", "value", "next_run_at",
                   "model", "effort"}
        cols = [k for k in fields if k in allowed]
        if not cols:
            return
        stmt = update(Job).where(Job.id == job_id).values(**{c: fields[c] for c in cols})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def record_run(self, job_id: int, ts: str, status: str) -> None:
        """The job row keeps only the latest outcome (that is what the tool and the
        ticker read); job_runs keeps the history the web UI shows."""
        async with self._engine.begin() as conn:
            await conn.execute(update(Job).where(Job.id == job_id).values(
                last_run_at=ts, last_status=status))
            await conn.execute(insert(JobRun).values(
                job_id=job_id, ts=ts, status=status))

    async def runs(self, job_id: int, limit: int = 30) -> list[dict]:
        stmt = (
            select(JobRun.ts, JobRun.status)
            .where(JobRun.job_id == job_id)
            .order_by(JobRun.ts.desc())
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def runs_between(self, owner_id: int, start_iso: str,
                           end_iso: str) -> list[dict]:
        """One owner's recorded runs with `start <= ts < end`, oldest first, each
        with its routine's name (`job_id`, `name`, `ts`, `status`).

        Joined to `jobs` for the owner scope: `job_runs` has no owner of its own,
        and a run of somebody else's routine must be as invisible here as the
        routine is on `GET /api/routines`. Text comparison is sound because every
        `ts` is written by `record_run` from a UTC `isoformat()`, and the caller
        normalises the bounds the same way.
        """
        stmt = (
            select(JobRun.job_id, Job.name, JobRun.ts, JobRun.status)
            .join(Job, Job.id == JobRun.job_id)
            .where(Job.owner_id == owner_id,
                   JobRun.ts >= start_iso, JobRun.ts < end_iso)
            .order_by(JobRun.ts, JobRun.id)
            .limit(RUNS_BETWEEN_LIMIT)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def delete(self, job_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(JobRun).where(JobRun.job_id == job_id))
            await conn.execute(delete(Job).where(Job.id == job_id))

"""Claiming: the at-most-once guarantee, the lease, and the dispatch that follows.

These are the tests that stop being about one process. `claim_due` is the whole
proof — `SELECT ... FOR UPDATE SKIP LOCKED` and the reschedule in the same
transaction — so they run against the real PostgreSQL, two connections at a time,
rather than against a fake that cannot express a row lock.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from claude_hermes.core.tasks import InlineQueue, TaskRegistry
from claude_hermes.modules.scheduler.application.service import SchedulerService
from claude_hermes.modules.scheduler.domain.models import Job
from claude_hermes.modules.scheduler.infrastructure.dispatch import QueueJobDispatcher
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository

T0 = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)
NOW = T0.isoformat()
EARLIER = "2026-07-12T09:00:00+00:00"
LATER = "2026-07-12T11:00:00+00:00"
MUCH_LATER = "2026-07-12T12:00:00+00:00"


def next_hour(job):
    """A `next_of` that does not need a schedule: the claim only cares that the
    row moves forward."""
    return MUCH_LATER


class FakeExecutor:
    def __init__(self, result=("ok", "output")):
        self.ran = []
        self._result = result

    async def run(self, job):
        self.ran.append(job["id"])
        return self._result


class RecordingDispatcher:
    """A worker that takes the job and never finishes it — which is exactly what a
    process killed between claim and run looks like from here."""

    def __init__(self):
        self.calls = []

    async def dispatch(self, job_id, fire_at):
        self.calls.append((job_id, fire_at))


# --- the claim ------------------------------------------------------------

async def test_two_concurrent_claims_are_disjoint(engine):
    """The core of at-most-once: whatever the interleaving, no fire is handed out
    twice and none is dropped."""
    repo = JobRepository(engine)
    ids = [await repo.create(f"j{i}", "interval", "60", "p", 1, "tg:private:1",
                             f"2026-07-12T09:{i:02d}:00+00:00")
           for i in range(8)]

    first, second = await asyncio.gather(
        repo.claim_due(LATER, next_hour), repo.claim_due(LATER, next_hour))
    a = {j["id"] for j in first}
    b = {j["id"] for j in second}

    assert not (a & b), "the same fire was claimed by both callers"
    assert a | b == set(ids), "a due fire was claimed by neither"


async def test_a_row_another_transaction_holds_is_skipped_not_waited_for(engine):
    """`SKIP LOCKED`, not `FOR UPDATE` alone. Without it the second ticker blocks
    on the first one's slowest statement instead of taking the other jobs."""
    repo = JobRepository(engine)
    held_id = await repo.create("held", "interval", "60", "p", 1, "tg:private:1",
                                EARLIER)
    free_id = await repo.create("free", "interval", "60", "p", 1, "tg:private:1", NOW)

    async with engine.begin() as holder:
        await holder.execute(select(Job).where(Job.id == held_id).with_for_update())
        claimed = await asyncio.wait_for(repo.claim_due(LATER, next_hour), timeout=5)

    assert [j["id"] for j in claimed] == [free_id]


async def test_the_claim_reschedules_and_reports_the_fire_it_consumed(engine):
    repo = JobRepository(engine)
    jid = await repo.create("j", "interval", "60", "p", 1, "tg:private:1", EARLIER)

    claimed = (await repo.claim_due(LATER, next_hour))[0]
    assert claimed["fire_at"] == EARLIER          # the fire, not the new next_run_at
    assert claimed["next_run_at"] == EARLIER      # the row as it was claimed

    row = await repo.get(jid)
    assert row["next_run_at"] == MUCH_LATER       # …already moved on disk
    assert row["claimed_at"] == LATER and row["lease_until"] > LATER


async def test_a_spent_one_shot_is_marked_done_inside_the_claim(engine):
    repo = JobRepository(engine)
    jid = await repo.create("once", "once", NOW, "p", 1, "tg:private:1", NOW)
    await repo.claim_due(LATER, lambda job: None)
    row = await repo.get(jid)
    assert row["state"] == "done" and row["next_run_at"] is None


# --- the lease ------------------------------------------------------------

async def test_a_claimed_job_is_not_returned_again_while_its_lease_holds(engine):
    """`next_of=None` keeps `next_run_at` where it is, so the lease is the only
    thing that can hide the row — which is what this asserts."""
    repo = JobRepository(engine)
    jid = await repo.create("j", "interval", "60", "p", 1, "tg:private:1", EARLIER)

    assert [j["id"] for j in await repo.claim_due(NOW, lease_seconds=3600)] == [jid]
    assert await repo.claim_due(LATER) == []      # 11:00 < the 11:00-ish lease


async def test_a_lease_left_by_a_dead_worker_is_reclaimable_once_it_expires(engine):
    repo = JobRepository(engine)
    jid = await repo.create("j", "interval", "60", "p", 1, "tg:private:1", EARLIER)
    await repo.claim_due(NOW, lease_seconds=60)   # lease runs to 10:01

    assert await repo.claim_due(NOW) == []        # still held
    reclaimed = await repo.claim_due(LATER)       # 11:00, long past 10:01
    assert [j["id"] for j in reclaimed] == [jid]


async def test_clear_lease_hands_the_job_straight_back(engine):
    repo = JobRepository(engine)
    jid = await repo.create("j", "interval", "60", "p", 1, "tg:private:1", EARLIER)
    await repo.claim_due(NOW, lease_seconds=3600)

    await repo.clear_lease(jid)
    row = await repo.get(jid)
    assert row["claimed_at"] is None and row["lease_until"] is None
    assert [j["id"] for j in await repo.claim_due(NOW)] == [jid]


async def test_a_jobs_own_timeout_raises_the_lease_floor(engine):
    """A 30-minute job must not have its fire re-claimed 5 minutes in."""
    repo = JobRepository(engine)
    await repo.create("slow", "interval", "60", "p", 1, "tg:private:1", EARLIER,
                      timeout_seconds=1800)
    quick = await repo.create("quick", "interval", "60", "p", 1, "tg:private:1",
                              EARLIER)

    rows = {j["name"]: j for j in await repo.claim_due(NOW, lease_seconds=300)}
    assert (await repo.get(rows["slow"]["id"]))["lease_until"] == "2026-07-12T10:30:00+00:00"
    assert (await repo.get(quick))["lease_until"] == "2026-07-12T10:05:00+00:00"


# --- the tick and what it dispatches --------------------------------------

async def test_the_tick_dispatches_primitives_and_leaves_the_lease_to_the_worker(engine):
    repo = JobRepository(engine)
    dispatcher = RecordingDispatcher()
    svc = SchedulerService(repo, dispatcher=dispatcher)
    jid = await repo.create("j", "interval", "3600", "p", 1, "tg:private:1",
                            T0.isoformat())

    await svc.tick(T0)
    assert dispatcher.calls == [(jid, T0.isoformat())]
    # nothing has run it, so the lease is still out — that is what stops the next
    # tick from handing the same fire to a second worker
    assert (await repo.get(jid))["lease_until"] > T0.isoformat()


async def test_run_now_fires_next_tick_even_while_the_last_claim_is_leased(engine):
    repo = JobRepository(engine)
    dispatcher = RecordingDispatcher()
    svc = SchedulerService(repo, dispatcher=dispatcher)
    jid = await repo.create("j", "interval", "3600", "p", 1, "tg:private:1",
                            T0.isoformat())

    await svc.tick(T0)
    await svc.run_now(jid, now=T0 + timedelta(seconds=5))
    await svc.tick(T0 + timedelta(seconds=6))

    assert [c[0] for c in dispatcher.calls] == [jid, jid]


async def test_a_missed_fire_is_recorded_and_the_lease_released_without_dispatch(engine):
    repo = JobRepository(engine)
    dispatcher = RecordingDispatcher()
    svc = SchedulerService(repo, dispatcher=dispatcher)
    jid = await repo.create("j", "interval", "3600", "p", 1, "tg:private:1",
                            T0.isoformat())

    await svc.tick(T0 + timedelta(minutes=45))    # 45m > min(30m, 2h)

    assert dispatcher.calls == []
    row = await repo.get(jid)
    assert row["last_status"] == "missed" and row["lease_until"] is None


async def test_execute_job_runs_records_and_releases(engine):
    repo = JobRepository(engine)
    executor = FakeExecutor()
    svc = SchedulerService(repo, executor=executor)
    jid = await repo.create("j", "interval", "3600", "p", 1, "tg:private:1",
                            T0.isoformat())
    await repo.claim_due(T0.isoformat(), next_hour)

    await svc.execute_job(jid, T0.isoformat(), T0)

    assert executor.ran == [jid]
    row = await repo.get(jid)
    assert row["last_status"] == "ok" and row["lease_until"] is None


async def test_a_job_deleted_between_claim_and_run_is_dropped_not_resurrected(engine):
    repo = JobRepository(engine)
    executor = FakeExecutor()
    svc = SchedulerService(repo, executor=executor)

    await svc.execute_job(404, T0.isoformat(), T0)

    assert executor.ran == []


async def test_the_queue_dispatcher_enqueues_run_job_with_primitives(engine):
    """The whole loop with the inline queue: tick → dispatch → registered task →
    execute_job. In redis mode only the middle link changes."""
    repo = JobRepository(engine)
    registry = TaskRegistry()
    queue = InlineQueue(registry)
    executor = FakeExecutor()
    svc = SchedulerService(repo, executor=executor,
                           dispatcher=QueueJobDispatcher(queue))
    enqueued = []

    @registry.task("run_job")
    async def run_job(job_id: int, fire_at: str) -> None:
        enqueued.append((job_id, fire_at))
        await svc.execute_job(job_id, fire_at)

    jid = await repo.create("j", "interval", "3600", "p", 1, "tg:private:1",
                            T0.isoformat())
    await svc.tick(T0)
    await queue.drain()

    assert enqueued == [(jid, T0.isoformat())]
    assert executor.ran == [jid]
    row = await repo.get(jid)
    assert row["last_status"] == "ok" and row["lease_until"] is None

"""The `manual` schedule kind and `wake_if_idle`.

An on-demand routine is `active` with `next_run_at` NULL: no tick ever claims it,
`run_now` fires it once, and after the claim it goes back to waiting — never
`done` like a spent one-shot. `wake_if_idle` is the content publisher's wake, one
compare-and-set that never touches a lease; the concurrency pin runs against the
real PostgreSQL because a dict fake cannot express a row lock.
"""
import asyncio
from datetime import datetime, timedelta, timezone

from conftest import ensure_users

from claude_hermes.modules.scheduler.application.ports import JobOutcome
from claude_hermes.modules.scheduler.application.service import SchedulerService
from claude_hermes.modules.scheduler.domain.schedule import (
    Schedule,
    fires_between,
    next_fire,
    parse_schedule,
    period_seconds,
)
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
from claude_hermes.modules.scheduler.presentation.schemas import to_wire
from claude_hermes.modules.scheduler.presentation.tool import (
    CRON_TOOL_DESCRIPTION,
    CRON_TOOL_SCHEMA,
    build_cron_tool,
)

T0 = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)
OWNER = 1


class FakeExecutor:
    def __init__(self):
        self.ran = []

    async def run(self, job):
        self.ran.append(job["id"])
        return JobOutcome("ok", "published")


async def make(engine):
    await ensure_users(engine, owner=OWNER)
    jobs = JobRepository(engine)
    executor = FakeExecutor()
    return SchedulerService(jobs, executor=executor), jobs, executor


async def create_manual(service, name="Content publisher", schedule="manual"):
    jid, err = await service.create_job(
        name=name, schedule=schedule, prompt="publish every due item",
        deliver_chat_id=OWNER, origin_session_key=f"tg:private:{OWNER}",
        timeout_seconds=1800, now=T0, owner_id=OWNER)
    assert err is None, err
    return jid


# --- the domain -----------------------------------------------------------

def test_parse_manual_is_case_insensitive_and_has_no_clock():
    for text in ("manual", "  Manual ", "MANUAL"):
        assert parse_schedule(text, T0) == Schedule("manual", "")
    sched = Schedule("manual", "")
    assert next_fire(sched, T0) is None
    assert period_seconds(sched) is None
    assert fires_between(sched, T0, T0, T0 + timedelta(days=7)) == []


# --- create, tick, run_now ------------------------------------------------

async def test_a_manual_routine_is_active_with_no_next_run(engine):
    service, jobs, _ = await make(engine)
    jid = await create_manual(service)
    job = await jobs.get(jid)
    assert (job["kind"], job["value"]) == ("manual", "")
    assert job["state"] == "active" and job["next_run_at"] is None
    wire = to_wire(job)
    assert wire["kind"] == "manual" and wire["schedule"] == "manual"
    assert wire["next_run_at"] is None


async def test_the_tick_never_fires_it_run_now_fires_it_once_and_it_stays_active(
        engine):
    service, jobs, executor = await make(engine)
    jid = await create_manual(service)

    await service.tick(T0 + timedelta(days=3))
    assert executor.ran == []

    await service.run_now(jid, T0)
    await service.tick(T0 + timedelta(seconds=30))
    assert executor.ran == [jid]
    job = await jobs.get(jid)
    assert job["state"] == "active"            # fired, not spent
    assert job["next_run_at"] is None          # back to waiting for a wake
    assert job["lease_until"] is None          # the run handed the lease back

    await service.tick(T0 + timedelta(minutes=5))
    assert executor.ran == [jid]               # once per run_now, not per tick

    await service.run_now(jid, T0 + timedelta(minutes=6))
    await service.tick(T0 + timedelta(minutes=6, seconds=30))
    assert executor.ran == [jid, jid]


async def test_a_one_shot_in_the_past_is_still_refused(engine):
    """The manual exemption is for `manual` alone: a None first fire of a one-shot
    keeps its refusal word for word."""
    service, _, _ = await make(engine)
    jid, err = await service.create_job(
        name="late", schedule="2020-01-01T00:00:00+00:00", prompt="p",
        deliver_chat_id=OWNER, origin_session_key="tg:private:1", now=T0,
        owner_id=OWNER)
    assert jid is None and err == "Rejected: one-shot time is in the past."


async def test_edit_switches_to_and_from_manual(engine):
    service, jobs, executor = await make(engine)
    jid, err = await service.create_job(
        name="brief", schedule="0 8 * * *", prompt="p", deliver_chat_id=OWNER,
        origin_session_key="tg:private:1", now=T0, owner_id=OWNER)
    assert err is None
    assert (await jobs.get(jid))["next_run_at"] is not None

    job = await service.edit(jid, {"schedule": "manual"}, now=T0, owner_id=OWNER)
    assert (job["kind"], job["value"], job["next_run_at"]) == ("manual", "", None)
    assert job["state"] == "active"
    await service.tick(T0 + timedelta(days=2))
    assert executor.ran == []

    job = await service.edit(jid, {"schedule": "0 8 * * *"}, now=T0, owner_id=OWNER)
    assert job["kind"] == "cron"
    assert job["next_run_at"] == "2026-09-29T08:00:00+00:00"


async def test_cron_manage_refuses_manual_and_keeps_its_prompt_cache_surface(engine):
    await ensure_users(engine, 42)
    service = SchedulerService(JobRepository(engine))

    class Ctx:
        session_key = "tg:private:42"
        chat_id = 42
        owner_id = 42

    handler, definition = build_cron_tool(service, Ctx())
    out = await handler({"action": "create", "name": "pub", "schedule": " Manual ",
                         "prompt": "publish"})
    assert out["content"][0]["text"] == "On-demand routines are created in the Tasks tab."
    assert await service.list_jobs() == []
    # The description and schema are prompt-cache surface: `manual` is not
    # advertised, and nothing about the tool's declaration moved.
    assert "manual" not in CRON_TOOL_DESCRIPTION
    assert definition["description"] == CRON_TOOL_DESCRIPTION
    assert definition["schema"] == CRON_TOOL_SCHEMA
    assert set(CRON_TOOL_SCHEMA) == {"action", "name", "schedule", "prompt",
                                     "deliver_chat_id", "job_id", "timeout_seconds"}


# --- wake_if_idle ---------------------------------------------------------

async def test_wake_if_idle_is_true_once_then_false_while_the_wake_is_pending(engine):
    service, jobs, executor = await make(engine)
    jid = await create_manual(service)

    assert await service.wake_if_idle(jid, T0) is True
    assert (await jobs.get(jid))["next_run_at"] == T0.isoformat()
    assert await service.wake_if_idle(jid, T0 + timedelta(seconds=1)) is False

    await service.tick(T0 + timedelta(seconds=30))
    assert executor.ran == [jid]
    # The run is over and handed the lease back: idle again.
    assert await service.wake_if_idle(jid, (T0 + timedelta(minutes=1)).isoformat())


async def test_wake_if_idle_is_false_while_a_lease_is_live_and_never_clears_it(engine):
    service, jobs, _ = await make(engine)
    jid = await create_manual(service)
    assert await service.wake_if_idle(jid, T0)
    # Claimed, and nothing handed it back yet: a run is in flight.
    [claimed] = await jobs.claim_due(T0.isoformat(), service._next_of(T0))
    assert claimed["id"] == jid
    job = await jobs.get(jid)
    assert job["next_run_at"] is None and job["state"] == "active"
    lease = job["lease_until"]
    assert lease is not None

    assert await service.wake_if_idle(jid, T0 + timedelta(minutes=1)) is False
    job = await jobs.get(jid)
    assert job["lease_until"] == lease and job["next_run_at"] is None

    # Once the lease has expired the routine counts as idle again.
    late = datetime.fromisoformat(lease) + timedelta(seconds=1)
    assert await service.wake_if_idle(jid, late) is True


async def test_wake_if_idle_refuses_a_paused_or_clocked_routine(engine):
    service, jobs, _ = await make(engine)
    paused = await create_manual(service, name="paused")
    await service.set_state(paused, "paused")
    assert await service.wake_if_idle(paused, T0) is False
    assert (await jobs.get(paused))["next_run_at"] is None

    clocked, _ = await service.create_job(
        name="hourly", schedule="every 2h", prompt="p", deliver_chat_id=OWNER,
        origin_session_key="tg:private:1", now=T0, owner_id=OWNER)
    before = (await jobs.get(clocked))["next_run_at"]
    assert await service.wake_if_idle(clocked, T0) is False
    assert (await jobs.get(clocked))["next_run_at"] == before

    assert await service.wake_if_idle(999_999, T0) is False


async def test_two_concurrent_wakes_set_exactly_one(engine):
    """In redis mode the content tick runs on several workers at once; the wake is
    one UPDATE, so the row lock decides and exactly one caller wins."""
    service, jobs, _ = await make(engine)
    jid = await create_manual(service)
    for _ in range(5):
        results = await asyncio.gather(
            *(service.wake_if_idle(jid, T0) for _ in range(8)))
        assert results.count(True) == 1
        # Consume the wake so the next round starts idle again.
        await jobs.update(jid, next_run_at=None)

"""The tick decision: delivery, delivery events, and the loop driver.

Wave 2: the decision moved from `cron.ticker.CronTicker` into
`SchedulerService.tick`, and the executor is a port. These tests inject a fake
executor instead of monkeypatching a module attribute — the injected fake is what
the ticker actually gets in production too. Step 5: the service is built the way
`main.build_app` builds it — over the module's own `JobRepository`, the sessions
module's `ThreadRepository` and a real `TurnBus` — with no legacy facade between.

The claim itself (at-most-once, the lease, catch-up, one-shots) is pinned in
`tests/modules/scheduler/test_scheduler_claiming.py`; what stays here is which
chat a report lands in and what the delivery lane sees.
"""
from datetime import datetime, timezone

from conftest import ensure_users

from claude_hermes.modules.scheduler.application.ports import JobOutcome
from claude_hermes.modules.scheduler.application.service import SchedulerService
from claude_hermes.modules.scheduler.domain.lane import delivery_lane
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
from claude_hermes.modules.scheduler.infrastructure.ticker import CronTicker
from claude_hermes.modules.sessions.infrastructure.repositories import ThreadRepository
from claude_hermes.modules.turns.application.bus import TurnBus
from claude_hermes.modules.turns.infrastructure.publishers import InProcessPublisher
from claude_hermes.modules.turns.infrastructure.repositories import TurnEventRepository


def dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class FakeExecutor:
    def __init__(self, result=JobOutcome("ok", "output")):
        self.ran = []
        self._result = result

    async def run(self, job):
        self.ran.append(job["id"])
        return self._result


class FakeDeliverer:
    def __init__(self):
        self.delivered = []

    async def deliver(self, chat_id, text, thread_id=None):
        self.delivered.append((chat_id, text, thread_id))


class Threads:
    """scheduler's `ThreadRegistry` over the sessions module's thread writer —
    the same adapter `main.build_app` wires (`ThreadEnsure`)."""

    def __init__(self, repo):
        self._repo = repo

    async def ensure(self, session_key, channel, owner_id, now_iso, *, talked=False):
        await self._repo.upsert(session_key, channel, owner_id, now_iso, talked=talked)


def make(engine, run_result=JobOutcome("ok", "output")):
    js = JobRepository(engine)
    ex, dl = FakeExecutor(run_result), FakeDeliverer()
    bus = TurnBus(TurnEventRepository(engine), InProcessPublisher())
    service = SchedulerService(js, executor=ex, deliverer=dl, events=bus,
                               threads=Threads(ThreadRepository(engine)))
    return js, service, dl.delivered, ex.ran, bus


async def listen(bus, lane):
    seen = []

    async def listener(ev):
        seen.append(ev)

    bus.subscribe(lane, listener)
    await bus.ready(lane)
    return seen


async def test_delivery_failure_does_not_stop_other_jobs(engine):
    js, service, delivered, ran, _ = make(engine)

    class BadDeliverer:
        async def deliver(self, chat_id, text, thread_id=None):
            raise RuntimeError("telegram down")

    service.bind_delivery(deliverer=BadDeliverer())
    await js.create("a", "interval", "3600", "p", 1, "tg:private:1",
                    "2026-07-12T10:00:00+00:00")
    await js.create("b", "interval", "3600", "p", 2, "tg:private:2",
                    "2026-07-12T10:00:00+00:00")
    await service.tick(dt("2026-07-12T10:00:30"))
    assert ran == [1, 2]                       # second job still ran


async def test_delivery_targets_the_jobs_topic(engine):
    js, service, delivered, ran, _ = make(engine)
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00", deliver_thread_id=77)
    await service.tick(dt("2026-07-12T10:00:31"))
    assert delivered == [(42, "output", 77)]


def test_delivery_lane_reproduces_the_topic_or_chat_key():
    assert delivery_lane({"origin_session_key": "tg:private:42", "deliver_thread_id": None}) \
        == "tg:private:42"
    assert delivery_lane({"origin_session_key": "tg:private:42", "deliver_thread_id": 9}) \
        == "tg:private:42:t9"
    assert delivery_lane({"origin_session_key": "tg:supergroup:-5:7:t3",
                          "deliver_thread_id": 9}) == "tg:supergroup:-5:7:t9"


async def test_a_run_is_emitted_as_a_cron_turn_on_the_delivery_lane(engine):
    js, service, delivered, ran, bus = make(engine)
    seen = await listen(bus, "tg:private:42:t9")
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00", deliver_thread_id=9)
    await service.tick(dt("2026-07-12T10:00:31"))
    assert [e.type for e in seen] == ["turn.start", "text.block", "turn.end"]
    assert seen[0].payload["channel"] == "cron" and seen[0].payload["job_id"] == 1
    assert seen[0].payload["run_id"]
    assert seen[1].payload == {"text": "output"}
    assert seen[2].payload["status"] == "ok"
    assert seen[2].payload["usage"] is None           # a delivery fills no window
    thread = await ThreadRepository(engine).get("tg:private:42:t9")
    assert thread["channel"] == "telegram"
    assert delivered == [(42, "output", 9)]          # Telegram delivery unchanged


async def test_a_failed_run_is_emitted_with_its_status(engine):
    js, service, delivered, ran, bus = make(engine,
                                            run_result=JobOutcome("error", "boom"))
    seen = await listen(bus, "tg:private:42")
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00")
    await service.tick(dt("2026-07-12T10:00:31"))
    assert seen[1].payload == {"text": "⚠️ boom"}
    assert seen[2].payload["status"] == "error"


async def test_the_loop_driver_calls_tick():
    """CronTicker owns the clock and the sleep and nothing else."""
    ticks = []

    class FakeService:
        async def tick(self, now):
            ticks.append(now)

    await CronTicker(FakeService(), 0.01).tick(dt("2026-07-12T10:00:00"))
    assert ticks == [dt("2026-07-12T10:00:00")]


async def test_the_ticker_runs_its_extras_on_the_same_clock_and_isolates_them():
    """`extra` is how inline mode reaches the work that rides the cron clock
    without a second one — today the turn reaper. Each one is isolated: a reap
    that raises must not stop the clock for the jobs, and a job that raises must
    not stop the reap."""
    ticks, reaped = [], []

    class FakeService:
        async def tick(self, now):
            ticks.append(now)

    async def boom(now):
        raise RuntimeError("the reaper fell over")

    async def reap(now):
        reaped.append(now)

    when = dt("2026-07-12T10:00:00")
    await CronTicker(FakeService(), 0.01, extra=(boom, reap)).tick(when)

    assert ticks == [when] and reaped == [when]


async def test_the_cron_turn_and_the_delivery_lane_carry_the_job_owner(engine):
    """Wave 4a (D6/D7): the report is a turn of the person who created the routine.
    Before this wave `turn.start` said `user_id: 0` and the delivery lane's thread
    row was ensured with no owner — invisible to the very user it was for."""
    js, service, delivered, ran, bus = make(engine)
    await ensure_users(engine, 42, owner=42)
    seen = await listen(bus, "tg:private:42:t9")
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00", deliver_thread_id=9, owner_id=42)
    await service.tick(dt("2026-07-12T10:00:31"))
    assert seen[0].payload["user_id"] == 42
    thread = await ThreadRepository(engine).get("tg:private:42:t9")
    assert thread["owner_user_id"] == 42


async def test_the_cron_turn_carries_the_resolved_pair_and_the_model_that_ran(engine):
    """AC #11: `turn.start` names the pair the executor resolved and `turn.end` the
    model that actually answered — the same rule a foreground turn follows, so a
    report's label is never the model that was merely asked for."""
    outcome = JobOutcome("ok", "output", "claude-opus-5-5", "high", "claude-sonnet-5")
    js, service, delivered, ran, bus = make(engine, run_result=outcome)
    seen = await listen(bus, "tg:private:42")
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00", model="claude-opus-5-5", effort="high")
    await service.tick(dt("2026-07-12T10:00:31"))

    assert seen[0].payload["model"] == "claude-opus-5-5"
    assert seen[0].payload["effort"] == "high"
    assert seen[2].payload["model"] == "claude-sonnet-5"       # what answered
    assert seen[2].payload["effort"] == "high"
    assert seen[2].payload["fallback_from"] == "claude-opus-5-5"


async def test_a_run_that_names_no_model_still_emits_the_keys(engine):
    """The keys are additive and always present: an executor that reports no model
    — a fake, or one that timed out before anything answered — leaves `turn.end`
    naming the resolved model and `fallback_from` null, never a missing key."""
    js, service, delivered, ran, bus = make(
        engine, run_result=JobOutcome("ok", "output", "claude-sonnet-5"))
    seen = await listen(bus, "tg:private:42")
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00")
    await service.tick(dt("2026-07-12T10:00:31"))

    assert seen[2].payload["model"] == "claude-sonnet-5"
    assert seen[2].payload["fallback_from"] is None
    assert seen[0].payload["effort"] is None


async def test_an_executor_that_answers_with_a_bare_pair_still_delivers(engine):
    """`JobOutcome.of` accepts the `(status, text)` the port answered with before
    models were a choice: the run is delivered, and the model keys are null."""
    js, service, delivered, ran, bus = make(engine, run_result=("ok", "output"))
    seen = await listen(bus, "tg:private:42")
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00")
    await service.tick(dt("2026-07-12T10:00:31"))

    assert delivered == [(42, "output", None)]
    assert seen[0].payload["model"] is None and seen[2].payload["model"] is None

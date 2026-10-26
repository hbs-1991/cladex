"""The five task bodies, end to end over the inline queue.

Inline mode is not a stand-in for redis mode here — it IS the deployment the whole
suite runs, and the task bodies are the same objects a worker would import. What
these tests pin is the seam between the two: a service enqueues a NAME and
primitives, the registry resolves it, and the body finds its services on the
bound `AppContext` rather than in its arguments.

`tasks.bind` is process-global, so every test binds its own context and the last
one wins; nothing here reads a context it did not just bind.
"""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from claude_hermes.core.config import Config
from claude_hermes.core.events import TurnObservation
from claude_hermes.core.meta import MetaStore
from claude_hermes.core.tasks import InlineQueue
from claude_hermes.modules.learning.application.services.loop import LearningLoop
from claude_hermes.modules.learning.application.services.nudges import NudgeTracker
from claude_hermes.modules.learning.application.services.review import ReviewService
from claude_hermes.modules.learning.domain.constants import REVIEW_GATE_META_KEY
from claude_hermes.modules.learning.infrastructure.dispatch import QueueReviewDispatcher
from claude_hermes.modules.learning.infrastructure.review_gate import MetaReviewGate
from claude_hermes.modules.scheduler.application.service import SchedulerService
from claude_hermes.modules.scheduler.infrastructure.dispatch import QueueJobDispatcher
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
from claude_hermes.workers import tasks

T0 = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)


class FakeExecutor:
    def __init__(self):
        self.ran = []

    async def run(self, job):
        self.ran.append(job["id"])
        return "ok", "output"


def bind(ctx, monkeypatch) -> None:
    """Bind a context for the duration of one test and put the old one back —
    the binding is a module global, and a leaked one would decide the next
    test's result."""
    monkeypatch.setattr(tasks, "_ctx", ctx, raising=False)


# --- the registry ---------------------------------------------------------

def test_the_five_names_are_registered_under_the_names_the_dispatchers_enqueue():
    """A name spelled differently on the two sides is a task that is enqueued and
    never runs; `UnknownTask` at the call site is what prevents that, and it can
    only fire if the registry holds exactly these five."""
    assert tasks.registry.names() == [
        "curator_tick", "learning_review", "run_job", "run_turn", "scheduler_tick"]


async def test_a_body_with_no_context_says_so(monkeypatch):
    """A worker that never ran its startup hook must fail by name, not with an
    `AttributeError` on None three frames down. (`_ctx` is cleared explicitly:
    any earlier `build_app()` in this process has already bound one.)"""
    bind(None, monkeypatch)
    with pytest.raises(tasks.TasksNotBound):
        tasks.context()


# --- scheduler ------------------------------------------------------------

async def test_scheduler_tick_claims_and_run_job_executes(monkeypatch, engine):
    """The whole loop the `scheduler` container drives: one enqueued
    `scheduler_tick` claims the due fire, `QueueJobDispatcher` puts `run_job` on
    the same queue, and the job runs — with nothing but an int and a string
    passing between the two."""
    repo = JobRepository(engine)
    queue = InlineQueue(tasks.registry)
    executor = FakeExecutor()
    scheduler = SchedulerService(repo, executor=executor,
                                 dispatcher=QueueJobDispatcher(queue))
    bind(SimpleNamespace(scheduler=scheduler, turns=FakeTurns()), monkeypatch)

    jid = await repo.create("j", "interval", "3600", "p", 1, "tg:private:1",
                            T0.isoformat())
    # `scheduler_tick` reads its own clock, so the fire has to be in the past.
    monkeypatch.setattr(tasks, "datetime", _FrozenClock(T0))
    await queue.enqueue("scheduler_tick")
    await queue.drain()

    assert executor.ran == [jid]
    row = await repo.get(jid)
    assert row["last_status"] == "ok"
    # The lease is released by `execute_job`, whichever process ran it.
    assert row["lease_until"] is None


class FakeTurns:
    """`TurnLauncher` as `scheduler_tick` sees it: one call, one clock."""

    def __init__(self):
        self.reaped = []

    async def reap(self, now):
        self.reaped.append(now)
        return 0


async def test_scheduler_tick_also_reaps_the_turns_nobody_will_finish(monkeypatch):
    """The reaper rides the cron clock rather than one of its own — a lane whose
    worker died is unusable until something frees it, and "something" should not
    be a second thing to get wrong about time. Both calls get the SAME `now`."""
    ticks, turns = [], FakeTurns()

    class Scheduler:
        async def tick(self, now):
            ticks.append(now)

    queue = InlineQueue(tasks.registry)
    bind(SimpleNamespace(scheduler=Scheduler(), turns=turns), monkeypatch)
    monkeypatch.setattr(tasks, "datetime", _FrozenClock(T0))

    await queue.enqueue("scheduler_tick")
    await queue.drain()

    assert ticks == [T0] and turns.reaped == [T0]


# --- turns ----------------------------------------------------------------

async def test_run_turn_hands_the_id_to_the_run_service_and_nothing_else(monkeypatch):
    """The whole argument list is one primitive: everything else about the turn is
    on the row, where a worker in another process can read it — and where the
    claim, the lease and the cancel flag live."""
    from claude_hermes.modules.turns.infrastructure.dispatch import QueueTurnDispatcher

    ran = []

    class Runs:
        async def run(self, turn_id):
            ran.append(turn_id)

    queue = InlineQueue(tasks.registry)
    bind(SimpleNamespace(turn_runs=Runs()), monkeypatch)
    await QueueTurnDispatcher(queue).dispatch("t-1")
    await queue.drain()

    assert ran == ["t-1"]


class _FrozenClock:
    """`datetime.now(tz)` only — the task body uses nothing else."""

    def __init__(self, when):
        self._when = when

    def now(self, tz=None):
        return self._when


# --- learning -------------------------------------------------------------

class FakeSessions:
    async def get(self, session_key):
        return {"sdk_session_id": "sdk-1", "system_append": ""}


class FakeUsage:
    def list_skill_dirs(self):
        return set()

    def mark_created(self, *a, **kw):
        raise AssertionError("no skill dir appeared")


class GateWatchingFork:
    """Answers "was the gate held while the fork ran?" — the only moment that
    matters, and the one a boolean on the loop object could not answer once the
    fork moved to another process."""

    def __init__(self, gate):
        self._gate = gate
        self.held_during = None
        self.kinds = None

    async def run_review(self, *, prompt, system_append, resume, skills_dir,
                         wiki_root, touched, workspace=None, owner_id=0):
        self.held_during = await self._gate.running()
        return "done"


class FakeScopes:
    """`LearningScopes` over a dict, as the task bodies see it: `for_user`,
    `owner`, `user_ids`, `observes`. `0` resolves to the owner, like the real
    registry. `observes` is the learning grant at the moment of asking: the
    owner always, everyone else unless they are in `withdrawn` — whose value,
    when it is an exception, is what the lookup raises for that id."""

    def __init__(self, owner: int, scopes: dict, withdrawn=None):
        self._owner = owner
        self._scopes = scopes
        self.withdrawn: dict = dict(withdrawn or {})
        self.asked: list[int] = []

    async def for_user(self, user_id):
        self.asked.append(user_id)
        return self._scopes[user_id or self._owner]

    async def owner(self):
        return self._scopes[self._owner]

    async def user_ids(self):
        return list(self._scopes)

    async def observes(self, user_id):
        uid = user_id or self._owner
        if uid == self._owner:
            return True
        answer = self.withdrawn.get(uid)
        if isinstance(answer, BaseException):
            raise answer
        return uid not in self.withdrawn


def learning_over(review, owner: int = 1, others: dict | None = None):
    """`ctx.learning` as the review task sees it: the owner's review service
    plus, optionally, other users' keyed by id."""
    scopes = FakeScopes(owner, {owner: SimpleNamespace(user_id=owner, review=review),
                                **{uid: SimpleNamespace(user_id=uid, review=r)
                                   for uid, r in (others or {}).items()}})
    return SimpleNamespace(scopes=scopes), scopes


async def test_learning_review_claims_the_gate_and_clears_it(tmp_path, monkeypatch, engine):
    meta = MetaStore(engine)
    gate = MetaReviewGate(meta, timeout_seconds=900.0)
    fork = GateWatchingFork(gate)
    review = ReviewService(sessions=FakeSessions(), usage=FakeUsage(), fork=fork,
                           skills_dir=tmp_path, wiki_root=tmp_path, gate=gate)
    queue = InlineQueue(tasks.registry)
    learning, _ = learning_over(review)
    bind(SimpleNamespace(learning=learning), monkeypatch)

    assert await gate.running() is False
    await QueueReviewDispatcher(queue).dispatch("tg:private:1", ["wiki", "skill"])
    await queue.drain()

    assert fork.held_during is True, "the curator could have started a pass"
    assert await gate.running() is False
    # Cleared, not left to expire: the row is there and empty.
    assert await meta.get(REVIEW_GATE_META_KEY) == ""


class Recorder:
    def __init__(self):
        self.seen = []

    async def run(self, session_key, kinds):
        self.seen.append((session_key, kinds))


async def test_the_kinds_arrive_as_a_list_and_reach_the_service(monkeypatch):
    """JSON has no set. The dispatcher sends a list and the body hands that list
    over unchanged — the service is what normalizes it."""
    recorder = Recorder()
    queue = InlineQueue(tasks.registry)
    learning, _ = learning_over(recorder)
    bind(SimpleNamespace(learning=learning), monkeypatch)
    await QueueReviewDispatcher(queue).dispatch("tg:private:7", {"wiki"})
    await queue.drain()

    assert recorder.seen == [("tg:private:7", ["wiki"])]


async def test_learning_review_runs_in_the_scope_the_nudge_named(monkeypatch):
    """`owner_id` travels with the payload (4b, D3): user 7's nudge runs USER
    7's review service, never the owner's."""
    owner, seven = Recorder(), Recorder()
    queue = InlineQueue(tasks.registry)
    learning, scopes = learning_over(owner, others={7: seven})
    bind(SimpleNamespace(learning=learning), monkeypatch)
    await QueueReviewDispatcher(queue).dispatch("tg:private:7", ["wiki"], owner_id=7)
    await queue.drain()

    assert seven.seen == [("tg:private:7", ["wiki"])] and owner.seen == []
    assert scopes.asked == [7]


async def test_a_legacy_payload_without_an_owner_runs_the_owners_review(monkeypatch):
    """A task queued by a 4a agent and drained by this worker carries no
    `owner_id`; it is the deploy owner's review, which `for_user(0)` resolves."""
    owner = Recorder()
    queue = InlineQueue(tasks.registry)
    learning, scopes = learning_over(owner)
    bind(SimpleNamespace(learning=learning), monkeypatch)
    await queue.enqueue("learning_review", session_key="tg:private:1", kinds=["wiki"])
    await queue.drain()

    assert owner.seen == [("tg:private:1", ["wiki"])]
    assert scopes.asked == [0]


# --- learning: the grant at run time ---------------------------------------

def user_scope(tmp_path, meta, user_id: int):
    """A real review stack for `user_id`: its gate over `meta`, a nudge tracker
    that fires on every turn, and a fork that records whether it ever ran and
    whether the gate was held when it did."""
    gate = MetaReviewGate(meta, timeout_seconds=900.0)
    fork = GateWatchingFork(gate)
    skills = tmp_path / f"skills-{user_id}"
    skills.mkdir()
    review = ReviewService(sessions=FakeSessions(), usage=FakeUsage(), fork=fork,
                           skills_dir=skills, wiki_root=tmp_path, gate=gate,
                           owner_id=user_id)
    scope = SimpleNamespace(
        user_id=user_id, gate=gate, review=review,
        sidecar=SimpleNamespace(bump_use=lambda *a: None, bump_patch=lambda *a: None),
        nudges=NudgeTracker(meta, Config(wiki_nudge_interval=1,
                                         skill_nudge_interval=100)))
    return scope, gate, fork


async def nudge_then_drain(tmp_path, monkeypatch, *, withdraw=None):
    """User 7's turn nudges a review — `LearningLoop.after_turn` claims their
    gate and enqueues — then, while the task sits on the queue, the owner's
    answer about user 7 becomes `withdraw` (`None`: still granted), and the
    queue drains."""
    meta = FakeMeta()
    owner_scope, _, _ = user_scope(tmp_path, FakeMeta(), 1)
    scope, gate, fork = user_scope(tmp_path, meta, 7)
    scopes = FakeScopes(1, {1: owner_scope, 7: scope})
    queue = InlineQueue(tasks.registry)
    bind(SimpleNamespace(learning=SimpleNamespace(scopes=scopes)), monkeypatch)

    await LearningLoop(scopes, QueueReviewDispatcher(queue)).after_turn(
        "tg:private:7", TurnObservation(), owner_id=7)
    assert queue.pending == 1 and await gate.running() is True   # claimed at dispatch
    if withdraw is not None:
        scopes.withdrawn[7] = withdraw
    await queue.drain()
    return gate, fork, meta


async def test_a_review_queued_before_the_grant_was_withdrawn_runs_no_fork(
        tmp_path, monkeypatch, caplog):
    """The grant is asked again when the task RUNS: a user whose learning the
    owner withdrew while their review sat on the queue is not forked after the
    fact. And the gate the enqueuing turn claimed goes back at once — only
    `ReviewService.run` would otherwise release it, so an early return that
    kept it would block their curator for `review_timeout_seconds`."""
    with caplog.at_level(logging.INFO):
        gate, fork, meta = await nudge_then_drain(tmp_path, monkeypatch,
                                                  withdraw=True)

    assert fork.held_during is None, "a fork ran for a withdrawn user"
    assert await gate.running() is False
    assert meta.rows[REVIEW_GATE_META_KEY] == ""            # released, not expired
    [line] = [r for r in caplog.records if "no learning grant" in r.getMessage()]
    assert "7" in line.getMessage()


async def test_a_granted_users_queued_review_still_runs(tmp_path, monkeypatch):
    """The check is a filter, not a new obstacle: a user whose grant is still
    on gets their review, gate held around the fork and released after."""
    gate, fork, _ = await nudge_then_drain(tmp_path, monkeypatch)

    assert fork.held_during is True
    assert await gate.running() is False


async def test_a_failed_grant_lookup_runs_no_fork_and_hands_the_gate_back(
        tmp_path, monkeypatch):
    """A lookup that raises is not a grant: no fork, and the gate still goes
    back rather than sitting out its lease. The error itself is the queue's to
    log (inline: `task_failed`; redis: the broker)."""
    gate, fork, _ = await nudge_then_drain(tmp_path, monkeypatch,
                                           withdraw=RuntimeError("db blip"))

    assert fork.held_during is None
    assert await gate.running() is False


async def test_the_owners_review_never_depends_on_a_grant(monkeypatch):
    """The owner — and `0`, a 4a payload — is always observed, and the body
    runs the review exactly as before."""
    owner = Recorder()
    queue = InlineQueue(tasks.registry)
    learning, scopes = learning_over(owner)
    scopes.withdrawn[1] = True                   # would refuse, if it were asked
    bind(SimpleNamespace(learning=learning), monkeypatch)
    await queue.enqueue("learning_review", session_key="tg:private:1", kinds=["wiki"])
    await QueueReviewDispatcher(queue).dispatch("tg:private:1", ["skill"], owner_id=1)
    await queue.drain()

    assert owner.seen == [("tg:private:1", ["wiki"]), ("tg:private:1", ["skill"])]


# --- curator --------------------------------------------------------------

class FakeMeta:
    def __init__(self, **rows):
        self.rows = dict(rows)

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


class FakeCurator:
    def __init__(self, log, user_id, boom=False):
        self.log, self.user_id, self.boom = log, user_id, boom
        self.during = None

    async def run_pass(self):
        if self.boom:
            raise RuntimeError("boom")
        if self.during is not None:
            await self.during(self.user_id)
        self.log.append(self.user_id)
        return {"ran": True, "counts": {}}


class SweepScopes(FakeScopes):
    """The sweep's view: every scope has `meta` (the owner's is unsuffixed) and
    a `curator`."""

    def __init__(self, owner: int, others=(), *, boom=frozenset()):
        self.log: list[int] = []
        self.meta = FakeMeta()
        scopes = {}
        for uid in (owner, *others):
            scopes[uid] = SimpleNamespace(
                user_id=uid, is_owner=uid == owner,
                meta=self.meta if uid == owner else FakeMeta(),
                curator=FakeCurator(self.log, uid, boom=uid in boom))
        super().__init__(owner, scopes)


async def test_curator_tick_sweeps_every_scope_without_a_lock_when_there_is_no_redis(
        monkeypatch):
    """Inline mode has one process, so there is nobody to race and no lock to
    take. The passes themselves are unchanged — `curator_tick` only decides that
    at most one worker is asking, and walks the owner first."""
    scopes = SweepScopes(1, (7,))
    queue = InlineQueue(tasks.registry)
    bind(SimpleNamespace(redis=None, cfg=SimpleNamespace(curator_max_turns=30),
                         learning=SimpleNamespace(scopes=scopes)),
         monkeypatch)
    await queue.enqueue("curator_tick")
    await queue.drain()

    assert scopes.log == [1, 7]


async def test_a_curator_pass_that_raises_is_swallowed(monkeypatch):
    """A background task has nobody to return an error to, and a curator that
    dies on one bad pass stops ageing the library for the life of the process
    — and must not stop the NEXT user's pass either."""
    scopes = SweepScopes(1, (7,), boom={1})
    queue = InlineQueue(tasks.registry)
    bind(SimpleNamespace(redis=None, cfg=SimpleNamespace(curator_max_turns=30),
                         learning=SimpleNamespace(scopes=scopes)),
         monkeypatch)
    await queue.enqueue("curator_tick")
    await queue.drain()          # no exception reaches here

    assert scopes.log == [7]


async def test_curator_tick_holds_the_sweep_lock_and_one_lock_per_user(monkeypatch):
    """Redis mode (R-1): `lock:curator:sweep` around the whole sweep, and the
    per-scope key around each pass — the owner keeps the legacy `lock:curator`,
    user 7 gets `lock:curator:7`. Checked from INSIDE each pass, which is the
    only moment the keys should exist."""
    from fakeredis.aioredis import FakeRedis

    redis = FakeRedis(decode_responses=True)
    scopes = SweepScopes(1, (7,))
    seen = {}

    async def during(uid):
        seen[uid] = sorted(k for k in await redis.keys("lock:*"))

    for scope in scopes._scopes.values():
        scope.curator.during = during
    queue = InlineQueue(tasks.registry)
    bind(SimpleNamespace(redis=redis, cfg=SimpleNamespace(curator_max_turns=30),
                         learning=SimpleNamespace(scopes=scopes)),
         monkeypatch)
    await queue.enqueue("curator_tick")
    await queue.drain()

    assert scopes.log == [1, 7]
    assert seen == {1: ["lock:curator", "lock:curator:sweep"],
                    7: ["lock:curator:7", "lock:curator:sweep"]}
    assert await redis.keys("lock:*") == []            # all released
    await redis.aclose()


async def test_a_second_tick_skips_while_a_sweep_is_running(monkeypatch):
    """A sweep of N users outlives the 15-minute tick; without the outer lock
    the next tick would start a second sweep behind the first."""
    from fakeredis.aioredis import FakeRedis

    redis = FakeRedis(decode_responses=True)
    await redis.set("lock:curator:sweep", "somebody-else", px=60_000)
    scopes = SweepScopes(1, (7,))
    queue = InlineQueue(tasks.registry)
    bind(SimpleNamespace(redis=redis, cfg=SimpleNamespace(curator_max_turns=30),
                         learning=SimpleNamespace(scopes=scopes)),
         monkeypatch)
    await queue.enqueue("curator_tick")
    await queue.drain()

    assert scopes.log == []
    assert await redis.get("lock:curator:sweep") == "somebody-else"   # not ours to drop
    await redis.aclose()


def test_the_owner_keeps_the_legacy_curator_lock_key():
    assert tasks.curator_lock_key(1, 1) == "lock:curator"
    assert tasks.curator_lock_key(7, 1) == "lock:curator:7"


# --- the taskiq producer's URL ---------------------------------------------
# No taskiq needed: `taskiq_app` imports it inside functions only, and the two
# assertions below never reach one.

async def test_the_taskiq_queue_captures_its_url_instead_of_reading_the_environment(
        monkeypatch):
    """The broker is built on the FIRST ENQUEUE, and in the agent that is a nudge
    from `after_turn` — minutes after `run()` popped CH_REDIS_URL. A queue that
    read the environment there would raise `RuntimeError` on every dispatch and
    no review would ever be queued, in the one mode that has a worker to run it.
    """
    from claude_hermes.workers import taskiq_app

    monkeypatch.setenv("CH_REDIS_URL", "redis://ignored:6379/0")
    queue = taskiq_app.TaskiqQueue(tasks.registry, url="redis://from-cfg:6379/1")
    monkeypatch.delenv("CH_REDIS_URL")               # exactly what run() does next

    dialled = []

    class FakeBroker:
        async def startup(self):
            dialled.append("startup")

    class FakeTask:
        def __init__(self):
            self.kicks = []

        async def kiq(self, **kw):
            self.kicks.append(kw)

    def fake_ensure(url=None):
        dialled.append(url)
        return FakeBroker()

    run_job = FakeTask()
    monkeypatch.setattr(taskiq_app, "_ensure_broker", fake_ensure)
    monkeypatch.setattr(taskiq_app, "_TASKS", {"run_job": run_job})

    await queue.enqueue("run_job", job_id=1, fire_at=T0.isoformat())

    assert dialled[0] == "redis://from-cfg:6379/1"   # the config's URL, not the env
    assert run_job.kicks == [{"job_id": 1, "fire_at": T0.isoformat()}]


def test_a_queue_built_with_no_url_and_no_environment_fails_at_construction(
        monkeypatch):
    """The fallback is the CLI's (`taskiq worker …:broker` reads the attribute
    while the environment is intact). If a producer ever comes through it, it has
    to fail where it is built — `main.build_app()`, which runs before the pop —
    and not silently at the first nudge."""
    from claude_hermes.workers import taskiq_app

    monkeypatch.delenv("CH_REDIS_URL", raising=False)
    with pytest.raises(RuntimeError):
        taskiq_app.TaskiqQueue(tasks.registry)


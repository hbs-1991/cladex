"""The review on the queue: the whole round-trip, inline, and the lease itself.

These are the two halves of Wave 3a's learning change.

The round-trip is the one that matters: a nudge goes through `LearningLoop` →
`QueueReviewDispatcher` → `core.tasks.InlineQueue` → the registered
`learning_review` task → `ReviewService` → the fork, with the gate claimed and
released around the pass. Nothing here fakes the queue, because the queue is the
seam being introduced; the fork and the sessions row are fakes because they are
the SDK and another module.

The lease tests are the reason the gate is an expiry and not a boolean: a worker
that dies mid-pass never clears its claim, and the curator has to start again
anyway once the claim runs out.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from claude_hermes.core.config import Config
from claude_hermes.core.events import TurnObservation
from claude_hermes.core.tasks import InlineQueue, TaskRegistry, UnknownTask
from claude_hermes.modules.learning.application.services.curator import CuratorService
from claude_hermes.modules.learning.application.services.loop import LearningLoop
from claude_hermes.modules.learning.application.services.nudges import NudgeTracker
from claude_hermes.modules.learning.application.services.review import ReviewService
from claude_hermes.modules.learning.domain.constants import REVIEW_GATE_META_KEY
from claude_hermes.modules.learning.infrastructure.dispatch import (
    LEARNING_REVIEW_TASK,
    QueueReviewDispatcher,
)
from claude_hermes.modules.learning.infrastructure.review_gate import MetaReviewGate
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar

NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc)


class FakeMeta:
    """`core.meta.MetaStore` in a dict — the gate is the only thing that writes
    `learning_review_until`, so a dict is the whole table that matters here."""

    def __init__(self, **rows):
        self.rows = dict(rows)

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


class FakeSessions:
    """`SessionSnapshotPort`, owned by the sessions module in production."""

    async def get(self, session_key):
        return {"sdk_session_id": f"sdk-{session_key}",
                "system_append": "APPEND"}


class FakeFork:
    """`ForkRunner`. Records the review calls and, optionally, checks the gate
    from inside the pass — which is the only moment it should be claimed."""

    def __init__(self, gate=None):
        self.calls = []
        self.gate_seen = []
        self._gate = gate

    async def run_review(self, *, prompt, system_append, resume, skills_dir,
                         wiki_root, touched, workspace=None, owner_id=0):
        self.calls.append({"resume": resume, "system_append": system_append})
        if self._gate is not None:
            self.gate_seen.append(await self._gate.running())
        return "saved 1 page"

    async def run_consolidation(self, *, prompt, skills_dir,
                                workspace=None):  # pragma: no cover
        raise AssertionError("the review never consolidates")


class OneScope:
    """`LearningScopes` with one scope: every id resolves to it, as the real
    registry does for `0`. The queue is the seam under test, not the registry."""

    def __init__(self, scope):
        self.scope = scope
        self.asked = []

    async def for_user(self, user_id):
        self.asked.append(user_id)
        return self.scope


def build(tmp_path, *, meta=None, timeout=900.0, now=None):
    """The inline wiring `main.build_app()` does: one registry, one queue, one
    gate, and the task body that a worker would otherwise run."""
    skills = tmp_path / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    sidecar = UsageSidecar(skills)
    meta = meta if meta is not None else FakeMeta()
    gate = MetaReviewGate(meta, timeout_seconds=timeout, now=now or (lambda: NOW))
    fork = FakeFork(gate)
    review = ReviewService(sessions=FakeSessions(), usage=sidecar, fork=fork,
                           skills_dir=skills, wiki_root=tmp_path / "wiki", gate=gate)
    scopes = OneScope(SimpleNamespace(
        user_id=1, sidecar=sidecar, gate=gate, review=review,
        nudges=NudgeTracker(meta, Config(wiki_nudge_interval=1,
                                         skill_nudge_interval=100))))

    registry = TaskRegistry()

    @registry.task(LEARNING_REVIEW_TASK)
    async def learning_review(*, session_key: str, kinds: list[str],
                              owner_id: int = 0) -> None:
        # Exactly what `workers/tasks.py` does: primitives in, scope out.
        await (await scopes.for_user(owner_id)).review.run(session_key, kinds)

    queue = InlineQueue(registry, {LEARNING_REVIEW_TASK: 1})
    loop = LearningLoop(scopes, QueueReviewDispatcher(queue))
    return loop, queue, gate, fork, meta


# ------------------------------------------------------------ round-trip ----

async def test_a_nudge_runs_the_review_through_the_inline_queue(tmp_path):
    loop, queue, gate, fork, _ = build(tmp_path)

    await loop.after_turn("tg:private:7", TurnObservation())
    assert queue.pending == 1                 # enqueued, not yet run
    await queue.drain()

    assert len(fork.calls) == 1
    # The review resumed the lane's own SDK session with the byte-identical
    # system append — the invariant the queue hop must not disturb.
    assert fork.calls[0] == {"resume": "sdk-tg:private:7", "system_append": "APPEND"}


async def test_the_gate_is_claimed_during_the_pass_and_cleared_after(tmp_path):
    loop, queue, gate, fork, meta = build(tmp_path)
    assert await gate.running() is False

    await loop.after_turn("k", TurnObservation())
    await queue.drain()

    assert fork.gate_seen == [True]            # claimed while the fork ran
    assert await gate.running() is False       # released on the way out
    assert meta.rows[REVIEW_GATE_META_KEY] == ""


async def test_a_failing_review_still_clears_the_gate(tmp_path):
    """The `finally` matters more than the expiry: a fork that raises must not
    cost the curator a full `review_timeout_seconds`."""
    loop, queue, gate, fork, _ = build(tmp_path)

    async def boom(**kw):
        raise RuntimeError("fork died")

    fork.run_review = boom
    await loop.after_turn("k", TurnObservation())
    await queue.drain()                        # InlineQueue logs and swallows
    assert await gate.running() is False


async def test_two_nudges_before_the_task_runs_dispatch_exactly_once(tmp_path):
    """The window the lease has to cover. `ReviewService` claims the gate in the
    process that RUNS the pass, which in redis mode is a worker that has not
    picked the task up yet — so between the enqueue and that claim the gate is
    free, and a nudge from another lane would queue a second review. Two forks
    writing one skill tree is exactly what Wave 2's task handle prevented, so the
    dispatch claims the lease itself and the service only extends it."""
    loop, queue, gate, fork, _ = build(tmp_path)

    await loop.after_turn("tg:private:1", TurnObservation())
    # Nothing has run yet: the task is on the queue, the worker is "busy".
    assert queue.pending == 1
    assert await gate.running() is True
    await loop.after_turn("tg:private:2", TurnObservation())
    assert queue.pending == 1                  # dropped, not queued

    await queue.drain()
    assert len(fork.calls) == 1
    assert await gate.running() is False       # and the pass released it


async def test_a_failed_enqueue_hands_the_lease_back(tmp_path):
    """Claiming before the dispatch means claiming before it can fail. A lease
    left behind by an enqueue that never reached Redis would block the curator
    for a full `review_timeout_seconds` with no pass to show for it."""
    loop, queue, gate, _, _ = build(tmp_path)

    class Broken:
        async def dispatch(self, session_key, kinds, owner_id=0):
            raise RuntimeError("redis is down")

    loop._dispatcher = Broken()
    with pytest.raises(RuntimeError):
        await loop.after_turn("k", TurnObservation())
    assert await gate.running() is False


async def test_an_unregistered_task_fails_at_the_call_site(tmp_path):
    """A typo in the task name is found by the process that enqueued it, not by
    the absence of an effect three days later."""
    queue = InlineQueue(TaskRegistry())
    with pytest.raises(UnknownTask):
        await QueueReviewDispatcher(queue).dispatch("k", ["wiki"])


async def test_the_dispatcher_always_sends_the_owner_id_as_an_int(tmp_path):
    """The wire shape must not depend on who nudged: a 4b payload is one shape,
    so a worker on the same deploy resolves the same scope every time. `0` is
    sent explicitly, not omitted."""
    seen = []

    class Queue:
        async def enqueue(self, name, **kwargs):
            seen.append((name, kwargs))

    await QueueReviewDispatcher(Queue()).dispatch("k", {"wiki"})
    await QueueReviewDispatcher(Queue()).dispatch("k", ["wiki"], owner_id=7)
    assert seen == [
        (LEARNING_REVIEW_TASK, {"session_key": "k", "kinds": ["wiki"], "owner_id": 0}),
        (LEARNING_REVIEW_TASK, {"session_key": "k", "kinds": ["wiki"], "owner_id": 7}),
    ]


# ------------------------------------------------------------ the lease -----

async def test_the_gate_expires_after_a_dead_worker(tmp_path):
    """Nothing releases the claim — the worker is gone. The clock does it."""
    meta = FakeMeta()
    gate = MetaReviewGate(meta, timeout_seconds=900.0, now=lambda: NOW)
    await gate.claim()
    assert await gate.running() is True

    later = NOW + timedelta(seconds=901)
    reader = MetaReviewGate(meta, timeout_seconds=900.0, now=lambda: later)
    assert await reader.running() is False

    # ... and the curator, which is what the whole gate exists to unblock.
    assert (await curator_over(tmp_path, meta, reader).run_pass())["ran"] is True


async def test_a_live_claim_blocks_the_curator(tmp_path):
    meta = FakeMeta()
    gate = MetaReviewGate(meta, timeout_seconds=900.0, now=lambda: NOW)
    await gate.claim()
    result = await curator_over(tmp_path, meta, gate).run_pass()
    assert result == {"ran": False, "reason": "review running"}


async def test_an_unreadable_gate_row_reads_as_free(tmp_path):
    """A hand-edited or half-written row must not wedge the curator forever."""
    gate = MetaReviewGate(FakeMeta(**{REVIEW_GATE_META_KEY: "not-a-timestamp"}),
                          timeout_seconds=900.0, now=lambda: NOW)
    assert await gate.running() is False


def test_a_lease_that_never_expires_is_refused():
    with pytest.raises(ValueError):
        MetaReviewGate(FakeMeta(), timeout_seconds=0)


# ------------------------------------------------------------- helpers ------

def curator_over(tmp_path, meta, gate):
    """A `CuratorService` whose only interesting gate is the review one."""
    from claude_hermes.core import snapshots
    from claude_hermes.modules.learning.domain.constants import LAST_RUN_META_KEY
    from claude_hermes.modules.learning.infrastructure.report import ReportWriter
    from claude_hermes.modules.learning.infrastructure.skill_archive import SkillArchive

    home = tmp_path / "home"
    skills = home / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    meta.rows.setdefault(LAST_RUN_META_KEY, (NOW - timedelta(days=30)).isoformat())
    sidecar = UsageSidecar(skills)

    class NoActivity:
        async def last_message_ts(self):
            return None

    return CuratorService(
        cfg=Config(curator_interval_hours=1.0, curator_min_idle_hours=0.0),
        logs_dir=home / "logs", skills_dir=skills, wiki_root=home / "wiki",
        usage=sidecar, meta=meta, activity=NoActivity(), snapshots=snapshots,
        archive=SkillArchive(skills, sidecar), consolidation=None,
        reports=ReportWriter(home / "logs"), gate=gate, now=lambda: NOW,
    )


# -------------------------------------------------- the curator's two modes --

class FakeLock:
    """`core.redis.Lock`'s context-manager contract: it REPORTS whether it was
    acquired instead of raising, because every caller wants "someone else is
    already doing this, skip"."""

    def __init__(self, held=True):
        self.held = held
        self.entered = 0
        self.released = 0

    async def __aenter__(self):
        self.entered += 1
        return self.held

    async def __aexit__(self, *exc):
        self.released += 1
        return None


class RecordingCurator:
    def __init__(self, result=None, boom=False):
        self.result = result if result is not None else {"ran": True, "counts": {}}
        self.boom = boom
        self.passes = 0

    async def run_pass(self):
        self.passes += 1
        if self.boom:
            raise RuntimeError("pass died")
        return self.result


async def test_run_once_without_a_lock_is_the_inline_pass(tmp_path):
    from claude_hermes.workers.curator import run_once

    svc = RecordingCurator()
    assert (await run_once(svc))["ran"] is True
    assert svc.passes == 1


async def test_run_once_skips_when_another_worker_holds_the_lock(tmp_path):
    """N workers may land on the same `*/15` minute; two overlapping passes would
    age and archive one skill tree twice."""
    from claude_hermes.workers.curator import run_once

    svc = RecordingCurator()
    lock = FakeLock(held=False)
    assert await run_once(svc, lock) == {"ran": False, "reason": "locked"}
    assert svc.passes == 0
    assert lock.released == 1        # the lease is still returned


async def test_run_once_runs_and_releases_when_it_wins_the_lock(tmp_path):
    from claude_hermes.workers.curator import run_once

    svc = RecordingCurator()
    lock = FakeLock(held=True)
    assert (await run_once(svc, lock))["ran"] is True
    assert svc.passes == 1
    assert lock.released == 1


async def test_a_raising_pass_never_escapes_run_once(tmp_path):
    """A curator that dies on one bad pass stops ageing the library for the life
    of the process, and the loop that used to swallow this is now only one of two
    callers."""
    from claude_hermes.workers.curator import run_once

    svc = RecordingCurator(boom=True)
    assert await run_once(svc) == {"ran": False, "reason": "error"}
    assert await run_once(svc, FakeLock(held=True)) == {"ran": False, "reason": "error"}

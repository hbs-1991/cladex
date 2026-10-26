"""The five registered tasks, and the one registry both sides of the queue share.

A task body is deliberately thin: look up the services, call one of them with the
primitives it was handed, and nothing else. Every decision — which jobs are due,
whether a review may start, whether the curator pass is worth running — lives in
the service, so the answer does not depend on which process asked.

**Why a module-level `AppContext`.** A task is called by a broker, which passes no
services and cannot be given any: the argument list has to survive being JSON on a
Redis list. So the process binds its context once at startup (`bind(ctx)`) and the
bodies read it back. `main.build_app()` binds for the agent (inline mode runs these
same functions in-process) and `workers.taskiq_app`'s worker-startup hook binds for
the worker. A body that runs before either raises `TasksNotBound`, which names the
mistake instead of an `AttributeError` on None three frames down.

The task *names* are constants owned by the modules that enqueue them
(`RUN_JOB_TASK`, `RUN_TURN_TASK`, `LEARNING_REVIEW_TASK`) — imported here rather
than retyped, since a name spelled differently on the two sides is a task that is
enqueued and never runs. `scheduler_tick` and `curator_tick` are enqueued by a
clock rather than by a module, so their names start here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from claude_hermes.core.redis import Lock
from claude_hermes.core.tasks import TaskRegistry
from claude_hermes.modules.learning.infrastructure.dispatch import LEARNING_REVIEW_TASK
from claude_hermes.modules.scheduler.infrastructure.dispatch import RUN_JOB_TASK
from claude_hermes.modules.turns.infrastructure.dispatch import RUN_TURN_TASK

from .curator import run_all

__all__ = [
    "CURATOR_LOCK_KEY", "CURATOR_SWEEP_LOCK_KEY", "CURATOR_TICK_TASK",
    "SCHEDULER_TICK_TASK", "TasksNotBound", "bind", "context", "curator_lock_key",
    "curator_tick", "learning_review", "registry", "run_job", "run_turn",
    "scheduler_tick",
]

# The two clock-driven names. In redis mode they are taskiq schedule labels
# (`* * * * *` and `*/15 * * * *`); inline mode reaches the same bodies through
# `CronTicker` and `curator_loop` instead.
SCHEDULER_TICK_TASK = "scheduler_tick"
CURATOR_TICK_TASK = "curator_tick"

# One pass over one skill tree at a time. N workers may tick on the same minute;
# the lock is what stops two of them rewriting the library at once. The owner
# keeps this legacy key; everybody else's is `lock:curator:<id>` (Wave 4b).
CURATOR_LOCK_KEY = "lock:curator"
# One SWEEP at a time (Wave 4b, advisor R-1): a sweep of N users outlives the
# 15-minute tick, and without an outer lock the next tick would start a second
# sweep behind the first — the per-user locks would keep them off the same tree
# but not off the same account.
CURATOR_SWEEP_LOCK_KEY = "lock:curator:sweep"


def curator_lock_key(user_id: int, owner_id: int) -> str:
    """The owner keeps `lock:curator`; a user gets `lock:curator:<id>`."""
    return CURATOR_LOCK_KEY if user_id == owner_id else f"{CURATOR_LOCK_KEY}:{user_id}"

log = structlog.get_logger(__name__)

# THE registry. Both sides import this module, so the producer cannot enqueue a
# name the consumer has never heard of.
registry = TaskRegistry()

_ctx: Any = None


class TasksNotBound(RuntimeError):
    """A task ran in a process that never called `bind()`."""


def bind(ctx: Any) -> None:
    """Hand this process's `AppContext` to the task bodies.

    Called once per process, at the end of `main.build_app()` and again by the
    worker's startup hook. Rebinding replaces it: a test that builds a second
    context is the only caller that ever does, and it wants the second one.
    """
    global _ctx
    _ctx = ctx


def context() -> Any:
    if _ctx is None:
        raise TasksNotBound(
            "no AppContext is bound to claude_hermes.workers.tasks; "
            "main.build_app() binds one, and so does the worker startup hook")
    return _ctx


@registry.task(SCHEDULER_TICK_TASK)
async def scheduler_tick() -> None:
    """Claim everything due and dispatch it, then reap the turns nobody will
    finish.

    Idempotent across processes: the claim is one transaction with SKIP LOCKED,
    so two ticks on the same second get disjoint job sets and a duplicate enqueue
    costs nothing. The reap is idempotent for the same reason — `mark_lost`
    matches an active row, so the second reaper of the same turn does nothing.

    The reaper rides this clock rather than one of its own: a lane whose worker
    died is unusable until somebody frees it, and "somebody" should not be a
    second thing to get wrong about time. Inline mode reaches the same call
    through `CronTicker(..., extra=(ctx.turns.reap, ...))`.

    Then the content plan's tick, when `content_enabled`: queue the approved
    items whose time has come and wake the publisher routine. It is idempotent
    across workers for the same reason the claim is — every step is one
    conditional statement, and the wake is the scheduler's `wake_if_idle`
    compare-and-set. It runs last and in its own `try`, like the ticker's extras
    in inline mode: a failing plan is logged, never the reason a tick that has
    already claimed and reaped reports itself failed.
    """
    now = datetime.now(timezone.utc)
    ctx = context()
    await ctx.scheduler.tick(now)
    await ctx.turns.reap(now)
    content = getattr(ctx, "content", None)
    if content is not None:
        try:
            await content.tick(now)
        except Exception:
            log.exception("content tick failed")


@registry.task(RUN_JOB_TASK)
async def run_job(*, job_id: int, fire_at: str) -> None:
    """Run one already-claimed fire. `fire_at` travels with the id because the
    claim has already moved `next_run_at`: the run is recorded against the fire
    it consumed, not against whatever the row says by the time we get here."""
    await context().scheduler.execute_job(job_id, fire_at)


@registry.task(RUN_TURN_TASK)
async def run_turn(*, turn_id: str) -> None:
    """Run one already-created turn. The id is the whole argument list: everything
    else about the turn is on the row, where a worker in another process can read
    it — and where the claim, the lease and the cancel flag live."""
    await context().turn_runs.run(turn_id)


@registry.task(LEARNING_REVIEW_TASK)
async def learning_review(*, session_key: str, kinds: list[str],
                          owner_id: int = 0) -> None:
    """The detached review fork, in the scope of `owner_id` (Wave 4b).

    `kinds` arrives as a list (JSON has no set) and the service normalizes it;
    the gate is claimed and released inside `run`, so a fork that raises does not
    leave the curator blocked. A payload with no `owner_id` — queued by a 4a
    agent and drained here — is the deploy owner's, which the registry resolves
    `0` to.

    The learning grant is asked again HERE, at run time, not only when the turn
    nudged: a review can sit on the queue while the owner withdraws the grant in
    Settings → Users, and a withdrawn user's conversation must not be forked
    after the fact. The owner (and `0`) is always observed and costs no lookup.
    """
    scopes = context().learning.scopes
    scope = await scopes.for_user(owner_id)
    try:
        observed = await scopes.observes(owner_id)
    except Exception:
        # A failed lookup is not a grant: no fork runs. The gate goes back
        # first for the same reason as below, then the error propagates so the
        # broker (or the inline queue) logs it rather than a silent skip.
        if scope.gate is not None:
            await scope.gate.release()
        raise
    if not observed:
        # `LearningLoop.after_turn` claimed this user's review gate BEFORE it
        # enqueued us, and only `ReviewService.run` would ever release it. An
        # early return that left it held would drop this user's nudges and
        # block their curator pass for the full `review_timeout_seconds`, for a
        # review that is never going to run — the failed-enqueue shape the loop
        # already guards. So hand it back, say so once, and stop.
        if scope.gate is not None:
            await scope.gate.release()
        log.info("learning_review: user %s has no learning grant now; "
                 "dropping the queued review of %s", owner_id, session_key)
        return
    await scope.review.run(session_key, kinds)


@registry.task(CURATOR_TICK_TASK)
async def curator_tick() -> None:
    """One curator SWEEP — every scope's pass, owner first — behind two locks
    wherever there is a Redis to hold them: `lock:curator:sweep` around the
    whole sweep so two ticks never run two sweeps at once, and `lock:curator`
    (`lock:curator:<id>` per user) around each pass, which is what keeps an
    inline `curator_loop` and a worker off the same tree and what still holds
    when a sweep outlives its outer lock's TTL. The pass's own interval and idle
    gates are unchanged."""
    ctx = context()
    scopes = ctx.learning.scopes
    if ctx.redis is None:
        await run_all(scopes)
        return
    # TTL from `curator_max_turns`: a pass spawns a fork with that many turns,
    # so the lease has to outlive the worst case rather than a guess — and the
    # sweep's lease outlives N of them. A killed worker's lock expires; it is
    # never released by hand.
    pass_ttl = int(ctx.cfg.curator_max_turns * 60_000)
    ids = await scopes.user_ids()
    owner_id = (await scopes.owner()).user_id

    def lock_for(user_id: int) -> Lock:
        return Lock(ctx.redis, curator_lock_key(user_id, owner_id), ttl_ms=pass_ttl)

    sweep = Lock(ctx.redis, CURATOR_SWEEP_LOCK_KEY, ttl_ms=pass_ttl * max(1, len(ids)))
    async with sweep as held:
        if not held:
            log.debug("curator: another worker is sweeping")
            return
        await run_all(scopes, lock_for, ids=ids)

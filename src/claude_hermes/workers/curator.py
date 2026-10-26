"""The curator's background loop, the per-user sweep, and the single pass both
modes share.

A worker, not a service: the pass itself is `CuratorService.run_pass()`, and all
this adds is the cadence, the mutual exclusion and the "never die" wrapper.

Wave 3a split the cadence from the pass. `run_once` is what runs ONE scope's
pass, holding that scope's lock in redis mode so that N workers scheduled on the
same minute do not run two overlapping passes over one skill tree.

Wave 4b adds the sweep. `run_all` walks `LearningScopes.user_ids()` — the deploy
owner first, then every other user whose own learning grant is on (Settings →
Users; `users_learning_enabled` is only the default for a user with no choice of
their own) — and runs `run_once` per scope, sequentially: N users is N passes
and each pass may spawn an LLM fork, so running them concurrently would be N
Claude CLI processes inside a container sized for one. Before it starts, it reads the UNSUFFIXED
`curator_paused` through the owner scope (advisor B-2): that row is the
operator's switch, `domain/constants.py` promises nothing in this code writes
it, and it has to stop ALL of the sweep — a user's own `curator_paused:<id>` is
what their own pass reads. `curator_loop` is inline mode's cadence over the
sweep: one process, one loop, no locks because there is nobody to race.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import structlog

from claude_hermes.modules.learning.domain.constants import PAUSED_META_KEY

log = structlog.get_logger(__name__)


async def run_once(service, lock=None) -> dict:
    """One pass. `lock` is a `core.redis.Lock` in redis mode, `None` inline.

    A `Lock` used as a context manager reports rather than raises, so the worker
    that loses the race simply returns — which is the right answer: the pass its
    sibling is running is the pass this one would have run.
    """
    if lock is None:
        return await _pass(service)
    async with lock as held:
        if not held:
            log.debug("curator: another worker holds the pass lock")
            return {"ran": False, "reason": "locked"}
        return await _pass(service)


async def _pass(service) -> dict:
    """Run the pass and log the outcome, swallowing every exception.

    A curator that dies on one bad pass stops ageing the library for the life of
    the process, and nobody notices until the skill list is a thousand entries
    long. The task body needs the same protection as the loop did — a raise there
    reaches a broker that logs and drops it, which is less legible than this.
    """
    try:
        result = await service.run_pass()
    except Exception:
        log.exception("curator pass failed")
        return {"ran": False, "reason": "error"}
    if result.get("ran"):
        log.info("curator: pass complete — %s", result.get("counts"))
    else:
        log.debug("curator: skipped (%s)", result.get("reason"))
    return result


async def run_all(scopes: Any,
                  lock_for: Callable[[int], Any] | None = None,
                  ids: list[int] | None = None) -> list[dict]:
    """One sweep: every scope's pass, owner first, one at a time.

    `lock_for(user_id)` builds the per-scope lock in redis mode (`None` inline).
    `ids` is the list `curator_tick` already read to size the sweep lock's TTL,
    so the lease and the sweep are provably over the same users; `None` reads
    `scopes.user_ids()` here (the inline loop).
    A scope that cannot even be resolved — a `users` row whose tree fails to
    seed — is logged and skipped, for the same reason `_pass` swallows: one bad
    user must not stop every other user's library from ageing.
    """
    owner = await scopes.owner()
    # The owner scope's `meta` has the empty suffix, so this IS the legacy row.
    if await owner.meta.get(PAUSED_META_KEY) == "1":
        log.debug("curator: paused by the operator; skipping the sweep")
        return [{"ran": False, "reason": "paused"}]
    results: list[dict] = []
    for user_id in (await scopes.user_ids() if ids is None else ids):
        try:
            scope = await scopes.for_user(user_id)
        except Exception:
            log.exception("curator: cannot resolve the scope of user %s", user_id)
            results.append({"ran": False, "reason": "error", "user_id": user_id})
            continue
        lock = lock_for(user_id) if lock_for is not None else None
        result = await run_once(scope.curator, lock)
        result.setdefault("user_id", scope.user_id)
        results.append(result)
    return results


async def curator_loop(scopes: Any, tick_seconds: float) -> None:
    """Run the sweep forever, `tick_seconds` apart. Inline mode only; in redis
    mode the `curator_tick` schedule takes its place."""
    while True:
        await run_all(scopes)
        await asyncio.sleep(tick_seconds)

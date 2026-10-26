"""What happens after every turn: count the skills used, nudge, maybe review.

Moved from `learning/loop.py`. Wave 3a took the review pass itself out of this
class: `after_turn` hands a `ReviewDispatcher` a session key and a sorted list of
kinds, and asks a `ReviewGate` — a lease in the `meta` table, not an
`asyncio.Task` handle — whether one is already in flight. In inline mode the
dispatcher is a queue that runs the task in this process, so the observable
behaviour is Wave 2's; in redis mode a worker picks it up, and the
drop-the-second-nudge rule now holds across every process instead of inside one.
The lease is claimed at dispatch, before the task is handed over, so the window
between "enqueued" and "a worker started the pass" is covered too.

The rule itself is unchanged and load-bearing: a nudge that arrives while a
review is running is **dropped, not queued**. Two forks writing the same skill
tree is the failure mode the etag precondition in the browser exists to make
visible, and the counters have already reset — the next interval fires again.

Wave 4b makes the loop a router over `LearningScopes`: `after_turn` takes the
lane owner's id, resolves THEIR scope, bumps THEIR sidecar, asks THEIR tracker
(counters under their meta keys), reads and claims THEIR gate, and dispatches
the review with that id so the worker resumes their session in their tree. The
Wave 2 `reviewer=` keyword and its in-process dispatcher went in 4b with the
constructor they hung off (plan R-3).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from claude_hermes.core.events import TurnObservation
from claude_hermes.modules.learning.application.ports import ReviewDispatcher

log = structlog.get_logger(__name__)


class LearningLoop:
    """`after_turn` is the runner's one call into learning.

    `scopes` is the `LearningScopes` registry (`for_user(uid)`); `dispatcher`
    is the `ReviewDispatcher` every scope shares — the queue is one, the scope
    the review runs in travels as `owner_id` on the payload.
    """

    def __init__(self, scopes: Any, dispatcher: ReviewDispatcher):
        self._scopes = scopes
        self._dispatcher = dispatcher

    async def review_in_flight(self, owner_id: int = 0) -> bool:
        """Is a review running for THIS user? Async, and named differently from
        Wave 2's `review_running` property on purpose: the answer is a row in a
        table, and a property that returned a coroutine would be truthy in every
        `if` that forgot to await it — including the curator's gate."""
        scope = await self._scopes.for_user(owner_id)
        return scope.gate is not None and await scope.gate.running()

    async def after_turn(self, session_key: str, stats: TurnObservation, *,
                         owner_id: int = 0) -> None:
        scope = await self._scopes.for_user(owner_id)
        now_iso = datetime.now(timezone.utc).isoformat()
        for name in stats.skill_invocations:
            scope.sidecar.bump_use(name, now_iso)
        for name in stats.skill_patches:
            scope.sidecar.bump_patch(name, now_iso)

        kinds = await scope.nudges.observe_turn(
            stats.wrote_wiki, stats.wrote_skill, stats.tool_uses
        )
        if not kinds:
            return
        gate = scope.gate
        if gate is not None and await gate.running():
            log.info("review already in flight for %s; dropping nudge %s",
                     scope.user_id, sorted(kinds))
            return
        # Claimed HERE and not only in `ReviewService.run`, because the gap
        # between the two is a real window: the pass is claimed by whichever
        # process picks the task up, and until it does, a nudge from another lane
        # reads a free gate and dispatches a second review — two forks writing
        # one skill tree, which Wave 2 prevented synchronously because the task
        # handle existed the moment `create_task` returned. The service claims
        # again on start (the lease is absolute, so a second claim only pushes it
        # out) and releases in its `finally`.
        if gate is not None:
            await gate.claim()
        try:
            # Sorted, so the same nudge produces the same task payload every time —
            # a list on a queue, never the tracker's set. `scope.user_id`, not the
            # raw `owner_id`: `0` has been normalised to the resolved owner, so the
            # worker resolves the same cache entry this process did.
            await self._dispatcher.dispatch(session_key, sorted(kinds),
                                            owner_id=scope.user_id)
        except Exception:
            # Nothing is going to run, so nothing is going to release: a lease
            # left behind by a failed enqueue would block the curator for a full
            # `review_timeout_seconds` for no pass at all.
            if gate is not None:
                await gate.release()
            raise

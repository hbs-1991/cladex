"""Where a due nudge goes: onto the queue, as a task name and two primitives.

Wave 2 spawned `asyncio.create_task(reviewer.run(...))` inside `LearningLoop`.
That made the review something only the agent process could do, and made the
single-flight something only that process could see. Both are the same mistake,
and both are fixed by the same move: the loop hands the queue a name and
primitives, and whichever process holds the other end runs it.

Inline mode is not a degraded mode. `core.tasks.InlineQueue` runs the task here
as an `asyncio.Task`, which is byte-for-byte what the app used to do; the only
difference is that the *name* now has to exist in the registry, so a typo fails
at the call site instead of vanishing.
"""
from __future__ import annotations

from claude_hermes.core.tasks import TaskQueue

# The registered task name. It is a wire value: the agent enqueues it and a
# worker that was started from a different deploy looks it up, so it is frozen
# the same way a queue name is.
LEARNING_REVIEW_TASK = "learning_review"


class QueueReviewDispatcher:
    """`ReviewDispatcher` over `core.tasks.TaskQueue`.

    Takes the port, never a broker: `InlineQueue` and `TaskiqQueue` both satisfy
    it, so nothing in this module knows whether Redis exists.
    """

    def __init__(self, queue: TaskQueue, *,
                 task_name: str = LEARNING_REVIEW_TASK) -> None:
        self._queue = queue
        self._task_name = task_name

    async def dispatch(self, session_key: str, kinds: list[str],
                       owner_id: int = 0) -> None:
        # `list(kinds)` rather than the caller's object: whatever is handed in
        # has to be JSON on a Redis list, and a set is not. `owner_id` is an
        # `int` for the same reason — and it is always sent, even when 0: a
        # 4a worker draining a 4b payload is a `TypeError` and a lost review
        # (R-10), so the wire shape must not depend on which user nudged.
        await self._queue.enqueue(self._task_name, session_key=session_key,
                                  kinds=list(kinds), owner_id=int(owner_id or 0))

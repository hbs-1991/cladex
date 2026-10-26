"""Where a started turn goes: onto the queue, as a task name and one primitive.

Until Wave 3b the web route ran the turn in `asyncio.create_task(run())` inside
the request handler, and the Telegram handler awaited it inline. Both made "who
runs a turn" a property of "who received the message", which is what an agent
restart mid-turn used to kill. The launcher now writes a row and hands the queue a
name and an id; whichever process holds the other end claims the row and runs it.

Inline mode is not a degraded mode: `core.tasks.InlineQueue` runs the same task
body here as an `asyncio.Task`, which is what the route used to do — the only
difference is that the *name* has to exist in the registry, so a typo fails at the
call site instead of vanishing.

`turn_id` is the whole argument list on purpose (the primitives rule): everything
else about the turn is on the row, where a worker in another process can read it.
"""
from __future__ import annotations

from claude_hermes.core.tasks import TaskQueue

# The registered task name. A wire value: the agent enqueues it and a worker
# started from a different deploy looks it up, so it is frozen the way a queue
# name is.
RUN_TURN_TASK = "run_turn"


class QueueTurnDispatcher:
    """`TurnDispatcher` over `core.tasks.TaskQueue`.

    Takes the port, never a broker: `InlineQueue` and `TaskiqQueue` both satisfy
    it, so nothing in this module knows whether Redis exists.
    """

    def __init__(self, queue: TaskQueue, *, task_name: str = RUN_TURN_TASK) -> None:
        self._queue = queue
        self._task_name = task_name

    async def dispatch(self, turn_id: str) -> None:
        await self._queue.enqueue(self._task_name, turn_id=turn_id)

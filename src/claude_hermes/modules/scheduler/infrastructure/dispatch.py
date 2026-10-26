"""`JobDispatcher` over the queue port.

One implementation, two behaviours, because the difference lives in the queue and
not here: with `core.tasks.InlineQueue` the task runs in this process (what the
ticker did before the queue existed), with the taskiq-backed queue it runs in a
worker. The scheduler never learns which — it enqueues a name and two primitives.

`RUN_JOB_TASK` is the shared name. Both sides of the queue have to spell it the
same, so it is a constant here and imported by whoever registers the body; a
typo raises `UnknownTask` at this call site rather than going silently missing in
a worker process.
"""
from __future__ import annotations

from claude_hermes.core.tasks import TaskQueue

RUN_JOB_TASK = "run_job"


class QueueJobDispatcher:
    """`JobDispatcher`: `queue.enqueue("run_job", job_id=…, fire_at=…)`.

    Arguments stay primitive on purpose — they have to survive being JSON on a
    Redis list. `fire_at` travels with the id because the worker must record the
    run against the fire the claim consumed, not against whatever `next_run_at`
    says by the time it gets there.
    """

    def __init__(self, queue: TaskQueue, *, task_name: str = RUN_JOB_TASK) -> None:
        self._queue = queue
        self._task_name = task_name

    async def dispatch(self, job_id: int, fire_at: str) -> None:
        await self._queue.enqueue(self._task_name, job_id=job_id, fire_at=fire_at)

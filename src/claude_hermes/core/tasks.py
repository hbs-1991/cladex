"""The queue seam: a registry of named background tasks and the port that puts
work on it.

The abstraction is ours, not the broker's. `TaskRegistry` maps a stable name
("run_job") to an async callable taking primitive keyword arguments; `TaskQueue`
is the one-method port a caller sees. Two implementations satisfy it:

* `InlineQueue` (here) runs the callable as an `asyncio.Task` in this process —
  what the app did before the queue existed, and what every test and every
  laptop deployment uses, with an optional per-name concurrency limit so that
  "inline" keeps meaning what the ticker used to do (`{"run_job": 1}`);
* `TaskiqQueue` (`workers/taskiq_app.py`) binds the same registry to a
  taskiq-redis broker so a separate worker process runs it.

Keeping the port here rather than importing taskiq is what lets `core` and the
modules stay ignorant of the broker: taskiq is imported in exactly one file, and
nothing in `core` or `modules` can reach it.

Task arguments are primitives — `job_id: int`, `session_key: str`,
`kinds: list[str]`. Never a service, never a dataclass: the argument list has to
survive being JSON on a Redis list, and a task that takes an object is a task
that only ever worked in inline mode.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger(__name__)

# An async callable taking keyword-only primitives and returning nothing useful.
TaskFn = Callable[..., Awaitable[Any]]


class UnknownTask(LookupError):
    """Enqueued a name nothing registered. Raised at the *caller*, on purpose: a
    typo that only surfaced as a silent no-op in a worker process would be found
    days later, from the absence of an effect."""


class TaskRegistry:
    """The names this deployment knows how to run.

    Both sides of the queue import the same registry module, so the producer
    cannot enqueue a name the consumer has never heard of.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, TaskFn] = {}

    def task(self, name: str) -> Callable[[TaskFn], TaskFn]:
        """Decorator: `@registry.task("run_job")`. Returns the function unchanged
        so the body stays directly callable (and directly testable)."""
        def register(fn: TaskFn) -> TaskFn:
            self.register(name, fn)
            return fn
        return register

    def register(self, name: str, fn: TaskFn) -> None:
        existing = self._tasks.get(name)
        if existing is not None and existing is not fn:
            raise ValueError(f"task {name!r} is already registered to {existing!r}")
        self._tasks[name] = fn

    def get(self, name: str) -> TaskFn:
        try:
            return self._tasks[name]
        except KeyError:
            raise UnknownTask(f"no task named {name!r}; known: {sorted(self._tasks)}") from None

    def __contains__(self, name: object) -> bool:
        return name in self._tasks

    def __iter__(self) -> Iterator[tuple[str, TaskFn]]:
        return iter(self._tasks.items())

    def names(self) -> list[str]:
        return sorted(self._tasks)


@runtime_checkable
class TaskQueue(Protocol):
    """What a service sees of the queue. One method, primitives only."""

    async def enqueue(self, name: str, **kwargs: Any) -> None: ...


class InlineQueue:
    """Runs tasks in this process, as `asyncio.Task`s.

    The name is resolved (and `UnknownTask` raised) synchronously inside
    `enqueue`, so a bad name fails at the call site in both modes, not only in
    redis mode where the worker would be the one to notice.

    Strong references are the point of `_running`: `asyncio.create_task` returns
    the only reference to a task, and the event loop keeps merely a weak one — a
    fire-and-forget task with no name held anywhere can be garbage-collected
    mid-await and simply never finish.

    `limits` is how inline mode keeps a redis-mode topology from changing what
    one process does at once. A worker deployment gets its concurrency from the
    number of worker containers; inline mode has no such dial, and a task whose
    body spawns the Claude CLI is not something to start N of because N jobs
    happened to fall on the same tick — before the queue existed the ticker
    awaited each job in turn and the next tick simply waited. `{"run_job": 1}`
    restores that. `learning_review` is limited to 1 as well since Wave 4b: the
    review lease is per user now, so the lease no longer bounds how many review
    forks one process starts, and this limit is what does in inline mode (redis
    mode has the `lock:review` slot). A name with no limit is unbounded.
    """

    def __init__(self, registry: TaskRegistry,
                 limits: Mapping[str, int] | None = None) -> None:
        self._registry = registry
        self._running: set[asyncio.Task[Any]] = set()
        for name, n in (limits or {}).items():
            if n < 1:
                raise ValueError(
                    f"limit for {name!r} must be at least 1: a queue nothing can "
                    "ever run is a silently dead deployment")
        # One semaphore per limited name, built here: `enqueue` may be called
        # from anywhere, and two callers racing to create the gate would be two
        # gates, i.e. no gate. `asyncio.Semaphore` binds no loop at construction.
        self._gates: dict[str, asyncio.Semaphore] = {
            name: asyncio.Semaphore(n) for name, n in (limits or {}).items()}

    async def enqueue(self, name: str, **kwargs: Any) -> None:
        fn = self._registry.get(name)
        task = asyncio.create_task(self._run(name, fn, kwargs), name=f"task:{name}")
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    async def _run(self, name: str, fn: TaskFn, kwargs: dict[str, Any]) -> None:
        gate = self._gates.get(name)
        try:
            if gate is None:
                await fn(**kwargs)
            else:
                # Acquired inside the task, never inside `enqueue`: the caller is
                # the cron tick, and a tick that blocked on a running job would
                # hold the claim transaction open behind it. Semaphore waiters are
                # FIFO, so the jobs still run in the order they were claimed.
                async with gate:
                    await fn(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A background task has nobody to return an error to. In redis mode
            # the broker logs and drops it; inline mode does the same rather than
            # letting the "task exception was never retrieved" warning be the
            # only trace.
            log.exception("task_failed", task=name)

    @property
    def pending(self) -> int:
        return len(self._running)

    async def drain(self) -> None:
        """Wait for everything enqueued so far, including what those tasks
        enqueue while draining. For tests: in redis mode the assertion point is
        the worker, and inline mode needs an equivalent. A task waiting on a
        `limits` gate is still pending, so a drain covers the whole queue."""
        while self._running:
            await asyncio.gather(*tuple(self._running), return_exceptions=True)

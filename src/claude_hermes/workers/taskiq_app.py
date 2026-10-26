"""The taskiq backend: the one file in this repository that knows taskiq exists.

**Unverified.** It was written on a machine with no network, where `taskiq` and
`taskiq-redis` could not be installed, so nothing here has ever been executed
against a broker. `pyproject.toml` declares both and the Dockerfile installs them
at build time; until someone runs the checklist in this package's README, treat
redis mode as unshipped and run inline. `tests/test_workers_taskiq.py` exists and
is guarded by `pytest.importorskip("taskiq")`, i.e. it is skipped, not green.

Everything taskiq is imported *inside a function*, and the module-level `broker`
and `scheduler` that `docker-compose.yml` names are produced by `__getattr__` on
first access. So importing this module costs nothing and fails nowhere — which is
what lets `tests/test_main.py` read the worker startup's source, and what keeps
`import claude_hermes.workers.tasks` working with taskiq absent.

The two frozen strings are the module paths in compose:

    taskiq worker claude_hermes.workers.taskiq_app:broker claude_hermes.workers.tasks
    taskiq scheduler claude_hermes.workers.taskiq_app:scheduler

Renaming this module or either attribute is a deployment change, not a refactor.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import structlog

from claude_hermes.core.logging import configure
from claude_hermes.core.tasks import TaskRegistry

from . import tasks as task_module
from .tasks import CURATOR_TICK_TASK, SCHEDULER_TICK_TASK, registry

log = structlog.get_logger(__name__)

__all__ = ["QUEUE_NAME", "SCHEDULES", "TaskiqQueue", "worker_shutdown",
           "worker_startup"]

# One list, named rather than defaulted: a second deployment sharing this Redis
# must not silently eat this one's jobs.
QUEUE_NAME = "hermes:tasks"

# The periodic labels. `LabelScheduleSource` reads them off the registered task,
# which is why they are attached at registration and not at import. Minute
# granularity is the plan's settled answer for `run_now` latency in redis mode.
SCHEDULES = {
    SCHEDULER_TICK_TASK: "* * * * *",
    CURATOR_TICK_TASK: "*/15 * * * *",
}

_BROKER: Any = None
_SCHEDULER: Any = None
_TASKS: dict[str, Any] = {}


def _redis_url() -> str:
    """The broker URL, from the environment.

    The fallback for the CLI's module-attribute path only: `taskiq worker
    …:broker` wants a broker at import time, before any `AppContext` exists, and
    that import happens before the startup hook pops `CH_REDIS_URL`.

    A producer must NOT come through here lazily. `main.run()` pops
    `CH_REDIS_URL` moments after `build_app()` returns, so a broker first built
    on the agent's first enqueue would be built from an environment that no
    longer has the URL and every review dispatch would raise. `TaskiqQueue` is
    therefore handed `cfg.redis_url` at construction — see its `url=`.
    """
    url = os.environ.get("CH_REDIS_URL", "")
    if not url:
        raise RuntimeError(
            "CH_REDIS_URL is not set: the taskiq broker only exists in redis "
            "mode. With it unset the app runs every task inline and never "
            "imports this module.")
    return url


def _register(broker: Any, reg: TaskRegistry) -> dict[str, Any]:
    """Put every registered name on the broker, with its schedule label if it has
    one. The registry is the source of truth for both sides, so a task added to
    `workers/tasks.py` needs no second entry here."""
    bound: dict[str, Any] = {}
    for name, fn in reg:
        labels: dict[str, Any] = {}
        cron = SCHEDULES.get(name)
        if cron is not None:
            labels["schedule"] = [{"cron": cron}]
        bound[name] = broker.register_task(fn, task_name=name, **labels)
    return bound


def _lifecycle(broker: Any) -> None:
    """Attach the worker's startup and shutdown hooks. Only a worker process
    fires them; the agent enqueues through the same broker object and never
    builds a second `AppContext`."""
    from taskiq import TaskiqEvents

    broker.on_event(TaskiqEvents.WORKER_STARTUP)(worker_startup)
    broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)(worker_shutdown)


def _make_broker(url: str) -> Any:
    from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

    # `socket_timeout=None` is load-bearing. redis-py 8 gives every connection a
    # 5-second read timeout by default, and the broker listens with a BRPOP that
    # blocks until a message arrives — so an idle queue raised TimeoutError every
    # five seconds, taskiq-redis catches only ConnectionError, and the worker
    # process died and restarted in a loop (seen in the first live smoke).
    return ListQueueBroker(url, queue_name=QUEUE_NAME,
                           socket_timeout=None).with_result_backend(
        RedisAsyncResultBackend(url))


def _ensure_broker(url: str | None = None) -> Any:
    """The process-wide broker. `url` is the one the caller captured while it was
    still readable; without it the environment is consulted, which only the CLI's
    `broker`/`scheduler` attributes may do."""
    global _BROKER, _TASKS
    if _BROKER is None:
        broker = _make_broker(url or _redis_url())
        _TASKS = _register(broker, registry)
        _lifecycle(broker)
        _BROKER = broker
    return _BROKER


def _ensure_scheduler() -> Any:
    global _SCHEDULER
    if _SCHEDULER is None:
        from taskiq import TaskiqScheduler
        from taskiq.schedule_sources import LabelScheduleSource

        broker = _ensure_broker()
        _SCHEDULER = TaskiqScheduler(broker, [LabelScheduleSource(broker)])
    return _SCHEDULER


def __getattr__(name: str) -> Any:
    """`broker` and `scheduler`, built on first access (PEP 562).

    This is the whole trick that keeps taskiq optional: `taskiq worker
    …:broker` reads the attribute and gets a live broker, while an import for
    any other reason touches nothing.
    """
    if name == "broker":
        return _ensure_broker()
    if name == "scheduler":
        return _ensure_scheduler()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class TaskiqQueue:
    """`core.tasks.TaskQueue` over the broker: `enqueue(name, **kw)` -> `.kiq()`.

    The name is resolved against the registry first, synchronously, exactly as
    `InlineQueue` does — so a typo raises `UnknownTask` at the call site in both
    modes rather than becoming a task the worker silently never receives.
    """

    def __init__(self, reg: TaskRegistry | None = None, *,
                 url: str | None = None) -> None:
        self._registry = reg if reg is not None else registry
        # The URL is captured HERE, not at the first enqueue. `main.build_app()`
        # constructs this queue and `main.run()` pops CH_REDIS_URL a few lines
        # later; the agent's first dispatch happens long after that, so a URL
        # read lazily would be read from an environment that no longer has one
        # and every `after_turn` nudge would raise instead of queueing a review.
        # `main` passes `cfg.redis_url`; the default keeps the CLI's own
        # `broker` attribute working, where the environment is still intact.
        self._url = url or _redis_url()
        self._started = False

    async def enqueue(self, name: str, **kwargs: Any) -> None:
        self._registry.get(name)              # UnknownTask here, not in a worker
        await self._startup()
        await _TASKS[name].kiq(**kwargs)

    async def _startup(self) -> None:
        """A producer has to open the broker's connection before its first `kiq`.
        Done on demand rather than at boot so building the app never dials
        Redis — `build_app()` stays the headless thing every test calls. The URL
        it dials with came from `__init__`, never from the environment."""
        if self._started:
            return
        broker = _ensure_broker(self._url)
        # Inside a `taskiq worker` process the module-level broker IS the one the
        # CLI imported and already started, and it carries `is_worker_process`.
        # Calling `startup()` on it again re-runs the WORKER_STARTUP handlers —
        # `worker_startup`, which calls `require_secrets_key()` after that same
        # process has already popped CH_SECRETS_KEY — and the first enqueue from
        # inside a turn (a learning-review nudge) ended the turn with
        # `SystemExit`. The live Wave 4a smoke found it. The CLI owns that
        # broker's lifecycle; a producer in a worker only borrows the connection.
        if not getattr(broker, "is_worker_process", False):
            await broker.startup()
        self._started = True

    async def aclose(self) -> None:
        if self._started:
            broker = _ensure_broker(self._url)
            if not getattr(broker, "is_worker_process", False):
                await broker.shutdown()
            self._started = False


async def worker_startup(state: Any) -> None:
    """Build this worker's application, then drop every secret it was handed.

    The worker spawns the Claude Code CLI exactly as the agent does — a scheduled
    job IS a turn — so it inherits the agent's rule: anything that can read these
    variables can be the bot, spend the OpenAI budget, read the secrets table or
    reach the database directly, and a plugin from the catalog is reviewed code
    but it is not OUR code. The objects that need the values already hold them by
    the time the pops below run, and nothing spawns a subprocess before this hook
    returns.

    Logging is configured FIRST, and for the same reason `main.run()` does it:
    the worker delivers a job's report through `BotApiClient`, httpx logs that
    request's URL at INFO, and the bot token is a path segment of it. Without
    `logsafe.RedactingFormatter` on the root handler the token lands in this
    container's `docker logs` verbatim — and taskiq's worker CLI has already
    configured root logging at INFO by the time this hook runs, so there IS a
    handler printing those lines. `configure()` replaces the root handlers, so
    what the CLI installed is gone by the time the first delivery happens.
    """
    configure(logging.INFO)

    from claude_hermes import main

    # Same gate as `main.run`: a worker is a process that writes secrets too, and
    # one that started with a generated key would store rows nothing can read.
    main.require_secrets_key()
    # The agent owns the migrations (compose starts the worker after it); a worker
    # upgrading too would race the agent in alembic_version on a fresh database.
    ctx = await main.build_app(migrate=False)
    cfg = ctx.cfg
    if cfg.telegram_token:
        # A job's report is delivered to Telegram from whichever process ran it,
        # so the worker needs the same delivery objects `run()` builds — same
        # helper, so the two cannot drift.
        state.delivery = await main.build_delivery(ctx, cfg.telegram_token)
    else:
        # Since Wave 3b the worker also RUNS TURNS, and a turn can ask a question
        # or send a document. Both go out through the delivery objects above, and
        # `ask_user` is registered whenever a presenter is wired — which
        # `AskService` always is. Without the token the tool exists, the keyboard
        # is never sent, and the turn parks for its whole timeout on a question
        # nobody saw. The worker's env IS the agent's env; the compose anchor
        # already guarantees it, so this is a misconfiguration worth a line.
        log.warning("worker_without_telegram_token: a Telegram turn that asks a "
                    "question will wait for an answer nobody can see")
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    os.environ.pop("OPENAI_API_KEY", None)
    os.environ.pop("CH_SECRETS_KEY", None)
    os.environ.pop("CH_S3_ACCESS_KEY", None)
    os.environ.pop("CH_S3_SECRET_KEY", None)
    os.environ.pop("CH_REDIS_URL", None)
    os.environ.pop("CH_DATABASE_URL", None)
    # The same credentials under the names compose also injects them as: it
    # derives CH_DATABASE_URL from POSTGRES_PASSWORD and CH_S3_SECRET_KEY from
    # MINIO_ROOT_PASSWORD, and `env_file: .env` puts the originals in the
    # container too. CLAUDE_CODE_OAUTH_TOKEN stays: the CLI is what it is for.
    os.environ.pop("POSTGRES_PASSWORD", None)
    os.environ.pop("MINIO_ROOT_PASSWORD", None)
    os.environ.pop("CH_WEB_SECRET", None)
    # The worker serves no webhook, but it spawns the same CLI from the same
    # container environment: a value that can forge an update to the agent's
    # public webhook URL must not be readable from a turn's Bash either.
    os.environ.pop("CH_TELEGRAM_WEBHOOK_SECRET", None)

    state.ctx = ctx
    # AFTER the pops, like everything else that could ever spawn. Neither of these
    # spawns anything, but "the pops are the first thing that happens once the
    # objects hold their values" is an invariant that is only worth having if it
    # is monotone — `tests/test_main.py` asserts the order on the source.
    #
    # The worker needs both: the event bus is how the turn it runs reaches the SSE
    # streams and the Telegram presenter in the agent, and the control channel is
    # how a cancel click and an `ask_user` answer reach this process.
    await ctx.events_bus.start()
    await ctx.control.start()
    # The task bodies read their services from here; nothing is passed through
    # the queue but primitives.
    task_module.bind(ctx)
    log.info("worker ready", queue_mode=cfg.queue_mode, blobs=cfg.blob_backend)


async def worker_shutdown(state: Any) -> None:
    """Close what the startup opened, in reverse. Best effort: a worker that is
    going away anyway must not hang on a socket that is already gone."""
    from claude_hermes import main

    delivery = getattr(state, "delivery", None)
    if delivery is not None:
        await delivery.aclose()
    ctx = getattr(state, "ctx", None)
    if ctx is not None:
        await main.shutdown(ctx)

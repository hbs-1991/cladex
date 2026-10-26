"""The queue seam: what a name means, and what `InlineQueue` guarantees."""
from __future__ import annotations

import asyncio

import pytest

from claude_hermes.core.tasks import InlineQueue, TaskQueue, TaskRegistry, UnknownTask


def test_task_decorator_returns_the_function_unchanged():
    registry = TaskRegistry()

    @registry.task("run_job")
    async def run_job(job_id: int) -> int:
        return job_id

    # Still directly callable: a task body must stay testable without a queue.
    assert registry.get("run_job") is run_job
    assert registry.names() == ["run_job"]
    assert "run_job" in registry


def test_registering_a_name_twice_is_refused():
    registry = TaskRegistry()

    @registry.task("tick")
    async def one() -> None: ...

    async def two() -> None: ...

    # Two modules claiming one name is how work silently stops happening.
    with pytest.raises(ValueError):
        registry.register("tick", two)
    # Re-registering the same function (a module imported twice) is fine.
    registry.register("tick", one)


def test_unknown_name_raises_at_the_caller():
    registry = TaskRegistry()
    with pytest.raises(UnknownTask):
        registry.get("nope")


async def test_inline_queue_runs_the_task():
    registry = TaskRegistry()
    seen: list[tuple[int, str]] = []

    @registry.task("run_job")
    async def run_job(job_id: int, fire_at: str) -> None:
        await asyncio.sleep(0)
        seen.append((job_id, fire_at))

    queue = InlineQueue(registry)
    assert isinstance(queue, TaskQueue)

    await queue.enqueue("run_job", job_id=7, fire_at="2026-09-16T10:00:00Z")
    # enqueue returns before the task has run — that is the whole point.
    assert seen == []
    await queue.drain()
    assert seen == [(7, "2026-09-16T10:00:00Z")]
    assert queue.pending == 0


async def test_inline_queue_rejects_an_unregistered_name_synchronously():
    """A typo must fail where it is written. In redis mode the enqueue would
    succeed and a worker process would be the only thing that ever noticed."""
    queue = InlineQueue(TaskRegistry())
    with pytest.raises(UnknownTask):
        await queue.enqueue("run_jobb", job_id=1)


async def test_a_failing_task_does_not_take_the_queue_down():
    registry = TaskRegistry()
    ran: list[str] = []

    @registry.task("boom")
    async def boom() -> None:
        raise RuntimeError("no")

    @registry.task("fine")
    async def fine() -> None:
        ran.append("fine")

    queue = InlineQueue(registry)
    await queue.enqueue("boom")
    await queue.enqueue("fine")
    await queue.drain()
    assert ran == ["fine"]


async def test_drain_waits_for_tasks_enqueued_by_tasks():
    """`after_turn` enqueues a review, which may enqueue a curator kick. A drain
    that only waited for the first generation would be a flaky test generator."""
    registry = TaskRegistry()
    order: list[str] = []
    queue: InlineQueue

    @registry.task("second")
    async def second() -> None:
        await asyncio.sleep(0.01)
        order.append("second")

    @registry.task("first")
    async def first() -> None:
        await asyncio.sleep(0.01)
        order.append("first")
        await queue.enqueue("second")

    queue = InlineQueue(registry)
    await queue.enqueue("first")
    await queue.drain()
    assert order == ["first", "second"]


async def test_running_tasks_are_strongly_referenced():
    """`asyncio.create_task` hands back the only strong reference there is; the
    loop keeps a weak one. Without the set, a long fire-and-forget task can be
    collected mid-await and simply never finish."""
    registry = TaskRegistry()
    started = asyncio.Event()

    @registry.task("slow")
    async def slow() -> None:
        started.set()
        await asyncio.sleep(0.05)

    queue = InlineQueue(registry)
    await queue.enqueue("slow")
    await started.wait()
    assert queue.pending == 1
    await queue.drain()
    assert queue.pending == 0


# --- inline concurrency ----------------------------------------------------
# A worker deployment sizes its concurrency with containers. Inline mode has no
# such dial, so the limit is the dial: without it, "the queue" would quietly turn
# the ticker's one-job-at-a-time into N Claude CLI processes on a busy minute.

async def test_a_limited_name_runs_one_at_a_time_in_claim_order():
    """What `CronTicker` did before the queue existed: each claimed fire ran to
    the end before the next one started."""
    registry = TaskRegistry()
    live = 0
    peak = 0
    order: list[int] = []

    @registry.task("run_job")
    async def run_job(job_id: int) -> None:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        order.append(job_id)
        live -= 1

    queue = InlineQueue(registry, {"run_job": 1})
    for job_id in range(3):
        await queue.enqueue("run_job", job_id=job_id)
    assert queue.pending == 3                    # all queued, one running
    await queue.drain()

    assert peak == 1
    # Semaphore waiters are FIFO, so a late job cannot overtake an earlier claim.
    assert order == [0, 1, 2]


async def test_an_unlimited_name_still_runs_concurrently():
    """A name with no limit is unbounded; the limit is per name, not a queue-wide
    serialiser. (`learning_review` used to be the example here — since Wave 4b
    `main.build_app()` limits it to 1 too, because the review lease is per user
    and no longer bounds how many forks one process starts.)"""
    registry = TaskRegistry()
    live = 0
    peak = 0

    @registry.task("unbounded")
    async def unbounded() -> None:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1

    queue = InlineQueue(registry, {"run_job": 1, "learning_review": 1})
    await queue.enqueue("unbounded")
    await queue.enqueue("unbounded")
    await queue.drain()
    assert peak == 2


async def test_a_failing_limited_task_hands_the_slot_back():
    """The gate is released by the `async with`, not by the task succeeding —
    otherwise one bad job would stop every routine in the deployment."""
    registry = TaskRegistry()
    ran: list[str] = []

    @registry.task("run_job")
    async def run_job(ok: bool) -> None:
        if not ok:
            raise RuntimeError("no")
        ran.append("ok")

    queue = InlineQueue(registry, {"run_job": 1})
    await queue.enqueue("run_job", ok=False)
    await queue.enqueue("run_job", ok=True)
    await queue.drain()
    assert ran == ["ok"]


def test_a_limit_below_one_is_refused():
    with pytest.raises(ValueError):
        InlineQueue(TaskRegistry(), {"run_job": 0})


# --- queue_mode ------------------------------------------------------------
# The switch between the two topologies is derived from one environment
# variable, and these pin that it stays derived. A `queue_mode:` key in
# config.yaml would be a second switch, and the two would eventually disagree —
# a deployment that believes it enqueues while nothing is listening.

def test_queue_mode_is_derived_from_the_redis_url():
    from claude_hermes.core.config import Config

    assert Config().queue_mode == "inline"
    assert Config(redis_url="redis://redis:6379/0").queue_mode == "redis"


def test_config_yaml_cannot_set_the_queue_mode_or_a_secret(tmp_path):
    from claude_hermes.core.config import load_config

    (tmp_path / "config.yaml").write_text(
        "queue_mode: redis\n"
        "redis_url: redis://evil:6379/0\n"
        "secrets_key: leaked\n"
        "s3_secret_key: leaked\n"
        "blob_backend: s3\n"
        "s3_bucket: pictures\n"
        "review_timeout_seconds: 60\n",
        encoding="utf-8")
    cfg = load_config(tmp_path)

    # Secrets and connection strings come from the environment only — the config
    # file sits on a volume the agent can write to.
    assert cfg.redis_url == ""
    assert cfg.secrets_key == ""
    assert cfg.s3_secret_key == ""
    assert cfg.queue_mode == "inline"
    # Non-secret behaviour still comes from the file.
    assert cfg.blob_backend == "s3"
    assert cfg.s3_bucket == "pictures"
    assert cfg.review_timeout_seconds == 60


def test_secrets_come_from_the_environment(tmp_path, monkeypatch):
    from claude_hermes.core.config import load_config

    monkeypatch.setenv("CH_REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("CH_SECRETS_KEY", "key")
    monkeypatch.setenv("CH_S3_ACCESS_KEY", "hermes")
    monkeypatch.setenv("CH_S3_SECRET_KEY", "hermes-test")
    monkeypatch.setenv("CH_S3_ENDPOINT", "http://minio:9000")
    cfg = load_config(tmp_path)

    assert cfg.queue_mode == "redis"
    assert (cfg.secrets_key, cfg.s3_access_key, cfg.s3_secret_key) == (
        "key", "hermes", "hermes-test")
    # The endpoint is an address, not a secret: compose sets it next to the
    # service name so the two cannot drift.
    assert cfg.s3_endpoint == "http://minio:9000"

"""The taskiq backend — skipped until taskiq can be installed.

Wave 3a was implemented offline: `taskiq` and `taskiq-redis` are declared in
`pyproject.toml` and installed by the Dockerfile, but they could not be installed
on the machine that wrote `workers/taskiq_app.py`. These tests are the checklist
that file's README points at. They are skipped, not green, and redis mode is
unshipped until they are not.

What they pin is the contract compose depends on: the two module attributes the
`worker` and `scheduler` commands name, the task names the agent enqueues, and the
two cron labels. Nothing here needs a broker to be reachable.
"""
import pytest

pytest.importorskip("taskiq",
                    reason="taskiq is not installed; redis mode is unverified "
                           "(see src/claude_hermes/workers/README.md)")

from claude_hermes.workers import tasks, taskiq_app  # noqa: E402


@pytest.fixture
def redis_broker(monkeypatch, redis_url):
    """A broker built against the test Redis, with the module singletons reset so
    one test cannot hand its broker to the next."""
    monkeypatch.setenv("CH_REDIS_URL", redis_url)
    monkeypatch.setattr(taskiq_app, "_BROKER", None)
    monkeypatch.setattr(taskiq_app, "_SCHEDULER", None)
    monkeypatch.setattr(taskiq_app, "_TASKS", {})
    return taskiq_app.broker


def test_the_two_attributes_compose_names_exist(redis_broker):
    """`taskiq worker …:broker` and `taskiq scheduler …:scheduler` are in
    docker-compose.yml, so these two names are a deployment contract."""
    assert redis_broker is not None
    assert taskiq_app.scheduler is not None


def test_every_registered_task_reaches_the_broker(redis_broker):
    """The registry is the source of truth for both sides: a task added to
    `workers/tasks.py` must need no second entry here."""
    assert set(taskiq_app._TASKS) == set(tasks.registry.names())


def test_only_the_two_clock_driven_tasks_carry_a_schedule(redis_broker):
    """`run_job` and `learning_review` are enqueued by a service, not by a clock.
    A schedule label on either would fire them with no arguments."""
    labelled = {name: task.labels.get("schedule")
                for name, task in taskiq_app._TASKS.items()}
    assert labelled[tasks.SCHEDULER_TICK_TASK] == [{"cron": "* * * * *"}]
    assert labelled[tasks.CURATOR_TICK_TASK] == [{"cron": "*/15 * * * *"}]
    assert labelled["run_job"] is None
    assert labelled["learning_review"] is None


async def test_an_unknown_name_fails_at_the_caller(redis_broker):
    """Same contract as `InlineQueue`: the typo is found where it was made, not
    as a task a worker silently never receives."""
    from claude_hermes.core.tasks import UnknownTask

    with pytest.raises(UnknownTask):
        await taskiq_app.TaskiqQueue().enqueue("no_such_task")


async def test_a_round_trip_through_redis(redis_broker):
    """The one test that needs the container: enqueue `run_job` and read the
    message back off the list the worker would consume."""
    queue = taskiq_app.TaskiqQueue()
    await queue.enqueue("run_job", job_id=7, fire_at="2026-07-12T10:00:00+00:00")
    try:
        async for message in redis_broker.listen():
            payload = message.decode() if isinstance(message, bytes) else message
            assert "run_job" in str(payload) and "2026-07-12T10:00:00+00:00" in str(payload)
            break
    finally:
        await queue.aclose()


def test_the_broker_listens_without_a_read_timeout(redis_broker):
    """redis-py 8 defaults every connection to a 5-second read timeout, and the
    broker's BRPOP blocks until a message arrives. Left at the default, an idle
    queue raised TimeoutError every five seconds and the worker process died and
    restarted in a loop — the first live smoke found it. The pool must say None."""
    kwargs = redis_broker.connection_pool.connection_kwargs
    assert "socket_timeout" in kwargs and kwargs["socket_timeout"] is None


async def test_a_producer_inside_a_worker_never_restarts_the_workers_broker(redis_broker):
    """The live Wave 4a smoke: the first enqueue from inside a turn (a review
    nudge) called `startup()` on the broker the CLI had already started, which
    re-ran `worker_startup` after the secrets were popped and ended the turn with
    `SystemExit`. Inside a worker the queue borrows the connection and owns
    nothing of its lifecycle."""
    calls = []

    async def startup():
        calls.append("startup")

    async def shutdown():
        calls.append("shutdown")

    redis_broker.startup, redis_broker.shutdown = startup, shutdown
    redis_broker.is_worker_process = True
    q = taskiq_app.TaskiqQueue(url="redis://127.0.0.1:1/0")
    await q._startup()
    await q.aclose()
    assert calls == []
    redis_broker.is_worker_process = False
    q = taskiq_app.TaskiqQueue(url="redis://127.0.0.1:1/0")
    await q._startup()
    await q.aclose()
    assert calls == ["startup", "shutdown"]

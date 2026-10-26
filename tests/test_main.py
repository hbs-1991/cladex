import inspect
from datetime import datetime, timezone

import pytest

from claude_hermes.core.app_context import AppContext
from claude_hermes.core.tasks import InlineQueue
from claude_hermes.main import build_app, dist_dir, shutdown
from claude_hermes.modules.sessions.infrastructure.repositories import SessionRepository
from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore

# Every CH_* value the two entry points must drop before anything can spawn the
# CLI. The reasoning is one sentence per name and it is the same sentence: a
# subprocess that can read it can be the bot, spend the budget, decrypt the
# secrets table, forge a task or read the whole database.
SECRET_ENV_NAMES = [
    "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "CH_SECRETS_KEY",
    "CH_S3_ACCESS_KEY", "CH_S3_SECRET_KEY", "CH_REDIS_URL", "CH_DATABASE_URL",
    # The same secrets under the names docker-compose ALSO injects (it derives
    # CH_DATABASE_URL from POSTGRES_PASSWORD and CH_S3_SECRET_KEY from
    # MINIO_ROOT_PASSWORD, then `env_file: .env` adds the originals), plus the
    # cookie-signing key. `CLAUDE_CODE_OAUTH_TOKEN` is deliberately absent: the
    # CLI subprocess is exactly what it is for.
    "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "CH_WEB_SECRET",
    # Wave 4a: anything that can read the webhook secret can POST a forged update
    # to the public webhook URL and speak to the agent AS someone it trusts.
    "CH_TELEGRAM_WEBHOOK_SECRET",
]


async def test_build_app_constructs_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    ctx = await build_app()
    assert isinstance(ctx, AppContext)
    assert ctx.cfg.telegram_token == "t"
    assert ctx.memory.wiki.root == tmp_path / "agent_home" / "wiki"
    assert ctx.workspace.skills_dir.is_dir()
    assert await SessionRepository(ctx.engine).get("tg:private:1") is None
    # The runner holds its collaborators, rather than having them assigned onto it.
    assert ctx.runner.bus is ctx.bus
    assert ctx.runner._sessions is ctx.sessions
    assert ctx.runner._observer is ctx.learning.loop
    await shutdown(ctx)


async def test_one_gatherer_answers_which_model_a_lane_runs(tmp_path, monkeypatch):
    """The turn, the web routes and /model must ask the SAME object, or the picker
    and the model that answers can disagree without anybody noticing."""
    from claude_hermes.http.deps import web_deps
    from claude_hermes.modules.sessions.application.services import SessionService

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    assert isinstance(ctx.lane_models, SessionService)
    assert ctx.runner._models is ctx.lane_models
    assert web_deps(ctx).lane_models is ctx.lane_models
    # the offered list is built (and its window warning logged) at boot
    assert [m.id for m in ctx.lane_models.offered_models()][0] == "claude-sonnet-5"
    await shutdown(ctx)


async def test_model_in_a_chat_nobody_has_spoken_in_asks_for_a_message_first(
        tmp_path, monkeypatch):
    """`SessionService.effective` never raises (the turn asks it before the thread
    row exists), so telegram's "no row" signal is spelled by `RowedLaneModels` —
    over the real gatherer, /model answers with a sentence instead of a menu whose
    every tap would fail."""
    from claude_hermes.core.app_context import RowedLaneModels
    from claude_hermes.modules.telegram.application.services.model_menu import (
        ModelMenuService,
    )
    from claude_hermes.modules.telegram.domain.messages import _MODEL_NO_THREAD
    from claude_hermes.modules.telegram.domain.models import MessageEvent

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        await ctx.acl.check(11, "private")          # claims the deploy: 11 is the owner
        menu = ModelMenuService(ctx.cfg, acl=ctx.acl,
                                lanes=RowedLaneModels(ctx.lane_models, ctx.threads),
                                defaults=ctx.acl)
        event = MessageEvent(chat_type="private", chat_id=11, user_id=11,
                             text="/model", mentioned=True)
        assert (await menu.open(event)).reply == _MODEL_NO_THREAD
        # once the lane has a row, the same call draws the menu
        await ctx.threads.touch("tg:private:11", "telegram", 11, "2026-09-23T10:00:00+00:00")
        opened = await menu.open(event)
        assert not opened.reply and opened.menu is not None
    finally:
        await shutdown(ctx)


def test_the_command_and_its_buttons_share_one_menu():
    """The message a tap edits must be drawn by the code that reads it: one
    `TelegramModelMenu`, handed to both the router and the dispatcher."""
    from claude_hermes import main

    source = inspect.getsource(main.build_telegram_gateway)
    assert "model_menu=model_menu)" in source.split("MessageRouter(")[1].split("\n    intake")[0]
    assert "model_menu=model_menu," in source.split("create_dispatcher(")[1]


async def test_build_app_holds_a_learning_scope_per_user_with_the_owner_eager(
        tmp_path, monkeypatch):
    """Wave 4b: the bundle's owner-level fields ARE the owner scope's objects,
    and `scopes` is what everything resolves a user through. The owner scope over
    an unclaimed deploy is byte-identical to 4a's paths."""
    from claude_hermes.modules.learning.application.scopes import LearningScopes

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    scopes = ctx.learning.scopes
    assert isinstance(scopes, LearningScopes)
    owner = await scopes.owner()
    assert owner.skills_dir == ctx.learning.skills_dir == ctx.workspace.skills_dir
    assert owner.logs_dir == ctx.learning.logs_dir == tmp_path / "logs"
    assert owner.meta.suffix == ""                     # the seven legacy keys
    # Unclaimed deploy: the scope is rebuilt per call, so identity is not the
    # test — the tree and the suffix are.
    assert (await scopes.for_user(0)).workspace == owner.workspace
    assert await scopes.user_ids() == [0]
    # The curator's quiet-hours check awaits an async id (B-7).
    assert await owner.curator._activity.last_message_ts() is None
    await shutdown(ctx)


async def test_inline_mode_bounds_reviews_to_one_at_a_time(tmp_path, monkeypatch):
    """The review lease is per user since 4b, so it no longer bounds how many
    review forks one process starts; the inline queue limit is the bound
    (redis mode has `lock:review`)."""
    from claude_hermes.modules.learning.infrastructure.dispatch import (
        LEARNING_REVIEW_TASK)
    from claude_hermes.modules.scheduler.infrastructure.dispatch import RUN_JOB_TASK

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    monkeypatch.delenv("CH_REDIS_URL", raising=False)
    ctx = await build_app()
    assert set(ctx.queue._gates) == {RUN_JOB_TASK, LEARNING_REVIEW_TASK}
    assert ctx.queue._gates[LEARNING_REVIEW_TASK]._value == 1
    # …and no Redis slot: the queue limit is the whole bound in this mode.
    assert (await ctx.learning.scopes.owner()).review._slot is None
    await shutdown(ctx)


async def test_build_app_is_headless(tmp_path, monkeypatch):
    """No token is read and no channel is bound: the Telegram half only exists
    after `run()` has a Bot. A test (and a web-only deployment) must be able to
    build the whole graph without one."""
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ctx = await build_app()
    assert ctx.telegram is None
    assert ctx.deliver_document.target is None
    assert ctx.ask_presenter.target is None
    assert ctx.scheduler.topics is None
    assert ctx.web_login_key == b"" and ctx.web_webapp_key == b""
    await shutdown(ctx)


async def test_build_app_in_inline_mode_needs_no_redis_and_no_bucket(tmp_path,
                                                                     monkeypatch):
    """The laptop deployment, and the one every test runs: no CH_REDIS_URL means
    the queue is `asyncio.create_task` in this process and the blobs are files
    under agent_home. Nothing dials anything."""
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    monkeypatch.delenv("CH_REDIS_URL", raising=False)
    ctx = await build_app()

    assert ctx.cfg.queue_mode == "inline"
    assert isinstance(ctx.queue, InlineQueue)
    assert ctx.redis is None and ctx.s3 is None
    assert isinstance(ctx.files._blobs, LocalBlobStore)
    # The dispatchers hold the queue this context chose, so an enqueued name is
    # one the worker would resolve.
    assert ctx.registry.names() == [
        "curator_tick", "learning_review", "run_job", "run_turn", "scheduler_tick"]
    # Wave 3b: the turn is a row and a task in BOTH modes. Inline mode is the one
    # process, so it holds both halves — the launcher every channel starts through
    # and the run service the queued task calls.
    from claude_hermes.core.eventbus import InProcessEventBus
    from claude_hermes.modules.turns.infrastructure.snapshots import MemorySnapshots

    assert isinstance(ctx.events_bus, InProcessEventBus)
    assert isinstance(ctx.bus._snapshots, MemorySnapshots)
    assert ctx.turns is not None and ctx.turn_runs is not None
    assert ctx.turn_store is not None and ctx.control is not None
    # One process, so the webhook's update dedup is an in-memory LRU. It is wired
    # in both modes: the route must never be the thing that decides it has none.
    from claude_hermes.modules.telegram.domain.dedup import MemoryUpdateDedup

    assert isinstance(ctx.update_dedup, MemoryUpdateDedup)
    await shutdown(ctx)


async def test_build_app_in_s3_mode_creates_the_bucket_and_mirrors_under_agent_home(
        tmp_path, monkeypatch, s3):
    """The boot the compose file describes, against the real MinIO.

    Two things only the composition root can get wrong, and neither shows up in a
    store-level test: `ensure_bucket()` must run at boot (a fresh volume has no
    bucket and the first upload would 404), and the mirror root must be
    `blob_root(home)` — pass `home` and the mirror lands one directory too high,
    where the agent's own Read/Glob cannot see it and the containment check has
    the wrong root.
    """
    # The same defaults tests/conftest.py uses. Spelled out rather than imported:
    # tests/ has no __init__.py, so `from tests.conftest import …` is not a thing.
    import os

    from claude_hermes.modules.files.infrastructure.s3_blob_store import S3BlobStore

    (tmp_path / "config.yaml").write_text(
        f"blob_backend: s3\ns3_bucket: {s3.bucket}\ns3_endpoint: {s3.endpoint}\n",
        encoding="utf-8")
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    monkeypatch.setenv("CH_S3_ACCESS_KEY",
                       os.environ.get("CH_TEST_S3_ACCESS_KEY", "hermes"))
    monkeypatch.setenv("CH_S3_SECRET_KEY",
                       os.environ.get("CH_TEST_S3_SECRET_KEY", "hermes-test"))
    ctx = await build_app()

    assert ctx.s3 is not None and ctx.s3.bucket == s3.bucket
    assert isinstance(ctx.files._blobs, S3BlobStore)
    # Wave 4a: the mirror root is looked up per OWNER rather than frozen at
    # construction, and `0` — an unclaimed deploy, and every legacy row — still
    # answers the one tree it always did.
    assert await ctx.files._blobs.root(0) == tmp_path / "agent_home"

    record = await ctx.files.put_upload("tg:private:1", "note.txt", b"hello")
    assert record["path"].startswith(f"s3://{s3.bucket}/inbox/")
    # In the bucket, and mirrored where the agent will look for it.
    assert await s3.get_object(record["path"].split("/", 3)[3]) == b"hello"
    assert (await ctx.files.resolve(record)).read_bytes() == b"hello"
    await shutdown(ctx)


async def test_build_app_in_redis_mode_takes_the_redis_backed_state(
        tmp_path, monkeypatch, redis_url):
    """`CH_REDIS_URL` is the only switch, and it moves two things besides the
    queue: the OAuth pending states (the browser can come back to a different
    process) and the client the `curator_tick` task takes its lock on.

    Boot does not dial the broker — `TaskiqQueue` connects on its first enqueue —
    which is what lets this run on a machine where taskiq is not installed.
    """
    from claude_hermes.modules.extensions.infrastructure.redis_pending_states import (
        RedisPendingStates)
    from claude_hermes.modules.extensions.infrastructure.refresh_locks import RedisLocks

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    monkeypatch.setenv("CH_REDIS_URL", redis_url)
    ctx = await build_app()

    assert ctx.cfg.queue_mode == "redis"
    assert not isinstance(ctx.queue, InlineQueue)
    assert isinstance(ctx.extensions.pending, RedisPendingStates)
    assert isinstance(ctx.extensions.locks, RedisLocks)
    assert await ctx.redis.ping() is True
    # …and the three objects a turn stops being local through. All of them are
    # CONSTRUCTED here and none is dialled: `build_app` stays the headless thing
    # every test calls, and `run()`/`worker_startup` start the bus after the pops.
    from claude_hermes.core.eventbus import RedisEventBus
    from claude_hermes.modules.turns.infrastructure.redis_ask_broker import (
        RedisAskBroker)
    from claude_hermes.modules.turns.infrastructure.snapshots import RedisSnapshots

    assert isinstance(ctx.events_bus, RedisEventBus)
    assert isinstance(ctx.bus._snapshots, RedisSnapshots)
    assert isinstance(ctx.ask_broker, RedisAskBroker)
    # Wave 4a: and the webhook's update dedup, for the same reason — with
    # `--scale agent=N` a Telegram retry may land on a different instance than
    # the first delivery, and an in-memory set there would run the turn twice.
    from claude_hermes.modules.telegram.infrastructure.update_dedup import (
        RedisUpdateDedup)

    assert isinstance(ctx.update_dedup, RedisUpdateDedup)
    # Wave 4b: and the deploy-wide review slot, one `lock:review` for every scope.
    from claude_hermes.modules.learning.infrastructure.review_slot import RedisReviewSlot

    slot = (await ctx.learning.scopes.owner()).review._slot
    assert isinstance(slot, RedisReviewSlot)
    assert slot._ttl_ms == int(ctx.cfg.review_timeout_seconds * 1000)
    await shutdown(ctx)


async def test_build_app_binds_its_context_to_the_task_bodies(tmp_path, monkeypatch):
    """A task takes primitives, so it finds its services on a module-level
    context. In inline mode the bodies run in THIS process — without the bind, a
    due job would raise `TasksNotBound` instead of running."""
    from claude_hermes.workers import tasks

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    assert tasks.context() is ctx
    await shutdown(ctx)


async def test_build_app_keeps_the_secret_box_for_the_paths_that_lost_the_key(
        tmp_path, monkeypatch):
    """`run()` pops CH_SECRETS_KEY seconds later. Everything that seals or opens
    a row has to share the box built here, or a refreshed OAuth token gets sealed
    with a key that dies with the process and the connector silently 'forgets'
    its credentials on the next restart. The OAuth refresher — the one writer
    that runs after the pop — is built over the same secrets store as the rest
    of the bundle, and that store holds this box."""
    from claude_hermes.core.crypto import SecretBox

    key = SecretBox.generate_key()
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    monkeypatch.setenv("CH_SECRETS_KEY", key)
    ctx = await build_app()
    monkeypatch.delenv("CH_SECRETS_KEY")            # what run() does next

    assert ctx.secret_box.open(ctx.secret_box.seal("v")) == "v"
    secrets = ctx.extensions.secrets
    assert secrets._box is ctx.secret_box
    assert ctx.extensions.compose._oauth._secrets is secrets
    await shutdown(ctx)


@pytest.mark.parametrize("name", SECRET_ENV_NAMES)
def test_every_secret_is_dropped_before_any_subprocess_can_inherit_it(name):
    """Anything that can read TELEGRAM_BOT_TOKEN can BE the bot: send messages as it, edit
    them, delete them. The agent's Bash, its hooks, and every MCP server an extension
    brings inherit main.py's environment — so the token is popped once the Bot object
    holds it. This test pins the call site: without it, a third-party plugin two hooks deep
    holds your bot's identity. Wave 3a's four connection strings and keys are the same
    argument with a different blast radius, so they are the same assertion."""
    from claude_hermes import main

    source = inspect.getsource(main.run)
    assert f'os.environ.pop("{name}"' in source
    # …before the gateway is built, i.e. before the dispatcher exists and
    # therefore before anything can spawn the CLI.
    assert (source.index(f'os.environ.pop("{name}"')
            < source.index("build_telegram_gateway("))


@pytest.mark.parametrize("name", SECRET_ENV_NAMES)
def test_the_worker_startup_drops_the_same_secrets(name):
    """The worker spawns the CLI too — a scheduled job IS a turn — so the rule is
    not the agent's, it is the process's. `taskiq_app` imports no taskiq at module
    level, which is what lets this read its source with taskiq absent."""
    from claude_hermes.workers import taskiq_app

    source = inspect.getsource(taskiq_app.worker_startup)
    assert f'os.environ.pop("{name}"' in source
    # Before the context is bound, and therefore before any task body — the
    # worker's equivalent of "before the dispatcher exists".
    assert source.index(f'os.environ.pop("{name}"') < source.index("bind(ctx)")


@pytest.mark.parametrize("entry", ["run", "worker_startup"])
def test_the_event_bus_and_the_control_channel_start_after_the_pops(entry):
    """Neither of them spawns anything — but "the pops are the first thing that
    happens once the objects hold their values" is an invariant only worth having
    if it is monotone, and both entry points have to keep it. Asserted on the
    source, like the pops themselves, because neither can be run in a test."""
    from claude_hermes import main
    from claude_hermes.workers import taskiq_app

    fn = main.run if entry == "run" else taskiq_app.worker_startup
    source = inspect.getsource(fn)
    starts = source.index("events_bus.start()")
    assert starts < source.index("control.start()")
    for name in SECRET_ENV_NAMES:
        assert source.index(f'os.environ.pop("{name}"') < starts
    # …and before this process can be handed any work: polling in the agent, a
    # bound task context in the worker.
    assert starts < source.index("run_polling" if entry == "run" else "bind(ctx)")


@pytest.mark.parametrize("entry", ["run", "worker_startup"])
def test_every_process_installs_the_redacting_log_handler_first(entry):
    """httpx logs every Telegram API URL at INFO and the bot token is a path
    segment of it, so a process that delivers anything to Telegram without
    `core.logging.configure()` writes the token into `docker logs` verbatim.

    The worker delivers too — a job's report goes out from whichever process ran
    it — and taskiq's worker CLI has already configured root logging at INFO by
    the time the startup hook runs, so there IS a handler printing those lines
    until `configure()` replaces the root handlers. Asserted on the source, like
    the env pops above, because neither entry point can be run in a test.
    """
    from claude_hermes import main
    from claude_hermes.workers import taskiq_app

    fn = main.run if entry == "run" else taskiq_app.worker_startup
    source = inspect.getsource(fn)
    assert "configure(logging.INFO)" in source
    # …before anything that can log a request: the app, the Bot, the delivery.
    assert source.index("configure(logging.INFO)") < source.index("build_app(")


def test_the_bot_token_is_dropped_before_any_subprocess_can_inherit_it():
    """The original single-name assertion, kept because it is the one that names
    the failure: a third-party plugin two hooks deep holding your bot's identity."""
    from claude_hermes import main

    source = inspect.getsource(main.run)
    assert 'os.environ.pop("TELEGRAM_BOT_TOKEN"' in source
    # …and it must happen before the gateway is built, i.e. before the dispatcher
    # exists and therefore before anything can spawn the CLI.
    assert (source.index('os.environ.pop("TELEGRAM_BOT_TOKEN"')
            < source.index("build_telegram_gateway("))


def test_the_openai_key_is_dropped_too():
    """Same reasoning, different bill: the voice key lives in the host process only."""
    from claude_hermes import main

    source = inspect.getsource(main.run)
    assert 'os.environ.pop("OPENAI_API_KEY"' in source
    assert source.index('os.environ.pop("OPENAI_API_KEY"') < source.index(
        "build_telegram_gateway(")


def test_the_transport_decision_is_the_last_thing_run_does():
    """Wave 4a: `run()` ends in `start_transport`, not in a bare `run_polling`.

    Source-anchored like the pops above, for the same reason: `run()` cannot be
    executed in a test. What the branch DOES is `tests/test_main_webhook.py`'s
    question; what this one pins is the ordering nothing else can see — the
    transport is started after the secrets are gone and after the channel bus is
    up, so an update that arrives on the first webhook POST reaches a process
    whose event bus can carry the turn it starts.
    """
    from claude_hermes import main

    source = inspect.getsource(main.run)
    assert "start_transport(" in source
    starts = source.index("start_transport(")
    assert source.index("events_bus.start()") < starts
    assert source.index("control.start()") < starts
    for name in SECRET_ENV_NAMES:
        assert source.index(f'os.environ.pop("{name}"') < starts
    # The web server is started first: in webhook mode the transport blocks on it.
    assert source.index('name="web"') < starts


def test_polling_deletes_a_set_webhook_before_it_starts():
    """The load-bearing order, asserted on the source of the function that owns it.

    Verified against aiogram 3.29.1: `start_polling` does NOT remove a webhook
    that is still registered, and `getUpdates` behind one answers 409 forever. A
    deploy that switched back from webhook mode would come up and hear nothing.
    `run()` names both literals too, so the branch cannot be quietly moved out of
    reach of this file.
    """
    from claude_hermes import main
    from claude_hermes.modules.telegram.infrastructure import transport

    source = inspect.getsource(transport.start_transport)
    assert source.index("delete_webhook") < source.index("run_polling")
    run_source = inspect.getsource(main.run)
    assert run_source.index("delete_webhook") < run_source.index("run_polling")


def test_dist_dir_prefers_env_then_package_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CH_WEB_DIST", str(tmp_path))
    assert dist_dir() == tmp_path
    monkeypatch.delenv("CH_WEB_DIST")
    monkeypatch.setattr("claude_hermes.main._PACKAGE_DIST", tmp_path / "missing")
    assert dist_dir() is None


# --- the hermes toolset fingerprint --------------------------------------------


class _Meta:
    def __init__(self):
        self.rows: dict[str, str] = {}

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


class _Runner:
    def __init__(self):
        self.resets = 0

    async def reset_all(self):
        self.resets += 1


async def test_a_changed_toolset_resets_every_lane_once():
    """Tool schemas are in the cached prefix and `allowed_tools` is rebuilt every
    turn while the append is frozen per session: a deploy that changes the
    toolset has to reset every lane, once, and never again for the same one (P1).
    The value is the executor's fingerprint, stored as-is."""
    from claude_hermes.main import HERMES_TOOLS_KEY, sync_hermes_toolset
    meta, runner = _Meta(), _Runner()
    first, second = "a" * 64, "b" * 64
    # first boot ever: nothing recorded -> reset + record
    assert await sync_hermes_toolset(first, meta, runner) is True
    assert runner.resets == 1 and meta.rows[HERMES_TOOLS_KEY] == first
    # same fingerprint next boot: neither
    assert await sync_hermes_toolset(first, meta, runner) is False
    assert runner.resets == 1
    # the toolset moved (a flag flipped, a description reworded): reset again
    assert await sync_hermes_toolset(second, meta, runner) is True
    assert runner.resets == 2 and meta.rows[HERMES_TOOLS_KEY] == second


async def test_the_first_boot_after_the_names_only_fingerprint_resets_once():
    """The key is the same one the names-only fingerprint wrote: its old value (a
    comma-joined list) can never equal a digest, so the deploy that ships the
    full-declaration fingerprint resets every lane exactly once."""
    from claude_hermes.main import HERMES_TOOLS_KEY, sync_hermes_toolset
    meta, runner = _Meta(), _Runner()
    meta.rows[HERMES_TOOLS_KEY] = "mcp__hermes__session_search,mcp__hermes__cost_report"
    digest = "c" * 64
    assert await sync_hermes_toolset(digest, meta, runner) is True
    assert await sync_hermes_toolset(digest, meta, runner) is False
    assert runner.resets == 1


async def test_toolset_sync_hashes_the_full_declaration(tmp_path, monkeypatch):
    """The boot sync reads `tool_fingerprint()`, not the names: rewording a
    BUILT-IN tool's description (a literal in `hermes_tools`) resets every lane
    once, although not one name moved (AC 11)."""
    from claude_hermes.main import HERMES_TOOLS_KEY
    from claude_hermes.modules.turns.infrastructure import hermes_server

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        executor = ctx.runner._executor
        assert await ctx.toolset_sync() is True
        assert await ctx.meta.get(HERMES_TOOLS_KEY) == executor.tool_fingerprint()
        assert await ctx.toolset_sync() is False
        names = executor.tool_names()

        real_tool = hermes_server.tool

        def reworded(name, description, schema):
            if name == "session_search":
                description += " Reworded."
            return real_tool(name, description, schema)

        monkeypatch.setattr(hermes_server, "tool", reworded)
        assert executor.tool_names() == names
        assert await ctx.toolset_sync() is True             # the reword: one reset
        assert await ctx.toolset_sync() is False
    finally:
        await shutdown(ctx)


# --- the content plan (`content_enabled`) ---------------------------------------


def _enable_content(home, **extra) -> None:
    lines = ["content_enabled: true"] + [f"{k}: {v}" for k, v in extra.items()]
    (home / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def test_flipping_content_enabled_moves_the_toolset_fingerprint(tmp_path,
                                                                    monkeypatch):
    """`content_plan` is a hermes tool, and the fingerprint covers every tool: the
    boot that turns the plan on appends exactly that one name (after
    `memory_search`, so every cached prefix before it is unchanged in order) and
    resets every lane once; the next boot with the same flag resets nothing (P1)."""
    from claude_hermes.main import HERMES_TOOLS_KEY

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        assert ctx.content is None
        before = ctx.runner._executor.tool_names()
        assert "mcp__hermes__content_plan" not in before
        await ctx.toolset_sync()
    finally:
        await shutdown(ctx)

    _enable_content(tmp_path)
    ctx = await build_app()
    try:
        after = ctx.runner._executor.tool_names()
        assert after == before + ["mcp__hermes__content_plan"]
        assert await ctx.toolset_sync() is True             # the flip: one reset
        assert (await ctx.meta.get(HERMES_TOOLS_KEY)
                == ctx.runner._executor.tool_fingerprint())
        assert await ctx.toolset_sync() is False            # and never again
    finally:
        await shutdown(ctx)


async def test_content_enabled_wires_one_plan_into_every_consumer(tmp_path, monkeypatch):
    """One `ContentService` behind the routes, the tick, the chat tool and the
    routine tool — and the publisher port reaches the REAL scheduler: `describe`
    through `get(job_id, owner_id)` (another owner's routine is None) and the wake
    through `wake_if_idle`, true once and then false while the wake is pending."""
    from claude_hermes.http.deps import web_deps
    from claude_hermes.modules.content.application.service import ContentService

    _enable_content(tmp_path, timezone="Asia/Tashkent")
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        assert isinstance(ctx.content, ContentService)
        assert ctx.content.timezone == "Asia/Tashkent"
        assert web_deps(ctx).content is ctx.content
        tool = ctx.runner._executor._content
        assert tool.name == "content_plan" and tool.service is ctx.content
        lent = ctx.scheduler._executor._routine_tools
        assert lent._tool is tool                           # one declaration, both channels
        servers, names = lent.for_owner(11, is_deploy_owner=True, job_id=5)
        assert list(servers) == ["hermes"] and names == ["mcp__hermes__content_plan"]
        assert lent.for_owner(12, is_deploy_owner=False, job_id=5) == ({}, [])

        await ctx.acl.check(11, "private")                  # 11 claims the deploy
        jid, err = await ctx.scheduler.create_job(
            name="Content publisher", schedule="manual", prompt="publish",
            deliver_chat_id=11, origin_session_key="tg:private:11", owner_id=11,
            timeout_seconds=1800)
        assert err is None
        publisher = ctx.content._publisher
        desc = await publisher.describe(jid, 11)
        assert desc == {"name": "Content publisher", "kind": "manual",
                        "state": "active", "next_run_at": None, "lease_until": None,
                        "timeout_seconds": 1800}
        assert await publisher.describe(jid, 12) is None    # not theirs = not there
        assert (await ctx.content.set_publisher(11, jid))["job_id"] == jid
        now = "2026-10-01T05:00:00+00:00"
        assert await publisher.wake_if_idle(jid, now) is True
        assert await publisher.wake_if_idle(jid, now) is False   # a wake is pending
        await ctx.content.tick(datetime.now(timezone.utc))  # nothing due: no error
    finally:
        await shutdown(ctx)


async def test_content_v2_wires_the_drafter_the_cards_and_the_decisions(
        tmp_path, monkeypatch):
    """ONE `SchedulerPublisher` behind both routine ports; the lead time and the
    calendar origin from config; the cards holder and the decisions on the
    context, where `build_delivery` and the gateway pick them up. Headless, the
    holder is unbound — the card outbox waits."""
    from claude_hermes.core.app_context import SchedulerPublisher
    from claude_hermes.modules.content.presentation.decisions import (
        ContentCardDecisions,
    )

    _enable_content(tmp_path, content_draft_lead_hours=6,
                    web_public_origin="https://cladex.example.com")
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        service = ctx.content
        assert isinstance(service._publisher, SchedulerPublisher)
        assert service._drafter is service._publisher         # one adapter, two ports
        assert service._publisher.scheduler is ctx.scheduler
        assert ctx.cfg.content_draft_lead_hours == 6
        assert service._lead.total_seconds() == 6 * 3600
        assert service.calendar_url(7) == "https://cladex.example.com/calendar?item=7"
        assert ctx.content_cards is service._cards and ctx.content_cards.target is None
        assert isinstance(ctx.content_decisions, ContentCardDecisions)
        assert ctx.content_decisions._service is service
    finally:
        await shutdown(ctx)


async def test_build_delivery_binds_the_card_sender_in_every_process(
        tmp_path, monkeypatch):
    """The agent and every worker call `build_delivery`, and the card outbox is
    drained by whichever tick claims it — so the sender is bound there, over
    the same Bot, API client and TopicManager the routines use."""
    from claude_hermes.main import build_delivery
    from claude_hermes.modules.telegram.infrastructure.cards import (
        TelegramApprovalCards,
    )

    _enable_content(tmp_path)
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        delivery = await build_delivery(ctx, "123456:TEST-token")
        try:
            cards = ctx.content_cards.target
            assert isinstance(cards, TelegramApprovalCards)
            assert ctx.content._cards_port() is cards
            assert cards._bot is delivery.bot and cards._api is delivery.api
            assert cards._topics is delivery.topics
        finally:
            await delivery.aclose()
    finally:
        await shutdown(ctx)


async def test_build_delivery_without_the_plan_binds_no_cards(tmp_path, monkeypatch):
    from claude_hermes.main import build_delivery

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        assert ctx.content_cards is None and ctx.content_decisions is None
        delivery = await build_delivery(ctx, "123456:TEST-token")
        await delivery.aclose()
        assert ctx.content_cards is None
    finally:
        await shutdown(ctx)


@pytest.mark.parametrize("enabled", [True, False])
async def test_the_gateway_gets_the_content_decisions(tmp_path, monkeypatch, enabled):
    """The `cnt:` buttons, a reply to a card and the titler skip all reach the
    plan through ONE object handed to the dispatcher; the plan off is `None`,
    which is a gateway that behaves exactly as before."""
    from claude_hermes import main
    from claude_hermes.modules.telegram.infrastructure import dispatcher

    seen: dict = {}

    def fake_create_dispatcher(cfg, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(dispatcher, "create_dispatcher", fake_create_dispatcher)
    if enabled:
        _enable_content(tmp_path)
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        main.build_telegram_gateway(ctx, bot=object(), username="bot", sender=object(),
                                    titler=object(), broker=None, writer=object())
        assert "content_decisions" in seen
        assert seen["content_decisions"] is ctx.content_decisions
        assert (seen["content_decisions"] is not None) is enabled
    finally:
        await shutdown(ctx)


async def test_the_plan_is_absent_without_the_flag(tmp_path, monkeypatch):
    """Off by default, and `None` everywhere: no tool on any lane, no routine
    tools, no routes (the shell's mounting condition), nothing on the tick."""
    from claude_hermes.http.deps import web_deps

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    ctx = await build_app()
    try:
        assert ctx.cfg.content_enabled is False and ctx.content is None
        assert ctx.runner._executor._content is None
        assert ctx.scheduler._executor._routine_tools is None
        assert web_deps(ctx).content is None
    finally:
        await shutdown(ctx)


class _Clock:
    def __init__(self, calls, name, fail=False):
        self._calls, self._name, self._fail = calls, name, fail

    async def tick(self, now):
        self._calls.append(self._name)
        if self._fail:
            raise RuntimeError("plan down")

    async def reap(self, now):
        self._calls.append(self._name)


@pytest.mark.parametrize("fail", [False, True])
async def test_the_worker_tick_runs_the_content_tick_last_and_survives_it(fail):
    """Redis mode's clock: claim, reap, then the plan — in its own `try`, so a
    failing plan is logged rather than failing a tick that already claimed."""
    from types import SimpleNamespace

    from claude_hermes.workers import tasks

    calls: list[str] = []
    ctx = SimpleNamespace(scheduler=_Clock(calls, "scheduler"),
                          turns=_Clock(calls, "reap"),
                          content=_Clock(calls, "content", fail=fail))
    saved = tasks._ctx
    tasks.bind(ctx)
    try:
        await tasks.scheduler_tick()
        assert calls == ["scheduler", "reap", "content"]
        ctx.content = None                                  # the flag off
        await tasks.scheduler_tick()
        assert calls[3:] == ["scheduler", "reap"]
    finally:
        tasks.bind(saved)


def test_the_inline_clock_ticks_the_plan_after_the_reap():
    """Inline mode's clock is `CronTicker`'s extras, each in its own `try` there:
    the content tick rides it after the reap, and only when the plan exists.
    Source-anchored, because `run()` cannot be executed in a test."""
    from claude_hermes import main

    source = inspect.getsource(main.run)
    assert "extra: tuple = (ctx.turns.reap,)" in source
    assert source.index("(ctx.turns.reap,)") < source.index("(ctx.content.tick,)")
    assert "if ctx.content is not None:" in source


def test_the_config_checks_the_timezone_and_normalises_the_channels(tmp_path):
    from claude_hermes.core.config import Config, load_config

    assert Config().timezone == "UTC" and Config().content_enabled is False
    assert Config().content_draft_lead_hours == 24
    assert Config().content_channels == [
        "linkedin", "telegram", "x", "threads", "instagram", "youtube"]
    (tmp_path / "config.yaml").write_text(
        "timezone: Asia/Tashkent\ncontent_channels: [LinkedIn, ' x ', '']\n",
        encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.timezone == "Asia/Tashkent"
    assert cfg.content_channels == ["linkedin", "x"]
    (tmp_path / "config.yaml").write_text("timezone: Asia/Tashkennt\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Asia/Tashkennt"):
        load_config(tmp_path)

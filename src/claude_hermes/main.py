"""The composition root.

`build_app()` builds every module service over one engine and one workspace and
returns an `AppContext`. It is **headless**: no bot token is read, no network call
is made, nothing is assigned onto a service afterwards. `run()` adds the Telegram
half — the only part that needs a token — starts the cron ticker and the web
channel, and polls.

Three edges are deliberately late-bound rather than resolved here, and all three
are about ordering rather than taste: the scheduler's report deliverer and topic
provisioner exist only once there is a Bot (`bind_delivery`, and the content
plan's approval-card sender with them), the extensions'
prompt-cache invalidator is the runner, which needs the extensions composer
(`_invalidate` below), and the content plan's publisher port is the scheduler,
whose executor needs the content plan's routine tools (`SchedulerPublisher`).
Everything else is one pass, top to bottom.

Since Wave 3a this file is also where the deployment's *shape* is decided, and
every one of those decisions reads exactly one config value:

* `queue_mode` (derived from `CH_REDIS_URL`) picks `InlineQueue` or `TaskiqQueue`,
  and with it whether `main.run` drives the clock itself (`CronTicker`,
  `curator_loop`) or a separate scheduler process enqueues the two periodic tasks.
  Since Wave 3b it also picks the three objects a turn stops being local through:
  the event bus (`InProcessEventBus` / `RedisEventBus`), the in-flight snapshot
  store (`MemorySnapshots` / `RedisSnapshots`) and the ask broker (`AskBroker` /
  `RedisAskBroker`). Everything above them is constructed once, in one shape;
* `blob_backend` picks `LocalBlobStore` or `S3BlobStore` over a MinIO bucket;
* `CH_SECRETS_KEY` is read into a `SecretBox` *before* the environment is stripped.

Nothing below the composition root ever asks which mode it is in.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from .core.app_context import (
    AppContext,
    ExtensionAdminTool,
    ExtensionCompose,
    JobLookup,
    LaneWorkspace,
    LateDocumentDelivery,
    LatePresenter,
    LearnPromptBuilder,
    LearningBundle,
    MemoryBundle,
    MessageActivity,
    QueuedTurnRunner,
    RowedLaneModels,
    SchedulerPublisher,
    SenderDeliverer,
    SkillAdminTool,
    TelegramAskPresenter,
    ThreadEnsure,
    TranslatingSecrets,
    WikiForkGuards,
    WikiHooksProvider,
)
from .core import snapshots
from .core.config import load_config
from .core.crypto import SecretBox, SecretsKeyError
from .core.db.engine import make_engine
from .core.db.migrate import upgrade_head
from .core.eventbus import InProcessEventBus, RedisEventBus
from .core.logging import configure
from .core.meta import MetaStore
from .core.redis import create_client
from .core.tasks import InlineQueue
from .core.workspace import Workspace, WorkspaceResolver
from .modules.content import wiring as content_wiring
from .modules.extensions import wiring as extensions_wiring
from .modules.extensions.infrastructure import catalog_repository as ext_catalog
from .modules.extensions.infrastructure import secret_key
from .modules.extensions.infrastructure.redis_pending_states import RedisPendingStates
from .modules.extensions.infrastructure.refresh_locks import RedisLocks
from .modules.extensions.infrastructure.repositories.extension_repository import (
    SqlExtensionRepository,
)
from .modules.extensions.infrastructure.repositories.oauth_token_repository import (
    SqlOauthTokenRepository,
)
from .modules.extensions.presentation import agent_tool as ext_agent_tool
from .modules.extensions.presentation import cli as drafts_cli
from .modules.files.application.service import FileService
from .modules.files.infrastructure.layout import blob_root
from .modules.files.infrastructure.local_blob_store import LocalBlobStore
from .modules.files.infrastructure.repository import FileRepository
from .modules.files.infrastructure.s3_blob_store import S3BlobStore
from .modules.identity.application.services import AccessControl
from .modules.identity.domain.telegram_proof import login_key, webapp_key
from .modules.identity.infrastructure.repositories import UserRepository
from .modules.learning import wiring as learning_wiring
from .modules.learning.application.services.loop import LearningLoop
from .modules.learning.infrastructure.dispatch import (
    LEARNING_REVIEW_TASK,
    QueueReviewDispatcher,
)
from .modules.learning.infrastructure.fork_runner import SdkForkRunner
from .modules.learning.infrastructure.review_slot import RedisReviewSlot
from .modules.memory.application.services.index import WikiIndexService
from .modules.memory.infrastructure.lint_fork import build_wiki_lint_service
from .modules.memory.infrastructure.page_index import PgWikiIndex
from .modules.memory.infrastructure.turn_hooks import (
    WikiTurnState,
    index_flush_factory,
    page_touch_factory,
    raw_guard_factory,
    stop_gate_factory,
    wiki_hooks,
)
from .modules.memory.infrastructure.wiki_store import WikiStore
from .modules.scheduler.application.service import SchedulerService
from .modules.scheduler.infrastructure.dispatch import RUN_JOB_TASK, QueueJobDispatcher
from .modules.scheduler.infrastructure.executor import SdkJobExecutor
from .modules.scheduler.infrastructure.repository import JobRepository
from .modules.scheduler.infrastructure.ticker import CronTicker
from .modules.scheduler.presentation.tool import CronToolPort
from .modules.sessions.application.services import (
    SessionRouter,
    SessionService,
    ThreadService,
    ThreadTitleService,
)
from .modules.sessions.infrastructure.repositories import (
    MessageRepository,
    SessionRepository,
    ThreadRepository,
)
from .modules.sessions.infrastructure.title_generator import SdkTitleGenerator
# The webhook's update dedup. Module-level, unlike the rest of `modules.telegram`
# (imported inside the two functions that need aiogram): this one imports nothing
# but structlog and its own domain, and `build_app` stays headless either way.
from .modules.telegram.infrastructure.update_dedup import (
    MemoryUpdateDedup,
    RedisUpdateDedup,
)
from .modules.turns.application.ask_service import AskService
from .modules.turns.application.bus import TurnBus
from .modules.turns.application.control import (
    CancelRegistry,
    MetaSlashCommands,
    TurnControl,
)
from .modules.turns.application.execution import TurnRunService
from .modules.turns.application.launcher import TurnLauncher
from .modules.turns.application.ports import TurnSettings
from .modules.turns.application.turn_service import TurnService
from .modules.turns.domain.exceptions import LaneBusy
from .modules.turns.infrastructure.ask_broker import AskBroker
from .modules.turns.infrastructure.dispatch import QueueTurnDispatcher
from .modules.turns.infrastructure.publishers import ChannelPublisher
from .modules.turns.infrastructure.redis_ask_broker import RedisAskBroker
from .modules.turns.infrastructure.repositories import (
    TurnCostRepository,
    TurnEventRepository,
)
from .modules.turns.infrastructure.sdk_runner import SdkTurnExecutor
from .modules.turns.infrastructure.snapshots import MemorySnapshots, RedisSnapshots
from .modules.turns.infrastructure.turn_repository import TurnRepository
from .workers import tasks as worker_tasks

log = structlog.get_logger(__name__)

# Where the built CladeX SPA lives inside the image. The Dockerfile copies the
# Node build stage's `web/dist` here and sets CH_WEB_DIST; on a dev host neither
# exists and the API simply serves no static files.
_PACKAGE_DIST = Path(__file__).parent / "web_dist"


def dist_dir() -> Path | None:
    """The directory holding the built SPA, or None when there is no build."""
    env = os.environ.get("CH_WEB_DIST")
    if env:
        return Path(env)
    return _PACKAGE_DIST if _PACKAGE_DIST.is_dir() else None


HERMES_TOOLS_KEY = "hermes_tools"


async def sync_hermes_toolset(fingerprint: str, meta: Any, runner: Any) -> bool:
    """Reset every lane when the `hermes` toolset is not what the last boot
    published. Returns True when it did.

    Tool schemas are part of the cached prompt prefix and `allowed_tools` is
    rebuilt every turn, while `system_append` is frozen on the session row —
    so a deploy that adds a tool would otherwise swap the toolset under every
    live session (P1). Same mechanism as an extension install (`_invalidate`),
    same `meta` write-only-when-changed shape as the slash commands. A flag
    such as `memory_search_enabled` flipping is caught the same way.

    `fingerprint` is `SdkTurnExecutor.tool_fingerprint()`: a digest of every
    tool's name, description AND schema, not the names alone — a deploy that
    rewords a description moves the cached prefix just as surely as one that
    adds a tool, and before this it did so without a reset. Stored as-is under
    the same key, so the first boot after the change resets every lane once
    (the stored value was the old comma-joined name list).
    """
    if await meta.get(HERMES_TOOLS_KEY) == fingerprint:
        return False
    await runner.reset_all()
    await meta.set(HERMES_TOOLS_KEY, fingerprint)
    log.info("hermes toolset changed: every lane starts a fresh session",
             fingerprint=fingerprint[:12])
    return True


async def build_app(*, migrate: bool = True) -> AppContext:
    """Every module service, wired into every port, over one engine.

    `migrate=False` is the worker's: the agent owns the schema, and two processes
    running `alembic upgrade head` against a fresh database at the same moment
    would race each other in the version table.
    """
    workspace = Workspace.resolve()          # creates and seeds the tree
    home = workspace.home
    cfg = load_config(home)
    if not cfg.database_url:
        raise SystemExit("CH_DATABASE_URL is not set")

    # The key that opens `extension_secrets`, read while it is still readable.
    # `run()` and the worker startup pop CH_SECRETS_KEY from os.environ a few
    # lines after this returns, so a box built any later would be built from an
    # environment that no longer has the key. Every store that seals or opens a
    # row is handed this one object (`extensions_wiring.build(box=)`), and nothing
    # reads the variable again.
    box = _secret_box(cfg)

    # Schema first, then the pool: a process that opens connections against a
    # database Alembic has not upgraded yet fails on the first query instead.
    if migrate:
        await upgrade_head(cfg.database_url)
    engine = make_engine(cfg.database_url)
    meta = MetaStore(engine)

    # --- the queue ---------------------------------------------------------
    # One registry, shared with the worker because both import the same module;
    # the queue behind it is the only thing that differs between the two modes.
    # Inline is not a degraded mode: it is `asyncio.create_task`, which is what
    # this process did before the queue existed.
    registry = worker_tasks.registry
    redis_client = None
    if cfg.queue_mode == "redis":
        # Imported here and nowhere else: `taskiq_app` is the one module that
        # knows taskiq exists, and a laptop without it must still boot.
        from .workers.taskiq_app import TaskiqQueue
        redis_client = create_client(cfg.redis_url)
        # `url=` and not the environment: `run()` pops CH_REDIS_URL a few lines
        # from here, and the broker is built on the first enqueue — which is a
        # nudge from `after_turn`, long after the pop. Handed the URL here, the
        # queue never reads the environment at all.
        queue: Any = TaskiqQueue(registry, url=cfg.redis_url)
    else:
        # One job at a time, which is what this process did before the queue
        # existed: the ticker awaited each claimed fire in turn. Without the
        # limit, N routines due on the same minute would fork N Claude CLI
        # processes at once inside a container sized for one. One review at a
        # time too, since Wave 4b: the review lease is per user now, so N users
        # could otherwise start N review forks on the owner's account at once —
        # this limit is inline mode's bound, `lock:review` is redis mode's.
        queue = InlineQueue(registry, {RUN_JOB_TASK: 1, LEARNING_REVIEW_TASK: 1})

    # --- sessions ----------------------------------------------------------
    session_repo = SessionRepository(engine)
    sessions = SessionRouter(session_repo, cfg)
    messages = MessageRepository(engine)
    thread_repo = ThreadRepository(engine)

    # --- turns: the bus first, because threads and titles publish onto it ---
    turn_events = TurnEventRepository(engine)
    costs = TurnCostRepository(engine)
    turn_store = TurnRepository(engine)
    # The channel bus and the in-flight snapshot store: the one place the
    # deployment's shape reaches the turn machinery. Inline mode is a listener
    # list and a dict — exactly what this was before Wave 3b — and redis mode is
    # pub/sub and keys, so a turn run in a worker reaches a browser attached to
    # the agent. Neither is DIALLED here: `build_app` stays headless, and the
    # entry point starts the bus after it has popped the secrets.
    if redis_client is not None:
        events_bus: Any = RedisEventBus(redis_client)
        snapshot_store: Any = RedisSnapshots(redis_client,
                                             cfg.turn_snapshot_ttl_seconds)
    else:
        events_bus = InProcessEventBus()
        snapshot_store = MemorySnapshots()
    # Constructed here rather than inside the runner because three other
    # consumers need the same instance: the Telegram presenter subscribes to it,
    # the cron ticker emits delivery turns onto it, and a new title announces
    # itself on it.
    bus = TurnBus(turn_events, ChannelPublisher(events_bus), snapshot_store)

    # The `turns` table is the one source of truth for "is this lane busy", so the
    # sidebar's running dots read it rather than the snapshot store: a dead
    # worker's snapshot outlives its lease by up to the TTL, and the row does not.
    # The repository already has `active_lanes(keys) -> set[str]`, which IS
    # sessions' `LaneActivity`, so there is no adapter here to translate.
    threads = ThreadService(thread_repo, turn_store)
    titles = ThreadTitleService(thread_repo, SdkTitleGenerator(cfg.title_model), bus)

    # --- identity ----------------------------------------------------------
    # The `users` table is the only owner record: a claim is one INSERT arbitrated
    # by `users_one_owner`, and the row migration `0005` materialised from the old
    # `meta.owner_user_id` key is the same row every later process reads. Nothing
    # writes that meta key any more (Step 5); a stale one on a live volume is inert.
    # `lambda: cfg.allowed_users` and not the list: Config is mutable at runtime,
    # and the ACL has always read the allowlist at check time. The two grant
    # defaults are read the same way: `users_shell_enabled`/`users_learning_enabled`
    # are only what a user nobody has set a grant for inherits, and `acl.grants` is
    # the one place that decides — turns, routines and the curator sweep all ask it.
    acl = AccessControl(UserRepository(engine),
                        allowed_users=lambda: cfg.allowed_users,
                        shell_default=lambda: cfg.users_shell_enabled,
                        learning_default=lambda: cfg.users_learning_enabled)

    # --- which model a lane runs ---------------------------------------------
    # ONE gatherer for every channel that asks — the turn about to call the CLI,
    # the composer's picker, /model in Telegram — over the thread's explicit pair,
    # the live session's pair and the owner's default; `core.model_catalog.
    # resolve()` is the rule. After the ACL, because identity's AccessControl IS
    # the `OwnerDefaults` port; on the bus, because a change is a `thread.model`
    # event every open tab listens for.
    lane_models = SessionService(thread_repo, sessions, cfg, defaults=acl, events=bus)
    # `offered()` warns when an offered model holds more than the review fork's
    # model can resume. Asked once here, so that warning is a boot-time fact; the
    # service caches the answer for every later caller.
    lane_models.offered_models()

    async def deploy_owner() -> int:
        """The resolver's second lookup. 0 = nobody has claimed the deploy yet,
        which the resolver deliberately does not cache."""
        owner = await acl.owner()
        return owner.id if owner is not None else 0

    # Core may not import a module, so the resolver takes both lookups as async
    # callables bound here. Every process that runs a turn gets this same object;
    # `workspace` (the owner's) stays what the curator and the skills library take.
    workspaces = WorkspaceResolver(home, workspace_dir_of=acl.workspace_dir_of,
                                   deploy_owner=deploy_owner)

    # --- files -------------------------------------------------------------
    # The bucket is the source of truth in s3 mode and the mirror sits under the
    # very directory the local store uses — the agent's own Read/Glob and every
    # prompt that names `inbox/…` keep working, and the containment check that
    # catches a forged row has the same root in both.
    #
    # Wave 4a: that root is looked up PER ROW rather than frozen here, because
    # there is a tree per user now (blocker B4). The lookup takes the row's
    # `owner_id` — never the requester's — so the download route re-resolves a
    # file against whoever the row says owns it, and a file written into user B's
    # inbox is read back out of user B's inbox.
    async def mirror_root(owner_id: int = 0) -> Path:
        """`agent_home` of the row's owner. `0` means the deploy owner.

        `0` is what a pre-4a row with a NULL column carries, and the deploy owner's
        tree is exactly where those files are. Falling back to `blob_root(home)` while nobody has claimed the deploy yet
        keeps a fresh install's very first upload from raising `UnknownUser`.
        """
        owner = int(owner_id or 0) or await workspaces.owner_id()
        return await workspaces.mirror_root(owner) if owner else blob_root(home)

    s3 = None
    if cfg.blob_backend == "s3":
        # Lazy: boto3 is a heavy import and a local deployment never pays for it.
        from .integrations.s3 import S3Client
        s3 = S3Client(cfg.s3_endpoint, cfg.s3_access_key, cfg.s3_secret_key,
                      cfg.s3_bucket, cfg.s3_region)
        await s3.ensure_bucket()             # idempotent; a fresh volume needs it
        blobs: Any = S3BlobStore(s3, mirror_root)
    else:
        blobs = LocalBlobStore(mirror_root)
    files = FileService(FileRepository(engine), blobs,
                        cfg.web_upload_max_mb * 1024 * 1024)

    # --- memory ------------------------------------------------------------
    # `wiki` is the DEPLOY OWNER's, which is what the lint pass, the curator and
    # the (owner-only) wiki routes take. A TURN does not use it: `wiki_for` builds
    # the lane owner's store per turn, because the index goes into a cached prompt
    # prefix and the hooks decide what a `Write` may touch — point either at the
    # wrong tree and user B's prefix carries user A's index while B's Stop gate
    # writes A's wiki (blocker B1). Building one is a path object plus one file
    # read, so per turn is free.
    wiki = WikiStore(home, cfg.wiki_index_max_chars)
    wiki.seed()

    def wiki_for(workspace) -> WikiStore:
        """This workspace's wiki. `WorkspaceResolver.for_user` has already created
        the directory (`Workspace.ensure`), and `WikiStore.seed` is idempotent and
        refuses to touch an existing index, so a brand-new user's first turn gets
        a real index rather than an empty string."""
        store = WikiStore.for_workspace(workspace, cfg.wiki_index_max_chars)
        store.seed()
        return store

    # The wiki INDEX: a derived `wiki_pages` table over the markdown trees, one
    # row per (owner, page). Markdown stays the source of truth — the model keeps
    # writing pages with Write/Edit — and the table is what `memory_search`
    # queries and what a boot rebuilds from disk. One store over the one engine;
    # the service is what the Stop hooks, the lint pass and the boot sweep call.
    page_index = PgWikiIndex(engine)
    indexer = WikiIndexService(page_index)

    async def reindex_all() -> None:
        """Rebuild every user's wiki index from disk — the boot-time trigger.

        The table is a cache and disk wins, so this is what makes a dropped
        table, a page edited by hand over SSH, or a deploy older than the index
        converge. Walks `acl.active_user_ids()` (the owner included) and builds
        each workspace from its row WITHOUT `ensure()`: a boot must not be what
        seeds a tree nobody has used yet (the resolver's own rule), and
        `reindex` is a no-op on a wiki that does not exist. Sequential and
        per-user fault-tolerant: one broken tree logs and the sweep goes on.
        """
        try:
            ids = await acl.active_user_ids()
        except Exception:
            log.exception("wiki reindex: could not list users; skipping")
            return
        for uid in dict.fromkeys(ids):
            try:
                ws = Workspace.for_user(home, await acl.workspace_dir_of(uid))
                await indexer.reindex(uid, ws)
            except Exception:
                log.exception("wiki reindex failed for one user", user_id=uid)
        log.info("wiki reindex done", users=len(ids))

    memory = MemoryBundle(
        wiki=wiki, wiki_root=wiki.root,
        hooks=WikiHooksProvider(wiki, wiki_hooks=wiki_hooks,
                                new_state=WikiTurnState, wiki_for=wiki_for,
                                indexer=indexer),
        indexer=indexer, reindex_all=reindex_all,
    )

    # --- extensions --------------------------------------------------------
    # The two registry tables, each its own repository over the one engine; the
    # bundle keeps both (`ExtensionsBundle.store` / `.tokens`) and the connectors
    # router reads the tokens off it.
    ext_store = SqlExtensionRepository(engine)
    ext_tokens = SqlOauthTokenRepository(engine)
    runner_box: list = []

    async def _invalidate(owner_id: int) -> None:
        """extensions' `PromptCacheInvalidator`. Late-bound: enabling a connector
        changes that user's cached prompt prefix, so THEIR lanes must go — but the
        runner needs the composer this same bundle provides, and one of the two
        edges has to be deferred.

        One owner and not the deployment: a connector one person enables reaches
        no other person's prefix, and evicting every warm lane for it is the
        cache-hit regression NFR-1 counts. `include_unowned` is the deploy
        owner's belt — group lanes, and lanes bound while the deploy was still
        unclaimed, carry a NULL owner and are theirs.
        """
        if not runner_box:
            return
        deploy_owner = await workspaces.owner_id()
        await runner_box[0].reset_owned(
            owner_id, include_unowned=owner_id == deploy_owner)

    extensions = extensions_wiring.build(
        home, engine, cfg=cfg, catalog_root=None,
        store=ext_store, tokens=ext_tokens, on_change=_invalidate,
        # A started OAuth flow has to survive the browser coming back to a
        # different process (or to this one after a redeploy), so in redis mode
        # the pending states live in Redis with their own TTL. Inline mode has
        # exactly one process, and the in-memory default is honest about that.
        pending=(RedisPendingStates(redis_client)
                 if redis_client is not None else None),
        # One refresh per connector, across processes. `for_scope` already runs
        # for `turn` and `cron` concurrently in one process; a worker composing
        # the same connector makes that two processes, and a provider that
        # rotates refresh tokens refuses the second use of the same one.
        locks=RedisLocks(redis_client) if redis_client is not None else None,
        box=box,
    )

    # --- content -----------------------------------------------------------
    # The content plan, and only when `content_enabled`: `None` is how the tool,
    # the routes and the tick all disappear. Built BEFORE the scheduler because
    # the scheduler's executor lends its `content_plan` to the deploy owner's
    # routines — and the plan wakes its publisher routine through the scheduler,
    # so that one edge (`SchedulerPublisher.scheduler`) is bound just below, once
    # the scheduler exists. Nothing calls it before `build_app` returns.
    # ONE adapter serves both of content's routine ports, `Publisher` and
    # `Drafter`: it takes the job id on every call, so which routine it wakes is
    # the plan's decision (meta `content_publisher_job_id` and
    # `content_drafter_job_id`), and there is one late-bound edge to close, not two.
    publisher = SchedulerPublisher()
    content = None
    if cfg.content_enabled:
        # `cards` is left to `build`: it makes the late-bound holder
        # (`content.cards`), and `build_delivery` binds telegram's card sender
        # into it once a process has a Bot.
        content = content_wiring.build(
            engine, workspaces=workspaces, publisher=publisher, drafter=publisher,
            meta=meta, timezone=cfg.timezone, channels=cfg.content_channels,
            # The stuck timeout of a publisher routine that declares no limit of
            # its own is the one every such routine runs under.
            publisher_timeout_seconds=cfg.cron_timeout_seconds,
            draft_lead_hours=cfg.content_draft_lead_hours,
            # The card's "Открыть в календаре" button, `<origin>/calendar?item=<id>`.
            # Empty (no public web) is a card without that button.
            calendar_origin=cfg.web_public_origin)

    # --- scheduler ---------------------------------------------------------
    job_store = JobRepository(engine)
    scheduler = SchedulerService(
        job_store,
        # A routine runs as the person who created it (D6): `jobs.owner_id` picks
        # the workspace, and a NULL one — every row from before this wave — is the
        # deploy owner's, which is where it has been running all along.
        # Its shell is the owner's grant at fire time, asked of the same resolver
        # a turn asks, so a routine and a chat of one person never disagree.
        # `routine_tools` is content's `RoutineTools`: `content_plan` in an
        # in-process `hermes` server for the deploy owner's routines, and for
        # nobody else's (the executor passes `is_deploy_owner`).
        executor=SdkJobExecutor(cfg, home, composer=extensions.compose,
                                recorder=messages, workspaces=workspaces,
                                grants=acl,
                                routine_tools=(content.routine_tools
                                               if content is not None else None)),
        events=bus,
        threads=ThreadEnsure(threads.touch),
        # A claimed fire is handed to the queue, never run inside `tick`. Inline
        # that is still this process; in redis mode it is whichever worker picks
        # the task up. Passing no dispatcher is the supported degradation (the
        # ticker runs the job itself) and is what `tests/test_cron_ticker.py`
        # still exercises.
        dispatcher=QueueJobDispatcher(queue),
        # A routine created with no owner belongs to the deploy owner (Wave 4b,
        # D11), not to nobody: a NULL-owner row is visible to no one and runs as
        # no one. Resolved at create time, so a lane-less fork that creates a
        # job still writes a real id — and 0 (unclaimed) still writes NULL.
        deploy_owner=workspaces.owner_id,
        # deliverer and topics arrive in run(), once there is a Bot.
    )
    publisher.scheduler = scheduler

    # --- learning ----------------------------------------------------------
    # Wave 4b: a learning SCOPE per workspace, built lazily by the registry and
    # cached per process. Everything below is what one scope needs from the
    # other modules, handed to the wiring as callables and objects — never as
    # imports, since modules are independent of each other.
    #
    # One fork runner for both detached passes. Memory's hooks travel with it:
    # `wiki/raw/` stays immutable and the "update the index before you stop" gate
    # still fires in a review fork, exactly as in a foreground turn.
    fork = SdkForkRunner(cfg, home, WikiForkGuards(
        new_state=WikiTurnState, raw_guard=raw_guard_factory,
        page_touch=page_touch_factory, stop_gate=stop_gate_factory,
        # The review fork is the wiki's main rewriter, so it flushes what it
        # wrote to the index exactly as a foreground turn does.
        index_flush=index_flush_factory, indexer=indexer))

    def activity_for(user_id: int) -> MessageActivity:
        """The curator's quiet-hours check, scoped to ONE user: "has this user
        talked recently?", so a neighbour chatting at 3am holds nobody else's
        pass off. An async callable, because `MessageActivity` awaits it and a
        bare lambda would `TypeError` inside the swallowed pass (B-7)."""
        async def owner() -> int:
            return user_id
        return MessageActivity(messages, owner)

    def wiki_lint_for(ws, user_id: int) -> Any:
        """memory's lint pass over THIS workspace's wiki, confined to that tree.
        A wet pass rewrites pages, so it ends by reindexing the whole tree."""
        async def after_lint() -> None:
            await indexer.reindex(user_id, ws)
        return build_wiki_lint_service(cfg, home, ws.wiki_root, workspace=ws,
                                       after_lint=after_lint)

    async def learning_of(user_id: int) -> bool:
        """Whether this user's conversations teach the agent — their learning
        grant, else the deploy default. Read per call: a grant set in Settings →
        Users reaches the next sweep and the next queued review with no restart."""
        return (await acl.grants(user_id)).learning

    scopes = learning_wiring.build_scopes(
        cfg, workspaces=workspaces, meta=meta,
        # `session_repo`, not the Database facade: the review fork resumes the
        # lane's own SDK session, and that row is the sessions module's to hand
        # over.
        sessions=session_repo, activity_for=activity_for,
        wiki_lint_for=wiki_lint_for, fork=fork,
        # `SnapshotStorePort` is satisfied by the module itself: a Protocol does
        # not care whether the methods hang off an object or a namespace.
        snapshots=snapshots,
        # Every non-disabled user, no limit — the curator sweep walks this, and
        # keeps the owner plus whoever `learning_of` answers yes for.
        users=acl.active_user_ids, learning_of=learning_of,
        # One review fork at a time, deploy-wide. The per-user gate ("is a
        # review in flight" — one `meta` row with an expiry per user) no longer
        # bounds concurrency; in redis mode this Redis lock does, and in inline
        # mode the queue limit above does, so the slot is None there.
        review_slot=(RedisReviewSlot(
            redis_client, ttl_ms=int(cfg.review_timeout_seconds * 1000))
            if redis_client is not None else None),
    )
    # The owner's scope, built eagerly: the bundle keeps its owner-level fields
    # (the curator/review tests read them by name) populated from it, and
    # everything else goes through `scopes`.
    owner_scope = await scopes.owner()
    loop = LearningLoop(scopes, QueueReviewDispatcher(queue))
    learning = LearningBundle(
        skills_dir=owner_scope.skills_dir, logs_dir=owner_scope.logs_dir,
        sidecar=owner_scope.sidecar, library=owner_scope.library,
        nudges=owner_scope.nudges, loop=loop, admin=owner_scope.admin,
        curator=owner_scope.curator,
        # The `learning_review` task reaches the service through `scopes`: the
        # process that runs a review is not the one that asked for it, and the
        # scope it runs in travels with the payload.
        review=owner_scope.review, gate=owner_scope.gate,
        scopes=scopes,
    )

    # --- turns: the runner -------------------------------------------------
    # The pending-question broker. In redis mode the worker that opens the
    # question is not the process a tap arrives at, so the question is a key and
    # the answer is a key; inline mode keeps both in a dict, because there is only
    # one process for them to be in.
    if redis_client is not None:
        ask_broker: Any = RedisAskBroker(redis_client, events_bus)
    else:
        ask_broker = AskBroker()
    ask_presenter = LatePresenter()          # the Telegram keyboard, bound in run()
    deliver_document = LateDocumentDelivery()
    settings = TurnSettings.from_config(cfg)
    executor = SdkTurnExecutor(
        settings=settings,
        home=home,
        search=messages,
        costs=costs,
        hooks=memory.hooks,
        compose=ExtensionCompose(extensions.compose),
        # The reviewed catalog: read-only code on a `:ro` mount, and one of the
        # roots the turn path guard admits (every lane may read a manifest). Read
        # off the module that owns the constant — once, here, at build time; a
        # test that patches `_CATALOG_DIR` afterwards has to rebuild the app.
        catalog_root=ext_catalog._CATALOG_DIR,
        scheduler=CronToolPort(scheduler),
        # Over the registry, not one service: `skill_admin` administers the
        # LANE OWNER's library, resolved per call from the turn context.
        skills=SkillAdminTool(scopes),
        # The tool is extensions' own: the module declares the name, the description
        # and the schema, and the port carries them to the MCP server, so there is
        # one `ext_manage` rather than a body here and a description over there.
        ext_admin=ExtensionAdminTool(
            extensions.admin, acl=acl,
            name=ext_agent_tool.TOOL_NAME,
            description=ext_agent_tool.TOOL_DESCRIPTION,
            schema=ext_agent_tool.TOOL_SCHEMA),
        deliver_document=deliver_document,
        file_store=files,
        ask=AskService(ask_broker, ask_presenter),
        # memory's wiki index as turns' `WikiSearch`: the `memory_search` tool.
        wiki_search=page_index,
        # content's `ContentPlanPort`: the module declares `content_plan`'s name,
        # description and schema on the object, like `ext_manage`. The executor
        # decides which lanes get it (the deploy owner's private and web lanes);
        # `None` — the flag off — is a toolset without it, and `tool_names()`
        # below moves with the flag, so flipping it resets every lane once.
        content=content.tool if content is not None else None,
    )
    runner = TurnService(
        settings=settings,
        sessions=sessions, messages=messages, threads=threads, wiki_for=wiki_for,
        bus=bus, executor=executor, observer=loop, jobs=JobLookup(scheduler),
        # The CLI reports its slash commands to whichever process ran the last
        # turn; `GET /api/commands` is served by another one. So the list is
        # recorded in `meta` when it changes, not kept on this object alone.
        commands_store=MetaSlashCommands(meta),
        # The only identity fact the runner needs: who owns a group or cron lane
        # (D3). The resolver caches it, so this is one query per process, and it
        # is the SAME number the workspace resolution uses — the row's owner and
        # the tree the turn runs in cannot disagree.
        deploy_owner=workspaces.owner_id,
        # Which (model, effort) each turn runs, resolved before `turn.start`.
        models=lane_models,
        # A non-owner lane's shell and learning, resolved per turn inside the
        # turn's error envelope — identity's AccessControl IS the `LaneGrants`
        # port, as it is sessions' `OwnerDefaults`.
        grants=acl,
    )
    runner_box.append(runner)

    # --- turns: the row and the task ---------------------------------------
    # The launcher is what every channel starts a turn through, and the run
    # service is what claims the row and runs the SDK loop. Both exist in both
    # modes and in both processes: inline mode runs the task body in the agent,
    # and redis mode runs it in a worker, through the same two objects.
    cancels = CancelRegistry()
    control = TurnControl(events_bus, cancels, ask_broker)
    turns = TurnLauncher(
        turn_store, QueueTurnDispatcher(queue), bus, control,
        queue_timeout_seconds=cfg.turn_queue_timeout_seconds,
        retention_days=cfg.turn_retention_days)
    # `bus=` is only for the envelope a failure BEFORE the turn body would never
    # get (a blob fetch, a forged attachment id): the turn's own events come from
    # `TurnService`, which owns the sequence.
    turn_runs = TurnRunService(turn_store, runner, files, cancels,
                               lease_seconds=cfg.turn_lease_seconds, bus=bus,
                               # Where a turn's workspace is decided, once, from
                               # the row: the key, the channel and the sender are
                               # all here and nowhere below (D6).
                               workspaces=workspaces)

    # --- telegram: webhook update dedup (Wave 4a, D9) ----------------------
    # Telegram redelivers an update it did not get a 2xx for within its timeout,
    # and what an accepted update starts is intake (a download, a MinIO put) and
    # voice transcription — so one slow document was two turns and two charges.
    # The mode picks WHERE "already accepted" is written, and only that: in redis
    # mode `--scale agent=N` means the retry may land on a different instance, so
    # the record has to be shared; inline mode is one process and an LRU is the
    # whole of it. Built here rather than in `run()` because the web app is built
    # from this context and the route reads it off the bundle.
    update_dedup = (RedisUpdateDedup(redis_client) if redis_client is not None
                    else MemoryUpdateDedup())
    # --- end telegram webhook dedup ----------------------------------------

    async def toolset_sync() -> bool:
        return await sync_hermes_toolset(executor.tool_fingerprint(), meta, runner)

    ctx = AppContext(
        cfg=cfg, workspace=workspace, engine=engine, meta=meta,
        toolset_sync=toolset_sync,
        sessions=sessions, threads=threads, titles=titles, messages=messages,
        lane_models=lane_models,
        bus=bus, turn_events=turn_events, costs=costs, runner=runner,
        ask_broker=ask_broker, ask_presenter=ask_presenter,
        deliver_document=deliver_document,
        acl=acl, workspaces=workspaces, files=files, scheduler=scheduler,
        jobs=job_store,
        extensions=extensions, learning=learning, memory=memory,
        registry=registry, queue=queue, redis=redis_client, s3=s3,
        secret_box=box,
        events_bus=events_bus, control=control, turns=turns,
        turn_runs=turn_runs, turn_store=turn_store,
        update_dedup=update_dedup,
        content=content.service if content is not None else None,
        content_cards=content.cards if content is not None else None,
        content_decisions=content.decisions if content is not None else None,
    )
    # The task bodies take primitives and read their services from here. Inline
    # mode runs those same bodies in this process, so the agent binds too; the
    # worker's startup hook binds the context it built for itself.
    worker_tasks.bind(ctx)
    return ctx


def _secret_box(cfg) -> SecretBox:
    """The `SecretBox` this process seals extension secrets with.

    `run()` and the worker refuse to start without `CH_SECRETS_KEY`
    (`require_secrets_key` below), so the fallback here is reached only by a test
    or by `build_app()` called directly: one generated key, cached for the life of
    the process, loudly. A deployment that reached it would write rows it cannot
    read after a restart, which is why it is not allowed to.
    """
    if cfg.secrets_key:
        return SecretBox(cfg.secrets_key)
    return secret_key.secret_box()


def require_secrets_key() -> None:
    """Refuse to boot without the key that opens `extension_secrets`.

    Checked before anything is built, and in every process that can write a
    secret: the failure it prevents is silent and permanent — rows sealed with a
    process-ephemeral key are unreadable the moment the process restarts, and the
    only symptom is a connector that "forgot" its credentials.
    """
    try:
        SecretBox.from_env()
    except SecretsKeyError as exc:
        raise SystemExit(str(exc)) from exc


async def shutdown(ctx: AppContext) -> None:
    """Close what `build_app()` opened, in the reverse order it opened it.

    Shared by `run()`'s `finally` and the worker's shutdown hook so the two cannot
    drift; every step is conditional because which clients exist is exactly what
    the two modes disagree about.

    The control subscription and the event bus go FIRST, and before the Redis
    client they read: a reader task still polling a connection that has been
    closed under it logs a connection loss and starts backing off against a dead
    client, which is a confusing way to describe a clean shutdown.
    """
    if ctx.control is not None:
        await ctx.control.aclose()
    if ctx.events_bus is not None:
        await ctx.events_bus.aclose()
    aclose = getattr(ctx.queue, "aclose", None)
    if aclose is not None:
        await aclose()
    if ctx.s3 is not None:
        await ctx.s3.aclose()
    if ctx.redis is not None:
        await ctx.redis.aclose()
    await ctx.engine.dispose()


@dataclass
class Delivery:
    """The Telegram objects a process needs in order to speak into a chat.

    Built by `build_delivery` from a token and nothing else. Both processes that
    deliver — the agent and the worker running a scheduled job — build it the same
    way, which is the point: a report must not look different depending on which
    one produced it.
    """
    bot: Any                  # aiogram Bot — only `TelegramDocuments` needs it
    http: Any                 # httpx.AsyncClient, closed by `aclose`
    api: Any                  # BotApiClient
    sender: Any               # RichSender
    topics: Any               # TopicManager
    documents: Any            # TelegramDocuments

    async def aclose(self) -> None:
        await self.http.aclose()
        # aiogram opens its own aiohttp session, and since Wave 4a this process
        # calls the Bot API through it at boot (`set_webhook` / `delete_webhook`)
        # even when nothing else ever touches `bot`. An unclosed session is an
        # "Unclosed client session" warning at exit and a connector that outlives
        # the loop; closing it is the same courtesy `http` already gets. Best
        # effort: shutdown must not fail on a socket that is already gone.
        session = getattr(self.bot, "session", None)
        if session is not None:
            try:
                await session.close()
            except Exception:                        # pragma: no cover - teardown
                log.warning("closing the aiogram session failed", exc_info=True)


async def build_delivery(ctx: AppContext, token: str) -> Delivery:
    """Build the Telegram half from the token, and bind it into `ctx`.

    The four seams `build_app()` left open — the document sender, the ask
    keyboard, the scheduler's report deliverer and the content plan's approval
    cards — are closed here, so a process that calls this can deliver and one
    that does not is simply headless.

    The cards seam is bound in EVERY process that calls this, not only the
    agent: in redis mode the content tick runs on every worker, and a card is
    sent by whichever tick claims its outbox row. A process that never calls
    this (web-only) leaves a pending card pending, without counting an attempt.

    The token ends up held by `Bot` and `BotApiClient` and is not needed again:
    the caller pops it from `os.environ` immediately after this returns. Nothing
    in here spawns a subprocess, which is what makes that ordering safe.
    """
    import httpx
    from aiogram import Bot

    from .modules.telegram.infrastructure.bot_api import BotApiClient
    from .modules.telegram.infrastructure.cards import TelegramApprovalCards
    from .modules.telegram.infrastructure.documents import TelegramDocuments
    from .modules.telegram.infrastructure.questions import TelegramQuestions
    from .modules.telegram.infrastructure.rich_sender import RichSender
    from .modules.telegram.infrastructure.topics import TopicManager

    http = httpx.AsyncClient(timeout=30.0)
    bot = Bot(token)
    api = BotApiClient(token, http)
    sender = RichSender(api, enabled=ctx.cfg.rich_enabled)
    topics = TopicManager(api)
    documents = TelegramDocuments(bot)

    ctx.deliver_document.target = documents.send_document
    ctx.ask_presenter.target = TelegramAskPresenter(TelegramQuestions(api))
    ctx.scheduler.bind_delivery(deliverer=SenderDeliverer(sender), topics=topics)
    if ctx.content_cards is not None:
        # telegram's card sender IS content's `ApprovalCards` port: media through
        # the Bot (multipart), the button edits through `api`, and a missing
        # "Контент" topic re-created through the TopicManager the routines use.
        ctx.content_cards.target = TelegramApprovalCards(bot, api, topics)
    return Delivery(bot=bot, http=http, api=api, sender=sender, topics=topics,
                    documents=documents)


async def drafts_main(argv: list[str]) -> int:
    """`python -m claude_hermes.main drafts list|show <name>|delete <name>`.

    The operator's window into the draft queue now that it is a table, and the
    exact spelling `scripts/approve_draft.py` wraps in `docker compose exec -T
    agent` — changing it breaks the host-side approval path.

    Deliberately NOT `configure()`d: structlog writes to stdout and `drafts show`
    writes the draft's bytes there for the host script to copy into `catalog/`. A
    log line in the middle of a manifest is a broken manifest.
    """
    ctx = await build_app()
    try:
        return await drafts_cli.run(argv, ctx.extensions.drafts)
    finally:
        await shutdown(ctx)


def build_telegram_gateway(ctx: AppContext, *, bot, username: str, sender, titler,
                           broker, writer, voice=None, send_voice=None, bot_call=None):
    """The aiogram half, assembled from the module's own services.

    Separate from `run()` so the token handling above it stays one readable block:
    everything below this line already has the token in an object and must never
    need `os.environ` again.

    Every port the telegram module declares is satisfied here by the object that
    owns the capability — extensions' secret store, approval service and catalog
    reader, learning's prompt, files' inbox layout, the module's own downloader.
    """
    from .modules.files.infrastructure.layout import lane_dir
    from .modules.learning.domain.prompts import build_learn_prompt
    from .modules.telegram.application.services.commands import CommandService
    from .modules.telegram.application.services.intake import IntakeService
    from .modules.telegram.application.services.model_menu import ModelMenuService
    from .modules.telegram.application.services.routing import MessageRouter
    from .modules.telegram.domain.exceptions import SecretNameError
    from .modules.telegram.infrastructure.attachments import AiogramDownloader
    from .modules.telegram.infrastructure.dispatcher import create_dispatcher
    from .modules.telegram.infrastructure.gateway import TelegramGateway
    from .modules.telegram.infrastructure.model_menu import TelegramModelMenu
    from .modules.telegram.presentation.presenter import Presenter

    commands = CommandService(
        acl=ctx.acl,
        # Two modules own a `SecretNameError` and neither may import the other's.
        secrets=TranslatingSecrets(ctx.extensions.secrets, error=SecretNameError),
        # Straight through since Wave 3a: the approval queue is a table, so the
        # service no longer takes the `home` an adapter used to bind into it.
        approvals=ctx.extensions.approvals,
        catalog=ctx.extensions.catalog,
        runner=ctx.runner,
        writer=writer,
    )
    # NOT `ctx.runner`: a Telegram message starts a turn and waits for it, and the
    # process that runs it may be a worker. `CommandService` above keeps the
    # runner, because `/new` only resets a lane's session and starts nothing.
    turn_runner = QueuedTurnRunner(ctx.turns, ctx.runner, busy=LaneBusy)
    # `/model` and its `mdl:` buttons: ONE menu object, handed to both the router
    # (the command) and the dispatcher (the taps), so the message a tap edits was
    # drawn by the same code that reads it. Without a Bot API call there is no
    # menu, and `/model` answers that switching is not configured here — it is
    # still intercepted, never handed to the CLI.
    model_menu = None
    if bot_call is not None and ctx.lane_models is not None:
        model_menu = TelegramModelMenu(bot_call, ModelMenuService(
            ctx.cfg, acl=ctx.acl,
            lanes=RowedLaneModels(ctx.lane_models, ctx.threads),
            defaults=ctx.acl))
    router = MessageRouter(ctx.cfg, runner=turn_runner, acl=ctx.acl,
                           commands=commands,
                           learn_prompt=LearnPromptBuilder(build_learn_prompt),
                           model_menu=model_menu)
    intake = IntakeService(
        # The lane's own inbox, not one bound home (blocker B5): with the
        # resolver a non-owner's photo lands in THEIR `agent_home/inbox/`, which
        # is the only tree their own turn is allowed to read.
        ctx.cfg, acl=ctx.acl,
        workspace=LaneWorkspace(lane_dir, ctx.workspaces),
        downloader=AiogramDownloader(),
        # With the file service an attachment gets a `files` row and, on the s3
        # backend, a copy in the bucket — the same path an upload from the web
        # takes. `workspace` answers where the lane's inbox is and whose row the
        # attachment becomes.
        files=ctx.files,
    )
    # `presenter_factory` is an argument because presentation and infrastructure
    # are independent siblings in the module's layer contract.
    dp = create_dispatcher(ctx.cfg, bot_username=username, router=router,
                           acl=ctx.acl, sender=sender, titler=titler, intake=intake,
                           bus=ctx.bus, presenter_factory=Presenter, voice=voice,
                           send_voice=send_voice, broker=broker, model_menu=model_menu,
                           # content's `ContentDecisions`: the `cnt:` card buttons,
                           # a reply to a card (a redraft, never a turn) and the
                           # "Контент" topic the titler must not rename. `None` —
                           # the plan off — is a gateway that behaves as before.
                           content_decisions=ctx.content_decisions)
    return TelegramGateway(bot, dp, username=username)


async def run() -> None:
    # Not plain basicConfig: httpx logs every Telegram API URL at INFO and the bot
    # token is a path segment of it, so `docker logs` was a credential leak.
    # `configure` keeps that redaction and adds structlog's key-values on top.
    configure(logging.INFO)
    require_secrets_key()
    ctx = await build_app()
    cfg = ctx.cfg
    if not cfg.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    from .modules.telegram.infrastructure.menu import ensure_menu_button

    # Every object that needs the bot token now holds it, and the worker builds
    # exactly the same set from the same function.
    delivery = await build_delivery(ctx, cfg.telegram_token)
    bot, bot_call = delivery.bot, delivery.api
    sender, topics, documents = delivery.sender, delivery.topics, delivery.documents
    me = await bot.get_me()

    # The token is now held in memory by Bot and by `bot_call`, and nothing spawned from
    # here needs it again. Drop it from the environment so it is NOT inherited by the CLI
    # subprocess — and therefore not by the agent's own Bash, nor by any hook or MCP server
    # an extension brings with it. Anything that can read this variable can impersonate the
    # bot: send messages as it, edit them, delete them. A plugin from the catalog is
    # reviewed code, but it is not OUR code, and it has no business holding the bot's
    # identity.
    #
    # The Login Widget check key is sha256(bot_token), and it must be derived HERE,
    # while the token is still around: the web package never sees the token itself,
    # only this digest, so a bug in a router cannot leak the bot's identity.
    ctx.web_login_key = login_key(cfg.telegram_token)
    ctx.web_webapp_key = webapp_key(cfg.telegram_token)   # the Mini App's initData key
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    # Same reasoning for the OpenAI key that powers voice: it lives in the host
    # process only. Drop it from the environment so the agent's Bash, hooks and any
    # extension MCP server cannot read it and run up a bill on our account.
    voice_key = cfg.openai_api_key
    os.environ.pop("OPENAI_API_KEY", None)
    # Wave 3a adds four more, and the reasoning does not change with the shape of
    # the secret. CH_SECRETS_KEY opens every stored extension credential and the
    # ciphertext is one query away; CH_S3_* is read/write on the bucket holding
    # every file the agent has ever produced; CH_REDIS_URL is the queue, where a
    # forged task is a turn nobody asked for; CH_DATABASE_URL is the whole state.
    # `Config`, the engine, the SecretBox and the S3 client already hold what they
    # need, so from here on the values exist in objects and not in the environment.
    os.environ.pop("CH_SECRETS_KEY", None)
    os.environ.pop("CH_S3_ACCESS_KEY", None)
    os.environ.pop("CH_S3_SECRET_KEY", None)
    os.environ.pop("CH_REDIS_URL", None)
    os.environ.pop("CH_DATABASE_URL", None)
    # …and the same three secrets under the names compose ALSO puts them in the
    # container as. `docker-compose.yml` derives CH_DATABASE_URL from
    # POSTGRES_PASSWORD and CH_S3_SECRET_KEY from MINIO_ROOT_PASSWORD, and
    # `env_file: .env` then injects the originals too — so popping only the
    # derived spelling would leave the identical credential one variable over.
    # CH_WEB_SECRET signs the session cookie: reading it is forging a login.
    # CLAUDE_CODE_OAUTH_TOKEN is deliberately NOT popped — the CLI subprocess is
    # what it is for, and the agent cannot run a turn without it.
    os.environ.pop("POSTGRES_PASSWORD", None)
    os.environ.pop("MINIO_ROOT_PASSWORD", None)
    os.environ.pop("CH_WEB_SECRET", None)
    # Wave 4a: the webhook's shared secret. Anything that can read it can POST a
    # forged update to the public webhook URL and speak to the agent AS a person
    # it trusts — an injection with a return address. `cfg` was loaded long before
    # this pop and the route reads it from there, so nothing needs the variable.
    os.environ.pop("CH_TELEGRAM_WEBHOOK_SECRET", None)

    # The channel bus and this process's control subscription, started AFTER the
    # pops. Neither spawns anything, but "the pops are the first thing that
    # happens once the objects hold their values" is only worth having as an
    # invariant if it is monotone — `tests/test_main.py` asserts the order on the
    # source of this function. `build_app` deliberately dials nothing, which is
    # what keeps it the headless thing every test calls.
    await ctx.events_bus.start()
    await ctx.control.start()

    from .modules.telegram.infrastructure.titler import Titler
    # The lane router and the title writer the rest of the process already uses —
    # one `ThreadTitleService`, so a title reaches the sidebar through the same
    # bus subscription whichever channel produced it. The generator is a stateless
    # model-name holder, so the titler gets one of its own.
    titler = Titler(topics, generator=SdkTitleGenerator(cfg.title_model),
                    sessions=ctx.sessions, titles=ctx.titles)

    # Voice: transcription in, synthesis out. Only wired when a key is present —
    # otherwise `voice` stays None and a voice note gets a polite "not set up" reply.
    voice = None
    voice_http = None
    if voice_key:
        import httpx

        from .integrations.openai.voice import OpenAIVoice
        voice_http = httpx.AsyncClient(timeout=60.0)
        voice = OpenAIVoice(
            voice_http, voice_key,
            stt_model=cfg.stt_model, tts_model=cfg.tts_model,
            tts_voice=cfg.tts_voice, tts_max_chars=cfg.tts_max_chars,
        )
        ctx.voice = voice
        log.info("voice enabled (stt=%s, tts=%s/%s)",
                 cfg.stt_model, cfg.tts_model, cfg.tts_voice)

    ctx.telegram = build_telegram_gateway(
        ctx, bot=bot, username=me.username or "", sender=sender, titler=titler,
        broker=ctx.ask_broker, writer=ctx.extensions.writer, voice=voice,
        send_voice=documents.send_voice, bot_call=bot_call,
    )

    # The clock. In inline mode this process is the only one there is, so it
    # drives both periodic jobs itself — exactly as it did before the queue. In
    # redis mode the `scheduler` container enqueues `scheduler_tick` and
    # `curator_tick` on their cron labels instead, and starting either loop here
    # would mean two clocks: harmless for the tick (the claim decides) and not
    # harmless for the curator, which would run a pass a worker is holding the
    # lock for and simply block.
    # The wiki index is rebuilt from disk once per boot, here and only here —
    # the main process, never a taskiq worker, so N workers do not sweep N
    # times. It runs beside the transports rather than before them: a big wiki
    # must not delay the first reply, and `memory_search` degrades to "no
    # match" (never an error) while rows are still arriving. It also races a
    # turn that deletes a page mid-sweep: the sweep may put that row back, and
    # the next lint or boot removes it again — acceptable for a cache.
    if ctx.toolset_sync is not None:
        await ctx.toolset_sync()

    reindex_task: asyncio.Task | None = None
    if ctx.memory.reindex_all is not None:
        reindex_task = asyncio.create_task(ctx.memory.reindex_all(), name="wiki-reindex")

    curator_task: asyncio.Task | None = None
    cron_task: asyncio.Task | None = None
    if cfg.queue_mode == "inline":
        if cfg.curator_enabled:
            from .workers.curator import curator_loop
            # keep a strong reference: the loop only weakly references tasks
            # The SWEEP (every user's pass, owner first), not one pass: inline
            # mode's clock has to walk the same scopes the worker's tick does.
            curator_task = asyncio.create_task(
                curator_loop(ctx.learning.scopes, cfg.curator_tick_seconds)
            )

        # The reaper rides the cron clock rather than one of its own: a lane whose
        # worker died is unusable until something frees it, and in redis mode the
        # same call hangs off `scheduler_tick`. So does the content plan's tick
        # (queue what is due, wake the publisher): after the reap, and in its own
        # `try` inside the ticker, so a failing plan never stops the cron clock.
        extra: tuple = (ctx.turns.reap,)
        if ctx.content is not None:
            extra += (ctx.content.tick,)
        ticker = CronTicker(ctx.scheduler, cfg.cron_tick_seconds, extra=extra)
        cron_task = asyncio.create_task(ticker.run_forever(), name="cron-ticker")
    else:
        log.info("queue mode: redis — the scheduler process drives the clock")

    # The CladeX web channel: one Starlette app on uvicorn inside THIS event loop,
    # next to aiogram's polling. Off by default — with web_enabled false nothing
    # below is imported and the process behaves exactly as it did before Phase 6.
    web_task: asyncio.Task | None = None
    if cfg.web_enabled:
        if not cfg.web_secret:
            raise SystemExit("web_enabled is true but CH_WEB_SECRET is not set")
        import uvicorn

        from .http.app import build_web_app
        web_app = build_web_app(ctx, bot_username=me.username or "",
                                dist_dir=dist_dir())
        # host 0.0.0.0 is the container's own network only: docker-compose publishes
        # no ports, and the single public surface is the cloudflared sidecar.
        # lifespan="off" because the app owns no startup/shutdown of its own — the
        # database, runner and bus are built and torn down by this function.
        server = uvicorn.Server(uvicorn.Config(web_app, host="0.0.0.0",
                                               port=cfg.web_port, log_level="info",
                                               lifespan="off"))
        web_task = asyncio.create_task(server.serve(), name="web")
        log.info("web channel listening on :%s", cfg.web_port)

        # The same UI as a Telegram Mini App: the bot's menu button opens the public
        # origin inside Telegram, where the page signs itself in with initData. Set
        # here, on every boot, so the button follows web_public_origin instead of a
        # one-off BotFather step nobody remembers to redo on a hostname change.
        if cfg.web_menu_button:
            await ensure_menu_button(bot_call, cfg.web_public_origin)

    try:
        # The transport decision, both halves of it, extracted into
        # `modules/telegram/infrastructure/transport.py` so a test can drive it
        # with a fake bot (`tests/test_main_webhook.py`) instead of booting a
        # process. Webhook mode registers the URL with `set_webhook` and then
        # blocks on `web_task` — the web server IS the main loop; polling mode
        # calls `delete_webhook` and then `run_polling`, in that order, because
        # aiogram 3.29.1's `start_polling` does NOT remove a webhook that is still
        # registered and `getUpdates` behind one answers 409 forever.
        # `tests/test_main.py` anchors on those two literals in this source.
        from .modules.telegram.infrastructure.transport import start_transport
        await start_transport(cfg, bot=bot, gateway=ctx.telegram, web_task=web_task)
    finally:
        if reindex_task is not None:
            reindex_task.cancel()
        if curator_task is not None:
            curator_task.cancel()
        if cron_task is not None:
            cron_task.cancel()
        if web_task is not None:
            web_task.cancel()
        await shutdown(ctx)
        await delivery.aclose()
        if voice_http is not None:
            await voice_http.aclose()


if __name__ == "__main__":
    # One composition root, two entry points. `drafts` is the operator's, and it
    # is dispatched before `run()` so it never builds a Bot or opens a poll.
    if sys.argv[1:2] == ["drafts"]:
        raise SystemExit(asyncio.run(drafts_main(sys.argv[2:])))
    asyncio.run(run())

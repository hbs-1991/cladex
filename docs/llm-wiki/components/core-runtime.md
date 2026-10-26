---
id: component.core-runtime
kind: component
status: current
summary: Process wiring and boot in both queue modes, the core/ kernel (engine, migrations, tasks, redis, eventbus, crypto, logging, exceptions), the turn as a row and a task, which model a turn runs, SDK options, config, paths, DB, sessions, the agent-owned wiki, ACL and bash guard.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:c7e7f1b0ae999f8036e97a4086315be3e26cb7308db0544f5c26bd44c7c901af
evidence:
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/core/meta.py
  - path: src/claude_hermes/core/contracts.py
  - path: src/claude_hermes/core/events.py
  - path: src/claude_hermes/core/snapshots.py
  - path: src/claude_hermes/core/auth.py
  - path: src/claude_hermes/core/di.py
  - path: src/claude_hermes/core/db/engine.py
  - path: src/claude_hermes/core/db/migrate.py
  - path: src/claude_hermes/core/db/registry.py
  - path: src/claude_hermes/core/db/base.py
  - path: src/claude_hermes/core/tasks.py
  - path: src/claude_hermes/core/redis.py
  - path: src/claude_hermes/core/eventbus.py
  - path: src/claude_hermes/core/crypto.py
  - path: src/claude_hermes/integrations/s3/client.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/workers/taskiq_app.py
  - path: src/claude_hermes/workers/curator.py
  - path: src/claude_hermes/modules/learning/application/scopes.py
  - path: src/claude_hermes/modules/learning/wiring.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_slot.py
  - path: src/claude_hermes/modules/sessions/infrastructure/repositories.py
  - path: src/claude_hermes/workers/README.md
  - path: src/claude_hermes/core/logging.py
  - path: src/claude_hermes/core/exceptions.py
  - path: src/claude_hermes/integrations/claude_sdk/options.py
  - path: src/claude_hermes/integrations/claude_sdk/guards.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/application/launcher.py
  - path: src/claude_hermes/modules/turns/application/execution.py
  - path: src/claude_hermes/modules/turns/application/control.py
  - path: src/claude_hermes/modules/turns/infrastructure/turn_repository.py
  - path: src/claude_hermes/modules/turns/infrastructure/snapshots.py
  - path: src/claude_hermes/modules/turns/infrastructure/publishers.py
  - path: src/claude_hermes/modules/turns/infrastructure/dispatch.py
  - path: src/claude_hermes/modules/turns/infrastructure/redis_ask_broker.py
  - path: src/claude_hermes/core/db/migrations/versions/0004_turns.py
  - path: src/claude_hermes/core/db/migrations/versions/0005_users.py
  - path: src/claude_hermes/modules/identity/infrastructure/repositories.py
  - path: src/claude_hermes/modules/turns/domain/policies.py
  - path: src/claude_hermes/modules/files/infrastructure/roots.py
  - path: src/claude_hermes/modules/telegram/infrastructure/transport.py
  - path: src/claude_hermes/modules/telegram/infrastructure/update_dedup.py
  - path: src/claude_hermes/modules/turns/domain/prompt.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/modules/turns/README.md
  - path: src/claude_hermes/modules/sessions/application/services.py
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/modules/memory/infrastructure/turn_hooks.py
  - path: src/claude_hermes/modules/memory/infrastructure/page_index.py
  - path: src/claude_hermes/modules/memory/application/services/index.py
  - path: src/claude_hermes/modules/memory/domain/tables.py
  - path: src/claude_hermes/modules/turns/infrastructure/tool_handlers/memory.py
  - path: src/claude_hermes/core/db/migrations/versions/0007_wiki_pages.py
  - path: src/claude_hermes/modules/identity/application/services.py
  - path: src/claude_hermes/core/db/engine.py
  - path: src/claude_hermes/core/logsafe.py
  - path: src/claude_hermes/core/naming.py
  - path: Dockerfile
  - path: docker-compose.yml
  - path: src/claude_hermes/core/model_catalog.py
  - path: src/claude_hermes/modules/turns/domain/exceptions.py
  - path: scripts/model_matrix.py
  - path: pyproject.toml
relations:
  - component.content
  - flow.message-turn
  - decision.multi-user-isolation
  - contract.state-db
  - decision.postgres-and-modular-core
  - component.gateway
  - component.web
  - decision.modules-and-ports
  - decision.redis-taskiq-scaling
  - decision.minio-blobs
  - decision.wiki-index-not-store
  - decision.prompt-cache-discipline
  - decision.model-choice-is-lane-state
---
# Core Runtime

## main.py — the composition root

`build_app() -> AppContext` (main.py) builds every module service over one engine
and plugs them into each other's ports; nothing is assigned onto a service
afterwards ([[decision.modules-and-ports]]). In order: `Workspace.resolve()` (the
tree is created and seeded) → `load_config` → **`upgrade_head(cfg.database_url)`**
→ `make_engine` + `MetaStore` (no facade since Step 5 — every repository holds
the engine) → **the queue** (`InlineQueue` or `TaskiqQueue`, and the Redis client
when there is one) → **the event bus and the snapshot store** (`InProcessEventBus`
+ `MemorySnapshots`, or `RedisEventBus` + `RedisSnapshots`) → sessions
(`SessionRepository`, `SessionRouter`,
`MessageRepository`, `ThreadRepository`) → turns (`TurnEventRepository`,
`TurnCostRepository`, `TurnRepository`, `TurnBus(turn_events,
ChannelPublisher(events_bus), snapshots)`) → `ThreadService(thread_repo,
turn_store)` / `ThreadTitleService` →
identity (`AccessControl(UserRepository(engine), allowed_users=lambda:
cfg.allowed_users, shell_default=lambda: cfg.users_shell_enabled,
learning_default=lambda: cfg.users_learning_enabled)` — since Step 5 the `users`
table is the only owner record; the `meta.owner_user_id` mirror is gone; since
`0009` the two config keys are read at call time as the per-user grants'
defaults, and `acl` is the one resolver every consumer asks) → **the workspace resolver**
(`WorkspaceResolver(home, workspace_dir_of=acl.workspace_dir_of,
deploy_owner=…)`) → **the blob store**
(`LocalBlobStore`, or `S3BlobStore` over a MinIO bucket `ensure_bucket()` has
created) → files (`FileService`) → memory (`WikiStore` + `wiki.seed()`, the hooks
provider — the lint fork is built per learning scope, `MemoryBundle.lint` went in
Step 5) → scheduler (`SchedulerService` with
`SdkJobExecutor(..., grants=acl)` — a non-owner's routine gets a shell only by
that owner's grant at fire time — a `QueueJobDispatcher` and, since 4b, `deploy_owner=
workspaces.owner_id`, so a job created with no owner belongs to the deploy owner
rather than to nobody) → learning (since 4b one call,
`learning_wiring.build_scopes(cfg, workspaces=, meta=, sessions=session_repo,
activity_for=, wiki_lint_for=, fork=, snapshots=, users=acl.active_user_ids,
learning_of=, review_slot=)` — `learning_of(uid)` is `(await
acl.grants(uid)).learning` since `0009` — → a `LearningScopes` registry that builds a `LearningScope` —
sidecar, library, `SkillAdminService`, nudges, `MetaReviewGate`, `ReviewService`,
curator, one `ScopedMeta` — per user on demand; the owner's scope is built
eagerly to populate `LearningBundle`'s owner-level fields, and
`LearningLoop(scopes, QueueReviewDispatcher(queue))` is the observer;
`review_slot` is a `RedisReviewSlot` on `lock:review` when there is a Redis
client and `None` inline — [[component.learning]]) → extensions (the wiring bundle, over a
`SecretBox` built from the pre-pop environment) → `TurnService` with all of it
(including `grants=acl`, its `LaneGrants` port) →
**the turn objects** (`CancelRegistry`, `TurnControl(events_bus, cancels,
ask_broker)`, `TurnLauncher`, `TurnRunService`) → `workers.tasks.bind(ctx)`.

Six things the two waves added to that list, all of them a *selection* rather than
a branch in the services ([[decision.redis-taskiq-scaling]],
[[decision.minio-blobs]]):

| Selector | inline / default | other |
|---|---|---|
| `cfg.queue_mode` (derived from `CH_REDIS_URL`) | `InlineQueue(registry, {RUN_JOB_TASK: 1, LEARNING_REVIEW_TASK: 1})` — the second limit is 4b's: the review lease is per user now, so this is inline mode's one-review-at-a-time bound | `TaskiqQueue(registry, url=cfg.redis_url)` + `core.redis.create_client`; the review bound is the `lock:review` slot |
| `cfg.blob_backend` | `LocalBlobStore(home)` | `S3BlobStore(S3Client(...), blob_root(home))`, `ensure_bucket()` at boot |
| a Redis client exists | in-memory `PendingStates`, `asyncio.Lock` refreshes | `RedisPendingStates` (10-min TTL), `RedisLocks` on `lock:oauth:<name>` |
| the event bus | `InProcessEventBus()` | `RedisEventBus(redis_client)` |
| the in-flight snapshot | `MemorySnapshots()` | `RedisSnapshots(redis_client, cfg.turn_snapshot_ttl_seconds)` |
| the ask broker | `AskBroker()` | `RedisAskBroker(redis_client, events_bus)` |

The last three are imported at module scope in `main.py`, unlike `TaskiqQueue` and
`S3Client`: `redis` is already a hard import through `core/redis.py`, and the
classes themselves live in `infrastructure/`, so nothing in
`modules.*.application` learns what a client is.

`TaskiqQueue` is handed `cfg.redis_url` at construction rather than reading the
environment lazily, because `run()` pops `CH_REDIS_URL` moments later and a broker
first built on the agent's first enqueue would be built from an environment that
no longer has the URL.

The bus is built here rather than inside the runner because three other consumers
need the *same* instance: the Telegram presenter subscribes to it, the cron ticker
emits delivery turns onto it, and a new title announces itself on it
([[component.web]]).

Two `AppContext` fields carry Wave 4a. **`ctx.workspaces`** is that
`WorkspaceResolver`, and every process that runs a turn (agent, worker, scheduler)
holds the same one, so nothing resolves a path out of `CH_HOME/agent_home`
directly any more — since 4b the learning services go through it too, one
`LearningScope` per workspace (`ctx.learning.scopes`); `ctx.workspace` stays the
**owner's** `Workspace`, which is what `LearningBundle`'s owner-level fields
mean. **`ctx.update_dedup`** is the webhook's `update_id` record
(`RedisUpdateDedup` when there is a client, `MemoryUpdateDedup` otherwise) and is
unused in polling mode and in every non-web process
([[decision.multi-user-isolation]], [[decision.redis-taskiq-scaling]]).

`AppContext` is **headless by construction** — no bot, no token — which is what
makes `build_app()` testable and what lets a web-only deployment exist. The two
late-bound ports say so in their names: `ask_presenter` and `deliver_document`
are `LatePresenter` / `LateDocumentDelivery` until `run()` has a Bot, and
`SchedulerService.bind_delivery(deliverer=, topics=)` takes the same shape. A third
late edge is a cycle rather than a Bot: with `content_enabled` the scheduler's
executor takes the content plan's routine tools and the plan's `Publisher` port is
the scheduler, so `build_app` builds the plan first over an unbound
`SchedulerPublisher` and sets its `.scheduler` right after the `SchedulerService`
exists; nothing calls it before `build_app` returns ([[component.content]]).
That ONE adapter is passed as both the plan's `Publisher` and its `Drafter` port —
every call names its job id, so which routine wakes is the plan's choice. A
fourth seam is a Bot again: `ctx.content_cards` is the plan's late-bound holder
of its `ApprovalCards` port, and `build_delivery` sets its `.target` to
telegram's `TelegramApprovalCards(bot, api, topics)` — in the agent AND in every
worker, because the card outbox is sent by whichever tick claims it; a process
that never calls `build_delivery` leaves a pending card pending.
**`ctx.content`** is the plan's `ContentService`, or `None` with the flag off —
which is the whole of "disabled": no routes, no `content_plan`, no tick.
`ctx.content_decisions` (the plan's `ContentCardDecisions`, telegram's
`ContentDecisions` port) goes to `create_dispatcher(content_decisions=)`, and is
`None` with the flag off too, which is a gateway that behaves as before.

**Migrations run before the pool opens**, and a missing `CH_DATABASE_URL` is a
`SystemExit` before any of that is built — a process that opened
connections against an un-upgraded database would fail later, on a query, in the
middle of somebody's turn. `make_engine` deliberately does *not* migrate;
boot, the test fixtures (`tests/conftest.py`'s `engine` fixture is a pool over
the per-process database `test_database` already migrated) and the import
script each say when that happens
([[contract.state-db]]).

`run()` (main.py) calls `core.logging.configure(logging.INFO)` (see "core/"
below), then `build_app()`, then `build_delivery(ctx, token)` — the shared builder
of the aiogram `Bot`, `BotApiClient`, `RichSender`, `Topics` and
`TelegramDocuments`, extracted so the worker process builds the *same* objects and
a scheduled report cannot look different depending on which process produced it.
It derives the Login-Widget key `sha256(bot_token)` and the Mini App key
`HMAC-SHA256("WebAppData", bot_token)` and then **pops ten names from
`os.environ`** (main.py) so the CLI subprocess, agent Bash, hooks and extension MCP
servers can never inherit them:

`TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `CH_SECRETS_KEY`, `CH_S3_ACCESS_KEY`,
`CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL`, `POSTGRES_PASSWORD`,
`MINIO_ROOT_PASSWORD`, `CH_WEB_SECRET`.

The keys must be derived above that line, and they are the only token-derived
values the web layer ever sees. The last three are popped because
`docker-compose.yml` derives `CH_DATABASE_URL` from `POSTGRES_PASSWORD` and
`CH_S3_SECRET_KEY` from `MINIO_ROOT_PASSWORD` while `env_file: .env` injects the
originals too — popping only the derived spelling would leave the identical
credential one variable over. `CLAUDE_CODE_OAUTH_TOKEN` is deliberately **not**
popped: the CLI subprocess is what it is for.
`build_telegram_gateway(ctx)` then builds the rest of the Telegram half (`Titler`,
`TelegramQuestions`, the dispatcher and `TelegramGateway`) and binds the ports
that needed a Bot; `tests/test_main.py` asserts every pop happens *before* that
call, in `run()` and in the worker startup alike. Immediately after the pops both
entry points `await ctx.events_bus.start()` and then `await ctx.control.start()` —
the channel bus's reader task and this process's subscription to `hermes:ctl`.
Nothing either one does spawns anything, but "the pops are the first thing after
the objects hold their values" is an invariant that is only worth having if it is
monotone, and `tests/test_main.py` asserts the order on the source of both.
After that: **in inline mode
only**, the curator loop (`curator_loop(ctx.learning.scopes,
cfg.curator_tick_seconds)` — since 4b it walks the same scopes the worker's tick
does, one pass per user) and `CronTicker(ctx.scheduler, cfg.cron_tick_seconds,
extra=extra)` with `extra = (ctx.turns.reap,)`, plus `ctx.content.tick` when the
content plan exists — the ticker awaits each `extra` after `service.tick`, each in
its own `try/except`, which is how inline mode reaches the same reap and content
tick redis mode gets from `scheduler_tick` without a second clock. In redis mode the
`scheduler` container drives both, and starting either loop here
would mean two clocks. Then the web server when `cfg.web_enabled` (uvicorn as an
asyncio task; `SystemExit` if `CH_WEB_SECRET` is unset), and finally
`run_polling`. `dist_dir()` (main.py) resolves the built SPA: `CH_WEB_DIST`, else
`<package>/web_dist`, else None.

`main.py` also carries the operator's `drafts list|show <name>|delete <name>`
subcommands (over `modules/extensions/presentation/cli.py`) — the draft queue is a
table now, so the host's `scripts/approve_draft.py` reads it through the app
([[component.extensions]]). They are the one place in `src/` besides `scripts/`
where `print()` is allowed.

## `workers/` — the process beside the agent

`workers/tasks.py` holds one `TaskRegistry` and five task bodies — `scheduler_tick`,
`run_job(job_id, fire_at)`, **`run_turn(turn_id)`**,
`learning_review(session_key, kinds, owner_id=0)`, `curator_tick` —
each a thin call into a service, with **primitive arguments only**. Since 4b
`learning_review` resolves `ctx.learning.scopes.for_user(owner_id)` and runs
*that* scope's `review.run(session_key, kinds)` — the review runs in the tree of
whoever's turn nudged it; `owner_id=0` (a payload queued by a 4a agent) is the
deploy owner's. Since `0009` it first asks `scopes.observes(owner_id)`: a review
queued before the owner withdrew that user's learning grant releases the scope's
review gate, logs and returns without a fork (a lookup that raises releases the
gate and propagates). `curator_tick` runs the per-user **sweep**, `run_all(scopes,
lock_for)`, inside an outer `Lock` on `lock:curator:sweep` (TTL
`curator_max_turns * 60 s * N users`, so two ticks never run two sweeps at once),
with `lock_for(uid)` a `Lock` on `lock:curator` for the owner and
`lock:curator:<id>` for anyone else around each pass ([[component.learning]]).
`run_turn` is
Wave 3b's: the turn id is the whole argument list, because everything else about
the turn is on the `turns` row where another process can read it, including the
claim, the lease and the cancel flag ([[contract.state-db]]). `scheduler_tick`
gained a second line on the same `now` — `await ctx.turns.reap(now)` — so a lane
whose worker died is freed by the clock that already exists rather than by a
second thing to get wrong about time, and a third since the content plan:
`ctx.content.tick(now)` when `ctx.content` is set, in its own `try` that logs, so a
failing plan never fails a tick that already claimed and reaped. `taskiq_app.SCHEDULES` is unchanged: a turn
is enqueued by a turn, never by the clock. Services come
from a module-level `AppContext` each process binds once (`bind(ctx)`, called at
the end of `build_app()` and again by the worker's startup hook); a body that runs
before either raises `TasksNotBound` rather than an `AttributeError` on `None`.

`workers/taskiq_app.py` is the only file in the repository that imports taskiq. It
imports it inside functions and produces its module-level `broker`/`scheduler`
through `__getattr__`, so importing it costs nothing and fails nowhere with taskiq
absent. It was written offline and verified afterwards against a live worker;
the one defect that smoke found (redis-py 8's default 5-second read timeout
killing the blocking BRPOP) is why `_make_broker` passes `socket_timeout=None`
(`src/claude_hermes/workers/README.md`, [[decision.redis-taskiq-scaling]]).

`workers/curator.py` holds `run_once(service, lock=None)` (one scope's pass,
under its lock in redis mode), `run_all(scopes, lock_for=None)` (4b: the sweep —
the unsuffixed `curator_paused` read first through the owner scope, then every
id from `scopes.user_ids()` — the owner first, then each user whose learning
grant is on — sequentially) and `curator_loop(scopes,
tick_seconds)` (inline mode's cadence over the sweep).

## Turn execution (`modules/turns/`)

Since Wave 3b a turn is **a row and a task**, and `TurnService` is only the part
that means "run it here, now". Three objects sit above it, all built in the
composition root and all present in both modes and both processes:

- **`TurnLauncher`** (application/launcher.py) — `start` inserts the `turns` row
  and enqueues `run_turn(turn_id)`; the insert's unique partial index is the lane
  lock ([[contract.state-db]]). `is_busy` reads the row. `cancel` is **flag first,
  nudge second**: one `request_cancel` UPDATE (which also turns a `queued` row
  `cancelled` outright, so it never runs), then a message on `hermes:ctl`.
  `wait(turn_id, lane)` is how the Telegram side gets its reply back — subscribe,
  `await ready`, read the row, and on `turn.end` re-read it at 50 ms intervals for
  up to 5 s (the event is emitted *inside* the turn body, before the execution
  service writes the terminal row) with a 15 s poll as the lost-message fallback.
  `reap(now)` marks every expired active row `lost`, emits the frozen synthetic
  `turn.end {"status": "error", "error": "worker lost: lease expired", …}` on the
  lane, and prunes rows older than `turn_retention_days`.
- **`TurnRunService`** (application/execution.py) — the side that claims the row
  (`queued` only), re-resolves the attachment ids against the row's own lane,
  registers the task, heartbeats the lease every `lease/3` seconds and writes the
  terminal row under `asyncio.shield` with its own `worker_id` on it. The
  heartbeat retries on error and, if this process's clock passes `lease_until`
  without a successful extension, cancels its own turn — the worker kills itself
  before the reaper has to be wrong about it. `worker_id` is
  `f"{hostname}:{pid}"`.
- **`TurnControl` + `CancelRegistry`** (application/control.py) — the subscription
  to `hermes:ctl` every process starts at boot, dispatching
  `{"op": "cancel", "turn_id"}` to the registry of tasks running *here* and
  `{"op": "ask", "qid", "answer"}` to the local ask broker. Both are no-ops for
  ids this process never heard of. `CancelRegistry.cancel` is **idempotent**: the
  heartbeat keeps seeing `cancel_requested_at`, and a second `task.cancel()`
  twenty seconds later would land on the terminal write's `await` and abort it.

`TurnService.run_turn` gained `turn_id=` (generated when omitted) and a `commands_store=` constructor keyword: after `_execute`,
a slash-command list that differs from the in-process copy is written to the meta
key `sdk_slash_commands`, because the worker that ran the last turn is not the
process serving `GET /api/commands`. Its per-lane `asyncio.Lock` stays — it costs
nothing and still serialises direct `run_turn` callers — but it is no longer what
anything means by "busy".

`TurnService` (modules/turns/application/turn_service.py; the `AgentRunner`
name went with the shims in Step 5) takes its collaborators as keyword arguments — a
`WikiStore`, a `TurnBus` (modules/turns/application/bus.py) and every port from
`application/ports.py` — and keeps a
per-session `_index_seen: dict[str, float]` — the index mtime each session last
saw, read by `index_refresh_factory` (see below), and since Wave 3b a **cache of
`sessions.index_mtime`** rather than the truth: it is refreshed with
`max(local, row)` on every turn and written back through `bind`/`touch`, so a lane
that lands on a worker which has never seen it does not re-inject the whole wiki
index ([[contract.state-db]]) — and `slash_commands`, the
names the CLI listed in its `init` `SystemMessage` on the last turn, which
`SlashCommandsService` merges into the composer's `/` menu ([[component.web]]). `TurnService.run_turn()`
(modules/turns/application/turn_service.py) serializes turns per session key via
`defaultdict(asyncio.Lock)`, and records the running task in `_tasks` so
`cancel(session_key)` (modules/turns/application/turn_service.py) can stop it; `is_busy` (modules/turns/application/turn_service.py) is
that lock. `_run_turn_locked()` (modules/turns/application/turn_service.py) owns the turn's *lifecycle*: it
mints a `turn_id`, builds the `TurnContext` with an `emit` closure bound to it,
emits `turn.start` and `user.message`, upserts the `threads` row, and wraps the
body so every exit emits exactly one `turn.end` — `ok`, `error` (then
re-raise), or `cancelled` on `asyncio.CancelledError`. `_run_turn_body()`
(modules/turns/application/turn_service.py) is the old body: since `0009` it first resolves the lane
owner's grants (`_with_grants` — a non-owner lane asks the `LaneGrants` port once,
inside the envelope, and a failure ends the turn in `turn.end {status: "error"}`;
an owner lane never asks) onto `ctx.shell_enabled`/`learning_enabled`; it routes
the session (resume vs fresh) and, when the row's `shell_enabled` stamp — or, for
a NULL stamp, `is_owner_lane or grant_defaults().shell` — differs from
`ctx.has_shell`, resets it through the store and says so with
`SHELL_CHANGED_NOTICE` ([[flow.message-turn]]); injects the cron→topic
context bridge when the thread is a scheduled job's delivery topic
(`_with_cron_context`, modules/turns/application/turn_service.py — see [[component.cron]]; a failure inside
it logs and the turn runs without context), builds the frozen
`system_append` (`build_system_append(self._wiki.index(), now.date())` on
first turn only, which also stamps `_index_seen[session_key]` to the index
mtime at that moment), calls `_execute`, retries once from scratch on
`ProcessError` with a stale resume id, records user/assistant messages into
the `messages` corpus, records cost, calls `learning.after_turn` (on an owner
lane, or with the lane owner's learning grant), and honors
`ctx.flags.reset_lane` (extension toolset changed → reset after the row
write).

`reset_all()` is the bulk form — `db.reset_all_sessions()`, one
`DELETE FROM sessions` — used by [[component.web]]'s extensions mutations, which
have no lane of their own to reset and change something (the toolset, the plugin
list) that every lane's cached prefix carries. Since 4b the skills mutations use
`reset_owned(owner_id, *, include_unowned=False)` instead — `DELETE FROM sessions
WHERE owner_id = :o`, plus the `owner_id IS NULL` rows only when the caller is
the deploy owner (a lane bound while the deploy owner was `0` wrote NULL) —
because a user's skill index reaches nobody else's prefix and evicting every
other user's warm cache for one edit would be an NFR-1 regression. Neither takes
a lane lock on purpose: a turn in flight writes its session row back when it ends
and so keeps its session, and the reset is for idle lanes — which is every lane in
practice when someone is clicking in a browser. The ordering rule is the same one
`reset_lane` obeys: after the row write, never before.

`_execute()` (modules/turns/application/turn_service.py) assembles the SDK call: hermes in-process MCP
server (`build_hermes_server`, now also threading `ask_user=self._ask_user`),
composed extensions (`compose.for_scope`), a fresh `WikiTurnState()`
(modules/extensions/application/services/compose_service.py) shared by that turn's wiki hooks, `base_options(...)` with the
`claude_code` preset system prompt + append, `allowed_tools =
builtin_tools(is_owner_lane=, shell_enabled=)` (all of `_BUILTIN_TOOLS` on an
owner lane or with a shell grant) `+ hermes tools + extension tools`, four hook events (modules/turns/application/turn_service.py — see
"hooks.py" below), `resume`, and `include_partial_messages=True` (the SDK's token deltas). It
iterates `query()` and turns each message into events on the bus via `ctx.emit`
— there is **no `progress` callback any more**, in the runner or the gateway:

- `StreamEvent` text deltas (skipping subagent tokens, `parent_tool_use_id`
  set) → `text.delta`;
- `TextBlock`s accumulate the reply and emit `text.block`; `ThinkingBlock` →
  `thinking`; `ToolUseBlock`s feed `observe_block`, stamp a monotonic start time
  and emit `tool.call`;
- `ToolResultBlock`s in a `UserMessage` → `tool.result` with `duration_ms` and
  output clipped to `web_tool_output_max_chars` (`_clip`/`_result_text`,
  modules/turns/infrastructure/sdk_runner.py);
- the duck-typed `ResultMessage` supplies `sdk_session_id`, optional final text
  and `total_cost_usd`.

The event contract itself lives in [[component.web]]; both the browser SSE
stream and the Telegram presenter read it.

`run_turn`, `_run_turn_locked` and `_execute` each take an optional
`attachments=None` (the gateway's `Attachment` list, see
[[component.gateway]]). Two module-level helpers turn it into a prompt:

- `_compose_text` (modules/turns/domain/value_objects.py) appends one `[attachment: <name> — saved at
  <path>]` line per file. It feeds **both** the model's text block and the
  `record_message` FTS row from one function on purpose — built separately,
  the searchable history and what the model actually saw would drift, and the
  drift would stay invisible until someone searched for a file the agent had
  already read.
- `_build_prompt` (modules/turns/infrastructure/sdk_runner.py) returns a **bare `str`** when nothing is
  attached, which is the shape every call site has always used — so text,
  voice, `/learn` and cron turns are byte-identical to before and no cached
  prefix moves (see [[decision.prompt-cache-discipline]]). With attachments it
  returns a fresh async iterator yielding one stream-json `user` message:
  base64 `image` blocks for images first, then a text block naming every file
  by path. It is called **inside** `_execute`, not above it — `_execute` runs a
  second time on the resume-retry path and an async generator is single-use, so
  a hoisted prompt would hand the retry an already-drained iterator.

`_BUILTIN_TOOLS` (modules/turns/domain/policies.py): Bash, Read, Write, Edit, Glob, Grep,
WebSearch, WebFetch, TodoWrite, Agent, Task.

`runner._deliver_document` and `runner._file_store` are `None` at construction
and set by `main.py`; the pair is what gates the `send_document` tool at
registration (a chat context plus SOMEWHERE to deliver: the Telegram callable,
or — on a web lane, where `ctx.chat_id` is 0 — the file store, which publishes
into `outbox/<lane>/` and emits the `file` event). Both are passed through
`build_hermes_server` in `_execute`; a runner that keeps a store it never hands
over leaves the tool unregistered on web lanes. `runner._ask_user` is set by `main.py` the same way and gates
the `ask_user` tool: absent, the tool is not registered at all, and even when
present the gate also requires `chat_type_from_key(ctx.session_key) ==
"private"` (`core/session_keys.py`, symmetric with `chat_id_from_key`) — which is what
keeps every background fork and every non-private lane unable to block on a
human. See [[component.gateway]] for `AskBroker`, the registry that owns the
race between a Telegram tap and this tool's wait.

## Which model a turn runs (`core/model_catalog.py`, `modules/sessions`, `modules/turns`)

Decided once per turn, before `turn.start`, by one rule and one gatherer
([[decision.model-choice-is-lane-state]]).

- **The catalog** — `core/model_catalog.py`, standard library only because every
  module imports it. `ModelInfo` holds what a model IS: `id` (the API's, what is
  stored and sent everywhere), `cli_id` (the only spelling the CLI is handed;
  `cli_id()` is the one way a model reaches `base_options`), `label`, `window`
  (0 = unknown, never blocks a choice), `price_in`/`price_out` per 1M,
  `cache_write`, `efforts`, `default_effort`, `billing`, `aliases`. `lookup()`
  accepts any spelling (id, `cli_id`, alias, `[1m]`) and every comparison of two
  model ids goes through it; an unknown id resolves to an entry that knows
  nothing. `offered(models, deploy_model, fork_model)` is the configured offer
  with the deploy default always in it, warning when an offered window outgrows
  the review fork's model. Every fact was read from the pinned CLI's embedded
  catalog and proved live by `scripts/model_matrix.py`.
- **The rule** — `resolve(thread, session, owner_default, *, shared, deploy_model,
  offered_ids)`: the lane's explicit pair, then what its live session runs, then
  the owner's default (never on a `lane_is_shared` lane — groups, forum topics,
  `cron:`), then the deploy default with no effort. A pair whose model left the
  offer is skipped; an effort the model does not take becomes `None` ("send no
  flag"). `validate()` refuses a write the rule would ignore
  (`ValueError("bad_model" | "bad_effort")`); `is_model_command()` is the one
  `^\s*/(model|effort)(\s|@|$)` guard every path uses.
- **The gatherer** — sessions' `SessionService.effective(session_key, owner_id)`
  collects those inputs (the session counts as live only by `SessionRouter`'s own
  expiry rule, applied without deleting the row; a live session with a NULL pair
  began before `0008` and counts as the deploy default) and never raises for a
  lane with no thread row, because the turn asks it before `threads.touch`
  creates one. `set_model` writes a lane's explicit pair, emits `thread.model`,
  and on a clear redirects the live session to the lane's default
  (`SessionRouter.set_session_pair`, no `updated_at` bump). `main.build_app`
  builds ONE instance and hands it to the turn (`TurnService(models=…)`), the web
  bundle (`WebDeps.lane_models`) and the Telegram menu, and calls
  `offered_models()` once at boot so the window warning is a boot-time fact.
- **The turn** — `TurnService._effective` asks the `LaneModels` port (a failure
  logs and runs the deploy default), puts `model`/`cli_model`/`effort` on
  `TurnContext`, and `turn.start` names the requested pair. `SdkRunner.execute`
  passes `model=ctx.cli_model` and `effort` only when set, and returns the model
  that ANSWERED (`lookup(AssistantMessage.model).id`, never `<synthetic>`, never
  the CLI's Haiku side call). `_ModelRun` carries `model`, `effort` and
  `fallback_from` onto every `turn.end` (`LOST_END_PAYLOAD` and the launcher's
  synthetic events carry the same three keys as `null`), and `bind`/`touch` write
  the answered pair to `sessions.model/effort`.
- **Failures** — `ModelRejected(requested, error_text)` (turns
  `domain/exceptions.py`) carries its facts on the exception, not on the shared
  executor, and is raised only for a failure before any `tool.call` whose
  `AssistantMessage.error` is `model_not_found` or whose `ResultError` is
  `terminal_reason == "api_error"` with status 400/404. On a non-default model the
  turn runs once more on the deploy default with the same resume id and announces
  it; the deploy default rejected, or a second rejection, fails without a reset.
  A model that answered differently from the one requested without any failure is
  announced the same way. `CliCallFailed` carries an errored message's text to
  `turn.end.error`; `is_cli_death` walks `__cause__`, so the wrap does not change
  which failures take the stale-resume reset. At most two executions per turn.
- **The guard** — a turn text that `is_model_command()` matches short-circuits in
  `_run_turn_body` with a canned `text.block` and no SDK call; its `turn.end` names
  no model. The CLI would otherwise run it as its own local command and switch the
  session's model with nothing recorded.

## SDK options and the prompt prefix (`integrations/claude_sdk/options.py`, `modules/turns/domain/prompt.py`)

`base_options(home, **overrides)` (integrations/claude_sdk/options.py) owns exactly the three
invariants shared by agentic call sites: `cwd=home/agent_home`,
`setting_sources=["project"]`, `permission_mode="bypassPermissions"`. The
titler deliberately bypasses it (see [[component.gateway]]).
`workspace_env(ws)` is the env every `base_options` call site passes:
`CLAUDE_CONFIG_DIR`, `TMPDIR` and `CLAUDE_CODE_NO_MODEL_FALLBACK=1` — the last
turns off the CLI's own model substitution for the turn, cron, every fork and the
prober in one line; the titler sets it in its own env.
`build_system_append(wiki_index, today)` (modules/turns/domain/prompt.py) produces the
per-session dynamic append: `TURN_RULES` + `WIKI_RULES` + (if non-empty) the
wiki index snapshot under a `## Wiki index (snapshot at session start)`
heading + date-only timestamp.

`TURN_RULES` (modules/turns/domain/prompt.py) states the harness's turn model, which the model
cannot otherwise infer: one inbound message is one turn, the turn ends when
output stops, and **nothing resumes it** — there is no background-task
notification, no scheduler wake-up, no polling loop anywhere in this codebase.
It therefore forbids ending a turn on "I'll wait for this to finish" and
forbids `run_in_background` for work whose result is needed, and states that a
pause is never the user withholding permission (every call site runs
`permission_mode="bypassPermissions"`; the two guards that can refuse — the
bash guard and the review write guard — *deny* with an explicit reason and
never ask). See [[quality.failure-modes]] for the live incident that put this
in the prompt.

`WIKI_RULES` (modules/turns/domain/prompt.py) — the wiki's operating rules (namespaces, the
frontmatter shape, `raw/` immutability, "re-read the index before concluding
something is missing", "update index.md and log.md in the same turn a page
changes") — is a **code-owned constant**, not a file under `bundle/` the way
the persona is. This is deliberate, per the comment at modules/turns/domain/prompt.py:
`CLAUDE.md` is seeded only when it is *absent* (`_copy_missing`; it is
deliberately outside 4b's `SeedManifest`, see the workspace section below), so
an edit to `bundle/CLAUDE.md` never reaches an `agent_home` that
already exists — a live deployment would then run the new wiki system with
none of its rules. Keeping the rules in code means every session gets them
regardless of when its volume was first seeded, and since the string is
static the prefix stays byte-stable and the provider cache stays warm (unlike
the wiki index, which does change and is appended after it). The managed
persona still lives in `agent_home/.claude/persona.md` — rewritten from
`bundle/persona.md` on every boot (`write_persona`) — and reaches the model
via the `@.claude/persona.md` import in the once-seeded `agent_home/CLAUDE.md`
stub, which is the agent's own editable file; that mechanism is unchanged.

## Config (`core/config.py`)

`Config` (core/config.py) holds every behavioral knob (model default
`claude-sonnet-5`; `models`, the list a lane may be switched to — only the offer,
`load_config` turning a bare string into a one-item list; `max_turns=50`, voice/learning/curator/cron/extensions
settings). The old char-capped-memory knobs (`memory_max_chars`,
`user_max_chars`, `memory_nudge_interval`) are gone; the wiki's knobs are
`wiki_index_max_chars: int = 8000` (core/config.py — above this the index
injected into the system append is truncated, loudly, by `WikiStore.index()`),
`wiki_nudge_interval: int = 4` (core/config.py — turns without a wiki write
before the ingest nudge fires), and `wiki_lint_enabled: bool = True` /
`wiki_lint_dry_run: bool = False` (core/config.py — the wiki half of the
curator pass; dry-run reports only because the raw-guard hook denies every
write in that mode), and `memory_search_enabled: bool = True` (core/config.py —
whether the `memory_search` tool exists on private and web lanes; flipping it is
a toolset change and costs every lane one session reset at the next boot).
The content plan added `timezone: str = "UTC"` (an IANA name, checked with
`zoneinfo` in `load_config` — an unknown zone raises `ValueError` naming it, so
the boot stops rather than reading the owner's times in the wrong zone; cron
expressions stay UTC), `content_enabled: bool = False` (the same kind of toolset
flip as `memory_search_enabled`) and `content_channels` (six names by default,
normalised like `models` and lower-cased); its v2 added
`content_draft_lead_hours: float = 24.0` (how long before `publish_at` the
drafter routine is woken) and passes `web_public_origin` as the approval card's
calendar link ([[component.content]]).
`ask_user_timeout_seconds: float = 120.0` /
`ask_user_max_per_turn: int = 2` (core/config.py) bound how long a turn may
park on a human tap and how many taps one turn may ask for (see
[[quality.invariants]] #17). Wave 3a added `redis_url` (`CH_REDIS_URL`),
`blob_backend` (`local`|`s3`), `s3_endpoint` / `s3_bucket` / `s3_region`,
`s3_access_key` / `s3_secret_key` (`CH_S3_*`, env only), `secrets_key`
(`CH_SECRETS_KEY`, env only) and `review_timeout_seconds` (900.0). Wave 3b added
the four turn knobs, all config.yaml-settable: `turn_lease_seconds: float = 60.0`
(the dead-man's switch on a *running* turn — the worker heartbeats every third of
it), `turn_queue_timeout_seconds: float = 300.0` (how long a `queued` turn may
hold a lane before the reaper frees it, and the ceiling on the Telegram adapter's
busy-lane retry), `turn_snapshot_ttl_seconds: int = 3600` (the Redis in-flight
snapshot's only garbage collector) and `turn_retention_days: int = 30` (the
`turns` row holds a third copy of the text and the reply, so it is pruned).
**`queue_mode`
is a property, not a key**: it is `"redis"` when `redis_url` is set and `"inline"`
otherwise, and `load_config` refuses a `queue_mode:` in `config.yaml` outright —
two switches that can disagree are a deployment that enqueues into a Redis nothing
reads. The `web_*` block (`web_enabled: False`, `web_port: 8080`,
`web_public_origin`, `web_upload_max_mb: 20`,
`web_tool_output_max_chars: 8000`, `web_session_days: 30`, `web_secret`) turns
the browser channel on and shapes it ([[component.web]]). `load_config(home)`
reads `config.yaml` and refuses to accept
`telegram_token`/`openai_api_key`/`web_secret`/`database_url` from it — secrets
come only from env (PRD P7); `web_secret` comes from `CH_WEB_SECRET` and
`database_url` from `CH_DATABASE_URL` (a connection URL with a password in it is
a secret, which is why the store is configured by env and not by the YAML). `NO_ADVISOR_ENV` (core/config.py)
disables the advisor tool for background forks.

## Workspace layout (`core/workspace.py`)

`home_dir()` resolves `CH_HOME` (default `~/.claude-hermes`, `/data` in the
container). Since Wave 4a the layout is **per user**:
`Workspace(home, root)` — `home` is `CH_HOME` and `root` is that user's tree.
`Workspace.owner(home)` is `Workspace(home, home)`, `Workspace.for_user(home,
workspace_dir)` is `home / workspace_dir`, and the owner's `workspace_dir` is the
empty string, so for the deploy owner `root is home` and every path below is
byte-identical to what it was ([[decision.multi-user-isolation]]).

* **off `root` (per user)**: `agent_home`, `claude_dir`, `skills_dir`,
  `agents_dir`, `wiki_root`, `inbox_dir`, `outbox_dir`, `logs_dir`, `tmp_dir`
  (`TMPDIR`) and `cli_config_dir` (`CLAUDE_CONFIG_DIR` — what `resume` reads).
* **off `home` (deploy-wide)**: `user_catalog`, `plugins_dir`
  (`CH_HOME/plugins`, moved out of `agent_home` in 4a, with the one-time
  `os.replace` inside `ensure()`), `secrets_path`, `drafts_dir`,
  `pending_approvals_dir`, and `shared_wiki_root`
  (`CH_HOME/agent_home/wiki/shared` — the one path outside their own tree a
  non-owner may read). Only the owner's `ensure()` creates those, and it seeds
  `shared/index.md` with a page saying who the tree is for.
  `is_owner` (`root == home`) is the predicate every caller asks instead of
  re-deriving that from a workspace_dir.

`Workspace.ensure()` creates and seeds one tree and returns `self`; `get_home()`
is now one line over `Workspace.owner(home_dir()).ensure()`. It seeds
`agent_home/` from `CH_ASSETS` (`bundle/`, `skills/`, `agents/`). Since 4b
`settings.json`, the bundled skills and the agents go through one
**`SeedManifest`** per tree (`agent_home/.claude/.seeded.json`; keys relative to
`agent_home`, values the sha256 at seed time), opened once in `ensure()` and
handed to `seed_settings`, `seed_skills` and `seed_agents` (`manifest=None`
keeps the old copy-if-absent `_copy_missing` for a caller without a manifest). Per file,
`_seed_file` applies one table: absent + no record → copy and record; absent +
recorded → **leave absent** (the user deleted it); present + no record → legacy,
record it iff its bytes equal the bundle's (pristine), refresh and record it
iff its CRLF-normalised sha256 is in `_PREVIOUSLY_SHIPPED_SEEDS` (a pristine
copy of an older bundle — the `_SHIPPED_PERSONA_SHA256` idea, frozen: a version
is added when it is replaced and never removed), else leave it and say so once
per tree at INFO; present +
record == disk != bundle → **refresh** atomically and record; present + record !=
disk → user-modified, leave it, log once per tree. Skills are seeded file by
file, so a bundled skill the user patched keeps their patch while its untouched
siblings refresh, and a name that sits under `skills/.archive/` is skipped
entirely — before 4b every boot resurrected an archived bundled skill. Only names
in the bundle are ever visited. `CLAUDE.md` is deliberately not in the manifest
(`seed_claude_md` and `_SHIPPED_PERSONA_SHA256` are a finished migration and
`persona.md` is the delivery channel); an unparseable manifest reads as empty and
never raises (the worst case is one extra refresh next boot); `save()` writes
only when something was recorded, re-reads and merges immediately before its
`os.replace` (disk wins for paths this process did not touch) with a pid-unique
temp name, so the three processes that `ensure()` one tree at boot cannot
permanently misclassify each other's records. An existing volume reads as
"legacy" until its first 4b boot records the pristine files, so the first refresh
lands on the *second* boot after a bundle change.
The managed persona is the older self-healing path: `write_persona` rewrites
`agent_home/.claude/persona.md` from `bundle/persona.md` on every boot, and
`seed_claude_md` migrates legacy volumes — a CLAUDE.md byte-matching any
historically shipped persona (`_SHIPPED_PERSONA_SHA256`, CRLF-normalized) is
swapped for the import stub; unknown content keeps everything, gains the
line-anchored `@.claude/persona.md` import, and warns loudly.
`user_catalog_dir(home)` → `<home>/user-catalog`.

**`WorkspaceResolver(home, *, workspace_dir_of, deploy_owner)`** is the port every
module receives. Core may not import a module, so both lookups arrive as async
callables that `main.build_app` binds to the ACL — this file knows the layout and
the rule, and nothing about who a user is. Two resolutions, and the difference
matters:

- `for_user(user_id)` asks the table, then `ensure()`s the tree once per user per
  process (cached only *after* a successful seed). It propagates `UnknownUser`
  rather than falling back to the owner's tree, because that fallback would be
  exactly the silent cross-user leak the wave exists to prevent. `mirror_root(
  owner_id)` is `for_user(owner_id).agent_home` — what the blob stores ask with
  the **row's** owner.
- `for_lane(session_key, channel, user_id)` **reads no table**: it applies
  `core/session_keys.py::lane_owner` given the cached deploy owner. `lane_owner`
  is the one rule (`web:` → the id in the key, `tg:private` → the chat id,
  `tg:group|supergroup` → the deploy owner, `cron:` → the job's owner), and
  `sessions.owner_id`, `threads.owner_user_id` and the `0005` backfill are all
  written from it, so a row and the running turn cannot disagree about a lane
  ([[contract.state-db]]). Since Step 5 it is the only such rule — `thread_owner`,
  the older one that answered `None` for a group lane, went with the shims.

`owner_id()` is cached only once it is a real id: on a deploy nobody has claimed
the answer is `0`, and caching that would leave the process resolving group and
cron lanes to nobody for the rest of its life.

## core/ — the shared kernel

`core/` is what the rest of the app is allowed to depend on and what depends on
nothing above it; import-linter enforces that in the gate
([[decision.postgres-and-modular-core]]).

- **`core/db/engine.py`** — `make_engine(url)` (with `pool_pre_ping`, so a
  container that slept through a PostgreSQL restart does not hand the first
  request a dead connection) and `make_session_factory(engine)`
  (`expire_on_commit=False`, because callers read attributes off an object after
  its transaction closed).
- **`core/db/base.py`** — the one `DeclarativeBase`, carrying a fixed constraint
  **naming convention**; without it, autogenerate re-proposes indexes an earlier
  migration already created.
- **`core/db/registry.py`** — imports every module's `domain/models.py` so
  `Base.metadata` is complete. This is the single sanctioned `core → modules`
  import, whitelisted in the import-linter contract, and a module missing from it
  silently ships without its table.
- **`core/db/migrate.py`** — `alembic_config(url)` builds Alembic's config in
  Python (no `alembic.ini`: the URL is a secret, and a `%` in a password would be
  read as ConfigParser interpolation, hence the escaping), `upgrade_head_sync`
  for scripts and tests, and `async upgrade_head` for boot — which runs the sync
  command in `asyncio.to_thread` because Alembic's env.py calls `asyncio.run`,
  impossible on the live loop.
- **`core/tasks.py`** — the queue seam: `TaskRegistry` (a stable name → an async
  callable taking primitive kwargs), the one-method `TaskQueue` Protocol, and
  `InlineQueue`, which runs a task as an `asyncio.Task` in this process. Keeping
  the port here is what lets `core` and every module stay ignorant of the broker.
  `InlineQueue` holds strong references to its running tasks (the loop keeps only
  a weak one, and a fire-and-forget task can otherwise be collected mid-await),
  logs a failing body instead of leaving a "task exception was never retrieved"
  warning as the only trace, and takes an optional per-name concurrency limit —
  `{"run_job": 1}`, which is what the cron ticker did before the queue existed.
- **`core/redis.py`** — `create_client(url)` (one per process; `health_check_interval`
  so an idle pooled connection that died behind a NAT reconnects instead of
  failing the caller) and `Lock`, a `SET NX PX` + token lock released by a Lua
  compare-and-delete. The token is what makes it safe: only the holder's own token
  can delete the key, and the TTL is the dead-man's switch. Constructed only when
  `CH_REDIS_URL` is set. The module name shadows the third-party `redis` package
  inside this directory only — Python 3 imports are absolute.
- **`core/eventbus.py`** — the channel bus every turn event and every control
  nudge rides on, added in Wave 3b because the process that *runs* a turn is no
  longer the process streaming it to a browser or drafting a status line into a
  Telegram chat. `EventBus` is `publish` / `subscribe` / `ready` / `start` /
  `aclose`; `InProcessEventBus` is a listener list per channel, `RedisEventBus` is
  one `PubSub` and one reader task per process. Channels are
  `hermes:turn:<session_key>` and the single `hermes:ctl` (`CONTROL_CHANNEL`);
  payloads are JSON dicts, because `core` may not import a module — turns'
  `ChannelPublisher` serializes the `TurnEvent` on the way out and rebuilds it on
  the way in. Four properties are load-bearing, and each was paid for:
  - **`subscribe` is sync and `ready` is the seam.** `ready(channel)` resolves on
    the **server's SUBSCRIBE confirmation frame**, read off the wire by the reader
    — not when the command was sent. redis-py's `PubSub.subscribe` only writes the
    command, and the snapshot read that follows goes out on a *different* pooled
    connection, so resolving on the send would let an event recorded and published
    in between land in neither the snapshot nor the subscription. The reader
    therefore reads with `ignore_subscribe_messages=False` and counts the
    confirmations still owed per channel.
  - **Fan-out runs on a per-channel delivery task**, not on the reader. The reader
    only parses and enqueues; one `asyncio.Queue` plus one consumer task per
    channel awaits the handlers in order. Per channel and not per process because
    the SSE watermark reads a per-lane `seq`, so order *within* a lane is a
    contract — and because a slow subscriber (the Telegram presenter does a
    network call per status change) would otherwise stall every SSE stream in the
    process and delay every pending `ready()` and every control nudge with it. A
    channel whose queue keeps growing logs every `SLOW_HANDLER_DEPTH` (100)
    pending payloads, so a wedged handler is visible rather than merely slow.
  - **A handler that raises is logged and never stops the others**, the same rule
    the in-process publisher always had.
  - **The reader survives a connection loss**: it logs, re-subscribes every
    channel that still has a handler, and continues with backoff. A pub/sub socket
    that died silently would freeze every SSE stream in the process.
  SUBSCRIBE/UNSUBSCRIBE are serialized *onto the reader* through a command queue
  rather than issued from the calling task — one connection and one parser means a
  command sent mid-read is how a confirmation gets dropped — which is why the
  reader polls with a short timeout (`POLL_SECONDS`, 50 ms) instead of blocking.
- **`core/crypto.py`** — `SecretBox(key)` with `seal(str) -> bytes` /
  `open(bytes) -> str` over Fernet, and `SecretBox.from_env(env)`, which takes the
  mapping so a caller can pass a snapshot taken *before* the startup pop. An MCP
  server's API key has to be readable by whatever launches it, so it cannot be
  hashed — the rule that makes encryption worth it is that the key
  (`CH_SECRETS_KEY`) never sits next to the ciphertext (a PostgreSQL row). Fernet
  is authenticated, so a row edited in the database fails to open rather than
  decrypting to attacker-chosen plaintext. `SecretsKeyError` is deliberately
  unrecoverable: booting without the key gives a deployment that accepts secrets
  it can never read back.
- **`core/exceptions.py`** — `ApplicationException(status, code, message)` and
  `NotFound` / `Forbidden` / `Unauthorized` / `Conflict` / `InvalidInput`.
  Nothing here imports a web framework: a service raises, and the HTTP layer is
  the only place that knows how to render ([[component.web]]).
- **`core/contracts.py`** — the dataclasses more than one module needs, so that
  neither has to import the other: `Attachment` (telegram and files produce it,
  turns consumes it, the web router renders it) and `CurrentUser(user_id,
  is_owner)` with its `Role` enum. `CurrentUser` lives *here* rather than in
  `core/auth.py` because `modules/identity/domain/models.py` aliases it, and a
  `domain` package that imported `core.auth` would pull Starlette in with it —
  the "domain layers are pure" contract forbids exactly that. No table backs it
  yet: the owner is a meta row, everyone else an entry in `Config.allowed_users`,
  and a `users` table arrives in Step 4.
- **`core/auth.py`** — the session-identity seam. `CurrentUserMiddleware` stamps
  `request.state.user_id` / `.is_owner` from injected callables
  (`read_session`, `is_owner`, and since 4b `admitted(uid) -> bool`, asked on
  **every** `/api/*` request — a cookie whose user is no longer on the allowlist
  or has been disabled gets the same 401 as no cookie at all, redirect included,
  because a cookie outlives the decision that issued it and after 4b a logged-in
  user can write prompt text into a tree; `None` means "not checked", the pre-4b
  behaviour), so core stays ignorant of cookies, secrets and the
  ACL; `current_user(request) -> CurrentUser` and
  `require_owner(user = Depends(current_user))` are the dependencies every router
  takes instead of reading `request.state` ([[decision.modules-and-ports]]).
- **`core/di.py`** — `bundle(request)` (`request.app.state.deps`) and
  `provide(name, *, required=True)`, the two functions every module's
  `presentation/dependencies.py` is built on. The bundle is duck-typed because
  core may not import the web package.
- **`core/logging.py`** — `configure(level)`, called once at the top of `run()`.
  structlog is wired **through** stdlib logging (`structlog.stdlib.LoggerFactory`
  + `ProcessorFormatter` on the root handler), still wrapped in
  `core/logsafe.py::RedactingFormatter` (in `core/` since Step 5), so `httpx` and
  every other third-party logger
  keeps arriving, keeps being redacted, and `caplog` keeps working. Application
  code uses `log = structlog.get_logger(__name__)` and passes key-values
  (`lane=...`) rather than formatting them into the message; there is no
  `logging.getLogger` left outside `core/logging.py` and `core/logsafe.py`, and no
  `print()` anywhere.

## The store and the lane router (`core/db/`, `modules/sessions/`)

One `AsyncEngine` (`core/db/engine.py::make_engine`, over `CH_DATABASE_URL`) is
what every repository holds — `SessionRepository`, `MessageRepository`,
`JobRepository`, `SqlExtensionRepository`, `SqlOauthTokenRepository` and the
rest — each method one statement on a short transaction; the `Database` facade
that fronted it for the old callers went with the shims in Step 5. It applies no
schema — Alembic does, at boot. See [[contract.state-db]] for the tables and for the
full-text search contract. `build_session_key`
produces `tg:<chat_type>:<chat_id>[:<user_id>][:t<thread_id>]`;
`web_session_key(user_id)` produces `web:<user_id>:<uuid4 hex>`, and
`chat_type_from_key` answers `"private"` for it, so every private-only rule
applies to a browser lane unchanged. `lane_owner(session_key, channel,
user_id, deploy_owner)` is who a lane belongs to: the creating user for `web:`,
the chat id for a private Telegram lane, the deploy owner for a group.
`SessionRouter.route()` resolves/expires rows; `alive(row, now)` is the same
expiry test without the delete, which is what `SessionService` reads a lane's
live session through.

## The agent-owned memory (`modules/memory/`)

There is exactly one way to write durable memory now: files under
`agent_home/wiki/`, edited with the same Read/Glob/Grep/Write/Edit tools the
agent already has. No MCP tool writes it (the old `memory_save` tool and
`memory.py` are both gone; `memory_search` below only reads) and no `WikiStore`
method writes page
content — **the model owns every byte**; code only seeds the tree and reads
the index back for the prompt. `build_app()` returns the store, and `run()`
hands `WikiStore.root` to `WebDeps.wiki_root` when the web channel is on: the
browser's Memory page scans that directory and renders it, owner-only and
read-only ([[component.web]]). Nothing on that path writes either — a second
author of this tree is exactly what the rule above forbids.

`WikiStore(home, index_max_chars=8000)` (modules/memory/infrastructure/wiki_store.py): `.root` (modules/memory/infrastructure/wiki_store.py)
is `home/agent_home/wiki`. `.index()` (modules/memory/infrastructure/wiki_store.py) reads `wiki/index.md` and,
if it exceeds `index_max_chars`, truncates it with a loud
`TRUNCATION_NOTE` (modules/memory/domain/constants.py) telling the model to re-read the file from
disk rather than conclude a page is missing. `.index_mtime()` (modules/memory/domain/constants.py)
is the index file's mtime, used by `index_refresh_factory` below to detect a
background pass changing the wiki mid-session. `.seed()` (modules/memory/infrastructure/turn_hooks.py) is
idempotent — it does nothing once `wiki/index.md` exists (modules/memory/infrastructure/wiki_store.py) —
and otherwise creates the `user/`, `projects/`, `topics/`, `env/`, `raw/`
namespaces (modules/memory/infrastructure/wiki_store.py) and calls `_migrate_legacy()` (modules/memory/infrastructure/wiki_store.py),
which copies `MEMORY.md`/`USER.md` in verbatim (untouched on disk), each
wrapped in a frontmatter block (modules/memory/infrastructure/wiki_store.py), into `env/notes.md` and
`user/profile.md` respectively (the `_MIGRATIONS` table, modules/memory/domain/constants.py) —
code does not restructure them, that is left to the first lint pass. Seeding
also writes an initial `index.md`/`log.md` listing whatever was migrated.

## The wiki index and `memory_search` (`modules/memory`, `modules/turns`)

The markdown stays the one source of truth; `wiki_pages` beside it is a
**cache** — one row per `(owner_id, path)`, re-read from disk, never written by
the model and never read to decide what a page says ([[contract.state-db]],
[[decision.wiki-index-not-store]]). Failure is swallowed and logged **per page
first, per call second**: a note that will not decode or store is skipped with a
warning and the rest of the sweep goes on, and only something outside the loop
reaches the outer `except`. A missing, stale or failed row is not an error for a
turn, a lint or a boot; dropping the table and rebooting rebuilds it.

`WikiIndexService(store)` (modules/memory/application/services/index.py) is the
one writer: `touch(owner_id, workspace, rel_paths)` re-reads each touched file
(`read_page`, `split_frontmatter`, both with `errors="replace"` — a note copied
over SSH in Windows-1251 is still a page with a title and links, and a
`UnicodeDecodeError` is a `ValueError` rather than the `OSError` the read was
guarding against), upserts it and turns a path whose file is gone into a
`delete`; `reindex(owner_id, workspace)` converges the table to
`page_paths(wiki_root)` **minus `raw/`** — the graph's scan still draws fetched
sources as nodes, but a source is not memory and gets no row from either
trigger, which is the rule `touch` already applied. It is duck-typed on
`.root`/`.wiki_root`/`.is_owner` so
the application layer never imports `core.workspace`, and `owner_id <= 0` means
"do not index". For a non-owner it additionally requires
`workspace.root.name == str(owner_id)` — the `users/<id>` convention identity
writes — and logs an error and writes nothing otherwise: a future caller pairing
user A's tree with owner B's id would make A's private pages searchable by B,
silently.

**Four triggers write it, and there is no periodic job:** the Stop hook of a
foreground turn (`index_flush_factory`, bounded by
`asyncio.wait_for(..., FLUSH_TIMEOUT_SECONDS)` — the failure to fear is a hung
connection holding the SDK's reply, not a swallowed exception); the Stop hook of
a **review fork**, which is the wiki's main rewriter, so without it
`memory_search` would quote a snippet the fork has already replaced
([[component.learning]]); a non-dry-run lint, through `after_lint`; and a boot
sweep over `acl.active_user_ids()` in the **main process only**
(`main.build_app()`'s `reindex_all`, run as the `wiki-reindex` task beside the
curator), which builds each workspace from its `users` row **without** `ensure()`
— a boot must not be what seeds a tree nobody has used. A page edited by hand
over SSH is therefore picked up at the next boot. The sweep runs beside the
transports rather than before them, so a big wiki does not delay the first reply
and `memory_search` answers "no match" (never an error) while rows are still
arriving; it also races a turn that deletes a page mid-sweep, which can put that
row back until the next lint or boot — acceptable for a cache, and stated so
nobody reads a resurrected row as a bug in the delete.

`mcp__hermes__memory_search` (modules/turns/infrastructure/tool_handlers/memory.py)
is the read side: `PgWikiIndex.search` ranks FTS hits by `ts_rank`, then adds the
pages they link to and the pages that link to them, up to `depth` hops
(clamped `0..2`). `turns` declares the shape as its own `WikiSearch` Protocol and
`main.build_app()` wires memory's `PgWikiIndex` into it, so neither module
imports the other. The tool is registered **last**, is absent on a group lane and
absent when `memory_search_enabled` is false — both being facts fixed for the
lane, never for the sender ([[decision.prompt-cache-discipline]]). It was the last
until the content plan: `content_plan` now registers after it, on the deploy
owner's private and web lanes only ([[component.content]]).

The **shared namespace** is `Workspace.shared_wiki_root`, `shared/` inside the
OWNER's wiki. Its rows carry `shared = true` — written only when the tree being
indexed is the owner's — and a non-owner searches under `owner_id = :me OR
shared`. They get the tree as an extra read root, as a read-only section of their
prompt index, and as a denied write root in both spellings
([[decision.multi-user-isolation]]).

Because the toolset is part of the cached prefix, `main.run()` compares
`executor.tool_fingerprint()` — a sha256 over every `mcp__hermes__*` tool's name,
description and schema — with `meta.hermes_tools` and calls
`runner.reset_all()` when it moved (`sync_hermes_toolset`) — the mechanism that
makes this cut, and every later flip of `memory_search_enabled`, cost one
deploy-wide session reset at boot instead of a mid-session toolset swap.

## Wiki guardrails (`modules/memory/infrastructure/turn_hooks.py`)

Four hook events are wired by the `TurnHooksProvider` memory supplies
(`build(session_key, index_seen, workspace=None, *, owner_id=0)` — the `owner_id`
is what the index flush stamps its rows with)
(modules/memory/infrastructure/turn_hooks.py, built in the composition root),
sharing one `WikiTurnState` (modules/memory/infrastructure/turn_hooks.py: `pages`, `index_written`,
`log_written`, `fetch_hinted`) built fresh per turn:

- **PreToolUse** `Write|Edit` → `raw_guard_factory(wiki_root, cwd=…)` (modules/memory/infrastructure/turn_hooks.py):
  `wiki/raw/` is immutable — an `Edit` there is always denied, a `Write` is
  denied only if the target file already exists (so a *new* source can still
  be filed).
- **PostToolUse** `Write|Edit` → `page_touch_factory` (modules/memory/infrastructure/turn_hooks.py): records
  which page path was touched into `state.pages`, or flags `index_written` /
  `log_written` for `index.md` / `log.md` specifically; paths under `raw/`
  are not counted as pages.

Both of those hooks take **`cwd=`**, and `wiki_hooks` passes `wiki_root.parent`
— the lane's `agent_home`, which is what `base_options` gives the CLI. The wiki
rules tell the model to write pages under `./wiki/`, so a relative
`wiki/topics/x.md` has to resolve where the CLI lands it and not where this
worker process happens to run: left to the process cwd, `page_touch` never
recognises the file as a page, `state.pages` stays empty and nothing is flushed
to the index until the next lint. It is the same `cwd=` rule the path and write
guards already follow, and for the same reason — two hooks on one matcher
disagreeing about one path.
- **Stop** → `stop_gate_factory` (modules/memory/infrastructure/turn_hooks.py): if any page was touched this
  turn without both the index and the log also being written, blocks with a
  reason naming the missing file(s). `input_data["stop_hook_active"]`
  (modules/memory/infrastructure/turn_hooks.py) is the loop breaker — if the hook already fired once
  this turn, it logs a warning and lets the turn finish instead of blocking
  forever, leaving the drift for the curator's lint pass to catch. With an
  `indexer` wired, `index_flush_factory` (modules/memory/infrastructure/turn_hooks.py)
  follows the gate in the same list and flushes `state.pages` into the index;
  flushing twice is idempotent, so nothing here depends on whether the SDK runs
  later hooks after a `block`.
- **UserPromptSubmit** → `index_refresh_factory(wiki, seen, session_key)`
  (modules/memory/infrastructure/turn_hooks.py), backed by `TurnService._index_seen`: compares
  `wiki.index_mtime()` against the mtime the session last saw and, only if
  it moved forward, injects the current `wiki/index.md` as additional
  context — since the system prompt (and its index snapshot) is frozen for
  the life of a resumed session, this is how a background wiki write becomes
  visible without a session reset. An unchanged wiki costs zero extra tokens.

`webfetch_hint_factory` (modules/memory/infrastructure/turn_hooks.py, **PostToolUse** `WebFetch`) is also
registered and points the model at the `wiki-ingest` skill after a fetch,
once per turn (`state.fetch_hinted`).

## Safety (`modules/identity/`, `integrations/claude_sdk/guards.py`)

`AccessControl.check()` (modules/identity/application/services.py): first private-chat user claims ownership;
thereafter owner or `cfg.allowed_users` → allow, else deny. `is_owner()`
(modules/identity/application/services.py) is stricter and gates extension
mutations and secrets — allowlisted users may chat but not change what the
agent *is*.

Since Wave 4a the answer comes from the **`users` table**, not from one `meta`
row: a claim is a `users` INSERT first (the partial unique index
`users_one_owner` is the arbiter — the loser's `IntegrityError` is rolled back and
answered `"deny"`) and nothing else: since Step 5 nothing writes
`meta.owner_user_id` — migration `0005` materialised the owner row from that key
once, `owner()` reads only the table, and a stale key on a live volume is inert.
A restored pre-0005 dump still gets its row from `0005` on upgrade; the one-way
door is that a claimed deploy must never be downgraded past `0005`, because
nothing would re-materialise the owner ([[contract.state-db]]). An
allowlisted id with no row gets one on first contact (`role='user'`,
`workspace_dir='users/<id>'`), and `workspace_dir_of(user_id)` is what the
workspace resolver asks ([[decision.multi-user-isolation]]).

Since the owner's user list shipped, the ACL has two writers a person drives
directly: `invite(user_id)` writes that same row **before** first contact — the
owner admitting somebody in advance is an admission through a door like any other
— and `set_disabled(user_id, bool)` is the first thing ever to write
`disabled_at`, which `check` and `admitted` had been reading since 4a. Revoking
bites on the next request, refuses the owner (`OwnerImmutable` — `is_owner` gates
every mutation, so it would take SQL on the host to undo) and, for an id that is
only in `cfg.allowed_users`, creates the row already disabled, because a row is
the only thing that outranks the config file. Both are reached from the browser
only ([[component.web]]); chat has no such command.

A third writer arrived with `0009`: **`set_grants(user_id, *, shell=None,
learning=None)`** gives or withdraws a person's shell (`Bash`/`Agent`/`Task`) and
learning loop — `UserRepository.set_grants` is one UPDATE of only the named
columns, `OwnerImmutable` for the owner, `UnknownUser` (never a created row) for
an id with no row — and **`grants(user_id) -> Grants`** is the reader every
consumer asks: turns (`LaneGrants`, once per turn), the scheduler's executor
(`OwnerGrants`, at fire time) and learning (`learning_of`, per sweep and per
queued review). One `get` per call, never cached, so a flip reaches the agent and
the worker on their next ask; the rule is the pure `Grants.of(user, defaults)` —
the owner's row `(True, True)`, any other row its column else the default, no
row (or `user_id <= 0`) the defaults — and `grant_defaults()` reads
`users_shell_enabled` / `users_learning_enabled` at call time
([[decision.multi-user-isolation]]). Nothing here resets a session: the turn
notices a moved shell at its own start.

`bash_guard` (integrations/claude_sdk/guards.py) is a `PreToolUse` hook on Bash denying `_RULES`
(integrations/claude_sdk/guards.py): fs-root recursive delete, `mkfs*`, `dd of=/dev/*`, fork bomb,
and any `.env` access. Everything else runs because
`permission_mode="bypassPermissions"` — the container is the sandbox.

`path_guard_factory(allowed_roots, cwd=…)` (Wave 4a) is the second
`PreToolUse` hook, over `Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep`, and it
is installed for **every** lane including the owner's, with that lane's
`agent_home`, `tmp/`, `logs/`, the shared plugin clones and the read-only
catalog as the allowed roots. It checks `file_path`, `notebook_path` and `path`, resolves
non-strictly so a planted symlink is judged by where it points, resolves a
relative path against the lane's `cwd`, and denies an **absolute**
`pattern`/`glob` (a `Glob` with no `path` would otherwise never show the guard a
path at all). An empty root list denies everything — fail closed. It is defence in
depth and not a boundary: every turn is the same uid, so the check is TOCTOU-racy
and `Bash` is not guarded by it at all ([[decision.multi-user-isolation]]). Since
4b the same hook — same roots minus the catalog, `cwd=agent_home` — is installed
on every background fork (review, consolidation, wiki lint), and
`write_guard_factory(cwd=)` resolves a relative path against that same `cwd`, so
the two hooks stacked on one matcher cannot disagree about one path
([[component.learning]]).

**Taking a tool away is `disallowed_tools`, not `allowed_tools`.** Under
`bypassPermissions` the allow list is an *auto-approve* list, so
`modules/turns/domain/policies.denied_tools` fills `ClaudeAgentOptions.
disallowed_tools` (the CLI's `--disallowedTools`) with `Bash`/`Agent`/`Task` for a
non-owner lane unless its owner holds a shell grant (`ctx.shell_enabled`, resolved
per turn — `TurnSettings` no longer carries a deploy-wide copy); `builtin_tools`
only keeps the
approve list in its frozen order so the cached prefix stays byte-stable. The
background forks carry the same three in `disallowed_tools` since 4b. The hermes
tools are shaped by the **lane**, never the sender: on a group lane
(`core/session_keys.is_group_lane`) `skill_admin`, `session_search` and
`cost_report` are not registered for anybody, the owner included, because a
per-sender flip would change the MCP schema list mid-session
([[decision.multi-user-isolation]]).

## Container

Dockerfile: a `webbuild` Node 20 stage builds the CladeX SPA from `web/` and
the runtime image copies only its `dist` to `/app/web_dist` (`CH_WEB_DIST`), so
no `node_modules` ships. The runtime stage is `python:3.12-slim` + Node 24,
`@openai/codex`, the Claude Code CLI from Anthropic's native installer (run as
`hermes` after `USER hermes`, so it lands in `/home/hermes/.local/bin`, which is
put on `PATH`; `DISABLE_AUTOUPDATER=1` keeps the pin from drifting in a running
container; pinned to 2.1.280, the version the pinned `claude-agent-sdk` 0.2.158
bundles and actually runs), `gh` (GitHub CLI, from its own apt repo — Debian's is too old
for `gh search commits`), `uv` (for `uvx` MCP servers), `python-pptx` (for the
agent's own deck-building scripts, not imported by `src/` — everything outside
`/data` is rebuilt on `up --build`, so an agent-run `pip install` would not
survive the next deploy); unprivileged user `hermes`
uid 1000 (the CLI refuses `bypassPermissions` under root); `CMD python -m
claude_hermes.main`. docker-compose mounts `./data:/data` (writable state) and
`./catalog:/app/catalog:ro` (the security boundary — see
[[decision.readonly-catalog-boundary]]), and runs a **`db` service**
(`postgres:16-alpine`, volume `pgdata`) the agent waits on with `depends_on:
condition: service_healthy` — the boot path migrates before it serves, so it must
not start against a server still initialising. Wave 3a added **`redis`**
(`redis:7-alpine --appendonly yes`), **`minio`**, and two more containers running
this same image with a different `command`: **`worker`** (the queued work) and
**`scheduler`** (a stateless enqueuer). Since Wave 3b the worker command ends
`--max-async-tasks 4`: a turn forks the Claude CLI, so concurrency per container
is a memory dial and a deployment scales `worker` **replicas** rather than that
number. `agent` and `worker` share their mounts
and environment through one YAML anchor, because a mount that drifts between them
is a scheduled job that behaves differently depending on which process ran it —
and, since 3b, because the worker runs the turns and therefore needs the same bot
token the agent has: whichever process runs a turn is the one that sends its
`ask_user` keyboard and its `send_document`. In redis mode `agent` no longer runs
turns at all; it takes the message, writes the row, and watches the lane. See
[[system.operations]] and [[decision.redis-taskiq-scaling]]. `CH_DATABASE_URL` is composed in
`docker-compose.yml` from the single `POSTGRES_PASSWORD` in `.env`, so the
agent's URL and the server's password cannot drift apart. Env: `CH_HOME=/data`,
`CH_ASSETS=/app`, `CH_CATALOG=/app/catalog`, `CODEX_HOME=/data/.codex`,
`GH_CONFIG_DIR=/data/.gh`, `CLAUDE_CONFIG_DIR=/data/.claude-cli`,
`CH_WEB_DIST=/app/web_dist`. A `cloudflared` sidecar (outbound only, no host
ports) is the single public surface when the web channel is on — see
[[decision.web-as-a-channel]]. Of those, the three CLI login dirs
all exist for the same reason: each CLI keeps its login outside the volume by
default, so `up --build` would log the agent out of Codex, GitHub, and the CLI's
own session store.

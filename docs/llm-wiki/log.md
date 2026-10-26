---
id: log.maintenance
kind: log
status: current
summary: Append-only LLM Wiki maintenance history.
verification:
  base_commit: 58a975e3df70e5cb14a9e5382d27fa8d169acded
  evidence_fingerprint: sha256:87b1d9e98979b7fe40d1c11e89c06641af04f7f633deae90fa439abc6414c92d
evidence:
  - path: llm-wiki.yaml
relations: []
---
# Maintenance Log

## [2026-07-18] init | Wiki scaffold generated

## [2026-07-18] init | Initial synthesis compiled from repository evidence

## [2026-07-30] sync | LinkedIn content pipeline: per-job cron timeout, gh in the image, three bundled skills

- `contract.state-db`, `component.cron`, `flow.scheduled-job`, `quality.invariants`:
  `jobs.timeout_seconds` (NULL = inherit `cron_timeout_seconds`) now bounds a run;
  recorded why the default is not materialized into the row.
- `component.cron`: documented that a scheduled run composes no hermes MCP server,
  so `send_document`/`session_search`/`ask_user` do not exist there — the constraint
  the content pipeline is designed around.
- `component.core-runtime`, `system.md`: `gh` (GitHub CLI) added to the image with
  `GH_CONFIG_DIR=/data/.gh`; `Dockerfile` added to core-runtime's evidence.
- Re-fingerprinted the three decision/flow pages that list `tools.py` as evidence
  after checking their prose was unaffected.

## [2026-07-30] sync | Both scheduled jobs stop at propose, not draft

- `linkedin-post` gained an explicit propose/draft split: an unattended run ends at
  step 3b with a claim + angle + visual archetype written to
  `content/briefs/<date>-shipped.md`, because cron scope has neither `send_document`
  (no image) nor `ask_user` (no angle approval). The weekly job moved to Monday
  09:00 local — offset from the daily 10:00 one because `ticker.tick` runs due jobs
  sequentially, so a shared fire time makes the second wait out the first's limit.
- Both jobs now write the same brief fields into `content/briefs/`, so
  `linkedin-post` step 1 reads either file without knowing which produced it.
- No code changed; `README.md` did, which restaled the three pages listing it as
  evidence (`system`, both decisions). Their prose was checked against the edit —
  the new section is about skills, not about the catalog boundary, threads, or the
  container — and re-fingerprinted.

## [2026-08-01] sync | Cron→topic context bridge; image-prompt references split

- `component.cron` (new section), `flow.scheduled-job` (new step 5),
  `flow.message-turn`, `component.core-runtime`: the first turn after a cron
  delivery now quotes the job's latest recorded output into the user message
  (`_with_cron_context`, runner.py:217; `JobStore.by_thread`, store.py:34;
  `Database.last_message`, db.py:141). Root cause on record: the ticker posts
  through RichSender, so the topic's chat lane never saw the brief and a "3"
  reply had nothing to refer to (live, 2026-08-01).
- `decision.prompt-cache-discipline`, `contract.state-db`,
  `quality.invariants` §9: the bridge rides in the user message (no cached
  prefix moves), consumes the `cron:<id>` FTS rows (the executor must keep
  recording them), and copies text — it never resumes a cron session.
- `quality.failure-modes`, `quality.invariants`, `component.core-runtime`:
  runner.py line references refreshed after the insertion shifted them.
- `decision.claude-agent-sdk` re-fingerprinted; prose unaffected by the diff.
- Not wiki, same change: `skills/gpt-image-2` references split into
  fundamentals/generate/edit/advanced (generate.md gained §4.1b, the
  scoped-zones architecture recipe), `skills/linkedin-post/references/visuals.md`
  gained "Diagram discipline" and now points at the new files instead of the
  deleted `prompting.md`; both skills seed copy-if-missing, so live
  deployments need the sync procedure documented in
  `docs/linkedin-content-pipeline.md`.

## [2026-08-15] sync | TURN_RULES in the append; gen.sh runs are serialized

- `component.core-runtime`, `quality.failure-modes`: `build_system_append` now
  leads with `TURN_RULES` (prompt.py:12) — the harness's turn model, which the
  model had been guessing wrong. Live incident 15.08: the agent ended three
  turns of a ten-image batch on "I'll wait, I'll be notified" (nothing in this
  codebase notifies), then read its own turn boundaries as the user approving
  each tool call and asked for a permission-mode change that was already
  `bypassPermissions` at every call site.
- `quality.failure-modes`: second entry for the same incident's other half —
  `gen.sh` attributed output by diffing a codex directory shared across
  sessions, so concurrent runs returned one another's images (five of ten
  slides were the same photo; "Istanbul" was Italian food). Runs now serialize
  on `$CODEX_HOME/.gen.lock`, exit 8 on lock-wait timeout. `prompt.py` and
  `skills/gpt-image-2/scripts/gen.sh` added to that page's evidence.
- `decision.prompt-cache-discipline` re-fingerprinted: `TURN_RULES` is another
  static block in the frozen append, so the prefix stays byte-stable per
  session. Reach caveat recorded — lanes older than the change keep their
  stored append until `/new`.
- `component.core-runtime` (Container): `python-pptx` added to the image. It is
  the agent's tool, not a project dependency — nothing under `src/` imports it —
  and it belongs in the Dockerfile because everything outside `/data` is rebuilt
  on `up --build`, so an agent-run `pip install` silently expires at the next
  deploy.

## [2026-09-14] sync | PostgreSQL, the `core/` kernel, structlog, FastAPI shell

- `contract.state-db` rewritten as the PostgreSQL schema contract (same id): one
  database behind `CH_DATABASE_URL`, Alembic owning the schema, ORM models under
  `modules/<m>/domain/models.py` collected by `core/db/registry.py`. Recorded the
  four deliberate deviations from the SQLite schema — `messages_fts` becoming a
  `messages` table with a generated `tsvector`, surrogate keys on `job_runs` /
  `turn_costs`, BIGINT Telegram ids, JSONB payloads — and why timestamps stay
  ISO-8601 `TEXT`.
- New `decision.postgres-and-modular-core`: why PostgreSQL, why the models live
  in `modules/*/domain` before the modules do, why structlog goes *through*
  stdlib logging, and why FastAPI adopts the existing Starlette `Route` objects
  instead of becoming `APIRouter`s in the same wave. Alternatives rejected are
  recorded there too.
- `component.core-runtime`: boot is now `upgrade_head` → `Database.open(url)`; new
  `core/` section (engine, base, registry, migrate, exceptions, logging); `db.py`
  described as a facade over one `AsyncEngine` that applies no schema;
  `database_url` added to the config rules; compose's `db` service and its
  health gate recorded. Runner/main line references refreshed.
- `component.web`: the app object is a `FastAPI` instance built from the same
  route list, OpenAPI deliberately off, and one `ApplicationException` handler
  renders every error — `ApiError` is now a subclass of it.
- `quality.testing`: the suite needs a real PostgreSQL (`make test-db`), one
  database per pytest process created and migrated by `tests/conftest.py`, a
  truncate-before-each-test fixture, and `lint-imports` beside ruff. 818 passing.
- `system.operations`, `system.overview`: state is split — PostgreSQL in the
  `pgdata` volume plus the `./data` volume — so `tar ./data` is no longer a
  complete backup; `pg_dump` added, the one-shot `scripts/import_sqlite.py`
  migration pointed at `docs/deploy.md` §4a, the prod healthcheck now proves a
  database connection, and the redaction section rewritten around
  `core.logging.configure`. Recorded that `scripts/deploy.sh` still asserts
  `data/state.db` in its archive — a leftover that would refuse a fresh host's
  backup.
- Table renamed in passing on every page that named `messages_fts`
  (`component.cron`, `component.gateway`, `component.learning`,
  `flow.scheduled-job`, `flow.message-turn`, `quality.failure-modes`,
  `decision.web-as-a-channel`, `decision.threads-as-session-lanes`,
  `decision.prompt-cache-discipline`, `decision.readonly-catalog-boundary`); the
  remaining pages were re-fingerprinted after checking their prose still holds
  — the structlog change touched every source file, so every fingerprint was
  stale without any page being wrong.

## [2026-09-15] sync | Wave 2: nine modules, consumer-side ports, one composition root

- New `decision.modules-and-ports`: the nine vertical slices and their four
  layers, ports declared by the consumer and wired in `main.build_app()`, the
  three shim kinds kept at the old import paths until Step 5, the
  `threads.has_turns` denormalization that removed the last cross-module join,
  the duck-typed router `deps` that survives until Wave 2b, and the four seams
  (`AskBroker`, `InProcessPublisher`, `CronTicker`, `PendingStates`) that stay
  in process memory until Wave 3.
- `components/*`: evidence lists re-anchored to `modules/<m>/**`, structure
  sections keyed by module path instead of the pre-move file name, and every
  `file.py:line` citation replaced by the module file that holds the symbol now.
  Line numbers were dropped rather than re-derived: they rot, the paths do not.
- `system.overview`: a Layout section for `core/ integrations/ modules/ workers/`
  and `build_app()`; `component.core-runtime`: the composition-root walk, the
  headless `AppContext` and the two late-bound ports.
- `contract.state-db`: `threads.has_turns` and migration `0002`, plus the note
  that every statement now lives in its module's repository and `db.py` is a
  delegating facade with no SQL left.
- `quality.invariants`: the two Wave 2 untouchable lists — the six prompt-cache
  rules and the six security checks that moved verbatim — each pointing at the
  module file that holds it now. `quality.testing`: `tests/modules/<module>/`
  beside the Wave 1 files, 1098 passing.
- Checked each `modules/<m>/README.md` against the code and corrected what Phase
  C had already paid: the turns debt table (no legacy tool or runner body left),
  scheduler (`create_job` forwards, `cron/adapters.py` is dead at composition),
  extensions (`ext_manage` runs through `ExtensionAdminService`), learning
  (`SkillAdminService` bound on both paths), memory (`run_wiki_lint` gone),
  identity (`web/app.py`'s copy of `auth_routes` gone), files and sessions.
- One code fix in passing: `learning`'s `SkillRepositoryPort` declared
  `live_names()`, which no implementation has, and omitted `placement(name,
  live)`, which the library service calls — the Protocol now describes what is
  actually used.

## [2026-09-15] sync | Wave 2b: `APIRouter` + `Depends` + Pydantic bodies + OpenAPI

- `component.web`: the app section rewritten — `FastAPI(openapi_url="/api/openapi.json",
  docs_url="/api/docs", redoc_url=None)` assembled with `include_router` from twelve
  module routers, the schema behind the session cookie (neither path is public),
  `tags` + a frozen `operation_id` per route, the bundle on `app.state.deps`, the
  `core/di.py` provider pattern, and the second exception handler that renders a
  `RequestValidationError` as **400 `bad_request`** rather than FastAPI's 422.
  Mounting is the shell's decision (`mount(module, enabled=…)`), so a deployment
  without a wiki/scheduler/file store simply has no such routes. Added a
  `Depends(current_user)` / `Depends(require_owner)` paragraph: no handler under
  `modules/` reads `request.state` any more.
- `decision.modules-and-ports`: new "Depends and providers" section — the
  `app.state.deps` bundle and why it stays duck-typed, one typed provider per
  collaborator built once at module scope (FastAPI keys `dependency_overrides` by
  the callable object), the 503 as a programming-error guard rather than a feature
  switch, `require_owner` as a route dependency, and why the dict wire shapes stay
  the contract (no `response_model`, lenient request models, 400 not 422). The old
  "duck-typed router `deps`" cost entry rewritten to what survived.
- `quality.testing`: an "Overriding a dependency instead of rebuilding the bundle"
  section with the `app.dependency_overrides[get_graph]` pattern, the two rules that
  make it work (identity-keyed lookup, clear them), and the `tests/modules/<m>/
  test_<m>_router.py` file per converted module; count 1098 → **1146**, and the note
  that `tests/test_web_*.py` passed the conversion unmodified.
- `quality.invariants`: two Wave 2b additions to the untouchables — a Pydantic 422
  never replaces a status or `code` a test asserts, and the owner gate is
  `require_owner` as a route dependency (solved before any parameter provider).
- `component.core-runtime`: kernel bullets for `core/contracts.py` (now also
  `CurrentUser`/`Role`, and why they live there and not in `core/auth.py` — a
  `domain` alias must not import Starlette), `core/auth.py` and the new `core/di.py`.
- `component.extensions`: `ext_manage` has exactly one definition
  (`presentation/agent_tool.py`); the root carries name/description/schema on
  `core.app_context.ExtensionAdminTool` and `hermes_server.declared_spec` reads all
  three back — a handler reaching the server without a declaration raises rather
  than registering under a guessed description (the text is cached prompt bytes).
- `decision.postgres-and-modular-core`: superseded-by notes on the two places that
  said OpenAPI was off and the `APIRouter` conversion was rejected — deferred, not
  abandoned, and done here. `flow.message-turn`: the lane check on the web entry is
  `Depends(owned_thread)` now.
- Checked each `modules/<m>/README.md` against the code: all 21 extensions routes,
  8 learning, 9 scheduler, 7 sessions, 8 turns, 6 identity, 2 files, 2 memory and
  1 telegram matched by method, path, `operation_id` and registration order, and
  every Dependencies table matched its module's providers. Corrected the stale
  debts the wave paid: the `web/*.py` shims now only return `list(router.routes)`
  (extensions, files, learning, scheduler, sessions), `wiring.{connectors,plugins,
  drafts}_routes` have no caller left, `presentation/router.routes(deps)` survives
  in memory and sessions but nothing calls it, identity's mixed-mode `mount()`
  branch is gone, and telegram's router is mounted on `webhook_enabled(cfg)`.
  Closed the extensions `agent_tool.py` debt row and dated the `core.events`
  extension event past Wave 2b.

## [2026-09-16] sync | Wave 3a: Redis queue + worker, claim-based scheduler, MinIO blobs, extension state in PostgreSQL

- Two new decision pages. `decision.redis-taskiq-scaling`: the queue port is ours
  (`core/tasks.py` — `TaskRegistry` + `TaskQueue`, `InlineQueue` here and
  `TaskiqQueue` in the one file that imports taskiq), `queue_mode` derived from
  `CH_REDIS_URL` and refused as a config key, the three dispatchers, the
  `claim_due` at-most-once proof written out case by case, the two Redis locks and
  why the learning gate is a `meta` lease instead, and what stays a singleton —
  the polling Telegram client (`getUpdates` is single-consumer), the in-process
  turn runner until 3b, and inline `{"run_job": 1}`.
  `decision.minio-blobs`: the async `BlobStore`, the address grammar in
  `files.path`, the bucket-plus-local-mirror shape and why the mirror exists
  (the agent's own `Read`/`Glob`, aiogram's `FSInputFile`), boto3-in-a-thread
  settling Open Question 2 the other way, and why `/api/files/{id}` stays
  ownership-checked rather than becoming a presigned URL.
- `contract.state-db`: `jobs.claimed_at`/`lease_until` and the three new tables in
  the column table, plus a "Migration `0003`" section — the lease semantics, why
  each of `extension_secrets` (BYTEA, Fernet), `catalog_drafts` (`raw` verbatim,
  because the token is `sha256(raw)[:8]`) and `approval_snapshots` has the shape
  it has, and why nothing is backfilled (the migration has no encryption key;
  `scripts/import_data_dir.py` does it deliberately). `files.path is not
  authority` rewritten for addresses; the `oauth_tokens` mirror target corrected
  from `secrets.env` to `extension_secrets`; `learning_review_until` added to the
  `meta` list.
- `system.operations`: a "six services" section (what each is, what it needs, the
  `x-app` anchor, why `redisdata` is a volume), the ten popped env names, why
  `CH_SECRETS_KEY` is the one secret with no second copy, how to run
  single-process instead, a "test stack" section (`make test-stack`, the three
  ports, db 15), backups as three things with the `mc mirror` line, §4b
  (`import_data_dir.py`) and the `drafts list|show|delete` operator commands, and
  a verification note that redis mode has never been run anywhere.
- `component.cron`: the ticker section replaced by claim → dispatch → execute, a
  "who drives the clock" table (`run_now` up to 60 s in redis mode), and the
  wiring paragraph rewritten. `component.learning`: the `ReviewGate` lease,
  `QueueReviewDispatcher`, `kinds` as a sorted list, and `curator_loop` /
  `run_once` under `lock:curator` in `workers/curator.py` — the page still said
  `curator_pass` and `curator_loop` lived in the service.
  `component.core-runtime`: `build_app`'s new steps and the three
  selector rows, `build_delivery`, the ten pops, the inline-only clock, the
  `drafts` subcommands, a `workers/` section, `core/{tasks,redis,crypto}.py`
  kernel bullets, the Wave 3a config keys with `queue_mode` as a derived property,
  and the four new containers. `component.extensions`: `PgSecrets`/`PgDrafts`/
  `PgSnapshots` in place of the three file stores, a "three files became three
  tables" section with the port table, Redis pending states and the oauth lock,
  and the operator path through the app.
- `quality.invariants`: 5 (every popped name, in both entry points, and the
  encrypted table), 7 (lease not flag), 10 (the reschedule inside the claim
  transaction), security 3 (ten names, worker too) and security 4 (one
  containment body, before any download) rewritten; a "Wave 3a additions" section
  with 19–22 (primitive task args, derived `queue_mode`, only the agent polls,
  no presigned URLs) and the honest caveat that `workers/taskiq_app.py` has never
  been executed.
- `quality.testing`: count 1146 → **1308 passed, 1 skipped**; the suite runs
  inline mode and the local blob store always; a "Redis and MinIO are optional"
  section with the four fixtures, why db 15 and a bucket per process, why they
  skip where the database fixture exits, the CI shape, and the deliberate
  `pytest.importorskip("taskiq")` skip; Wave 3a's own test files listed.
- Re-anchored for the same wave and re-fingerprinted: `component.gateway`
  (attachments through `FileService.put_upload`, `/secret` into the sealed table),
  `component.web` (the async `BlobStore`, addresses, no presigned URLs),
  `flow.scheduled-job` (claim/dispatch/execute as separate steps, `run_now`
  latency), `flow.extension-install` (three tables, evidence repointed off the
  deleted `draft_store.py`), `decision.readonly-catalog-boundary` and
  `glossary` (a draft is a row), `decision.modules-and-ports` (learning's two
  background passes go through the queue), `system.md` (what `./data` still
  holds), `index.md` (the two new decisions). `decision.claude-agent-sdk`,
  `decision.postgres-and-modular-core`, `decision.web-as-a-channel` and
  `quality.failure-modes` were checked against the diff, found unaffected, and
  re-fingerprinted only.
- Checked every touched `modules/<m>/README.md` and `workers/README.md` against
  the code. Scheduler, files, telegram and turns matched. Three corrections:
  `workers/README.md` listed seven popped env names where the code pops ten;
  `learning/README.md` still said Phase C would delete the `reviewer=`/`learning=`
  keywords, which it did not (the root passes `dispatcher=`/`gate=`, the legacy
  keywords survive with their own tests); `extensions/README.md` still described
  deleting `BoundApprovals` and making `TranslatingSecrets.put` async as work to
  do, both already done in `core/app_context.py`.
- Left standing as a debt, recorded in `workers/README.md` and on both new
  decision pages: `workers/taskiq_app.py` was written with no network and has
  never run against a broker, so redis mode is unshipped until that checklist is
  executed.


## [2026-09-17] sync | Wave 3a redis mode verified against a live worker

- Network back: `pip install -e ".[dev]"` brought taskiq 0.12.6, taskiq-redis
  1.2.3 and redis-py 8.1.0; `tests/test_workers_taskiq.py` runs instead of skipping.
- Live smoke (`taskiq worker` against `hermes-test-redis`, `run_job` and
  `scheduler_tick` enqueued by hand) executed both tasks and found one defect:
  redis-py 8's default 5-second read timeout killed the broker's blocking BRPOP
  every five seconds and the worker restarted in a loop. `_make_broker` now passes
  `socket_timeout=None`; a second 45-second run had zero restarts; the test file
  pins the setting.
- `docker compose config` and `docker compose build` pass; the image carries
  taskiq and the `taskiq` CLI the worker/scheduler commands name.
- Pages that called redis mode unverified rewritten: components/core-runtime,
  decisions/redis-taskiq-scaling, operations, quality/invariants, quality/testing,
  workers/README.md. Still unobserved: a job delivered to Telegram from a
  production worker (checklist step 5).


## [2026-09-17] sync | Wave 3b — the turn is a row and a task

- **What changed in the code.** `POST /api/threads/{id}/messages` and a Telegram
  message now insert a `turns` row and enqueue `run_turn(turn_id)`; a worker
  claims the row and runs the SDK loop. New: migration `0004_turns` (the table,
  three partial indexes, `sessions.index_mtime`), `core/eventbus.py`
  (`InProcessEventBus` / `RedisEventBus` on `hermes:turn:<lane>` and `hermes:ctl`),
  turns' `launcher.py` / `execution.py` / `control.py`, `turn_repository.py`,
  `snapshots.py` (`MemorySnapshots` / `RedisSnapshots`), `dispatch.py`,
  `redis_ask_broker.py`, and `QueuedTurnRunner` in `core/app_context.py`. The 202
  body gained `turn_id` — the only wire change, and additive.
- `contract.state-db`: the `turns` table, `sessions.index_mtime`, a "migration
  0004" section stating that the unique partial index IS the lane lock and giving
  the one-statement semantics of each repository method, plus two contract notes
  (the row is the one truth for "running"; `turns` is a work-submission surface,
  not an authorization).
- `component.core-runtime`: the composition now selects an event bus, a snapshot
  store and an ask broker as well as a queue and a blob store; a `core/eventbus.py`
  bullet covering the four load-bearing properties (`ready` resolves on the
  server's SUBSCRIBE **confirmation**, per-channel delivery tasks, a raising
  handler, reader recovery); the launcher / run service / control channel above
  `TurnService`; `run_turn` as the fifth task and the reap on `scheduler_tick`;
  the four new config keys; `--max-async-tasks 4` and why the worker needs the bot
  token.
- `component.web`: `TurnBus` is now bus + publisher + `SnapshotStore`, `snapshot`
  and `is_running` are async, `ready` is the seam; the two orderings
  (snapshot-before-publish, ready-before-read) written down as what makes the
  stream at-least-once; the `hermes:snap:` key layout; the turns router bullet
  rewritten around the launcher, the 202 `turn_id` and the SSE **seq watermark**.
- `component.gateway`: the dispatcher's one added `await bus.ready(key)` and why a
  missed `turn.start` is silent; `QueuedTurnRunner` (a busy lane is retried, not
  refused); the agent still sends the Telegram reply, from `wait`; the worker's
  token requirement; the async/sync split on the ask broker and `RedisAskBroker`.
- `flow.message-turn`: step 6 split into the row, the claim and the turn; the
  Telegram reply traced back to `TurnLauncher.wait` with its bounded re-read; the
  web entry rewritten (202 with `turn_id`, no background task, the later spinner);
  a new section on cancel, `ask_user` and a dead worker across processes.
- `decision.redis-taskiq-scaling`: the singleton table loses the in-process turn
  runner; a new "Wave 3b" section with the was/is table for the four process-local
  pieces, the two-instance argument, the list of what the agent still holds in
  memory (and why none of it is correctness), and the reap false-positive window.
  Four alternatives recorded as rejected, including re-executing a lost turn.
- `quality.invariants`: #6 rewritten (the unique partial index, not the
  `asyncio.Lock`), #18 extended (a failure before the body and a reaped turn both
  still emit a closing event), and seven new ones — the `worker_id` guard with its
  one sanctioned exception, flag-then-nudge and idempotent cancel,
  snapshot-before-publish / ready-before-read, the SSE watermark,
  subscribe-then-`ready`, the sync `discard`/`deliver`, and per-channel fan-out.
- `quality.failure-modes`: a dead worker (reclaimable lane, no re-execution), the
  reap false positive, `lost` vs `dispatch failed` as two different rows, a worker
  without the bot token, and a slow subscriber. The 2026-09-11 restart entry is
  now scoped: in redis mode restarting `agent` no longer kills the turn.
- `quality.testing`: a Wave 3b section — fakeredis at the unit level and the
  container only for the two-process proof; the parametrized snapshot-store
  contract file that keeps `MemorySnapshots` and `RedisSnapshots` honest; the
  eventbus tests that pin unconfirmed-`ready` and per-channel isolation.
- `system.operations`: the service table (the worker runs turns and needs the
  token; the scheduler's tick reaps), the Redis namespaces as wire values, a
  smaller deploy bill in redis mode, and a `turns`-table check for verifying a
  deploy. `README.md` and `docs/deploy.md` gained the same three facts a reader
  acts on: the worker runs the turns, a restart of `agent` is survivable, and the
  four new config keys.
- `modules/turns/README.md` checked against the final code (the pre-body event
  envelope and the presenter-binding debt added); `workers/README.md` rewritten
  for five tasks, `--max-async-tasks 4` and the token requirement.

## [2026-09-17] sync | Wave 4a — users, per-user workspaces, webhook mode

*(Written after the fact, from the decision page — this wave's entry was never
logged when the pages were compiled, commit `eddca86`.)*

- New `decision.multi-user-isolation`: `users.id` is the Telegram id, the
  partial unique index `users_one_owner` is the claim arbiter, `owner_id` on
  `sessions`/`messages`/`files`/`jobs`/`turn_costs`/`catalog_drafts` (and the FK
  on `threads.owner_user_id`), `turns.user_id` deliberately without one, the
  lane-owner rule in one place (`core/session_keys.lane_owner`), a `Workspace`
  per user with the owner's root staying `CH_HOME`, per-user `tmp/` and
  `CLAUDE_CONFIG_DIR`, the plugin clones moved to `CH_HOME/plugins`, and the
  honest scope: isolation against accident inside one uid, not a boundary.
- `contract.state-db`: migration `0005`, the SQL backfill that derives the owner
  from the session key rather than blanket-assigning, the per-table read/write
  rule, NULL-owner semantics. `component.core-runtime`: `WorkspaceResolver`,
  `for_user` vs `for_lane`, `path_guard_factory`, `disallowed_tools` as the
  control, `ctx.workspaces`/`ctx.update_dedup`. `component.web`: `/healthz`,
  `/api/me`'s two additive profile keys, the isolation test through the routes.
  `component.gateway` and `system.operations`: Telegram webhook mode.
  `quality.invariants` 30-34; `quality.failure-modes`: wrong-workspace
  resolution, duplicate webhook updates, NULL-owner rows.
- Recorded as 4b debts on the decision page: owner-only learning, the
  `/data/agent_home` literals in the seeded skills, profile-less Mini App
  admission, `_copy_missing` never refreshing, a NULL-owner job.

## [2026-09-18] sync | Wave 4b — the learning loop per user

- **What changed in the code** (commit f3010d1; the plan is 1de31dd).
  New: `modules/learning/application/scopes.py` (`LearningScope`,
  `LearningScopes`), `modules/learning/wiring.py` (`build_scope`/`build_scopes`),
  `modules/learning/infrastructure/scoped_meta.py` (`ScopedMeta`) and
  `review_slot.py` (`RedisReviewSlot` on `lock:review`), `core/workspace.py::
  SeedManifest`, `core/session_keys.is_group_lane`, `workers/curator.run_all`,
  `AccessControl.admitted`/`active_user_ids`, `SessionRepository.reset_owned`,
  `verify_webapp_user`, `SchedulerService(deploy_owner=)`, the
  `users_learning_enabled` switch (default false). Changed: `LearningLoop(scopes,
  dispatcher)` routing `after_turn(owner_id=)`, `learning_review(..., owner_id=0)`,
  every fork taking `workspace=` with the turn's path guard, `write_guard_factory
  (cwd=)`, `disallowed_tools` on forks, `CuratorService(logs_dir=)`, the skills
  API per requesting user with no `require_owner`, `CurrentUserMiddleware
  (admitted=)`, `send_document` relative to and inside `agent_home` (its
  description changed), the inline queue limit `learning_review: 1`, the seeded
  skills without `/data/agent_home` literals. Verified by a live two-user smoke
  through a real taskiq worker; 1715 tests, the SPA suite, ruff and
  lint-imports clean. The advisor's code pass added `_PREVIOUSLY_SHIPPED_SEEDS`
  (a pristine copy of an older bundle refreshes on the first boot, a legacy
  file that matches nothing is said once), the review slot's re-acquire guard,
  the gate release on a raising `acquire`, a read-only `AccessControl.admitted`,
  `send_document` sharing the guards' notion of "absolute", and a 401 for a
  cookie whose `users` row is gone.
- `decision.multi-user-isolation`: 4b folded in — the deploy-wide table loses
  the learning row, `SeedManifest` in the layout section, a "Tool presence is a
  property of the LANE" section (why 4a's per-sender rule was a P1 defect), a
  "The learning loop per user" section (scopes, suffixed meta, the switch and
  why it defaults off, the review slot, confined forks, the sweep, per-request
  admission, `send_document`, the NULL-owner job, the Mini App profile),
  "Extensions stay deploy-wide — final" with the reasoning, the 4b costs,
  "What a non-owner user gets after 4a and 4b", and the debts marked paid with
  Step 5's kept. One finding, fixed in the same wave: the SPA did gate
  *New skill* and the edit/archive controls behind `me.role === "owner"`
  (the plan said it did not); the gate is gone and the buttons follow the API.
- `component.learning`: the owner-only paragraph replaced by the 4b shape —
  scopes and the registry (the `for_user(0)` and `user_ids()` rules),
  `ScopedMeta` and the seven byte-identical owner keys, the switch and the live
  smoke, the loop as a router dispatching with `owner_id`, the per-user gate,
  `review_slot.py`, the fork confinement (`workspace=`, `fork_roots`, `cwd=` on
  the write guard, `FORK_DISALLOWED_TOOLS`), the per-user library on both
  channels with `reset_owned`, the curator as one pass per scope with
  `logs_dir=`, `run_all` and the unsuffixed pause, the three curator locks, the
  archived-skill resurrection fix. Evidence extended.
- `component.core-runtime`: the composition root's learning step is
  `build_scopes(...)`, `SchedulerService(deploy_owner=)`, the inline queue's
  second limit, `ctx.workspaces` now covering learning, `curator_loop(ctx.
  learning.scopes, …)`, `learning_review(session_key, kinds, owner_id=0)` and
  the `curator_tick` sweep with its locks, `reset_owned` beside `reset_all`,
  the `SeedManifest` rules in the workspace section (and the `CLAUDE.md`
  exception in the `WIKI_RULES` paragraph), `CurrentUserMiddleware(admitted=)`,
  the path guard and `disallowed_tools` on the forks, the lane-shaped hermes
  tools.
- `component.web`: `admitted` as the third middleware callable and the
  per-request re-check (why: a cookie outlives the decision, and 4b lets a user
  write); `verify_webapp_user` returning the profile; the learning router bullet
  rewritten (per requesting user, no `require_owner`, `reset_owned`, the SPA
  caveat); "The `reset_all` / `reset_owned` rule".
- `system.overview`: the multi-user paragraph — per-user learning behind the
  switch, extensions deploy-wide as a final decision, the group-lane rule.
- `system.operations`: the Redis lock list, the worker row, "drain the queue
  before a multi-container upgrade" (a 4b payload on a 4a worker is a
  `TypeError`), the prefix changes at the 4b deploy (`send_document`'s
  description, three tools gone from group lanes), a "The curator per user"
  section (the switch and its cost, global vs per-user `curator_paused`, the
  four lock keys, `users/<id>/logs/curator/`, a `meta` query), and the
  first-boot manifest note.
- `contract.state-db`: the `meta` row rewritten for suffixed keys; a contract
  note on `ScopedMeta`, the operator's unsuffixed `curator_paused` and the
  per-user lease; `reset_owned` with its NULL belt in the `threads`-vs-`sessions`
  note, the scoping table and the NULL-owner section; `jobs` created with no
  owner now resolve to the deploy owner.
- `quality.invariants`: 1, 7, prompt-cache untouchables 2 (the one deliberate
  `send_document` description change) and 5 updated; a "Wave 4b additions"
  section, 35-39 — tool presence by lane, one review fork deploy-wide, the
  operator's pause stops every pass, a deleted or archived seed is never
  re-seeded, forks confined like a turn plus per-request admission.
- `quality.failure-modes`: the seed-never-updates entry rewritten as fixed with
  the second-boot caveat and the manifest recovery; new entries for the
  archived-skill resurrection (fixed), N concurrent review forks (bounded, what
  the drop looks like), a stale cookie of a de-allowlisted user (401 per
  request), and a 4b payload drained by a 4a worker.
- `flow.message-turn`: the toolset-per-lane sentence now states the group-lane
  rule instead of 4a's `skills=None` for non-owner lanes. `index.md`: the
  learning and multi-user lines.
- Not touched here, for the maintainer: the module READMEs (learning, turns,
  memory, identity, scheduler, sessions, workers), `README.md` and
  `docs/deploy.md` are Phase D's other half.

## [2026-09-18] sync | Step 5 — every shim removed, full import-linter contracts, dead code

- **What changed in the code** (commit 12a3460; the plan is 9e2c7f9; docs
  commit: the docs commit that follows). Deleted: the fifteen legacy top-level modules (`acl config
  db filestore guard hooks logsafe options paths prompt runner sessions tools
  turnbus wiki`) and the five packages (`cron extensions gateway learning web`),
  in one commit — nothing but `main.py` and the tests imported them. Moved: the
  FastAPI assembly and the bundle to `http/{app,deps}.py` (a deliberate fourth
  composition root beside `main.py` and `workers/`; `web/errors.py` folded in,
  `ApiError` gone), `Titler` to `modules/telegram/infrastructure/titler.py`
  over telegram's own `TitleGeneratorPort` / `LaneBindingPort` /
  `ThreadTitleWriterPort`, the attachment downloader to
  `modules/telegram/infrastructure/attachments.py::AiogramDownloader`,
  `safe_name`/`lane_token` to `core/naming.py` (files' `domain/naming.py`
  re-exports — core is the back door between modules for invariants only),
  `logsafe` to `core/logsafe.py`. Dropped: the identity owner mirror
  (`meta.owner_user_id`, `OwnerStore`, `MetaOwnerRepository`) — the `users`
  table is the only owner record, `0005` materialised the row, a stale meta row
  is inert, never downgrade a claimed deploy past `0005`; `WebDeps.{db,
  job_store, ext_store, library, skills_dir, sidecar}`; `get_upload_limit`;
  the commands router's runner fallback (`sdk_commands` only); every
  `routes(deps)`; `LaneWorkspace`'s resolver fallback; `thread_owner`;
  `MemoryBundle.lint`; `CuratorService(learning=)`; learning's
  `PromptCacheInvalidator.reset_all`; extensions' `LegacyOAuthRefresher`,
  `oauth_client.ensure_fresh`, `secret_key.bind`, the repositories' `.engine`.
  `pyproject.toml`'s import-linter section is one layers contract (`main :
  http : workers` > `modules` > `integrations` > `core`) plus independence,
  nine per-module layers and the three purity contracts — 14, and a deliberate
  `modules/turns → modules/sessions/infrastructure` import fails three
  (verified). Tests: 99 legacy-importing files ported to module objects or
  deleted with a named twin per test, `tests/web_helpers.py` over
  `http.deps.WebDeps` and real module objects, an `engine` fixture in
  `tests/conftest.py`; 1585 pass. Live smoke: a claim writes no meta row,
  `/api/skills` and the curator sweep per user, `AiogramDownloader` contains
  `../../etc/passwd` to `42_etc_passwd`, a web turn for user 2 runs in
  `users/2/`. No wire change, no migration, no config change, no prompt-cache
  miss.
- `decision.modules-and-ports`: summary and evidence retargeted (`http/`,
  `core/naming.py`, the moved telegram files, the plan); the shim and
  `routes(deps)` paragraphs rewritten as history; a new "Step 5 (2026-09-18):
  shims removed" section — the fourteen contracts and the colon rule, why
  `http/` is a fourth root, the `core/naming.py` invariants-only rule, the
  mirror removal and the one-way door, what went in the modules, the tests.
- `decision.multi-user-isolation`: the mirror paragraph rewritten as history
  with the one-way door; the debts list split into paid-in-4b, paid-in-Step-5
  (mirror, `AgentRunner` head, `LaneWorkspace` fallback, `thread_owner`, the
  Skills page gate) and the genuine open ones (no per-user quotas, memory routes
  owner-only, Telegram reply re-send after a restart, presenter binding on a
  busy lane, the reap window).
- `contract.state-db`: `db.py` evidence → `core/db/engine.py`; the `meta` row's
  `owner_user_id` marked historical and inert; "every caller goes through a
  repository"; a "`0005` is the only materialiser" paragraph with the
  never-downgrade rule and the recovery `INSERT`.
- `component.core-runtime`: the build order without the facade and the
  mirror (`AccessControl(UserRepository(engine), allowed_users=)`), the memory
  bundle without `lint`, the store section rewritten over `core/db/engine.py`,
  `lane_owner` as the only lane rule, `core/logsafe.py`, the claim paragraph.
- `component.web`: evidence → `http/{app,deps}.py` + the `http/README.md`; the
  app section says why `http/`; `WebDeps` by its new path and contents; the
  commands source is the meta key alone; `LocalFileStore` and `turnbus.py`
  mentions gone.
- `component.gateway`: evidence → the moved `titler.py` / `attachments.py`,
  telegram's ports, `core/naming.py`; the attachments and titler bullets
  rewritten (`AiogramDownloader`, the three Protocols, the smoke); the
  bare-`home` fallback gone.
- `component.cron`, `component.extensions`, `component.learning`: shims as
  history; `JobRepository(engine)`; the cron→topic bridge over
  `MessageRepository.last`; the secret box paragraph over `wiring.build` and
  `OAuthService(wire=http_wire)`; `LearningBundle` vs `WebDeps.learning`;
  `reset_owned` as learning's only invalidation.
- `quality.testing`: the shim test files out of evidence, `test_turns_bus.py`
  and `test_telegram_ports.py` in; the layout paragraph rewritten (ported or
  twinned per test, the counts 89 + 65), the `engine` fixture, the harness over
  `http.deps.WebDeps`. `quality.invariants`: 32 over the table alone; the
  "legacy" wording gone from 6, 38, 39.
- `system.overview`, `system.operations`, `decision.postgres-and-modular-core`,
  `decision.web-as-a-channel`, `decision.minio-blobs`,
  `decision.redis-taskiq-scaling`, `decision.threads-as-session-lanes`,
  `decision.prompt-cache-discipline`, `flow.message-turn`, `index.md`: every
  legacy path renamed to where the code is, the layout block gains `http/`, the
  contract count is 14, the operations rollback note carries the `0005` door.
- Not touched here, for the maintainer: the module READMEs, `README.md` and
  `docs/deploy.md`; fingerprints, `validate` and the receipt.

## [2026-09-22] sync | The wiki index: `wiki_pages`, `memory_search`, the `shared/` namespace

- New page `decision.wiki-index-not-store`: why memory did NOT move into
  PostgreSQL (P2's narrow waist — native `Write`/`Edit` against two tool schemas
  billed per call; NFR-4's auditable plain text; NFR-5, with `pgdata` a named
  volume outside `./data`, so derivability is what keeps a `tar ./data` honest;
  P6's declarative shape), what the index buys (search past the 8000-char index,
  link and backlink hops, per-owner scope as a WHERE clause), and what was
  deferred (pgvector — `postgres:16-alpine` has none; a periodic reindex;
  Graphiti/mem0-style entity extraction, and why it is the wrong altitude for a
  two-user deploy). Linked from `index.md` under Decisions.
- `contract.state-db`: the `wiki_pages` row in the table list and a
  "Migration `0006`" section — PK `(owner_id, path)`, `ON DELETE CASCADE` rather
  than `0005`'s `SET NULL` and why, the generated `'simple'` `content_tsv`, the
  three indexes and the query each answers, no embedding column, and why the
  revision id is `0006_wiki_pages` and not the bare `"0006"` (a duplicate
  identifier kills every Alembic command at script load; a second head is a loud
  `upgrade head` failure). Plus the `meta` key `hermes_tools` and
  `indexed_at` as the one `TIMESTAMPTZ`.
- `component.core-runtime`: a new section on the index and `memory_search` — the
  table as a cache, `WikiIndexService` as its one writer with the
  `workspace.root.name == str(owner_id)` tripwire, the four reindex triggers
  (turn Stop hook, review-fork Stop hook, post-lint `after_lint`, the boot sweep
  in the main process only), the shared namespace, and the boot fingerprint that
  resets every lane. The Stop-hook bullet gained the flush after the gate;
  `shared_wiki_root`/`is_owner` added to the workspace layout;
  `memory_search_enabled` added to the config knobs.
- `decision.multi-user-isolation`: a "One shared wiki namespace" section — where
  it lives and why inside the owner's tree, the double fence over both `shared/`
  spellings, `shared = true` rows written only for the owner's tree, the
  quarter-budget read-only prompt section, and the known gap that nothing *tells*
  the owner the namespace exists (`WIKI_RULES` is frozen; the seeded
  `shared/index.md` is the only carrier and `persona.md` is the follow-up) —
  also recorded under "Still open".
- `decision.prompt-cache-discipline`: a tool is appended, never inserted;
  `sync_hermes_toolset` fingerprints the hermes tool list against
  `meta.hermes_tools` at boot and resets every lane when it moved, which this cut
  trips once; a `memory_search_enabled` flip is a toolset change like any other.
- `component.learning`: the review fork flushes what it wrote under the scope's
  user id and why (it is the wiki's main rewriter), the non-owner fork's shared
  read root, `WRITE_TOOLS` fence and the `shared_readonly` lint sentence, and the
  wet lint's `after_lint` reindex.
- `README.md`: a shared-wiki paragraph under **Multi-user** (what it is, that
  only the owner writes it, how to use it — pages under
  `agent_home/wiki/shared/`, listed in `shared/index.md`) and `memory_search`
  beside the wiki description, with `memory_search_enabled` and the session reset
  a flip costs.
- Not touched here, for the maintainer: `config.yaml.example` has no
  `memory_search_enabled` key yet, and `validate` reports pre-existing
  `stale-evidence` on every page this cut did not re-fingerprint (the branch's
  code moved under them).

## [2026-09-22] sync | Index review findings: per-page failure, `raw/` from both triggers, the real toolset fingerprint

Follow-up to the entry above, over commit `ece95e8`.

- `component.core-runtime`: indexing swallows and logs **per page** before it
  swallows per call, so one note that will not decode does not leave the rest of
  a sweep stale; `read_page`/`_write` read with `errors="replace"` (a
  Windows-1251 note is still a page with a title and links, and a
  `UnicodeDecodeError` is a `ValueError`, not the `OSError` the read guarded
  against); `reindex` converges to `page_paths(wiki_root)` **minus `raw/`**, the
  rule `touch` already applied; `raw_guard_factory` and `page_touch_factory` take
  `cwd=` and `wiki_hooks` passes `wiki_root.parent`, so a relative
  `wiki/topics/x.md` resolves where the CLI lands it rather than where the worker
  runs — without it `state.pages` stays empty and nothing is flushed; the boot
  sweep's placement beside the transports and its race with a mid-sweep delete
  (a resurrected row, corrected at the next lint or boot).
- `contract.state-db`, `decision.wiki-index-not-store`: the same two rules where
  each page states the cache contract — per-page failure, and no row for `raw/`
  from any trigger.
- `decision.prompt-cache-discipline`: `tool_names()` builds its handlers with a
  synthetic private `TurnContext`, because `send_document` and `ask_user` exist
  only with a chat context and a `ctx=None` probe would have fingerprinted a list
  those two are missing from — and quietly stopped watching them.
- `decision.multi-user-isolation`, `component.learning`: the fence matcher is
  `WRITE_TOOLS` on every fork, the lint fork included, and why — under
  `bypassPermissions` a fork's `tools=` list is not a toolset, so only a matcher
  naming `MultiEdit`/`NotebookEdit` fences them.
- `system.operations`: under *What a deploy costs*, a deploy that moves the
  hermes toolset costs every lane one fresh session and says so at INFO —
  the page went stale on `config.yaml.example` alone, and this is the
  operator-facing half of that key.
- Resolved from the entry above: `config.yaml.example` now carries
  `memory_search_enabled` with its cost stated at the key.

## [2026-09-22] sync | Merge: the wiki index migration is `0007`, after per-user connectors

Two branches from `e3820f1` each added a migration after `0005`. At merge the
wiki index (`0006_wiki_pages`, revision id chosen to avoid a duplicate
identifier) became `0007_wiki_pages.py` with `revision = "0007"` and
`down_revision = "0006"`, behind `0006_per_user_extensions`. The two touch
disjoint tables. `contract.state-db`, `component.core-runtime`,
`decision.wiki-index-not-store`, `index` and `tests/test_migrations.py` name
the new number; the earlier entries above describe the id as it was on the
branch.

## [2026-09-24] sync | Per-user shell and learning grants (migration `0009`)

Over commits `10a0d0c`, `62362e4` and `b04d88a` (branch `feat/per-user-grants`).
`users_shell_enabled` / `users_learning_enabled` stop being deploy-wide switches
frozen into `TurnSettings` at boot: `users.shell_enabled` / `learning_enabled`
(nullable, NULL = inherit the config key, which stays as the default) are set per
person from Settings → Users (`PATCH /api/users/{id}`) and resolved by one rule,
`Grants.of`, through `AccessControl.grants` — per turn, per fire, per sweep and
per queued review, in whichever process runs it. `sessions.shell_enabled` stamps
the `has_shell` a session was built with, and the turn resets a lane whose shell
moved at its own start, with a one-line notice.

- `decision.multi-user-isolation`: a new "A shell and learning are grants, per
  person (0009)" section (the columns, the one rule, the three consumer ports,
  owner lanes never ask, the lazy reset and the NULL-stamp rule, the PATCH route's
  refusals, group lanes stay the owner's and the UI says so); every place that
  named `users_shell_enabled` / `users_learning_enabled` as the switch; Costs (the
  prefix is byte-stable per *session*; the `0009` rolling-deploy caveat); a
  "Paid with `0009`" debt; the summary shortened under the limit.
- `decision.model-choice-is-lane-state`: non-owners' grants versus the shared
  model offer; the accepted limit that a shell reset keeps the model the old
  session ran (`_effective` resolves before `route()`).
- `component.core-runtime`: the composition (`AccessControl(shell_default=,
  learning_default=)`, `SdkJobExecutor(grants=acl)`, `build_scopes(learning_of=)`,
  `TurnService(grants=acl)`), `learning_review`'s run-time grant check, the sweep
  over granted users, `_run_turn_body`'s grants and lazy reset, `set_grants` /
  `grants` as the ACL's third writer and its reader, and `disallowed_tools` from
  `ctx.shell_enabled`.
- `component.learning`: `user_ids()` per grant with a per-id failure left out,
  `observes()`, the observer gate on `ctx.learning_enabled`, the queued review
  that releases its gate when the grant was withdrawn.
- `contract.state-db`: the three columns and a `0009` section.
- `system.operations`: a third drain-the-queue reason (an old worker ignores the
  grants), admitting a second user from Settings → Users, the curator sweep per
  grant.
- `flow.message-turn` (re-verified whole from `ab28496`): grants resolved in the
  envelope, `has_shell`, the lazy reset and its notice, the stamp on
  `bind`/`touch`, the observer gate; also fixed what had drifted — intake is
  `IntakeService.intake`, the `/model`/`/effort` interception on both channels,
  which pre-body failures get an envelope (attachments only), composition per
  lane owner, the shared-wiki root and fence, the Stop-hook index flush, cost
  recorded by the executor, `voice_reply_enabled`.
- `system.overview` (re-verified whole from `12a3460`): the grants in Settings →
  Users with the config keys as defaults; also the seven-service stack, queue mode
  by default, `memory_search`, per-user connectors, `reset_owned` vs `reset_all`,
  Caddy in production, `scripts/deploy.sh`, the popped secrets.
- `component.web` (re-verified whole from `b03137a`): `PATCH /api/users/{id}` and
  the member wire's four fields, the Bash/Learning switches (`ui/switch.tsx`, the
  "default" marker, disabled config rows, the Bash confirmation, the legend and
  the group-chat note), `settings.spec.ts`'s grants walk; also the users router's
  mount order, per-user connector and plugin routes and resets, the owner's own
  secrets list, the caller's own `/api/commands`, `create_or_raise`, the
  Customize tabs' real gates, eleven store slices, the context meter and the
  model pill.
- Operator docs and READMEs (not wiki pages, but evidence here):
  `config.yaml.example`, `README.md`, `docs/deploy.md` (§3, §7 and an upgrade note
  for `0009`), `skills/gpt-image-2/SKILL.md`, and the identity, sessions, turns,
  scheduler, learning, memory and `workers/` READMEs describe the grants with the
  two config keys as defaults. `README.md` also stops saying extensions and
  secrets are the owner's alone: connectors have been per person since `0006`;
  only catalog entries and plugin clones are the owner's.
- Follow-up: re-verified and stamped `component.extensions` and
  `decision.wiki-index-not-store` (unchanged — still true);
  `decision.prompt-cache-discipline` (the per-session shell stamp and lazy reset
  as the P1 mechanism for a grant change, the narrow NULL-stamp gap, and browser
  mutations resetting the affected owners' lanes rather than every lane);
  `component.cron` (re-verified whole — a non-owner routine's shell is its owner's
  grant at fire time, failing closed); `component.web` (`GET /api/users` lists the
  first 100 rows); and re-stamped the pages whose evidence moved only by the
  comment edits in `core/config.py` and `AccessControl.list_users`. Still stale,
  untouched: the 15 pages whose evidence had already moved before this branch.

## [2026-09-24] sync | Imported rows had no owner; blobs moved into MinIO on the production deployment

- `contract.state-db`: `scripts/import_sqlite.py` now finishes with migration
  `0005`'s attribution (imported from the migration module, NULLs only). Without
  it every row copied from `state.db` kept `owner_id` NULL, because `upgrade head`
  runs 0005 on the empty target; the user-scoped reads (Routines, message search,
  cost report) silently left them out. `tests/test_import_sqlite.py` gains the
  regression test, and its group-thread expectation moves from `None` to the
  deploy owner — D3's `lane_owner`, which 0005 already applied.
- `system.operations`: the production repair (dump first, then 0005's statements
  `WHERE owner_id IS NULL`: 17 jobs, 1144 messages, 304 turn costs, 19 files,
  5 threads, 1 draft) and a new section, *Blobs are in MinIO*: `blob_backend: s3`
  on the host, the bucket mirrored from `agent_home/{inbox,outbox}`, every `files`
  row rewritten to `s3://hermes/<key>` and read back byte-for-byte, and what does
  and does not reach the bucket.
- `decision.minio-blobs`: the mirror root is the row owner's `agent_home` through a
  `RootLookup` since Wave 4a (the page still said one frozen `blob_root(home)`);
  the one-shot upload is done on this host; a `local:` row is never read from the
  bucket, so an upload without the rewrite protects nothing; only `FileService`
  traffic reaches the bucket.
- Still stale, untouched: the other 15 pages whose evidence moved before this
  branch.

## [2026-09-28] sync | The Calendar and the content plan

- New `component.content` and `flow.content-publish`: the tenth module
  (`modules/content`, migration `0010`), the owner's approval bound to body and
  media hashes, `content_plan` in chat (deploy owner's private/web lanes, after
  `memory_search`) and lent to the deploy owner's routines, the tick that queues
  due items and wakes the publisher through `wake_if_idle`, the owner's retry.
- `component.cron`, `flow.scheduled-job`: the `manual` kind (`STAY_IDLE` in the
  claim), `wake_if_idle`, the `RoutineTools` port merged after the composed
  extensions, `GET /api/routines/calendar`, the content tick on both clocks, the
  late-bound `SchedulerPublisher`.
- `component.web`: the content router and its mounting condition, the calendar
  route, the Calendar/Tasks pages, the `/calendar` URLs with `/routines`
  redirects, the store's twelfth slice. The story wrote route errors as
  `{"detail": …}`; every router answers `{"error": {"code", "message"}}` and the
  page says so.
- `contract.state-db` (`content_items`, `meta.content_publisher_job_id`, `jobs.kind`
  `manual`), `quality.invariants` (40-47), `quality.failure-modes`,
  `quality.testing`, `decision.prompt-cache-discipline` (the new tool is appended
  and is a lane fact; `cron_manage`'s bytes untouched), `decision.modules-and-ports`,
  `decision.postgres-and-modular-core`, `system.md` (ten modules, fifteen
  contracts), `decision.multi-user-isolation`, `decision.redis-taskiq-scaling`,
  `component.core-runtime` (config keys `timezone`/`content_enabled`/
  `content_channels`, `ctx.content`, the third late-bound edge),
  `flow.message-turn`, `system.operations`, `glossary` (four terms; it had been
  stale before this branch).
- Re-verified and re-stamped, prose unchanged: `component.extensions`,
  `component.gateway`, `component.learning`, `decision.claude-agent-sdk`,
  `decision.minio-blobs`, `decision.model-choice-is-lane-state`,
  `decision.web-as-a-channel`, `decision.wiki-index-not-store`,
  `flow.extension-install` — their evidence moved only by this change's additions
  (the content wiring in `main.py`, `app_context.py`, `config.py`, `http/`, the
  turns and scheduler ports, `tzdata` in `pyproject.toml`).
- Operator doc: `docs/content-calendar.md`; bundled skill
  `skills/content-publish/SKILL.md`.

## [2026-09-28] sync | Content plan v2: theme approval, the drafter, Telegram approval cards

- `component.content`, `flow.content-publish` rewritten for v2: `planned` and
  `drafting`, theme approval (single and per week), the approval gate
  (`empty_body`, `media_required` by format, the empty-body tick guard), the
  drafter routine (one item per run, its wake budget, the drafting timeout, three
  attempts), the card outbox (one card per tick, the «Контент» topic behind a
  `meta_swap` reservation, flat fallback, topic re-create), the card's revision
  approve and reply redraft, superseding on every content write, missed posts.
- `component.gateway`: a new "Content approval cards" section — the sender, the
  `cnt:` callbacks (owner first, stale answer), reply interception before intake,
  titling and routing, the untitled card topic; three new evidence files.
- `decision.prompt-cache-discipline`, `contract.state-db` (`meta.hermes_tools`),
  `component.core-runtime`, `system.operations`, `index`: the boot fingerprint is
  now a sha256 over every hermes tool's name, description and schema
  (`SdkTurnExecutor.tool_fingerprint()` over `hermes_server.hermes_tools`), so a
  reworded built-in description resets every lane once instead of slipping by.
- `contract.state-db`: migration `0011` (thirteen columns, no backfill), the two
  new statuses, `meta.content_drafter_job_id` and `meta.content_card_topic`.
- `quality.invariants` 48-53 (theme approval schedules nothing, one gate, the
  button approves its revision only, supersede in the same statement, one drafter
  item / card / topic, a card reply is never a turn); 40 and 47 amended.
- `component.core-runtime` (`content_draft_lead_hours`, one `SchedulerPublisher`
  for both ports, `ctx.content_cards` bound in `build_delivery` in every process,
  `ctx.content_decisions`), `component.cron`, `component.web` (v2 routes, dialog,
  drafter picker, `?item=` deep link), `decision.modules-and-ports`,
  `decision.multi-user-isolation`, `decision.redis-taskiq-scaling`,
  `decision.threads-as-session-lanes`, `flow.message-turn`,
  `quality.failure-modes`, `glossary` (theme approval, approval card, drafter).
- Re-verified and re-stamped, prose unchanged: `component.extensions`,
  `component.learning`, `decision.claude-agent-sdk`, `decision.minio-blobs`,
  `decision.model-choice-is-lane-state`, `decision.postgres-and-modular-core`,
  `decision.readonly-catalog-boundary`, `decision.wiki-index-not-store`,
  `flow.extension-install`, `system.md` — their evidence moved only by this
  change's wiring (`main.py`, `app_context.py`, `config.py`, `config.yaml.example`,
  `README.md`, the turns fingerprint helper).
- Operator doc: `docs/content-calendar.md` (drafter setup, the «Контент» topic and
  Threaded Mode, HyperFrames on the volume, no killing of an orphaned render);
  bundled skill `skills/content-draft/SKILL.md`.

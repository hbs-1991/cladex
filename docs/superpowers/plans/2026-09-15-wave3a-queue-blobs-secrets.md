# Wave 3a — Redis + taskiq workers, claim-based scheduler, MinIO blobs, secrets/drafts in PostgreSQL

Status: plan, 2026-09-15. Follows Wave 2b (2720682, 734b430). Spec:
`docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`
req. 2, 3, 15, 19, 20, 21, 22, 23, 26, 33, 37, 38.

Step 3 of the spec is split in two shippable waves. **3a** (this plan) builds the
queue, the worker process, MinIO, the PostgreSQL-backed extension state and the
test stack, and moves *scheduled jobs, learning reviews and the curator* onto the
queue. **3b** moves *turns* off the request path (turns table with lease, Redis
event bus + in-flight snapshot, cross-process ask_user and cancel, presenters
subscribing to Redis) — req. 16, 17, 18. Nothing in 3a changes how a turn runs.

## Goal

After 3a a deployment is `db + redis + minio + agent + worker + scheduler`:
- `agent` (`python -m claude_hermes.main`) serves HTTP, polls Telegram, runs
  turns in-process (until 3b) and *enqueues* everything else.
- `worker` (`taskiq worker claude_hermes.workers.app:broker claude_hermes.workers.tasks`)
  runs scheduled jobs, learning reviews and curator passes; N of them are safe.
- `scheduler` (`taskiq scheduler claude_hermes.workers.app:scheduler`) is a
  stateless enqueuer of two periodic tasks; a duplicate is harmless because
  claiming is idempotent.
- Uploads and agent-produced files live in a MinIO bucket behind the `files`
  blob port; the on-disk `inbox/`/`outbox/` layout under `agent_home` becomes a
  local materialization cache with the same keys.
- Extension secrets (Fernet-encrypted), catalog drafts and approval snapshots are
  PostgreSQL tables. No `secrets.env`, `catalog-drafts/`, `pending-approvals/`.
- A laptop run with no Redis/MinIO still works: `queue_mode: inline` runs tasks
  in-process through taskiq's `InMemoryBroker`, `blob_backend: local` keeps the
  filesystem store. Tests exercise both.

## Locked Decisions

### Queue
- **taskiq** with `taskiq-redis` (`ListQueueBroker` + `RedisAsyncResultBackend`).
  `core/tasks.py` builds the broker from config: `CH_REDIS_URL` set →
  Redis broker; unset → `InMemoryBroker()` (`queue_mode` is derived, never a
  second switch). `workers/app.py` instantiates `broker` and `scheduler =
  TaskiqScheduler(broker, [LabelScheduleSource(broker)])` at import time from
  the environment (taskiq needs the object at decoration time). `main.py` and the
  worker import the same module, so both sides share queue names.
- **Task arguments are primitives**: `job_id: int`, `fire_at: str`,
  `session_key: str`, `kinds: list[str]`. Never a service or a dataclass.
- **Worker startup** (`@broker.on_event(WORKER_STARTUP)`) calls
  `main.build_app()` (headless), builds the Telegram delivery objects it needs
  from the token (`Bot`, `BotApiClient`, `RichSender`, `TopicManager`), binds
  them, then pops every secret from `os.environ` exactly as `main.run` does
  (`TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, and the new `CH_SECRETS_KEY`,
  `CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL` — see Security). The
  shared helper `main.build_delivery(ctx, token) -> Delivery` is extracted so
  `run()` and the worker build the same objects. Services hang off
  `state.ctx`; tasks read them from `Context`/`TaskiqDepends`.
- **Tasks** (`workers/tasks.py`): `scheduler_tick` (periodic, cron `* * * * *`),
  `run_job(job_id, fire_at)`, `learning_review(session_key, kinds)`,
  `curator_tick` (periodic, cron `*/15 * * * *`). Periodic labels are attached
  only when `queue_mode == "redis"`; in inline mode `main.run` keeps today's
  `CronTicker.run_forever` (calling the same `tick`) and `curator_loop`.
- **`run_now` latency** becomes up to 60 s in redis mode (was `cron_tick_seconds`,
  30 s). `cron_tick_seconds` stays as the inline-mode period. Documented in
  README and the decision page.

### Scheduler claiming (req. 19)
- Migration `0003`: `jobs.claimed_at TEXT NULL`, `jobs.lease_until TEXT NULL`,
  plus the three extension tables below.
- `JobRepository.claim_due(now_iso, next_of, limit=100) -> list[dict]` is ONE
  transaction: `SELECT … WHERE state='active' AND next_run_at <= :now AND
  (lease_until IS NULL OR lease_until < :now) ORDER BY next_run_at LIMIT :n FOR
  UPDATE SKIP LOCKED`, then per row `UPDATE jobs SET next_run_at = next_of(row),
  claimed_at = :now, lease_until = :now + timeout` and return the rows with an
  extra `fire_at` key (the pre-update `next_run_at`). `next_of` is a callable
  the service passes (domain `next_fire` over the row's schedule) so the
  reschedule happens inside the claim — that is the at-most-once guarantee
  across processes. `reschedule()` stays for `edit`/`run_now`.
- `SchedulerService.tick(now)`: claim, then per job either `record_run(…,
  "missed")` (catch-up window unchanged) or `await self._dispatch(job["id"],
  job["fire_at"])` through a new `JobDispatcher` port
  (`dispatch(job_id: int, fire_at: str) -> None`). Inline impl awaits
  `execute_job` directly; redis impl `kiq`s `run_job`.
- `SchedulerService.execute_job(job_id, fire_at, now)`: load the row (404 →
  log and return), `executor.run`, `record_run`, `_emit_delivery`, `deliverer`,
  then `jobs.clear_lease(job_id)`. A lease that expires mid-run is not
  re-claimed (the job's `next_run_at` already moved); the lease only guards
  the window between claim and the worker picking the task up.
- `tests/modules/scheduler/test_scheduler_claiming.py`: two concurrent
  `claim_due` calls on the same engine return disjoint sets; a claimed job is
  not returned again until its lease expires; `run_now` still fires next tick.

### Learning on the queue
- `LearningLoop.after_turn` calls a `ReviewDispatcher` port
  (`dispatch(session_key, kinds)`); inline impl runs `ReviewService.run` in an
  `asyncio.Task` as today, redis impl `kiq`s `learning_review`.
- `review_running` (read by the curator gate and `TurnService`) becomes a
  `ReviewGate` over `core.meta.MetaStore`: key `learning_review_until` (ISO,
  set to now + `review_timeout_seconds` when a review starts, cleared when it
  ends). Works in both modes and survives a dead worker. Meta key added to the
  do-not-rename list.
- `curator_tick` task: `SET NX PX` Redis lock `lock:curator` (TTL =
  `curator_max_turns * 60 s`) around `CuratorService.run_pass()`; inline mode
  keeps `workers/curator.py`'s loop. The pass's own interval/idle gates are
  unchanged.

### MinIO (req. 23, 26)
- `integrations/s3/client.py`: `S3Client(endpoint, access_key, secret_key,
  bucket, region="us-east-1")` over **aiobotocore** — `put_object(key, data)`,
  `get_object(key) -> bytes`, `upload_file(key, path)`, `download_file(key,
  path)`, `ensure_bucket()`, `delete_object(key)`, `list_prefix(prefix)`. One
  session per process, created lazily; `aclose()` on shutdown.
- `files` `BlobStore` port becomes **async** and keeps its three methods plus
  `open(stored) -> bytes`. Stored addresses: `local:<key>` for the filesystem
  store, `s3://<bucket>/<key>` for MinIO. Keys are exactly today's
  `inbox/<lane>/<name>` / `outbox/<lane>/<name>` (`lane_token` unchanged).
- `S3BlobStore(client, cache_root)`: `put` uploads then writes the local mirror
  at `cache_root/<key>`; `copy_in` uploads and copies; `local_path(stored)`
  returns the mirror, downloading it first if absent; the containment check
  (`cache_root` in `parents`) is kept verbatim — it is the security control
  that catches a forged row. `cache_root` = `blob_root(home)` so the agent's
  own `Read`/`Glob` and the prompts that name `inbox/…` paths keep working.
- `FileService` methods that touch the store become async (`resolve`,
  `attachments_for`, `publish`, `put_upload`); consumers updated: files router
  (download still `FileResponse` from `local_path`), turns `send_document`
  tool + `FilePublisher.resolve` (async), telegram `TelegramDocuments`, the
  telegram intake (`LaneWorkspace`) which today writes straight into `inbox/`
  — it now goes through `FileService.put_upload` so a Telegram attachment is
  in the bucket too.
- Config: `blob_backend: local|s3` (default `local`), `s3_endpoint`,
  `s3_bucket` (default `hermes`), `s3_region`; `CH_S3_ACCESS_KEY` /
  `CH_S3_SECRET_KEY` env only. `main.build_app` calls `ensure_bucket()` at boot
  in s3 mode.
- Rows written before 3a hold absolute paths; `FileService.resolve` treats a
  stored value without a scheme as `local:` (compat, documented; Step 5 adds a
  one-shot upload of the old outbox into the bucket).

### Extension state in PostgreSQL (req. 22)
- Tables (migration `0003`): `extension_secrets(name TEXT PK, ciphertext BYTEA,
  updated_at TEXT)`, `catalog_drafts(kind TEXT, name TEXT, raw TEXT, status
  TEXT 'pending'|'rejected', reason TEXT NULL, created_at TEXT, PK(kind, name))`,
  `approval_snapshots(kind TEXT, name TEXT, raw BYTEA, created_at TEXT,
  PK(kind, name))`. ORM models in `modules/extensions/domain/models.py`.
- **Encryption**: Fernet (`cryptography`) with `CH_SECRETS_KEY` (env only,
  required at boot; `.env.example` shows `python -c "from cryptography.fernet
  import Fernet; print(Fernet.generate_key().decode())"`). `core/crypto.py`
  holds `SecretBox(key)` with `seal(str) -> bytes` / `open(bytes) -> str`.
- Ports become **async** and lose their `home` parameter: `SecretStore.load /
  put / forget`, `DraftStore.save(raw) -> Manifest / pending() / rejected() /
  read(kind, name) -> str / delete(kind, name)`, `SnapshotStore.write / read /
  clear`. `EnvFileSecrets`, `draft_store.py`, `snapshot_store.py` are replaced
  by `PgSecrets`, `PgDrafts`, `PgSnapshots` in
  `modules/extensions/infrastructure/repositories/`. The domain validation a
  draft goes through (`drafts.save` → catalog validator, shell-string args
  refused, YAML errors) moves unchanged into `PgDrafts.save`; `describe`,
  `safe`, `DraftError` stay in domain.
- Services (`ComposeService`, `OAuthService`, `ExtensionService`,
  `AdminService`, `ApprovalService`) and telegram's `CommandService` ports
  await the stores. `_refresh_locks` becomes a `core.redis.Lock` in redis mode
  (`lock:oauth:<name>`) and an `asyncio.Lock` inline. `PendingStates` (OAuth
  flow state) moves to Redis with a 10-minute TTL in redis mode; the in-memory
  class stays for inline.
- **Operator paths.** `python -m claude_hermes.main drafts list|show <name>|
  delete <name>` are new subcommands of the composition root (no new top-level
  package). `scripts/approve_draft.py` keeps running on the HOST and keeps
  writing `catalog/` on the host — that is the security model — but reads the
  draft through `docker compose exec -T agent python -m claude_hermes.main
  drafts show <name>` (override with `CH_DRAFTS_CMD` for a non-compose host).
  `scripts/import_data_dir.py` (new, one-shot) imports an existing
  `secrets.env`, `catalog-drafts/` and `pending-approvals/` into the tables;
  `docs/deploy.md` §4b documents it next to the SQLite import.
- Tests: `tests/test_drafts.py`, `tests/test_secrets.py`, `tests/test_web_drafts.py`,
  `tests/test_compose.py`, `tests/test_manage.py`, `tests/test_oauth.py`,
  `tests/test_ext_manage.py`, `tests/modules/extensions/*` move from `tmp_path`
  files to the DB-backed stores (they already have the per-process database);
  every assertion about *behaviour* (validator parity, shadow refusal, token
  bound to bytes, rejected drafts listed, owner-only) is kept.

### Process topology, compose, tests (req. 20, 21, 37)
- `docker-compose.yml`: `db`, `redis` (`redis:7-alpine`, `--appendonly yes`,
  volume), `minio` (`minio/minio`, `server /data --console-address :9001`,
  volume, healthcheck on `/minio/health/live`), `agent` (as today), `worker`
  and `scheduler` (same image, different `command`), `cloudflared`. `agent` and
  `worker` share `./data:/data` and `./catalog:/app/catalog:ro`. `scheduler`
  needs only Redis. Env: `CH_REDIS_URL=redis://redis:6379/0`,
  `CH_S3_ENDPOINT=http://minio:9000`, `CH_S3_ACCESS_KEY=${MINIO_ROOT_USER}`,
  `CH_S3_SECRET_KEY=${MINIO_ROOT_PASSWORD}`, `CH_SECRETS_KEY`. `.env.example`
  gains `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `CH_SECRETS_KEY`.
- `docker-compose.prod.yml`: logging + limits for `worker` (same ceilings as
  `agent`: it spawns the CLI too), healthchecks for redis/minio.
- **Only `agent` polls Telegram.** The worker never calls `run_polling`.
- Backups (`scripts/deploy.sh`, `docs/deploy.md`): `pg_dump` + `mc mirror` of
  the bucket through the `minio/mc` image + tar of `data/` (workspace) with the
  agent and worker stopped. `core/snapshots` (skills/wiki backups) is unchanged:
  they live on the workspace volume.
- **Test stack**: `make test-stack` starts `hermes-test-pg` (55432),
  `hermes-test-redis` (127.0.0.1:56379) and `hermes-test-minio`
  (127.0.0.1:59000/59001); `make test-db` stays as an alias for the DB only.
  `tests/conftest.py` adds session fixtures `redis_url` (flushes db 15 before
  each test that uses it) and `s3_client` (one bucket per pytest process,
  deleted at the end); both `pytest.exit` with a "run `make test-stack`"
  message when unreachable, like the DB fixture. CI adds `redis:7-alpine` and
  `minio/minio` services. Unit tests default to inline mode and the local blob
  store; the Redis/MinIO fixtures are used by the claiming, blob-store, lock and
  broker round-trip tests only.

### Security invariants (new, alongside the Wave 2 list)
- Every `CH_*` secret and connection string is popped from `os.environ` before
  anything can spawn the CLI, in `main.run` AND in the worker startup:
  `CH_SECRETS_KEY`, `CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL` (the
  engine already holds the URL). `tests/test_main.py` gets the same
  source-order assertion it has for the bot token, for each name.
- The agent's Bash inherits nothing that can read the secrets table; the catalog
  `:ro` mount stays the outer boundary; `scripts/approve_draft.py` still writes
  `catalog/` only from the host.
- `S3BlobStore.local_path` containment check verbatim; `FileService` remains
  the only writer of the `files` table.

## Global Constraints

- Checks: `.venv/Scripts/python.exe -m pytest <files> -q -p no:cacheprovider`
  (stack via `make test-stack`), `ruff check src tests scripts`,
  `.venv/Scripts/lint-imports` (16 contracts + the new `integrations.s3`
  falls under "integrations depend only on core"; `workers` may import `main`
  and `core` and module *application* services through `AppContext` only).
- New deps in `pyproject.toml`: `taskiq`, `taskiq-redis`, `redis>=5`,
  `aiobotocore`, `cryptography`. Pin to the versions pip resolves at
  implementation time.
- `tests/test_web_*.py` unchanged. Prompt-cache invariants untouched (this
  wave changes no tool description, no prompt text, no `allowed_tools`).
- No `print()` outside `main.py`'s `drafts` subcommands and `scripts/`; no
  `logging.getLogger`; structlog only.
- Every touched module README updated (ports, tables, debts paid/added);
  `docs/llm-wiki/` pages fingerprinted and validated through the container.

## Phases

### Phase A — Foundation (serial, one agent)
Owns: `pyproject.toml` deps; `core/config.py` (new keys: `redis_url`,
`blob_backend`, `s3_endpoint`, `s3_bucket`, `s3_region`, `s3_access_key`,
`s3_secret_key`, `secrets_key`, `review_timeout_seconds`; `queue_mode`
property); `core/redis.py` (client factory + `Lock`); `core/crypto.py`;
`core/tasks.py`; `workers/app.py`; `workers/tasks.py` (skeleton: startup event
building `AppContext`, the four task functions calling ports that Phase B
fills — bodies may be `NotImplementedError` behind a clear name); `integrations/s3/`;
migration `0003`; `modules/extensions/domain/models.py` (three models) and
`modules/scheduler/domain/models.py` (two columns); `Makefile` (`test-stack`),
`tests/conftest.py` fixtures, CI services; compose files, `.env.example`,
`docker-compose.prod.yml`; `tests/test_core_redis.py`, `tests/test_core_crypto.py`,
`tests/test_integrations_s3.py`, `tests/test_migrations.py` (0003).
Verification: full suite, ruff, lint-imports, `make test-stack` up.

### Phase B — Streams (parallel, disjoint)
- **B1 scheduler**: repository `claim_due`/`clear_lease`, `JobDispatcher` port
  + inline/redis impls (`modules/scheduler/infrastructure/dispatch.py`),
  `SchedulerService.tick`/`execute_job`, `workers/tasks.py::scheduler_tick`
  + `run_job` bodies, `CronTicker` unchanged, README, tests (claiming,
  dispatcher contract, `test_cron_ticker.py` parity).
- **B2 learning**: `ReviewDispatcher` + `ReviewGate` ports and impls,
  `LearningLoop`, `CuratorService` gate read, `workers/tasks.py::learning_review`
  + `curator_tick` bodies, `workers/curator.py` (inline loop stays), README,
  tests (`test_learning_loop.py`, `test_curator_pass.py` retargeted to the
  gate; new `test_learning_queue.py`).
- **B3 files**: async `BlobStore`, `LocalBlobStore`, `S3BlobStore`,
  `FileService`, files router, turns `send_document` handler +
  `FilePublisher` port, telegram `TelegramDocuments` + `LaneWorkspace` intake
  through the service, READMEs, tests (`test_filestore.py`,
  `test_send_document.py`, `test_web_files.py` unchanged behaviour,
  `tests/modules/files/test_files_s3.py` against MinIO).
- **B4 extensions**: `PgSecrets`/`PgDrafts`/`PgSnapshots`, async ports and
  services, Redis-backed `PendingStates` + oauth lock, telegram
  `CommandService` port, `main.py drafts` subcommands (a `cli.py` helper inside
  `modules/extensions/presentation/` that takes the services), `scripts/approve_draft.py`,
  `scripts/import_data_dir.py`, READMEs, tests listed above.

### Phase C — Compose (serial)
`main.py` (`build_delivery`, queue-mode wiring of the dispatchers and gate,
blob backend selection, `ensure_bucket`, secret pops, `drafts` subcommands
entry), `workers/tasks.py` startup wiring, `core/app_context.py` new fields,
`tests/test_main.py` (pops), `docs/deploy.md` §4b + backups, `scripts/deploy.sh`.
Verification: full suite in inline mode, then the Redis/MinIO tests, ruff,
lint-imports; `docker compose config` validates; a smoke `taskiq worker
--help` import of `claude_hermes.workers.tasks` succeeds.

### Phase D — Advisor (read-only), E — Fix, F — Docs & Gate
Advisor: at-most-once proof for `claim_due` (transaction shape), secrets never
reach a subprocess env, `local_path` containment, `approve_draft.py` still
host-only for `catalog/`, inline mode parity with HEAD behaviour, no
prompt-cache text changed, task args primitive, both compose files valid,
tests green on the stack.
Docs & Gate: wiki `decisions/redis-taskiq-scaling.md` (topology, what is still a
singleton and why — polling Telegram client, the stateless scheduler process),
`decisions/minio-blobs.md`, `contracts/state-db.md` (0003 tables), `operations.md`
(services, backups, `make test-stack`), `components/*` re-anchored,
`quality/invariants.md` (env pops), `index.md`, `log.md`; `README.md` install
section (Redis + MinIO + `CH_SECRETS_KEY`); fingerprint + validate; full gate.

## Open Questions (defaulted)
1. Scheduler cadence in redis mode: 60 s cron vs a 30 s loop inside the tick
   task. **Default: 60 s**, documented.
2. S3 client library: aiobotocore vs minio-py in a thread. **Default:
   aiobotocore** (async, S3-generic).
3. Where the worker's Telegram delivery comes from: full `Bot` vs `BotApiClient`
   only. **Default: both, via the shared `build_delivery`** — `TelegramDocuments`
   needs aiogram's `FSInputFile`.

## Offline amendment (2026-09-16)

PyPI, GitHub and Docker Hub are unreachable from the build machine while this
wave is implemented. What is available: `redis==6.4.0`, `fakeredis`, `boto3`,
`cryptography` (installed offline from the uv cache), the Docker images
`redis:7-alpine` and `minio/minio` (cached), the test PostgreSQL. **Not**
available: `taskiq`, `taskiq-redis`, `aiobotocore`. The plan changes as follows;
everything else stands.

- **Queue abstraction is ours; taskiq is one backend, isolated.** `core/tasks.py`
  defines `TaskRegistry` (`@registry.task("run_job")` registers an async callable
  taking primitive kwargs) and the `TaskQueue` Protocol (`async enqueue(name,
  **kwargs) -> None`). Two implementations: `InlineQueue(registry)` runs the
  callable as an `asyncio.Task` in-process (used when `CH_REDIS_URL` is unset and
  by every test), and `TaskiqQueue` in `workers/taskiq_app.py`, which binds the
  registry to a `taskiq-redis` `ListQueueBroker` (`broker.register_task(fn,
  task_name=name)`) and enqueues with `.kiq(**kwargs)`. `workers/taskiq_app.py`
  is the ONLY file that imports taskiq, it imports lazily inside functions, and no
  test imports it. It carries a module docstring stating it is unverified until
  the dependency can be installed, and `tests/test_workers_taskiq.py` is written
  but marked `pytest.importorskip("taskiq")`. Periodic tasks (`scheduler_tick`,
  `curator_tick`) are taskiq labels in redis mode and `CronTicker`/`curator_loop`
  in inline mode, as planned.
- **Dispatcher ports call the queue.** `JobDispatcher`, `ReviewDispatcher` and
  the curator kick are all `queue.enqueue("<task>", ...)`; the inline queue makes
  them behave exactly like today's `asyncio.create_task`.
- **S3 client uses boto3 in a thread**, not aiobotocore: `integrations/s3/client.py`
  wraps a `boto3.client("s3", endpoint_url=…)` and runs every call through
  `asyncio.to_thread`. Open Question 2 is settled the other way. The interface
  in the plan (`put_object`, `get_object`, `upload_file`, `download_file`,
  `ensure_bucket`, `delete_object`, `list_prefix`, `aclose`) is unchanged.
- **Redis client**: `redis.asyncio` (redis-py 6.x); `core/redis.py::Lock` is a
  `SET NX PX` + Lua-release lock written by us (redis-py's `Lock` is fine too —
  implementer's choice, documented). Unit tests use `fakeredis.aioredis`;
  `tests/test_core_redis.py` also runs against the real container when
  `CH_TEST_REDIS_URL` is reachable.
- **pyproject**: add `redis>=6.4`, `boto3>=1.40`, `cryptography>=48`,
  `taskiq>=0.11`, `taskiq-redis>=1.0`; dev: `fakeredis`. `pip install -e .`
  cannot run offline, so the implementer edits `pyproject.toml` without
  reinstalling; the venv already has every importable package. The Dockerfile
  is unchanged (it installs from pyproject at build time, online).
- **Test stack**: `make test-stack` starts the three containers from the cached
  images. Fixtures in `tests/conftest.py`: `redis_url` (`CH_TEST_REDIS_URL`,
  default `redis://127.0.0.1:56379/15`, FLUSHDB before each use) and `s3` (a
  boto3 client to `CH_TEST_S3_ENDPOINT`, default `http://127.0.0.1:59000`,
  credentials `hermes`/`hermes-test`, one bucket per pytest process). Both skip
  their tests with a clear reason when unreachable instead of exiting the run,
  so the suite stays green on a laptop without the stack.
- **Follow-up when the network returns** (recorded as a debt in
  `workers/README.md`): `pip install -e ".[dev]"`, run
  `tests/test_workers_taskiq.py` and a manual `taskiq worker
  claude_hermes.workers.taskiq_app:broker` smoke against the redis container,
  `docker compose config`, then a `docker compose build`.

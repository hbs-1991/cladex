---
id: quality.testing
kind: quality
status: current
summary: Unit tests without network or a live SDK, against a real PostgreSQL (and an optional Redis + MinIO) — fakes for query(), pytest-asyncio, a database and a bucket per pytest process, inline queue mode everywhere, and FastAPI dependency_overrides to swap one collaborator on a real app.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:8df00b4baf75e95e9e946cec23ef668bf45faf5dc1276718705d78e07b74eccb
evidence:
  - path: pyproject.toml
  - path: Makefile
  - path: tests/conftest.py
  - path: tests/test_migrations.py
  - path: tests/test_import_sqlite.py
  - path: tests/test_import_data_dir.py
  - path: tests/test_core_tasks.py
  - path: tests/test_core_redis.py
  - path: tests/test_core_crypto.py
  - path: tests/test_integrations_s3.py
  - path: tests/test_workers_tasks.py
  - path: tests/test_workers_taskiq.py
  - path: tests/modules/scheduler/test_scheduler_claiming.py
  - path: tests/modules/files/test_files_s3.py
  - path: tests/modules/learning/test_learning_queue.py
  - path: tests/modules/extensions/test_extensions_redis_state.py
  - path: tests/test_core_eventbus.py
  - path: tests/modules/turns/test_turns_repository.py
  - path: tests/modules/turns/test_turns_snapshots.py
  - path: tests/modules/turns/test_turns_launcher.py
  - path: tests/modules/turns/test_turns_execution.py
  - path: tests/modules/turns/test_turns_control.py
  - path: tests/modules/turns/test_turns_dispatch.py
  - path: tests/modules/turns/test_turns_redis_ask_broker.py
  - path: tests/modules/turns/test_turns_two_processes.py
  - path: tests/test_web_turns.py
  - path: tests/ext_helpers.py
  - path: tests/test_core_logging.py
  - path: tests/test_gateway.py
  - path: tests/test_catalog.py
  - path: tests/test_wiki_hooks.py
  - path: .github/workflows/deploy.yml
  - path: tests/modules/turns/test_turns_service.py
  - path: tests/modules/identity/test_identity_router.py
  - path: tests/modules/scheduler/test_scheduler_service.py
  - path: tests/modules/turns/test_turns_bus.py
  - path: tests/modules/telegram/test_telegram_ports.py
  - path: tests/modules/memory/test_memory_router.py
  - path: tests/modules/sessions/test_sessions_router.py
  - path: tests/web_helpers.py
  - path: tests/test_web_app.py
  - path: src/claude_hermes/core/di.py
relations:
  - decision.postgres-and-modular-core
  - decision.modules-and-ports
  - component.web
  - decision.redis-taskiq-scaling
  - decision.minio-blobs
  - system.operations
---
# Testing Strategy

Every module is unit-tested without network or a live SDK (PRD NFR-6):
`query()` and Bot API calls are faked; `pytest-asyncio` in `asyncio_mode =
"auto"` (pyproject.toml). Run: `make test`, or
`.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`. Current count after
Wave 3b: **1480 passed, 0 skipped**, about 3.5 minutes with the full test stack
up. Zero skips is itself information — it means taskiq is installed and the Redis
and MinIO fixtures found their containers. On a laptop with only PostgreSQL the
same run is green with a handful of SKIP lines, and `-rs` is how you tell the two
apart.

**The suite runs in inline queue mode and on the local blob store**, always. That
is not a shortcut: inline mode is what a laptop deployment is
([[decision.redis-taskiq-scaling]]), and the dispatchers, the claim, the review
gate and the blob-store port behave identically either way by construction. The
handful of tests that need a real Redis or a real MinIO ask for a fixture and skip
without one.

## The suite needs a real PostgreSQL

There is no in-memory database and no SQLite fallback: a suite that passed
against another engine would prove nothing about the generated `tsvector`
column, `ON CONFLICT` or `RETURNING`, which is where the storage layer's
behaviour now lives ([[decision.postgres-and-modular-core]]).

- `make test-db` starts a throwaway `postgres:16-alpine` as `hermes-test-pg` on
  **127.0.0.1:55432** — the default `CH_TEST_DATABASE_URL`
  (`postgresql+asyncpg://hermes:hermes@127.0.0.1:55432/hermes`) points at it.
  The CI job (`.github/workflows/deploy.yml`) publishes the same port from a
  `services: postgres`, so neither side needs extra environment. `make
  test-stack` starts that plus Redis and MinIO (below); `test-db` remains all the
  suite strictly needs.
- `tests/conftest.py` treats that URL as a **server**, not a database. The
  session-scoped autouse `test_database` fixture creates `hermes_test_<8 hex>`
  on it, runs `upgrade_head_sync` against the new database, exports
  `CH_DATABASE_URL`, and drops it `WITH (FORCE)` at the end. Two pytest
  processes therefore never share state — parallel runs, and a second run
  started while the first is going, are safe by construction. The
  function-scoped **`engine` fixture** (Step 5) is a `make_engine` pool over that
  database, disposed after the test — what every repository, service and
  composition-root object takes, so a test builds the real module object over it
  rather than a facade.
- Because every run migrates from empty, **every run is also a test that
  `alembic upgrade head` works.** `tests/test_migrations.py` goes further and
  diffs the upgraded database against `core/db/registry.metadata`, failing if
  the ORM and the migrations have drifted.
- The function-scoped autouse `_clean_tables` fixture `TRUNCATE ... RESTART
  IDENTITY CASCADE`s every table in `metadata.sorted_tables` before each test.
  Restarting identities is what keeps id-sensitive assertions (the SSE cursor,
  job ids) stable regardless of test order.
- If the server is unreachable the fixture calls `pytest.exit` with the URL and
  the `make test-db` hint rather than letting 1300 tests fail one by one.
- `tests/test_import_sqlite.py` builds an old-shape SQLite file with the stdlib
  `sqlite3` module and imports it, pinning the one-shot migration path
  ([[contract.state-db]]); `tests/test_core_logging.py` pins that `configure()`
  renders structlog key-values, still redacts a bot token, and still carries
  third-party stdlib records.

## Redis and MinIO are optional, and skip rather than exit

The asymmetry is deliberate: **no database, no suite** (every test writes rows),
but a laptop with only PostgreSQL up must still get a green run.

| Fixture | Points at | Behaviour |
|---|---|---|
| `redis_url` (function) | `CH_TEST_REDIS_URL`, default `redis://127.0.0.1:56379/15` | `PING`, then `FLUSHDB` before each use |
| `s3_bucket` (session) | `CH_TEST_S3_ENDPOINT`, default `http://127.0.0.1:59000`, `hermes`/`hermes-test` | one `hermes-test-<8 hex>` bucket per pytest process, purged and dropped at the end |
| `s3` (function) | the same bucket | the app's own `S3Client`, emptied first |
| `s3_for` (function) | any bucket name | for the tests that need a bucket the fixture does not own — `ensure_bucket` creating one, most of all |

Both entry points `pytest.skip` with the endpoint and a "run `make test-stack`"
reason when unreachable. **db 15** and not db 0, because the same server may be
holding a dev app's keys and `FLUSHDB` on the wrong database is the kind of mistake
you make exactly once; one bucket per process for the same reason the database is
per process — parallel runs must not share a namespace. `redis_url` is
function-scoped because a lock test that left `lock:curator` behind would otherwise
decide the next test's result.

CI (`.github/workflows/deploy.yml`) publishes the same ports: `redis:7-alpine` as
a service container, and MinIO started with `docker run` in a step because a
`services:` entry cannot pass the `server /data` command the image needs.

**`tests/test_workers_taskiq.py` opens with `pytest.importorskip("taskiq")`.**
The taskiq binding was written offline; with the dependency installed the file
runs green (it also pins the broker's `socket_timeout=None`, the defect the first
live worker smoke found), and it skips only on a machine without taskiq — see
`src/claude_hermes/workers/README.md`.

## Wave 3b: fakeredis for the unit level, the container for the proof

The turn-as-a-row work added one rule to the ones above: **every Redis class is
unit-tested against `fakeredis` (`FakeAsyncRedis`, and a shared `FakeServer` when
a test needs "two processes"), and the container is used only where the claim
itself is about two processes.** `fakeredis>=2.23` is a dev dependency, so those
files run on a laptop with nothing started but PostgreSQL.

- `tests/test_core_eventbus.py` — the in-process bus, two `RedisEventBus`
  instances on one `FakeServer`, `ready`, unsubscribe, a raising handler, reader
  recovery after a dropped connection (`_FlakyClient` / `_DyingPubSub`), and the
  two properties the advisor pass added: `ready()` does **not** resolve while the
  SUBSCRIBE is provably on the wire but unconfirmed (a gated pub/sub wrapper holds
  every `get_message` behind an event), and a handler parked on an `asyncio.Event`
  holds up only its own channel while another lane still delivers and a third
  channel's `ready()` still resolves.
- `tests/modules/turns/test_turns_repository.py` — against the real PostgreSQL,
  because the index *is* the lock: the second insert raises `LaneBusy`, `claim` is
  `queued`-only, `heartbeat`/`finish` refuse a foreign `worker_id`, `fail_queued`
  never reaches a running turn, `active` ignores the lease, plus `active_lanes`,
  `expired`, `mark_lost` and `prune`. `tests/test_migrations.py` reads the three
  partial index definitions straight out of `pg_indexes` and round-trips `0004`.
- `tests/modules/turns/test_turns_snapshots.py` — **parametrized over
  `MemorySnapshots` and `RedisSnapshots`**, running the same eight contract tests
  against both, `seq` on the synthetic deltas included. One store drifting from
  the other would break the SSE watermark silently, and this file is what stops
  it; the Redis parameter is `skipif`-guarded on fakeredis so a missing dev extra
  shows up as a SKIP line rather than as quietly halved coverage.
- `tests/modules/turns/test_turns_launcher.py`, `…_execution.py`, `…_control.py`,
  `…_dispatch.py` — fakes only. Between them they pin the flag-then-nudge order,
  the bounded re-read in `wait`, the frozen reaper payload, the `worker_id` guard,
  the shielded terminal write surviving a second cancel, the heartbeat retrying on
  error and self-cancelling on a lapsed lease, and the pre-body failure emitting
  its own `turn.start` + `turn.end`.
- `tests/modules/turns/test_turns_redis_ask_broker.py` — fakeredis: a resolve from
  a *second* broker instance wakes the parked `wait`, a stranger is refused with
  the hash untouched, the answer-key fallback covers a lost nudge, a timeout
  leaves no hash, and `discard` is synchronous.
- `tests/modules/turns/test_turns_two_processes.py` — the only file that needs the
  **container** Redis and exercises the turn stack. Two independent "processes",
  each with its own client, its own `RedisEventBus` reader task, its own
  `RedisSnapshots` and its own `TurnBus`, sharing only the server and a stand-in
  for the one `turn_events` table: `event_stream` on B tails a turn A is running
  without doubling a delta (the generator is driven one frame at a time so that
  A's delta lands while B is suspended between its replay and its snapshot — the
  only way to stage "in the snapshot AND on the wire"), a `TurnControl` on A
  cancels a task registered there when B publishes the nudge, and an ask opened on
  A is answered on B. It skips cleanly without the container.

Wave 3a's own files, all inline-mode unless they name a fixture:
`tests/test_core_tasks.py` (registry, `InlineQueue`'s strong references, the
`{"run_job": 1}` limit, `UnknownTask` raised at the *call site* in both modes),
`tests/test_core_crypto.py`, `tests/test_core_redis.py` (fakeredis, plus the real
container when it is up — the Lua release and its WATCH/MULTI fallback are the
same compare-and-act, so both paths are covered),
`tests/test_integrations_s3.py`, `tests/test_workers_tasks.py` (the four bodies
over a bound fake context), `tests/modules/scheduler/test_scheduler_claiming.py`
(two concurrent `claim_due` calls on one engine return disjoint sets — the
at-most-once proof), `tests/modules/files/test_files_s3.py` (MinIO),
`tests/modules/learning/test_learning_queue.py`,
`tests/modules/extensions/test_extensions_redis_state.py` and
`tests/test_import_data_dir.py`.


## Layout

Two shapes side by side. The older files stay where they are —
`tests/test_<name>.py`, roughly one per pre-Wave-2 source module. From Wave 2 to
Step 5 they drove the old import paths through the shims, which is how the move
was proved not to change behaviour; Step 5 ported each one onto the module object
the shim used to build (`SessionRouter(store)`, `TurnService` + ports,
`JobRepository(engine)`, `build_hermes_server(handlers)`, the telegram services —
same docstring, same assertions, only the setup changed) or deleted it where a
test under `tests/modules/` already pinned the same behaviour, named as a twin
**per test**, never per file (99 files touched; 1585 pass). No test imports a
legacy path or asserts that one name is another any more. Tests written against a
module live in `tests/modules/<module>/` with file names prefixed `test_<module>_`
(`tests/modules/turns/test_turns_tool_gates.py`): `tests/` has no `__init__.py`,
so two files with the same basename would collide. Several of those pin the seams
the split created — the `test_<module>_ports.py` files assert that the real
infrastructure satisfies the Protocols its module declares (telegram's covers the
moved `Titler`) ([[decision.modules-and-ports]]).

Roughly one `tests/test_<module>.py` per source module (89 files at the top level, 65 more under `tests/modules/`),
covering gateway routing/keys/ACL, presenter chunking, topics fail-safety,
voice payload shapes, attachment path safety and gate-then-download intake,
catalog validation, drafts inertness, approvals
tokens, compose scoping, install pinning, secrets file mode, cron
store/schedule/ticker/executor, learning loop single-flight, curator aging,
backup/rollback for both the skills tree and the wiki, wiki seeding and
index truncation, wiki hook contracts (raw immutability, the stop gate,
index refresh), the schema and its migrations, runner resume/retry and guard
rules, and the ask-user broker/tool/callback triple below.

Two more gates run beside pytest (`make lint`): `ruff check src tests`, and
**`lint-imports`**, which fails the build on an import that crosses an
architectural boundary — core reaching into the application, one module reaching
into another, or a `domain/` package importing a framework.

Behavior contracts worth knowing:

- `tests/test_catalog.py` parses the **real** `catalog/` — adding a manifest
  entry is validated by running it.
- `tests/test_gateway.py` pins the session-key formats
  (`tg:private:1`, `tg:private:1:t9`, `tg:supergroup:-5:7`,
  `web:<id>:<uuid4 hex>`), the
  4000-char chunking, `/new` resetting only its thread, and `/secret` being
  wiped-but-unanswered in un-addressed groups.
- `tests/test_attachments_intake.py` pins the ordering that matters: an
  un-addressed group attachment fetches no byte *and* fires no typing
  indicator, a denied user downloads nothing, and an oversize file is refused
  before any download. It tests `intake_attachments` directly because
  `create_dispatcher` is not unit-tested (below) — extracting the coroutine is
  what makes the ordering testable at all.
- `tests/test_runner_attachments.py` pins that `_build_prompt` still returns a
  bare `str` with no attachments (the guard on every pre-existing call site),
  and that the resume-retry gets a live, undrained prompt iterator.
- `tests/test_drafts.py` asserts drafts never land in the reviewed catalog
  dir and are not installable — against the `catalog_drafts` table since Wave 3a
  (`tests/ext_helpers.py` builds the DB-backed stores the extensions tests share),
  with every behavioural assertion kept: validator parity, shadow refusal, a token
  bound to bytes, rejected drafts listed with their reason, owner-only.
- `tests/test_cron_ticker.py` pins the catch-up `missed` rule and one-shot
  `done` transitions — unchanged through Wave 3a, which is the parity check that
  the claim did not move a behaviour while it moved the reschedule.
- `tests/test_wiki_hooks.py` pins the wiki hook contracts: `raw_guard_factory`
  denies editing or overwriting a source but allows a new one;
  `stop_gate_factory` blocks once on an untracked page and never blocks
  twice (`stop_hook_active`); `index_refresh_factory` injects the index only
  when its mtime moved, and only once per move.
- `tests/test_wiki.py` pins `WikiStore.seed()`: idempotent (never overwrites
  an existing `index.md`), and migrates legacy `MEMORY.md`/`USER.md` into
  frontmatter-wrapped pages exactly once.
- `create_dispatcher` (aiogram wiring) is exercised by the Docker smoke run,
  not unit tests.
- `tests/test_ask_broker.py` pins the click-at-deadline race: the timed-out
  future is never cancelled, and across 150 interleavings the value the waiter
  returns always agrees with what the clicker was told. Flipping `asyncio.wait`
  back to `asyncio.wait_for` turns this file red, which is the point of it.
- The web channel is tested without a live server: `tests/web_helpers.py`
  builds an `http.deps.WebDeps` of real module objects (repositories,
  `ThreadService`, `FileService`, sessions' and identity's services, and a
  `FakeLearningScopes` set unconditionally so the skills router always mounts)
  over the test engine — the `engine` fixture when a test also reads rows
  directly — plus one fake that is both `deps.runner` and `deps.turns`, and the
  routers are driven in-process through httpx against the app `http.app.
  build_web_app` assembles. `tests/test_web_app.py` pins the
  middleware (cookie, `Origin`, per-route content type), the 400-not-422 shape of
  a rejected body and the OpenAPI document listing every frozen `/api/*` path
  (the content plan's routes separately: they are mounted only when
  `deps.content` is set, so the default bundle must not list them);
  `test_web_auth.py` the Login-Widget check string and cookie signing,
  `test_web_threads.py` visibility per user, `test_web_turns.py` the 202/409
  contract and the SSE cursor — the two assertions that compare the 202 body by
  equality `pop("turn_id")` first and compare the rest unchanged, which is the
  whole of Wave 3b's wire diff, and one test drives a fake bus whose snapshot
  carries a watermark to prove a delta the snapshot already covered is dropped —
  `test_web_files.py` upload limits and containment,
  `test_web_routines.py` and `test_web_voice.py` the remaining routers.
  `tests/modules/turns/test_turns_bus.py` pins event ordering, persistence, the snapshot and
  listener isolation. **These files are the wire contract**: Wave 2b rewrote every
  handler underneath them and they passed unmodified, which is the only reason the
  conversion could be called parity.

## Overriding a dependency instead of rebuilding the bundle

Since Wave 2b a handler takes its collaborators through `Depends` on a per-module
provider ([[decision.modules-and-ports]]), and a test swaps one of them by
overriding **the provider callable**, on a real app:

```python
from claude_hermes.modules.memory.presentation.dependencies import get_graph

app = build_web_app(deps)
app.dependency_overrides[get_graph] = lambda: FakeGraph()
...
app.dependency_overrides.clear()
```

`tests/modules/<module>/test_<module>_router.py` is that shape, one per converted
module. Two rules come out of how FastAPI resolves overrides, and breaking either
produces a test that silently keeps the real object:

- **Override the same callable object the handler depends on.** The lookup is by
  identity, not by name, which is why every module builds its providers once at
  module scope; `provide("wiki_root")` called a second time is a different
  callable and an override of it reaches nothing.
- **Clear the overrides.** They live on the app, so a fixture that builds one app
  per test gets this for free, and a shared app does not.

A test that needs a different *user* overrides `core.auth.current_user` or
`require_owner` the same way, rather than forging a cookie — though the
`tests/test_web_*.py` files deliberately still go through the real cookie and the
real middleware, because that path is part of what they pin.

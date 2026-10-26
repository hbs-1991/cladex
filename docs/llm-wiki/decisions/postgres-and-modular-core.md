---
id: decision.postgres-and-modular-core
kind: decision
status: current
summary: PostgreSQL replaces the SQLite file, ORM models live in modules/*/domain, structlog routes through stdlib logging, and the app becomes a FastAPI shell — wave 1 of the modular architecture.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:d0de8824d48762ab15de619a9d301ca0c442ea9f07863ef7d233247ff93ebfa7
evidence:
  - path: docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md
  - path: src/claude_hermes/core/db/engine.py
  - path: src/claude_hermes/core/db/migrate.py
  - path: src/claude_hermes/core/db/registry.py
  - path: src/claude_hermes/core/logging.py
  - path: src/claude_hermes/core/exceptions.py
  - path: src/claude_hermes/http/app.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/main.py
  - path: pyproject.toml
  - path: docker-compose.yml
relations:
  - contract.state-db
  - component.core-runtime
  - component.web
  - quality.testing
  - system.operations
  - decision.modules-and-ports
---
# Decision: PostgreSQL and a modular core

## Context

Everything durable lived in one SQLite file (`data/state.db`) opened with
`aiosqlite`, with the schema as a `_SCHEMA` string plus a hand-rolled
`_MIGRATIONS` list in `db.py` and message recall as an FTS5 virtual table. The
web channel was a Starlette app, logging was `logging.basicConfig` wrapped by
`logsafe`, and every module imported every other one freely.

That shape has a ceiling: one writer, one process, one host. The target
architecture (`docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`)
is N API instances and M workers over PostgreSQL, Redis and MinIO, with
vertical-slice modules (`domain` / `application` / `infrastructure` /
`presentation`) and an import-linter gate. The whole of it is too large to land
at once, and the agent is a bot somebody is talking to — every step has to ship
on its own.

**Wave 1 is step 0 + step 1 of that spec**: change the storage engine, the
logging setup, the error hierarchy and the application object, and move nothing
else. The routers, the runner and the gateway are untouched.

## Decision

- **PostgreSQL is the only relational store**, via SQLAlchemy 2.0 async +
  `asyncpg`. The connection string is `CH_DATABASE_URL` in the environment — a
  URL, not a path, and a secret, so it never enters `config.yaml` (PRD P7).
  `aiosqlite` is gone from `pyproject.toml`; the one-shot import script reads the
  old file with the stdlib `sqlite3` module.
- **Alembic owns the schema**, and `main.build_app` runs `upgrade_head` before
  opening the pool. Alembic's async `env.py` calls `asyncio.run`, which cannot
  run on the live loop, so `core.db.migrate.upgrade_head` wraps the sync
  `command.upgrade` in `asyncio.to_thread`. There is no `alembic.ini`: the URL is
  a secret and a config file is the wrong place for it, so `alembic_config(url)`
  builds the config in Python.
- **ORM models live in `modules/<name>/domain/models.py` from day one** —
  sessions, scheduler, turns, extensions, files — even though the code that uses
  them is still in the old flat layout. Wave 2 then *moves code to the tables*
  instead of moving both at once. `core/db/registry.py` imports them all so
  Alembic sees one metadata; that single `core → modules` edge is whitelisted in
  import-linter and is the only one allowed.
- **The `Database` facade keeps every public method it had.** Same names, same
  arguments, same dict-shaped returns, re-implemented over the engine; the three
  raw-SQL stores (`cron/store.py`, `extensions/store.py`,
  `extensions/tokens.py`) move to SQLAlchemy Core the same way. Nothing above the
  store layer changed, which is why the 814 tests that existed before the wave
  are what proves it: they pass unmodified in behaviour, against a real
  PostgreSQL. *(Superseded: Wave 2 moved every statement into the repository
  that owns its table, and Step 5 deleted the facade and the three stores with
  the shims — every caller holds a repository over the one engine.)*
- **`ApplicationException(status, code, message)` in `core/exceptions.py` is the
  root of every application error**, and `web/errors.ApiError` becomes a subclass
  with the same constructor *(Step 5 deleted `ApiError`; routers raise
  `ApplicationException` and `http/app.py` renders it)*. One FastAPI exception handler renders
  `{"error": {"code", "message"}}` for the whole hierarchy, so a service can
  raise `NotFound` without importing a web framework — the rule that makes
  modules movable later.
- **structlog routes through stdlib logging**, not around it:
  `structlog.stdlib.LoggerFactory` plus a `ProcessorFormatter` on the root
  handler, still wrapped in `logsafe.RedactingFormatter` (`core/logsafe.py`
  since Step 5). `caplog`, third-party
  loggers and the token redaction all keep working, and `log =
  structlog.get_logger(__name__)` replaces every `logging.getLogger`.
- **FastAPI serves the existing Starlette `Route` objects as-is**
  (`FastAPI(routes=...)`), with OpenAPI switched off. The app object changes;
  no route does. *(Superseded in Wave 2b: the routers are `APIRouter`s with
  `Depends`-injected collaborators and the schema is served at
  `/api/openapi.json` — see [[decision.modules-and-ports]], "Depends and
  providers". The alternative rejected below was deferred, not abandoned.)*
- **import-linter runs in the gate** with three contracts: core depends on no
  application package, modules are independent of each other, and `domain` is
  pure (no framework). *(Fourteen since Step 5, fifteen since the content module —
  [[decision.modules-and-ports]].)*

## Consequences

- **Postgres is now a hard dependency of a dev machine and of CI.** `make
  test-db` starts a throwaway `postgres:16-alpine` on 127.0.0.1:55432 and every
  pytest process creates its own database on it ([[quality.testing]]); the deploy
  workflow gained a `services: postgres`. There is no in-memory fallback on
  purpose — a suite that passes against SQLite proves nothing about the generated
  `tsvector` column, `ON CONFLICT`, or `RETURNING`.
- **The backup story changed shape.** `tar ./data` no longer captures the
  database: it lives in the `pgdata` volume and needs `pg_dump`
  ([[system.operations]]).
- **FTS5 became a generated `tsvector` column**; the search contract
  (`search_messages`, its snippet markers, its never-raise property) is preserved
  deliberately, because the Telegram recall block and the web UI both parse it
  ([[contract.state-db]]).
- **Compose grew a `db` service** the agent waits on (`condition:
  service_healthy`), because the boot path migrates before it serves, and the
  prod healthcheck now proves a database connection rather than the existence of
  a file.
- The `modules/` tree is real but nearly empty: only `domain/models.py` per
  module. That is the intended intermediate state, not an abandoned refactor — a
  reader should expect `application/`, `infrastructure/` and `presentation/` to
  arrive module by module in Wave 2.
- Out of scope in this wave, and still in-process singletons: the turn bus and
  its in-flight snapshot, the cron ticker, and Telegram long polling. Redis,
  taskiq workers, MinIO and the multi-user data model are Waves 3-4
  ([[decision.web-as-a-channel]] still describes the channel shape).

## Alternatives rejected

- **Keep SQLite and add WAL/pooling.** It fixes nothing that matters: the goal is
  several processes on several hosts, and a file lock does not cross hosts.
- **Move modules first, storage later.** The tables are where the coupling lives;
  a module split over a schema nobody could migrate would have to be redone.
- **Convert routers to `APIRouter` in the same wave.** That is a change to every
  request path, on top of a change to every write path — two large diffs whose
  failures would be indistinguishable. FastAPI adopting the existing `Route`
  objects keeps this wave's blast radius inside the storage layer. *(Done on its
  own in Wave 2b, once a composition root existed to build the dependencies, and
  `tests/test_web_*.py` passed unmodified — which is what the separation bought.)*
- **`timestamptz` now.** Every comparison in the codebase is lexicographic on
  ISO-8601 strings; converting types belongs to the module that owns each table.

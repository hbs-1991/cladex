# Modular FastAPI architecture on PostgreSQL + Redis + MinIO — design spec

Status: accepted 2026-09-14. Execution is incremental (Steps 0–5 below); each
step ships on its own with tests green.

## Task
Incrementally restructure the claude-hermes agent into a horizontally scalable, multi-user-ready, modular FastAPI application on PostgreSQL + Redis + MinIO, using vertical-slice modules with DDD layers.

## Context
- Package `src/claude_hermes/` — a personal Telegram agent on the Claude Agent SDK with a web channel ("CladeX", React SPA in `web/`).
- Current layout: flat package with subpackages `gateway/`, `cron/`, `extensions/`, `learning/`, `web/` plus top-level runtime modules (`main.py`, `runner.py`, `turnbus.py`, `filestore.py`, `sessions.py`, `db.py`, `tools.py`, `hooks.py`, `guard.py`, `prompt.py`, `options.py`, `acl.py`, `wiki.py`, `config.py`, `paths.py`, `logsafe.py`).
- Web/API layer is **Starlette** today (`starlette>=0.40`, `uvicorn>=0.30`); uvicorn runs inside the same asyncio loop as aiogram. REST/SSE routers live in `src/claude_hermes/web/`.
- Persistence is a **SQLite** file `data/state.db` via `aiosqlite` (schema in `src/claude_hermes/db.py`, documented in `docs/llm-wiki/contracts/state-db.md`). It includes an **FTS5 virtual table** `messages_fts` for message search.
- In-process state that blocks horizontal scaling today: the turn-event bus keeps listeners and an in-flight-turn snapshot in memory (`turnbus.py`); the cron ticker runs in-process (`cron/ticker.py`); the Telegram client uses long polling.
- Non-DB state under `data/`: `config.yaml`, `secrets.env`, `agent_home/` (SDK working dir + agent memory/wiki), `catalog-drafts/`, `pending-approvals/`, `user-catalog/`, `logs/`, `USER.md`.
- Reference architecture (from `D:\projects\ssc-backend-fastapi\.claude\rules\architecture.md`):
  ```
  src/modules/<domain>/
  ├── domain/          # models, enums, value_objects, events, exceptions
  ├── application/     # services, ports, dto
  ├── infrastructure/  # repositories, publishers, registry
  └── presentation/    # router, schemas, dependencies
  ```
  Layer contracts: `domain` pure; `application` imports `domain` + `application/ports.py`, NOT `infrastructure/`; `infrastructure` imports `domain` + `application` + external SDKs; `presentation` imports `domain` + `application`, NOT `infrastructure/` directly (via DI); `modules/*` never import each other directly — share via `core/events/` or Protocols.
- Existing decisions in `docs/llm-wiki/decisions/` must keep holding: Claude Agent SDK as the base, prompt-cache discipline, read-only catalog as the security boundary, Telegram threads are session lanes, web is a channel not a second application.
- `docs/llm-wiki/` is the team memory; the Stop hook expects affected wiki pages and README sections to be updated.

## Requirements

### A. Target layout
1. Base directories: `src/claude_hermes/core/`, `modules/`, `integrations/`, `workers/`. No others without a stated reason.
2. `core/` holds: config, paths, structlog setup, DB engine/session factory, Redis client, the event bus abstraction (in-process + Redis pub/sub), the `ApplicationException` hierarchy and its FastAPI exception handlers, DI wiring, auth primitives (current-user dependency), the workspace port.
3. `integrations/` holds thin adapters for external SDKs: Claude Agent SDK, Redis, S3/MinIO client. `workers/` holds taskiq worker entrypoints.
4. Modules (each with `domain/application/infrastructure/presentation` and its own `README.md`):
   - **identity** — users, ACL, auth (Telegram Login Widget, Mini App, tokens), per-user settings.
   - **sessions** — session lanes, threads, session_key ↔ SDK session id, titles, message history + search.
   - **turns** — runner, turn-event bus, turn_events persistence, tools, hooks, bash guard, prompt/options assembly, slash commands, voice; SSE endpoint; the taskiq task that runs a turn.
   - **files** — uploads/FileStore behind a blob-storage port.
   - **scheduler** — jobs, schedules, claiming of due jobs, executor (was `cron/`).
   - **extensions** — catalog, registry, install, secrets, drafts, approvals, compose, probe; connectors/plugins/skills routers.
   - **memory** — the agent wiki: read/write/lint, memory graph API.
   - **learning** — observe, nudges, review pass, usage sidecar, curator, archive, backups.
   - **telegram** — gateway: routing, intercepted commands, topics, presenter, rich replies, titler. Supports **webhook mode** (production, any API instance handles updates) and polling (local dev only).
5. The former `web/` package dissolves: each module contributes its router; the FastAPI app in `core/` (or `main.py`) only assembles routers, middleware and exception handlers. The React SPA is served as before.
6. Modules without DB tables keep `domain/` as plain dataclasses/enums; SQLAlchemy ORM is required only where a module owns tables. Pydantic schemas only in `presentation/schemas.py`.
7. `HTTPException` only in presentation/handlers; services raise `ApplicationException` subclasses mapped centrally.
8. **structlog** everywhere; one setup in `core/`; no `print()` / `logging.getLogger` outside it. Logs go to stdout (container logging), not to `data/logs/`.
9. **import-linter** in `pyproject.toml`, run by `make`/CI. Contracts are added per module as that module is migrated; by the final step all contracts from the reference are active and a violation fails CI.

### B. PostgreSQL
10. PostgreSQL replaces SQLite as the only relational store; driver `asyncpg` with SQLAlchemy async sessions; schema via Alembic; connection string from config/env.
11. Message search: replace FTS5 with a generated `tsvector` column (config `simple`) + GIN index; the search API keeps its current contract.
12. Every user-owned table gets `owner_id` (FK to users) from the first migration; queries are scoped by owner through the repository layer, never ad hoc in handlers.
13. One-shot import script `state.db → PostgreSQL` that preserves sessions, messages, jobs, job runs, turn events, extension state, learning data, and assigns everything to the single existing user.
14. `aiosqlite` and any SQLite path assumptions are removed from runtime code (the import script may read SQLite).

### C. Redis, workers, scaling model
15. Add Redis; use **taskiq** (Redis broker) for background work: agent turns, learning passes, scheduled job execution. Task arguments are primitives only (IDs, dicts).
16. HTTP handlers never run a turn inline: `POST` a turn → enqueue → return turn id; results stream over SSE.
17. Turn events: durable events persisted to PostgreSQL by the worker; all events (including text/thinking deltas) published to a Redis pub/sub channel per session; API instances subscribe and fan out to SSE clients and the Telegram presenter. The in-flight snapshot for late subscribers lives in Redis with a TTL.
18. Exactly one running turn per session lane, enforced in PostgreSQL (unique partial index on running turns) with a lease/heartbeat so a dead worker's turn is reclaimable.
19. Scheduler has no singleton ticker: any worker claims due jobs with `SELECT … FOR UPDATE SKIP LOCKED`; a job runs at most once per due time.
20. API processes hold no correctness-relevant in-memory state; N API instances + M workers against one PostgreSQL + one Redis behave as a single system.
21. Document in a decision page which components remain singletons in this iteration and why (expected: none in webhook mode; the polling Telegram client in dev mode).

### D. Non-DB state
22. Catalog drafts, pending approvals and extension secrets move to PostgreSQL tables; secrets are encrypted at rest with a key from env.
23. Uploads move to **self-hosted MinIO** (S3 API) behind the `files` blob port. MinIO is a service in `docker-compose.yml` for dev and in `docker-compose.prod.yml` for production, with a persistent volume; bucket name, endpoint and credentials come from config/env.
24. Agent working directory (`agent_home/`) and the read-only `user-catalog/` stay on a shared volume, per-user subdirectories, accessed only through the workspace port in `core/` so the backend can later be swapped for materialize-on-turn from MinIO.
25. `config.yaml` stays a deploy-time file; `USER.md` becomes per-user data in the workspace.
26. Backup story is redefined and documented: PostgreSQL dump + MinIO bucket snapshot + workspace volume replace "tar ./data"; the `learning` backups task is updated accordingly.

### E. Multi-user readiness
27. A `users` entity exists; every request and every task resolves to a user; ACL becomes a per-user check in `identity`.
28. Execution isolation: a worker runs a turn with cwd = that user's workspace only; no path outside it is readable or writable by the turn's tools; the read-only catalog mount stays the outer boundary. Record this as a security decision alongside `readonly-catalog-boundary`.
29. Telegram chats and web logins map to users; a Telegram user id maps to exactly one user.

### F. Migration order (incremental, always shippable)
30. Step 0: `core/` skeleton, structlog, import-linter (permissive), PostgreSQL + Alembic reproducing the current schema, import script, repositories over `asyncpg`. App runs on PostgreSQL before any module moves.
31. Step 1: FastAPI replaces the Starlette app; existing routers are mounted first, then converted one by one.
32. Step 2: modules migrate one at a time in this order: identity → sessions → turns → files → scheduler → extensions → memory → learning → telegram. Each step ships with a temporary shim re-exporting old import paths, its README, wiki page updates, tests green, and its import-linter contract enabled.
33. Step 3: Redis + taskiq workers; turns and learning move off the request path; scheduler switches to claiming; MinIO replaces the on-disk FileStore.
34. Step 4: multi-user data model + auth + workspace isolation; Telegram webhook mode.
35. Step 5: remove all shims; full import-linter contracts; delete dead code.
36. Preserve external contracts throughout: REST/SSE API consumed by `web/src/api/client.ts`, Telegram behaviour, `config.yaml.example` keys (extended, not renamed), compose entrypoints.
37. Tests run against a PostgreSQL + Redis + MinIO test stack (compose profile or testcontainers); each module boundary gets contract tests.
38. Update `docs/llm-wiki/` (components, flows, new contracts page for the PostgreSQL schema replacing `state-db.md`, decision pages: modular architecture, PostgreSQL, Redis/taskiq scaling model, MinIO for blobs, multi-user isolation) and `README.md` (install now needs PostgreSQL, Redis, MinIO); run `.llm-wiki/llm-wiki validate`.

## Acceptance criteria
- [ ] `src/claude_hermes/` contains `core/`, `integrations/`, `workers/` and `modules/{identity,sessions,turns,files,scheduler,extensions,memory,learning,telegram}` each with the four layers and a README; no `web/`, `gateway/`, `cron/` packages remain.
- [ ] `import-linter` passes with the full reference contracts; a deliberately added `modules/turns → modules/sessions/infrastructure` import fails CI.
- [ ] `alembic upgrade head` on an empty PostgreSQL builds the schema; the import script ingests a real `state.db` and the app shows the imported threads, jobs and extensions.
- [ ] `docker compose up` starts app, worker, PostgreSQL, Redis and MinIO; an upload lands in the MinIO bucket and is served back through the files API.
- [ ] Starting 2 API instances and 2 workers against one PostgreSQL + Redis + MinIO: a turn started via instance A streams over SSE from instance B; a scheduled job due once runs exactly once; two turns on the same lane never run concurrently.
- [ ] Killing a worker mid-turn leaves the lane reclaimable within the lease period.
- [ ] Telegram webhook mode works behind the existing Caddy setup; all flows in `docs/llm-wiki/flows/` behave as before.
- [ ] The React SPA works unchanged; `web/e2e/` specs pass.
- [ ] `grep` for `print(`, `logging.getLogger`, `aiosqlite`, `sqlite3` in `src/` hits only the structlog setup and the import script.
- [ ] Message search returns the same results as before for the imported data.
- [ ] Two different users cannot see each other's threads, files, drafts or workspace files, via API or via the agent's tools.
- [ ] Full test suite passes; `.llm-wiki/llm-wiki validate` passes; README and wiki reflect the new stack.

## Out of scope
- Rewriting the React SPA — only its API contract is preserved.
- Per-user sandbox containers for turn execution (workspace-directory isolation only in this iteration; container-per-user is a follow-up decision).
- Reference-project rules that don't apply here: money-as-cents fields, Laravel/Sanctum compatibility.

## Open questions
- Whether Russian-specific stemming is wanted for message search later; `simple` config is the baseline.

## Decisions taken while answering the open questions (2026-09-14)
- Queue: asyncio-native — **taskiq** with Redis; Redis also serves pub/sub for turn events and the in-flight snapshot.
- DB driver: **asyncpg** + SQLAlchemy async sessions.
- Object storage: **self-hosted MinIO** in every environment.
- Reference patterns not carried over: mandatory ORM per module (plain dataclasses where a module owns no tables); import-linter contracts activate per migrated module, not all at once.
- Full-text search: `tsvector` + GIN, `simple` config.
- Migration: incremental, module by module, with temporary shims.

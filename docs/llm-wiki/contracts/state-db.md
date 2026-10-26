---
id: contract.state-db
kind: contract
status: current
summary: PostgreSQL schema owned by Alembic — users and owner_id on every user-owned row, sessions, messages, threads, meta, jobs, turns, turn_events, costs, per-owner extensions, secrets, OAuth tokens, wiki_pages, model pairs (0008), content_items (0010) and its drafting/card columns (0011).
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:d82adec5fd867b18d9e9a6ff474b2b210c96443799de1fd6fca82002895cd119
evidence:
  - path: src/claude_hermes/core/db/migrations/versions/0001_initial.py
  - path: src/claude_hermes/core/db/registry.py
  - path: src/claude_hermes/core/db/base.py
  - path: src/claude_hermes/core/db/migrations/versions/0002_threads_has_turns.py
  - path: src/claude_hermes/core/db/migrations/versions/0003_queue_and_extension_state.py
  - path: src/claude_hermes/core/db/migrations/versions/0004_turns.py
  - path: src/claude_hermes/core/db/migrations/versions/0005_users.py
  - path: src/claude_hermes/core/db/migrations/versions/0006_per_user_extensions.py
  - path: src/claude_hermes/core/db/migrations/versions/0007_wiki_pages.py
  - path: src/claude_hermes/core/db/migrations/versions/0008_model_choice.py
  - path: src/claude_hermes/core/db/migrations/versions/0009_user_grants.py
  - path: src/claude_hermes/core/db/migrations/versions/0010_content_items.py
  - path: src/claude_hermes/core/db/migrations/versions/0011_content_drafting.py
  - path: src/claude_hermes/modules/content/domain/models.py
  - path: src/claude_hermes/modules/content/infrastructure/repository.py
  - path: src/claude_hermes/core/model_catalog.py
  - path: src/claude_hermes/modules/identity/domain/tables.py
  - path: src/claude_hermes/modules/scheduler/domain/models.py
  - path: src/claude_hermes/modules/memory/domain/tables.py
  - path: src/claude_hermes/modules/memory/infrastructure/page_index.py
  - path: src/claude_hermes/modules/memory/application/services/index.py
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/modules/identity/domain/models.py
  - path: src/claude_hermes/modules/identity/infrastructure/repositories.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/modules/sessions/domain/policies.py
  - path: src/claude_hermes/modules/turns/infrastructure/turn_repository.py
  - path: src/claude_hermes/modules/sessions/domain/models.py
  - path: src/claude_hermes/modules/scheduler/domain/models.py
  - path: src/claude_hermes/modules/turns/domain/models.py
  - path: src/claude_hermes/modules/extensions/domain/models.py
  - path: src/claude_hermes/modules/files/domain/models.py
  - path: src/claude_hermes/core/db/engine.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/repository.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/extension_repository.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/oauth_token_repository.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_secrets.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_drafts.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_snapshots.py
  - path: src/claude_hermes/core/crypto.py
  - path: scripts/import_sqlite.py
  - path: scripts/import_data_dir.py
  - path: src/claude_hermes/modules/learning/infrastructure/scoped_meta.py
  - path: src/claude_hermes/modules/learning/domain/constants.py
  - path: src/claude_hermes/modules/sessions/infrastructure/repositories.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
relations:
  - component.core-runtime
  - component.web
  - component.extensions
  - decision.postgres-and-modular-core
  - decision.redis-taskiq-scaling
  - decision.minio-blobs
  - decision.multi-user-isolation
  - decision.wiki-index-not-store
  - decision.prompt-cache-discipline
---
# Contract: PostgreSQL schema

One PostgreSQL database, reached over SQLAlchemy async + `asyncpg`; the URL is
`CH_DATABASE_URL` and never a file path ([[decision.postgres-and-modular-core]]).
**Alembic owns the schema**: `0001_initial.py`, `0002_threads_has_turns.py`,
`0003_queue_and_extension_state.py`, `0004_turns.py`, `0005_users.py` and
`0006_per_user_extensions.py`, `0007_wiki_pages.py`, `0008_model_choice.py`,
`0009_user_grants.py`, `0010_content_items.py` and `0011_content_drafting.py` under `core/db/migrations/versions/`, and `main.build_app()` runs `upgrade_head`
before it opens the pool, so a process never queries a database it has not
migrated. Building the engine (`make_engine`) does not migrate — tests
(`tests/conftest.py` upgrades one `hermes_test_<8 hex>` database per pytest
process and hands it out through the `engine` fixture) and the import script
decide when that happens.

The ORM side is declarative models under `modules/<m>/domain/models.py` (plus
`meta` in `core/db/models.py`, and `modules/memory/domain/tables.py` — a row no
application layer may import, so it is spelled `tables.py` like identity's),
collected by `core/db/registry.py` into one
`Base.metadata` — the one deliberate `core -> modules` import, and the single
`ignore_imports` entry in the import-linter contracts
([[decision.modules-and-ports]]). Every statement against these tables lives in
the repository of the module that owns the table
(`modules/<m>/infrastructure/repositor{y,ies}.py`, plus `core/meta.py` for
`meta`); no facade fronts them since Step 5. A table not imported there does not exist as far as Alembic is
concerned. `tests/test_migrations.py` compares `upgrade head` against that
metadata and fails on any difference, so the two descriptions cannot drift.

| Table | Purpose | Columns |
|---|---|---|
| `users` | everyone the agent has actually talked to (`0005`) | `id` BIGINT PK — **the Telegram user id**, `role` (`owner`/`user`), `workspace_dir` (`''` for the owner, `users/<id>` for everyone else), `display_name`, `username` (both TEXT NOT NULL DEFAULT `''`), `created_at`, `last_seen_at`, `disabled_at` NULL, **`default_model`/`default_effort`** TEXT NULL (`0008`), **`shell_enabled`/`learning_enabled`** BOOLEAN NULL (`0009` — the owner's per-person grants, NULL = inherit the config default); UNIQUE partial index `users_one_owner ON (role) WHERE role = 'owner'` |
| `sessions` | lane → resumable SDK session | `session_key` PK, `sdk_session_id`, `system_append`, `created_at`, `updated_at`, **`index_mtime`** DOUBLE PRECISION NULL (`0004` — see below), **`owner_id`** BIGINT NULL → `users.id` (`0005`), **`model`/`effort`** TEXT NULL (`0008` — what RAN this session), **`shell_enabled`** BOOLEAN NULL (`0009` — whether this session was started with a shell); index `ix_sessions_owner` |
| `messages` | cross-session recall corpus | `id` BIGSERIAL PK, `session_key`, `role`, `content`, `ts`, `content_tsv` (generated `tsvector`); indexes `ix_messages_session_key_ts`, GIN `ix_messages_content_tsv`; **`owner_id`** BIGINT NULL → `users.id` (`0005`, denormalised on purpose — see below), index `ix_messages_owner_ts (owner_id, ts)` |
| `threads` | lane metadata for the web thread list | `session_key` PK, `title`, `title_manual`, `channel` (`web`/`telegram`), `owner_user_id` BIGINT **→ `users.id` since `0005`**, `archived_at`, `last_event_at`, `created_at`, `has_turns` (bool, not null, default false — see below), **`model`/`effort`** TEXT NULL (`0008` — what the user CHOSE) |
| `meta` | key/value store | `owner_user_id` (historical: the pre-`0005` owner record, read once by that migration; since Step 5 nothing writes or reads it and a stale row is inert), `sdk_slash_commands`, **`hermes_tools`** (the toolset fingerprint the last boot published: a sha256 over every `mcp__hermes__*` tool's name, description and schema — see below), and the learning loop's rows **per user since Wave 4b**: the two nudge counters (`turns_since_wiki_review`, `iters_since_skill_write`), `curator_last_run_at`, `curator_paused`, `curator_last_report_path`, `curator_last_dry_run_at`, `learning_review_until` (the review gate's lease — see below) — each **unsuffixed for the deploy owner** and suffixed `:<user_id>` for everybody else (`ScopedMeta`, [[component.learning]]); and **`content_publisher_job_id`**, the content plan's publisher routine, by id (`0010`'s feature; written by `PUT /api/content/publisher`, [[component.content]]), **`content_drafter_job_id`** (its drafter routine, `PUT /api/content/drafter`) and **`content_card_topic`** (the «Контент» topic's thread id in the owner's private chat, or `creating:<iso>` while one tick reserves its creation — written by a compare-and-set, `ContentRepository.meta_swap`) (`0011`'s feature) |
| `jobs` | cron store | `id` BIGSERIAL PK, `name`, `kind` (`once`/`interval`/`cron`/`manual` — `manual` has `value` `''` and a NULL `next_run_at` while idle; no migration, the column is TEXT), `value`, `prompt`, `deliver_chat_id` BIGINT, `deliver_thread_id` BIGINT, `origin_session_key`, `state` (`active`/`paused`/`done`), `next_run_at`, `last_run_at`, `last_status`, `timeout_seconds`, `created_at`, **`claimed_at`**, **`lease_until`** (both `0003`, both NULL = never claimed), **`owner_id`** BIGINT NULL → `users.id` (`0005`), **`model`/`effort`** TEXT NULL (`0008`); index `ix_jobs_owner` |
| `job_runs` | per-fire history of a cron job (Routines run list) | `id` BIGSERIAL PK, `job_id`, `ts`, `status`; index `job_runs_job (job_id, ts)` |
| `turns` | one row per turn — the lane lock, the work item and the lease (`0004`) | `id` TEXT PK (uuid4 hex, the `turn_id` every event carries), `session_key`, `channel` (`web`/`telegram`), `user_id` BIGINT, `text`, `attachment_ids` JSONB, `status` (`queued`/`running`/`ok`/`error`/`cancelled`/`lost`) — `user_id` is the **sender** and deliberately carries no FK (see `0005` below) —, `created_at`, `started_at`, `finished_at`, `lease_until`, `worker_id`, `cancel_requested_at`, `reply`, `error`; three partial indexes, below |
| `turn_events` | durable per-turn event stream, one row per non-transient event | `id` BIGSERIAL PK, `session_key`, `turn_id`, `seq`, `ts`, `type`, `payload` JSONB; index `turn_events_lane (session_key, id)` |
| `turn_costs` | per-turn spend | `id` BIGSERIAL PK, `ts`, `session_key`, `cost_usd` (aggregated by `cost_by_day` for `cost_report`), **`owner_id`** BIGINT NULL → `users.id` (`0005`) |
| `extensions` | installed-extension registry, **one row per owner per catalog name** | **`(owner_id, name)` PK** (`0006`; `owner_id` BIGINT **NOT NULL** → `users.id` `ON DELETE CASCADE`), `kind`, `catalog_version`, `state` (`enabled`/`disabled`), `installed_at`, `installed_by` BIGINT (audit, within that owner's row), `last_error` (`needs_reauth` is a sentinel the connectors tab reads), `tool_count` (NULL = never probed), `tool_names` (JSON list as text) |
| `oauth_tokens` | one owner's sign-in with a remote MCP server | **`(owner_id, name)` PK** (`0006`, NOT NULL, CASCADE), `issuer`, `client_id`, `client_secret`, `access_token` (mirrored into that owner's `extension_secrets` so the composed MCP server can read it), `refresh_token`, `expires_at`, `scope`, `updated_at` |
| `files` | opaque ids for files crossing the turn boundary | `id` PK (uuid4 hex), `session_key`, `path` (an **address** since Wave 3a — see below), `name`, `media_type`, `size`, `created_at`, **`owner_id`** BIGINT NULL → `users.id` (`0005`, and it is this column — never the requester — that decides which tree the blob mirror resolves in); index `ix_files_owner` |
| `extension_secrets` | the credentials an extension's MCP server needs, Fernet-sealed, **per owner** | **`(owner_id, name)` PK** (`0006`, NOT NULL, CASCADE), `ciphertext` BYTEA, `updated_at` (`0003`) — `name` is an env-var name, so two people's `NOTION_TOKEN` are two rows under the one `SecretBox` |
| `catalog_drafts` | the queue an agent proposes into | `(kind, name)` PK, `raw` TEXT, `status` (`pending`/`rejected`), `reason`, `created_at` (`0003`), **`owner_id`** BIGINT NULL → `users.id` (`0005`, who proposed — the queue itself stays deploy-wide) |
| `approval_snapshots` | the bytes a human was shown, held between the two halves of an approval | `(kind, name)` PK, `raw` BYTEA, `created_at` (`0003`) |
| `wiki_pages` | the **derived** full-text index over each user's markdown wiki (`0007`) — the one table here that is a cache, not state | `(owner_id, path)` PK — `owner_id` BIGINT NOT NULL → `users.id` **`ON DELETE CASCADE`**, `path` the wiki-root-relative posix path **without** `.md`; `ns`, `title`, `summary`, `content` (body, frontmatter stripped, capped at 200k chars), `tags` TEXT[], `links` TEXT[], `shared` BOOLEAN, `indexed_at` TIMESTAMPTZ, `content_tsv` (generated, persisted); GIN `ix_wiki_pages_content_tsv`, GIN `ix_wiki_pages_links`, partial `ix_wiki_pages_shared WHERE shared` |
| `content_items` | the content plan: one row per publication on one channel (`0010`, [[component.content]]) | `id` BIGSERIAL PK, **`owner_id`** BIGINT **NOT NULL** → `users.id` `ON DELETE CASCADE`, `channel`, `format`, `title`, `body` (exactly what is published), `media` JSONB (`[{"path", "sha256"}]`, paths relative to the owner's `agent_home`, hashes set at approval), `publish_at` (NULL = backlog), `status` (`idea`/`planned`/`drafting`/`draft`/`review`/`scheduled`/`queued`/`publishing`/`published`/`failed`/`stuck`/`skipped`), `group_key`, `source_path`, `thread_id` (links, never content), `created_via` (`web`, `chat` or `cron:<job_id>`), `approved_at`, `approved_by`, `attempts` + `next_wake_at` (the tick's wake bookkeeping), `claimed_at`, `published_url`, `error`, `created_at`, `updated_at`; since `0011` `theme_approved_at`, the drafter's `draft_attempts`, `draft_wakes`, `draft_next_wake_at`, `drafting_since`, `redrafts`, `draft_comment`, and the card outbox `card_state` (`none`/`pending`/`sending`/`sent`/`failed`/`superseded`), `card_revision`, `card_chat_id`, `card_message_id`, `card_attempts`, `card_claimed_at` — every time ISO-8601 UTC TEXT; indexes `ix_content_items_owner`, `(owner_id, publish_at)`, `(status, publish_at)` |

## What changed from the SQLite era, and why

The schema is deliberately 1:1 with the old `state.db` — same table names, same
column names — so Wave 2 moves code, not data. Four departures:

- **`messages_fts` becomes `messages`.** FTS5 was a virtual table; PostgreSQL has
  no equivalent, so the message store is an ordinary table whose `content_tsv` is
  a **generated** column (`to_tsvector('simple', regexp_replace(content,
  '[^[:alnum:]]+', ' ', 'g'))`, stored) with a GIN index. The regexp is not
  cosmetic: PostgreSQL's parser keeps `report.pdf` or a URL as one indivisible
  token, so a search for `report` would miss it. The query side strips
  punctuation the same way, and the two have to change together or search goes
  quietly half-blind.
- **Surrogate keys.** `job_runs` and `turn_costs` gained `id BIGSERIAL`: the ORM
  needs a primary key on every mapped table. `messages.id` additionally breaks
  `ts` ties — two messages of one turn share a timestamp, and `last_message` has
  to mean the one written last.
- **Telegram ids are `BIGINT`** (`deliver_chat_id`, `owner_user_id`,
  `installed_by`) — they no longer fit in 32 bits.
- **`turn_events.payload` is `JSONB`**, not JSON text. `events_after` hands back a
  dict either way, so the wire shape is unchanged.

Wave 3a's `0003` is the first migration that is *not* 1:1 with the old file: it
adds two columns and three tables that had no SQLite ancestor, because what they
replace was not a table at all (see below).

Timestamps stay **ISO-8601 UTC `TEXT`**, not `timestamptz`. Every comparison in
the codebase is lexicographic on those strings; converting the type is a change
each owning module makes when it takes its table over in Wave 2, not a
cross-cutting one now. `wiki_pages.indexed_at` is the one `TIMESTAMPTZ`: the
table has no SQLite ancestor to stay 1:1 with and nothing compares the column to
a string — it is a cache's write stamp, read by no query.

`users` / `owner_id` arrived in Wave 4a as migration `0005` — one migration over
every table rather than a schema rewrite, which is what having Alembic in place
bought ([[decision.multi-user-isolation]]).

## Contract notes

- `session_key` format: `tg:<chat_type>:<chat_id>[:<user_id>][:t<thread_id>]`,
  `cron:<job_id>`, or `web:<user_id>:<uuid4 hex>`; an absent thread reproduces the
  pre-threads key byte-for-byte.
- `system_append` must be reused byte-identical across resumed turns
  ([[decision.prompt-cache-discipline]]).
- **Search never raises on model input.** `search_messages` builds its query with
  `plainto_tsquery`, which treats the input as words — no operator soup from the
  model becomes a syntax error. Results are ranked by `ts_rank`; the snippet comes
  from `ts_headline` with `StartSel=[, StopSel=]`, the same shape SQLite's
  `snippet()` produced, so the web highlighting and the Telegram recall block
  render unchanged. The `'simple'::regconfig` cast (`_TS_CONFIG` in
  modules/sessions/infrastructure/repositories.py) is load-bearing:
  asyncpg types every bind parameter, and a `regconfig` argument sent as varchar
  makes PostgreSQL fail to resolve the function at all.
- The `cron:<job_id>` rows in `messages` are not only recall: the newest assistant
  row is what `Database.last_message` hands the cron→topic context bridge
  ([[component.cron]]), so the executor must keep recording its output there.
- `extensions` records *that*, never *what*: no command/url/args/repo columns
  exist by design ([[decision.readonly-catalog-boundary]]).
- **The three extension tables are keyed by owner, and the key is the whole
  enforcement.** `state`, `last_error`, `tool_count` and `tool_names` are one
  user's verdict on one server — a probe with their token can fail while
  another's succeeds — so an overlay table beside a shared install row would make
  two verdicts fight over one column. Every repository statement filters
  `owner_id`, and every port takes it keyword-only with **no default**, so a
  forgotten call site is a `TypeError` rather than a silent read of the deploy
  owner's credentials ([[component.extensions]]).
- The session row is committed before the reply is delivered so a crashed turn
  never loses the mapping (PRD NFR-3).
- **`threads` is not `sessions`.** Thread metadata (title, archive state, owner)
  must outlive a context reset, so `reset_session` deletes the `sessions` row and
  never touches `threads` — and `reset_all_sessions` (`DELETE FROM sessions`, what
  an extensions mutation calls through `runner.reset_all`) is the same rule at
  table scale: every lane loses its resume id, no lane loses its thread. Since
  Wave 4b there is a middle form, **`reset_owned(owner_id, *,
  include_unowned=False)`** — `DELETE FROM sessions WHERE owner_id = :o`, plus
  `OR owner_id IS NULL` when the caller is the deploy owner — which the skills
  mutations use because a skill edit changes only its author's cached prefix.
  The NULL belt is there for lanes bound while the deploy owner was still `0`
  (the runner writes `None` for owner `0`): those are the owner's, and only the
  owner's reset may sweep them.
  `set_thread_title(..., manual=False)` is a no-op once `title_manual` is 1: an
  automatic title never overwrites one the user typed.
- **`meta` keys are per user by suffix, never by a column.** The learning loop
  reads and writes its seven rows through `ScopedMeta(meta, suffix)`, which
  appends `""` for the owner — decided by the workspace (`root == home`), so an
  unclaimed deploy's tree keeps the bare keys too — and `:<user_id>` for anyone
  else. The owner's rows are therefore **byte-identical to 4a's** (no migration;
  `skill_admin status` reads three of them by literal; `scripts/import_sqlite.py`
  copies the whole table so suffixed rows come along for free). The unsuffixed
  `curator_paused` is the operator's switch: nothing in the code writes it, and it
  stops every user's curator pass; `curator_paused:<id>` stops one. The review
  lease `learning_review_until[:<id>]` is per user and is a single-flight rule
  for that user, no longer the deploy-wide concurrency bound (that is
  `lock:review` in Redis, or the inline queue limit) ([[component.learning]]).
- **`turn_events.id` is the SSE cursor** — a client passes `after=<id>` and the
  stream replays from there ([[component.web]]). `text.delta` and
  `thinking.delta` events are deliberately **never** persisted, so the table grows
  per block, not per token.
- **The `turns` row is the one source of truth for "running".** Every busy check
  in the system — the web route's 409, the sidebar's per-lane dot, the Telegram
  adapter's retry — reads `turns`, never the in-flight snapshot in Redis, which a
  dead worker's keys outlive by up to `turn_snapshot_ttl_seconds`.
- **`turns` is a work-submission surface, not an authorization.** Anything that can
  write this database can insert a row and have a worker spawn the CLI with chosen
  `text`, `user_id` and `channel`. That is the same posture the `jobs` table has
  had since Wave 3a — env scrubbing is inheritance hygiene, not a boundary — and
  the read-only catalog mount stays the boundary
  ([[decision.readonly-catalog-boundary]]). The worker re-validates every
  `attachment_ids` entry against the row's own `session_key`, so a forged row is
  still not a reason to read another lane's file.
- **`files.path` is not authority.** It holds an *address* since Wave 3a —
  `local:<key>`, `s3://<bucket>/<key>`, or a bare absolute path for a row written
  earlier, which `files.domain.addressing.normalize` reads as `local:`. Whichever
  it is, `layout.resolve_within` re-checks that the resolved path is under
  `agent_home` before anything is opened, downloaded or written, so a stale or
  forged row cannot become an arbitrary file read
  ([[decision.minio-blobs]]).
- `jobs.timeout_seconds` is NULL for "inherit `cron_timeout_seconds`", never the
  config value copied in. Materializing the default would freeze each job at the
  limit that happened to be configured when it was created, so raising the global
  knob would stop reaching existing jobs.
- Every caller goes through a module repository (`SessionRepository`,
  `MessageRepository`, `ThreadRepository`, `JobRepository`,
  `SqlExtensionRepository`, `SqlOauthTokenRepository`, …), and they all hold the
  same `AsyncEngine` and run one statement per short transaction — no object
  hands a live session around, and since Step 5 no facade fronts them.
- **`jobs.claimed_at` / `jobs.lease_until` are never read outside
  `JobRepository`.** They are bookkeeping for `claim_due`, not state a service
  reasons about; the only thing a caller sees is the `fire_at` key on a claimed
  row ([[decision.redis-taskiq-scaling]]).

## Migration `0003` — leases, and three things that were files

`0003_queue_and_extension_state.py` adds `jobs.claimed_at` and `jobs.lease_until`
(both NULL for every existing row, which reads as "never claimed" — the pre-3a
state exactly) plus the three extension-state tables.

**The leases.** `claim_due` selects due jobs `FOR UPDATE SKIP LOCKED` and, in the
same transaction, moves `next_run_at` forward and stamps
`lease_until = now + max(timeout_seconds, 300s)`. That single transaction is the
at-most-once guarantee across processes; the proof is in
[[decision.redis-taskiq-scaling]]. The lease guards the window between the claim
and a worker picking the task up, nothing more: a run that outlives it is not
re-claimed, because `next_run_at` has already moved past the fire.

**The three tables replace three paths on the data volume** — `data/secrets.env`,
`data/catalog-drafts/` and `data/pending-approvals/`. The volume is writable by
the agent, which has Bash; a database the agent reaches only through the
application is not ([[decision.readonly-catalog-boundary]]).

| Table | Why this shape |
|---|---|
| `extension_secrets(name, ciphertext BYTEA, updated_at)` | A Fernet token is bytes and stays bytes end to end — round-tripping it through TEXT is one encoding assumption away from an undecryptable row. The key is `CH_SECRETS_KEY` (`core/crypto.py::SecretBox`), held in the environment and popped at startup, so the ciphertext and the key are never in the same place. A stolen dump is not a stolen credential. |
| `catalog_drafts(kind, name, raw TEXT, status, reason, created_at)` | `raw` is the YAML **exactly as submitted**, never a re-serialisation, because the approval token is `sha256(raw)[:8]`. A draft the validator has since tightened against becomes `status='rejected'` with the validator's own words, so an unapprovable request stays visible instead of silently vanishing. |
| `approval_snapshots(kind, name, raw BYTEA, created_at)` | The bytes a human was shown, held between the two halves of `/approve`. Storing the parsed manifest would make the token check compare a manifest to itself. |

Nothing is backfilled by the migration: the secrets have to be **encrypted** on the
way in, with a key a schema migration does not have. The one-shot import is
`scripts/import_data_dir.py` (`docs/deploy.md` §4b, [[system.operations]]) — run
deliberately, idempotent, and it deletes nothing.

## Migration `0004` — the turn becomes a row

`0004_turns.py` adds the `turns` table, its three partial indexes and
`sessions.index_mtime`. It is the migration Wave 3b is built on: before it, a turn
was a coroutine inside whichever process took the message, and "is this lane busy?"
was an `asyncio.Lock` in that process's memory ([[decision.redis-taskiq-scaling]]).

```sql
CREATE UNIQUE INDEX turns_one_active_per_lane ON turns (session_key)
  WHERE status IN ('queued', 'running');
CREATE INDEX turns_active_lease ON turns (lease_until)
  WHERE status IN ('queued', 'running');       -- the reaper's scan
CREATE INDEX turns_finished ON turns (finished_at)
  WHERE finished_at IS NOT NULL;               -- pruning
ALTER TABLE sessions ADD COLUMN index_mtime DOUBLE PRECISION NULL;
```

**The unique partial index IS the lane lock.** Nothing anywhere does
check-then-insert: `TurnRepository.create` inserts and translates the index's
`IntegrityError` into `LaneBusy`, which the web route renders as the same
`409 busy` its courtesy pre-check does. Two API instances that both believe a lane
is free still produce exactly one turn, and that property is a property of
PostgreSQL rather than of how the processes are arranged.

Every `TurnRepository` method is **one** statement:

| method | the statement, and why it is shaped that way |
|---|---|
| `claim` | `… SET status='running', worker_id=:w, lease_until=:l WHERE id=:id AND status='queued' RETURNING *`. **`queued` only** — a `running` row whose lease lapsed belongs to the reaper and is never re-executed, because re-running a half-completed SDK loop doubles its side effects and its cost. |
| `heartbeat` | extends the lease `WHERE id AND worker_id AND status='running'`, returning `cancel_requested_at`. `None` back means the row is no longer ours. |
| `finish` | the terminal write, **guarded by `worker_id`**, so a worker whose turn was already reaped cannot flip the row; it returns a bool and the refusal is logged with the row's real status. |
| `fail_queued` | the one terminal write with **no** `worker_id` — there is no worker yet. It is narrowed to `status='queued'` instead, and its only caller is the launcher's dispatch-failure branch. |
| `request_cancel` | one `UPDATE … SET cancel_requested_at=:now, status = CASE WHEN status='queued' THEN 'cancelled' ELSE status END …`, so a claim racing it is decided by the status check rather than by a read. |
| `active` / `active_lanes` | "busy" **ignores the lease** — the index defines busy and the reaper is the only thing that frees a lane, so `is_busy` can never disagree with `create`. `active_lanes` answers a whole page of the sidebar in one query. |
| `expired` / `mark_lost` / `prune` | the reaper's three: active rows past their lease, the `lost` write, and the delete of terminal rows older than `turn_retention_days` (30) — the row holds a third copy of `text`/`reply`, so it is not kept forever. |

**`sessions.index_mtime` is a cache line, not history - and since `0005` it is
the mtime of *that lane owner's* wiki index**, because the wiki is per user
([[decision.multi-user-isolation]]).  `index_refresh_factory`
re-injects the whole wiki index (up to `wiki_index_max_chars`, ~2k uncached tokens)
whenever the index file's mtime moved past what this lane last saw. That "last
saw" used to be a process-local dict, which with N workers × 4 tasks meant a lane
landed on a process that had never seen it on a large fraction of turns — a direct
hit on the cache-hit-rate NFR. `SessionLane.route` now returns the column,
`TurnService` seeds its in-process dict with `max(local, row)` on **every** turn
(a second worker may have bumped it past this one's copy), and `bind`/`touch`
write it back after the turn. `index_mtime=None` means "leave it alone": writing
NULL from a turn that injected nothing would un-say an earlier injection.

## Migration `0005` — users and owners

`0005_users.py` creates `users`, adds `owner_id` to six tables, gives
`threads.owner_user_id` the same FK, and backfills every one of them. Three things
in it are the whole design ([[decision.multi-user-isolation]]).

**`users_one_owner` is the claim arbiter.** A UNIQUE *partial* index over
`role = 'owner'` — one owner ever, any number of users. Two first private chats
racing produce one owner row and one `IntegrityError`, which `AccessControl.check`
answers `"deny"`. Same shape as `turns_one_active_per_lane`, and for the same
reason: a check-then-insert is a race.

**The owner's `workspace_dir` is `''`, and must stay so.** Their root stays
`CH_HOME` itself, so `/data/agent_home`, `/data/.claude-cli` and `/data/logs` do
not move: a live volume needs no file migration and every SDK session keeps
resuming. Everybody else gets `users/<id>`. Since Wave 4b it is also what keys
the learning `meta` rows: `root == home` is how the owner's scope gets the empty
suffix and the seven pre-4b keys (`curator_paused` among them). A future wave
that promoted a `users/<id>` row to owner without moving their tree to `''` would
silently move those rows under a suffix and stop the operator's pause being read.

**The backfill DERIVES the owner from the session key; it never blanket-assigns.**
`UPDATE ... SET owner_id = <the deploy owner> WHERE owner_id IS NULL` would hand an
allowlisted user's private lanes, messages and files to the owner — and, because
the workspace resolver applies the same rule, would resolve that user's lanes into
the owner's tree forever, silently. So the rule is `lane_owner` spelled in SQL over
the four keyed tables (`sessions`, `messages`, `files`, `turn_costs`) and over
`threads.owner_user_id WHERE NULL`:

```sql
CASE
  WHEN split_part(session_key,':',1) = 'web'
       AND split_part(session_key,':',2) ~ '^-?[0-9]+$'
    THEN split_part(session_key,':',2)::bigint          -- web:<uid>:...
  WHEN split_part(session_key,':',1) = 'tg'
       AND split_part(session_key,':',2) = 'private'
       AND split_part(session_key,':',3) ~ '^-?[0-9]+$'
    THEN split_part(session_key,':',3)::bigint          -- tg:private:<chat>[:t<n>]
  ELSE :owner                                           -- groups, cron, anything unseen
END
```

`split_part` rather than `LIKE`, with a regexp guard, so a key grammar nobody has
seen falls through to the deploy owner instead of raising a cast error halfway
through a volume's upgrade.

The rows it inserts, in order:

1. the **owner** from `meta.owner_user_id`, `role='owner'`, `workspace_dir=''`,
   `ON CONFLICT (id) DO NOTHING`;
2. **one `role='user'` row for every distinct non-owner id those keys yield**,
   `workspace_dir = 'users/<id>'` — exactly what `AccessControl.check` would have
   created on their first message — inserted *before* any backfill, or the foreign
   keys would reject the very rows the migration is trying to attribute;
3. `jobs` and `catalog_drafts` -> the deploy owner outright: both reach the deploy
   only through owner-only routes today, so there is no key to derive from.

**`sessions.sdk_session_id` is set to NULL for every lane whose derived owner is
not the deploy owner.** That lane's `cwd` and `CLAUDE_CONFIG_DIR` move to the
user's own root, so the CLI transcript the id names is unresumable anyway; nulling
it turns a guaranteed failed `resume` into a clean first turn.

**`turns.user_id` gets no foreign key.** It is the *sender*, an audit field, NOT
NULL, and a group turn's sender may have no row at all; constraining it would break
every test that inserts a turn with an arbitrary id and buy nothing, because a
turn's *workspace* comes from `lane_owner` and not from that column.

On a fresh database (no `meta.owner_user_id`) there is nothing to backfill and
every data step is a no-op. A database with rows and no owner is not a state the
app produces.

**`0005` is the only materialiser of the owner row (Step 5).** Until Step 5 the
ACL mirrored the owner into `meta.owner_user_id` and could rebuild the row from
it; now a claim writes one `users` row and nothing else, and the meta key is read
by nobody — a stale one on a live volume is inert (`DELETE FROM meta WHERE key =
'owner_user_id'` for the tidy-minded). The one-way door that buys: **never
downgrade a claimed deploy past `0005`.** A downgrade followed by an upgrade
would find no meta key to materialise from and yield an unclaimed deploy that the
next private DM could claim; recovery is `INSERT INTO users (id, role,
workspace_dir, …) VALUES (<id>, 'owner', '', …)` ([[decision.multi-user-isolation]]).

### Who scopes what (the per-table rule)

| table | write | read |
|---|---|---|
| `sessions` | `bind`/`touch` carry `owner_id` from `lane_owner`; `reset_owned(owner_id, include_unowned=)` deletes one owner's lanes (4b) | `owner_of(session_key)` for the resolver |
| `threads` | `touch(..., owner_user_id=lane_owner(...))` | `list_visible`/`get_visible`, policy in `modules/sessions/domain/policies.visible` |
| `messages` | `add(..., owner_id)` | `search(query, owner_id, ...)`; the recall tool passes the turn's owner |
| `files` | `put_upload(..., owner_id)`, `publish(..., owner_id)` | `get(file_id)` unchanged; `attachments_for` also checks `owner_id`; the blob root is resolved from the **row's** owner |
| `jobs` | `create(..., owner_id)` - the turn's owner via the agent tool, the request user via the routines API; since 4b `create_job(owner_id=None)` resolves the **deploy owner** (`SchedulerService(deploy_owner=)`), NULL only on an unclaimed deploy | `list(owner_id)`, `get(id, owner_id)` |
| `turn_costs` | `record(..., owner_id)` | `cost_by_day(owner_id)` - the report stays owner-only, now scoped to their own turns |
| `catalog_drafts` | `propose(..., owner_id)` | listing unchanged (owner-only routes); the row records who proposed |
| `turn_events` | - | no owner column: read by lane, and the lane is already guarded by the thread policy |
| `extensions` | `add`/`set_state`/`set_error`/`set_tools`/`remove(..., *, owner_id)` — the lane owner on a turn, `job["owner_id"]` on a routine, `user.user_id` on a route | `get`/`list`/`enabled(*, owner_id)`; `owners_of(name) -> list[int]` is the one unscoped read, for the two clone-level events (`DELETE /api/plugins/{name}` and a re-fetch for review), and answers ids, never a credential |
| `extension_secrets` | `put`/`forget(name, *, owner_id)` | `load`/`names(*, owner_id)` — plaintext only inside `PgSecrets.load` and `compose.server_spec` |
| `oauth_tokens` | `put`/`delete(name, *, owner_id)` | `get(name, *, owner_id)`; the refresh lock is `lock:oauth:<owner>:<name>` |

`approval_snapshots` and the `catalog_drafts` queue stay deploy-wide by decision,
not by omission: approving is one owner-only channel over a shared catalog
([[decision.readonly-catalog-boundary]], [[decision.multi-user-isolation]]).

### NULL owner semantics

Every `owner_id` `0005` added is nullable with `ON DELETE SET NULL`, for two
reasons: an old row has to survive the migration, and deleting a user must not
delete their history. (The three `0006` columns are the deliberate exception —
NOT NULL, `ON DELETE CASCADE`, for the reason that section gives.)
But **nothing writes NULL** on a claimed deploy — every code path carries a
non-NULL owner (since 4b that includes a routine created with no lane and no
requesting user, which now resolves to the deploy owner) — and a NULL row is
**visible to nobody**: every scoped read compares against a real id. Two
deliberate exceptions, both belts for the deploy owner: the visibility belt for
group threads (`policies.visible`), which shows the deploy owner a non-`web:`,
non-`cron:` lane whose `owner_user_id` is unset, so a group thread the backfill
somehow missed can never vanish from the sidebar with no way to get it back; and
`sessions.reset_owned(..., include_unowned=True)`, which lets the owner's skill
edit also reset the lanes that were bound while the deploy was still unclaimed
(the runner writes `None` for owner `0`) — those lanes are the owner's.

## Migration `0006` — per-user extensions

`0006_per_user_extensions.py` moves `extensions`, `extension_secrets` and
`oauth_tokens` from a `name` primary key to `PRIMARY KEY (owner_id, name)`. Three
things in it are the design ([[decision.multi-user-isolation]]).

**One row per owner, not an overlay beside a shared install row.** `state`,
`last_error`, `tool_count` and `tool_names` are already one user's verdict on one
server: a probe with their token can fail while another's succeeds, and a single
row makes those two verdicts fight.

**`owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE`** — unlike
every `owner_id` `0005` added, which is nullable with `SET NULL`. There a row
could outlive its owner harmlessly; here an ownerless row is a decryptable
credential belonging to nobody, and therefore to everybody. Deleting a user takes
their connectors, their keys and their tokens with them, which is the intended
meaning of deleting a user.

**The backfill assigns the deploy owner, and refuses to guess.** Only an owner
could install, so any existing row implies an owner row; if the table somehow
holds rows and `users` holds no `role='owner'`, the upgrade **raises** rather than
inventing an id or leaving the column NULL. On a fresh database every data step is
a no-op.

**`downgrade` is destructive and says so.** A primary key on `name` alone cannot
hold two people, so it keeps the deploy owner's rows and DELETES everybody else's.
Their OAuth tokens do not come back — `CH_SECRETS_KEY` protects a dump, not a
deleted row — so the extra dump of the three tables is a prerequisite of the
upgrade, not of the rollback ([[system.operations]]).

Constraint names follow `core/db/base.NAMING` (`pk_<table>`,
`fk_<table>_owner_id_users`), the same convention `0005` used, because
`test_upgrade_head_matches_orm_metadata` compares the migration against the ORM
metadata and a differently-named constraint is a difference.

## Migration `0007` — the wiki index, and the one table that may be dropped

`0007_wiki_pages.py` adds `wiki_pages`: one row per `(owner_id, path)` over the
markdown under each user's `agent_home/wiki/`, written by memory's
`WikiIndexService` and read by the `memory_search` tool
([[decision.wiki-index-not-store]]).

**It is the only table here that is derived.** The markdown is the state; these
rows are a cache re-read from disk, so `DROP TABLE wiki_pages` + `alembic upgrade
head` + a reboot costs nothing but the boot sweep, and no read path treats a row
as a fact the file does not also carry. That is also why indexing swallows and
logs **per page** before it swallows per call: a missing, stale or failed row is
never an error for a turn, a lint or a boot, and one note that will not decode
is not a reason for the rest of a sweep to stay stale
([[component.core-runtime]]). `raw/` sources get no row from any trigger — a
fetched source is not memory.

```sql
CREATE TABLE wiki_pages (
  owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  path TEXT NOT NULL,                        -- 'topics/tea', never 'topics/tea.md'
  ns TEXT, title TEXT, summary TEXT, content TEXT,
  tags TEXT[], links TEXT[],
  shared BOOLEAN NOT NULL DEFAULT false,
  indexed_at TIMESTAMPTZ NOT NULL,
  content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple',
    regexp_replace(title || ' ' || summary || ' ' || content,
                   '[^[:alnum:]]+', ' ', 'g'))) STORED,
  PRIMARY KEY (owner_id, path));
CREATE INDEX ix_wiki_pages_content_tsv ON wiki_pages USING gin (content_tsv);
CREATE INDEX ix_wiki_pages_links       ON wiki_pages USING gin (links);
CREATE INDEX ix_wiki_pages_shared      ON wiki_pages (shared) WHERE shared;
```

**`ON DELETE CASCADE`, not the `SET NULL` every `0005` column uses.** A wiki
belongs to exactly one person and a row whose owner is gone is a page searchable
by nobody — the opposite of history worth keeping, which is what `SET NULL`
protects elsewhere. Deleting a user therefore deletes their index and leaves
their markdown on disk, where it always was.

**The primary key is `(owner_id, path)`, and identity is that pair everywhere** —
the `seen` set of the search, the neighbour exclusion, the returned dicts. A
non-owner may invent their own `shared/home` beside the owner's shared
`shared/home`, and both are legitimately in their scope
([[decision.multi-user-isolation]]). `path` carries no `.md` because that is the
string `read_page()` and `WikiGraphService.graph()` already use, so the graph
route and the index name a page identically.

**`content_tsv` is generated, persisted and `'simple'`** — the same shape and the
same configuration `messages.content_tsv` uses, and for the same two reasons: the
`regexp_replace` splits `report.pdf` and URLs, which PostgreSQL's parser keeps as
one indivisible token, and no built-in stemmer handles Russian, so a stemming
configuration would search worse than none. Query and column strip punctuation
the same way and have to change together. Title and summary are concatenated into
the vector so a page is findable by its heading, not only by its body. The three
indexes answer the three queries: the GIN on `content_tsv` for the FTS hits, the
GIN on `links` for the backlink hop (`links && ARRAY[...]`), and the partial one
for the `owner_id = :me OR shared` scope a non-owner searches under.

**There is no embedding column.** Production runs `postgres:16-alpine`, which
carries no `pgvector`; a vector column arrives with its own migration when the
image does.

**Revision `0007`, chained after `0006`.** The two migrations were written on two branches from the same `0005`; at merge the wiki index was re-chained after the per-user extensions rather than left as a second head, so `upgrade head` has one head and the file numbers state the order a fresh volume replays them in. Neither touches the other's tables.

### `meta.hermes_tools` — the toolset fingerprint

`main.run()` asks the executor for `tool_fingerprint()` — a sha256 over the
name, description and input schema of every `mcp__hermes__*` tool it would
register on an owner's private lane (a comma-joined name list before the content
plan's v2) — compares it with this key (`sync_hermes_toolset`), and when it
moved calls `runner.reset_all()` and writes the new value — the same
write-only-when-changed shape `sdk_slash_commands` has, and the same invalidation
an extension install performs. Tool schemas are part of the cached prefix and
`allowed_tools` is rebuilt every turn while `system_append` is frozen on the
`sessions` row, so without this key a deploy that adds a tool swaps the toolset
under every live session ([[decision.prompt-cache-discipline]]). An absent key on
an existing volume — or the old name-list value — reads as "changed" and costs
exactly one deploy-wide reset.

## Migration `0008` — the model a lane runs

`0008_model_choice.py` adds eight nullable `TEXT` columns, a (model, effort) pair
in four places, and each place means something different
([[decision.model-choice-is-lane-state]]):

| Columns | Meaning | Written by |
|---|---|---|
| `threads.model/effort` | what a person CHOSE for the lane | `PUT /api/threads/{id}/model`, Telegram `/model` (`SessionService.set_model`) |
| `sessions.model/effort` | what actually RAN the lane's current session | `bind`/`touch` after every turn, from `AssistantMessage.model`; `set_session_pair` when a choice is cleared |
| `users.default_model/default_effort` | a user's default for new sessions on their own lanes | `PUT /api/me/model-default`, `/model default` |
| `jobs.model/effort` | a routine's own pair | routine create/edit (never `cron_manage`) |

The pair lives on `threads` rather than `sessions` because a choice must outlive
`/new` and every `reset_all`/`reset_owned`; `sessions` carries the second pair so
a changed default can wait for the next session — the resolver reads it back as
"what this live session runs".

**NULL everywhere is the deploy default**, which is what every lane, user and
routine ran before the migration, so nothing is backfilled. A live session whose
pair is NULL began before `0008` and counts as `(normalize(cfg.model), None)`;
that is also why clearing a lane's choice writes the lane's default into its
session rather than NULL. Values are catalog ids (`claude-opus-5-5`), never the
CLI's spelling (`claude-opus-5[1m]`); `core.model_catalog` maps between them, and
every write is validated against it (`bad_model`, `bad_effort`). `set_session_pair`
does not touch `updated_at` — a menu click is not activity and must not stretch an
idle session's life. Downgrade drops the eight columns; an older image simply
ignores them.

## Migration `0009` — per-user grants

`0009_user_grants.py` adds three nullable `BOOLEAN` columns with no server
default and no backfill; downgrade drops exactly those three, and an older image
ignores them ([[decision.multi-user-isolation]]):

| Column | Meaning | Written by | NULL means |
|---|---|---|---|
| `users.shell_enabled` | this person's lanes (and routines) get `Bash`/`Agent`/`Task` | `PATCH /api/users/{id}` (`UserRepository.set_grants`) | inherit `users_shell_enabled` |
| `users.learning_enabled` | the learning loop observes this person and sweeps their tree | the same PATCH | inherit `users_learning_enabled` |
| `sessions.shell_enabled` | the `has_shell` this session's toolset was built with | `bind`/`touch` after every turn | started before `0009` — under `is_owner_lane or users_shell_enabled` |

**Only `Grants.of` reads the two `users` columns** (identity's
`AccessControl.grants` and the member wire); the owner's row resolves to
`(True, True)` whatever it holds, and `set_grants` refuses the owner, so the
owner's columns stay NULL. `set_grants` is one `UPDATE` of only the named
columns and never an upsert — a grant must not create a row, because a row is
an admission. There is no writer that puts a column back to NULL.

**`sessions.shell_enabled` follows the `owner_id` rule** — `None` on
`bind`/`touch` leaves it alone, a bool writes it — because NULL there is a real
answer ("started under the pre-`0009` rule"), not an absence. The turn compares
it with the lane's `has_shell` at its start and, on a difference, deletes the
row through `reset` like any other reset; the fresh row is stamped by the next
`bind` ([[flow.message-turn]]). Every existing session is NULL after the upgrade
and compares equal to the rule it was started under, so the migration itself
resets nothing.

## Migration `0010` — the content plan

`0010_content_items.py` creates `content_items` (above) and touches nothing else;
downgrade drops it. The ORM model is `modules/content/domain/models.py`, imported
by `core/db/registry.py` like every other module's, and every statement against
the table is `modules/content/infrastructure/repository.py`'s.

- **`owner_id` is NOT NULL with `ON DELETE CASCADE`**, unlike `jobs.owner_id`
  (`SET NULL`): an ownerless item is readable and publishable by nobody, so there
  is no history worth keeping. v1 plans exist for the deploy owner only, but the
  column is on every row, so a plan per user later needs no migration.
- **Every state change is a conditional `UPDATE`** naming the statuses it may
  leave, so a web request and the tick racing on one row cannot both win. `claim`
  is one `UPDATE … WHERE status='queued' … RETURNING` (two publishers → one
  winner); the tick's promotion (`scheduled → queued`), the late-window failure,
  and the wake count (`attempts`, `next_wake_at`, advanced by a compare-and-set on
  `next_wake_at <= :now`) are single statements too, so N workers ticking at once
  neither queue twice nor count a wake twice.
- **Times compare as text** because every writer goes through one helper
  (`domain/time.py` `iso_utc`: `astimezone(timezone.utc).isoformat()`), the same
  rule `jobs` lives by.
- The `jobs` side needed no migration: `manual` is a new value in the TEXT `kind`
  column, and its idle state is the NULL `next_run_at` the column already allows.

## Migration `0011` — drafting and the approval card

`0011_content_drafting.py` adds thirteen columns to `content_items` and nothing
else; every one is nullable or has a server default, so existing rows need no
backfill, and downgrade drops them in reverse. The two new statuses (`planned`,
`drafting`) need no DDL: `status` is free text owned by `domain/status.py`.

- **Two approvals, two columns.** `theme_approved_at` is the owner approving a
  THEME (`idea → planned`); `approved_at`/`approved_by` keep meaning the owner
  approved the CONTENT, which alone schedules a post.
- **Two budgets, two sets of columns.** `attempts`/`next_wake_at` stay the
  publisher's wakes; `draft_wakes`/`draft_next_wake_at` are the drafter's
  (advanced by the same compare-and-set shape), `draft_attempts` counts claimed
  runs that failed or timed out (3 → `failed`), `drafting_since` is the claim
  time the drafting timeout counts from.
- **The card outbox is a column set, not a table**: one card per item at a time.
  Every transition is one conditional `UPDATE` — `pending → sending`
  (`card_claimed_at`), `sending → sent` with the chat and message ids or back to
  `pending` with `card_attempts + 1`, and the approve itself `WHERE
  status='review' AND card_state='sent' AND card_revision=:rev`. A content write
  that must supersede a live card reads the old card state through a
  `FROM (SELECT … FOR UPDATE)` subquery, because PostgreSQL's `RETURNING` shows
  only the new row.
- No new index: the drafter and card queries run over the owner's handful of
  items, and `(status, publish_at)` already serves the tick.

## Importing an old `state.db`

`scripts/import_sqlite.py` copies a pre-PostgreSQL file in, once, with the app
stopped (`docs/deploy.md` §4a, [[system.operations]]). It reads the old file with
the stdlib `sqlite3` module — the runtime itself carries no SQLite driver at all
— and is safe to re-run: keyed tables insert `ON CONFLICT DO NOTHING`, and the
three append-only tables (`messages`, `job_runs`, `turn_costs`) are skipped
entirely once the target already holds rows, because nothing there could tell a
copy from the original. `jobs` and `turn_events` keep their ids (a delivery topic
and the SSE cursor both refer to them), so their sequences are advanced past the
imported maximum afterwards.

**It attributes what it copies.** `upgrade head` runs `0005` against the EMPTY
target, so 0005's backfill touches nothing, and the old file has no `owner_id`
columns to copy. The importer therefore finishes with 0005's own attribution —
`_DERIVED_OWNER` over `sessions`, `messages`, `files`, `turn_costs` and
`threads.owner_user_id`, the deploy owner on `jobs` and `catalog_drafts` —
imported from the migration module rather than copied, and only where the column
is still NULL. Until 2026-09-24 it did not: every imported row stayed NULL, and
every read scoped to the asking user left it out — the Routines page listed none
of production's 17 routines, message search missed the history before the move
and the cost report missed its spend, while the ticker (which reads NULL as the
deploy owner) kept firing them all. A deploy imported before the fix needs the
same `UPDATE … WHERE owner_id IS NULL` statements run once by hand
([[system.operations]]).

## `threads.has_turns`

Migration `0002` adds a boolean, not null, default false, backfilled with
`UPDATE threads SET has_turns = true WHERE EXISTS (SELECT 1 FROM turn_events e
WHERE e.session_key = threads.session_key)`; `scripts/import_sqlite.py` runs the
same statement after its copies.

It exists because the thread list used to decide "has this lane ever spoken?" by
joining `turn_events` — the one cross-module join left after the Wave 2 split,
and `modules/sessions` may not read `modules/turns`' table
([[decision.modules-and-ports]]). `upsert_thread(session_key, channel,
owner_user_id, now, *, talked: bool = False)` sets the flag when `talked=True`
and **never clears it**; the runner's turn-start touch and the cron delivery
touch pass `talked=True`, while thread creation from the web and job-topic
creation do not. `list_threads` lists `channel = 'web' OR title IS NOT NULL OR
has_turns`, so a silent backfilled lane stays visible exactly as before.

---
id: system.overview
kind: system
status: current
summary: Self-improving personal Telegram agent on the Claude Agent SDK — one asyncio process in Docker, relational state in PostgreSQL and everything the agent works with in the ./data volume.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:4553a39abf183ecfed18358aecd83fc08220b2e2fa879c059b31169eaf6f6bac
evidence:
  - path: docs/PRD.md
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: pyproject.toml
  - path: Dockerfile
  - path: docker-compose.yml
  - path: README.md
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/modules/learning/application/scopes.py
  - path: config.yaml.example
  - path: src/claude_hermes/core/model_catalog.py
  - path: .env.example
  - path: scripts/deploy.sh
  - path: src/claude_hermes/workers/taskiq_app.py
  - path: src/claude_hermes/http/deps.py
  - path: src/claude_hermes/core/db/migrations/versions/0006_per_user_extensions.py
  - path: src/claude_hermes/modules/identity/application/services.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
relations:
  - decision.model-choice-is-lane-state
  - component.core-runtime
  - contract.state-db
  - decision.postgres-and-modular-core
  - system.operations
  - component.gateway
  - component.extensions
  - component.learning
  - component.cron
  - component.web
  - decision.modules-and-ports
  - decision.multi-user-isolation
---
# System Overview

claude-hermes is a persistent, self-improving personal AI agent on the
**Claude Agent SDK (Python)** — an independent reimplementation of the ideas
behind NousResearch/hermes-agent. It lives on a VPS in Docker, is reached
through **Telegram** (long polling by default, or a webhook on the web channel's
public origin) and optionally a browser, and accumulates
capability over time: an agent-owned wiki memory that the model itself grows
and restructures, self-authored skills, full-text recall over all past
sessions, scheduled automations, and a curated extensions catalog.

The SDK provides the agent loop, tool dispatch, context compaction, prompt
caching, skills runtime, subagents, hooks and session transcripts. This repo
implements what the SDK does not: the messaging gateway, the learning loop,
scheduled automations, cross-session recall, and the extensions machinery
([[decision.claude-agent-sdk]]).

## Shape

The agent is one asyncio Python process (`python -m claude_hermes.main`). In the
default compose stack — `db`, `redis`, `minio`, `agent`, `worker`, `scheduler`,
`cloudflared` — `CH_REDIS_URL` selects queue mode: the same image also runs as
`worker` (taskiq: the turns, scheduled jobs, learning reviews and curator passes)
and `scheduler` (it only enqueues two periodic tasks), and only `agent` talks to
Telegram and serves HTTP. Unset `CH_REDIS_URL` and drop those two services and
everything runs inside the agent ([[decision.redis-taskiq-scaling]]). What the
process is made of:

- **[[component.gateway]]** — aiogram dispatcher; Telegram topics are session
  lanes; `/secret`, `/approve`, `/new`, `/model`, `/effort` are gateway-intercepted; live status
  drafts + rich final replies; optional OpenAI voice in/out; inbound photos and
  documents, ACL-gated on the caption before anything is downloaded.
- **[[component.core-runtime]]** — `TurnService` (one SDK `query()` per turn
  with `resume`), `SessionRouter`,
  `WikiStore`, custom `hermes` MCP server
  (`session_search`, `cost_report`, `cron_manage`, `skill_admin`, `ext_manage`,
  `send_document`, `ask_user` (private chats only, capped per turn),
  `memory_search` — a search over `wiki_pages`, the derived index of the wiki,
  not a memory store: the wiki is plain files under `agent_home/wiki/`, written
  with Write/Edit ([[decision.wiki-index-not-store]])), the ACL with each user's
  grants, bash guard, and the `core/` kernel — the
  SQLAlchemy engine, Alembic migrations run at boot, structlog and the
  `ApplicationException` hierarchy — over PostgreSQL ([[contract.state-db]],
  [[decision.postgres-and-modular-core]]).
- **[[component.learning]]** — nudge counters → background forked review pass
  (wiki + skills writes only) → provenance sidecar → weekly curator
  (archive-only skill aging + opt-in consolidation + a wiki-lint pass); since
  Wave 4b one scope of all that per user, each fork confined to its user's tree.
- **[[component.cron]]** — job store, ticker with catch-up window, isolated
  `cron:<id>` sessions, delivery into per-job topics.
- **[[component.extensions]]** — curated catalog of MCP servers/plugins;
  registry records *that*, the read-only catalog defines *what*
  ([[decision.readonly-catalog-boundary]]).
- **[[component.web]]** — optional second channel (`web_enabled`, off by
  default): a `TurnBus` of typed turn events, a FastAPI app on uvicorn in the
  same loop, Telegram Login Widget auth, REST + SSE, a file store, and the
  Customize tabs (connectors, plugins, skills, plus the shared draft-approval
  screen) and the owner's Settings (users, with each person's Bash and Learning
  switches, and extension secrets). A connector is each user's own; plugin
  installs and catalog approvals are the owner's. A mutation drops the lanes of
  the user it changed (`reset_owned`), and approving a catalog draft every lane
  (`reset_all`). Reached through an outbound Cloudflare Tunnel, or through Caddy
  terminating TLS on the host's 80/443 where there is no Cloudflare account —
  which is how production runs today ([[decision.web-as-a-channel]],
  [[system.operations]]).

**Multi-user, since Wave 4a.** A `users` row exists for everyone the agent has
actually talked to (`users.id` **is** the Telegram user id), every user-owned row
names its `owner_id`, and a turn runs in its **lane owner's** workspace — `cwd`,
the wiki, `inbox/`/`outbox/`, `CLAUDE.md`, `.claude/{settings.json,skills,agents}`,
`tmp/` and the CLI's own transcript store are per user. The deploy owner's root is
`CH_HOME` itself, so nothing on a live volume moved. **Since Wave 4b the learning
loop is per user too**: every user has their own skills library, `skill_admin`
and Skills page regardless, and — with a learning grant — their own nudge
counters, review forks, weekly curator pass and reports under
`users/<id>/logs/`, with one review fork at a time deploy-wide. **Connectors are
per user since migration `0006`**: each person connects their own, with their own
secrets and OAuth tokens (`owner_id` on `extensions`, `extension_secrets`,
`oauth_tokens`); the catalog, the plugin clones and the user-catalog stay
**deploy-wide and owner-managed**. Non-owner lanes get no `Bash`/`Agent`/`Task`
without a shell grant. Both grants — **Bash** and **Learning** — are set per
person by the owner in Settings → Users and read per turn, so no restart; a person
the owner has not chosen for inherits `users_shell_enabled` /
`users_learning_enabled` from `config.yaml`, both off by default (a shell reaches
every user's files and credentials in the one container, and learning forks run
on the owner's account). Tool presence is otherwise a property of the **lane**,
never the sender — on a group lane nobody, the owner included, has `skill_admin`,
`session_search`, `cost_report` or `memory_search`, because a per-sender flip would
change the cached prefix mid-session, and a group lane is the owner's lane, so
the grants are no boundary there. Inside one container every turn runs
as the same uid, so this is isolation against accident and a well-behaved model —
**not** a boundary against a hostile user; the `:ro` catalog mount stays the only
hard boundary and a container per user is the follow-up
([[decision.multi-user-isolation]]).

Telegram runs on **polling or webhook** (`telegram_mode`); webhook mode is what
makes more than one `agent` instance possible at all.

## Layout

Wave 2 split `src/claude_hermes/` into four kinds of code, and Step 5 added the
HTTP composition root and deleted everything else
([[decision.modules-and-ports]]):

```
core/          config, model_catalog, workspace, session_keys, naming, meta, contracts, events,
               snapshots, auth, di, db (engine, migrations, registry), tasks,
               redis, eventbus, crypto, logging, logsafe, exceptions,
               app_context  — imports no module
integrations/  claude_sdk (options, guards), openai (voice), s3  — one adapter per
               external SDK; imports core and its third-party package only
modules/<m>/   identity sessions turns files scheduler extensions memory
               learning telegram content — each with domain / application /
               infrastructure / presentation and its own README.md
workers/       curator, tasks, taskiq_app — the background loops and the queue
http/          app, deps — the FastAPI assembly and the WebDeps bundle
main.py        build_app() -> AppContext: the process composition root
```

`main.py`, `http/` and `workers/` are one composition layer (they import each
other; nothing below imports them).

A module never imports another module. What it needs from one it declares as a
`typing.Protocol` in its own `application/ports.py`, and `build_app()` hands it
an object that satisfies it — import-linter enforces both halves (15 contracts,
`pyproject.toml`: one layers contract `main : http : workers` > `modules` >
`integrations` > `core`, module independence, and the per-module layer and purity
contracts). The pre-Wave-2 paths (`runner.py`, `tools.py`, `sessions.py`,
`cron/*`, `gateway/*`, `extensions/*`, `learning/*`, `web/*`) were shims from
Wave 2 until Step 5 (2026-09-18, commit `12a3460`) deleted them; nothing under
`src/` or `tests/` imports a legacy path.

## Binding principles (PRD §4)

P1 prompt caching is sacred ([[decision.prompt-cache-discipline]]); P2 narrow
waist; P3 learning never competes with the user; P4 reversible autonomy
(archive, never delete); P5 truthful reporting; P6 declarative memory; P7
secrets in env, behavior in `config.yaml`; P8 no inbound ports on the host —
all connectivity outbound, the single public surface an HTTPS URL behind
Cloudflare Tunnel and Telegram-identity authorization
([[decision.web-as-a-channel]]).

## Deployment and state

The live deployment is `ssh deploy@<server-ip>` (your server), repo
checked out at `~/claude-hermes`, deployed by `./scripts/deploy.sh main` over
`docker-compose.yml` + `docker-compose.prod.yml` + `docker-compose.caddy.yml`
(build, stop, back up, start — [[system.operations]]).
The bot is `@your_bot`. **One Telegram token means one long-poller** — bring
this host down (`docker compose ... down`, which leaves `data/` untouched)
before running a container anywhere else on the same token, or both get
`409 Conflict` and neither answers. Runbook: `docs/deploy.md`.

`docker compose up -d` is the whole deployment: `db` (`postgres:16-alpine`),
`redis`, `minio`, `cloudflared`, and the app image — `python:3.12-slim` + Node 24
+ `@openai/codex` + `gh`, unprivileged user `hermes` (uid 1000),
`restart: unless-stopped` — as `agent`, `worker` and `scheduler`. The Claude Code CLI on the image's `PATH` comes from
Anthropic's native installer, run as `hermes` (it installs under `$HOME`, so
`/home/hermes/.local/bin/claude`) with `DISABLE_AUTOUPDATER=1`, pinned to 2.1.280
— the same version the pinned `claude-agent-sdk` 0.2.158 bundles and actually
runs (the SDK prefers its bundled CLI); `pyproject.toml` and the `Dockerfile` move
the pair together. Which models a chat may run, and how one is proved on that CLI
before it is offered, is [[decision.model-choice-is-lane-state]].

**State is split in two** — three with `blob_backend: s3`, where the MinIO
bucket holds the files — and a backup is only complete if it covers all of it
([[system.operations]]):

- **PostgreSQL**, in the `pgdata` volume — users and their grants, sessions,
  messages, threads, turns and their events, jobs and their runs, costs, the
  per-owner extension registry, its secrets and OAuth tokens, catalog drafts and
  the derived `wiki_pages` index ([[contract.state-db]]). The URL is `CH_DATABASE_URL`, composed by
  `docker-compose.yml` from the one `POSTGRES_PASSWORD` in `.env`; Alembic
  upgrades the schema at boot. `pg_dump` backs it up; `tar ./data` does not
  contain it.
- **The `./data` volume** (`CH_HOME=/data`) — everything the agent works *with*:
  `config.yaml`, `agent_home/` (persona, skills, plugins, and the `wiki/` the
  model owns), `user-catalog/`, `logs/` — and, with `blob_backend: s3`, a cache
  of the bucket. `secrets.env`, `catalog-drafts/` and `pending-approvals/` moved
  into PostgreSQL in Wave 3a ([[contract.state-db]]).

A deployment that predates the move keeps its `state.db` on disk until
`scripts/import_sqlite.py` has copied it in (`docs/deploy.md` §4a); nothing
reads that file afterwards. Legacy
`MEMORY.md`/`USER.md` are no longer written; if present from an older
deployment they are consumed exactly once by `WikiStore.seed()`, carried
verbatim into `wiki/user/profile.md` / `wiki/env/notes.md`, and left in place
untouched (modules/memory/infrastructure/wiki_store.py).
Secrets (`POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`,
`OPENAI_API_KEY`, `MINIO_ROOT_PASSWORD`, `CH_SECRETS_KEY`, and with the web
channel `CH_WEB_SECRET` and `TUNNEL_TOKEN`) arrive via `.env`. The agent and the
worker pop every one they already hold in an object — the bot token, the OpenAI
key, `CH_SECRETS_KEY`, the database, Redis and S3 credentials under both their
derived and their `.env` names, `CH_WEB_SECRET`, the webhook secret — from the
process environment at startup, so the agent's own Bash can never read them;
`CLAUDE_CODE_OAUTH_TOKEN` stays, because the CLI needs it.

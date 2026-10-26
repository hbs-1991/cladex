---
id: index.root
kind: index
status: current
summary: Navigation index for claude-hermes project knowledge.
verification:
  base_commit: 58a975e3df70e5cb14a9e5382d27fa8d169acded
  evidence_fingerprint: sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
evidence: []
relations: []
---
# claude-hermes LLM Wiki

Self-improving personal Telegram agent on the Claude Agent SDK. Start with
the [system overview](system.md); follow only the links relevant to your
task.

## System

- [System overview](system.md)
- [Wiki schema](schema.md)
- [Glossary](glossary.md)

## Components

Each component is one or more of the ten modules under
`src/claude_hermes/modules/` and every module carries its own `README.md` (ports,
routes, tools, invariants, debts) — read that next to the page here
([[decision.modules-and-ports]]).

- [Core runtime](components/core-runtime.md) — boot and `main.build_app()` in
  both queue modes, `core/` (config, workspace, session keys, naming, meta,
  engine, migrations, tasks, redis, the channel **eventbus**, crypto, logging,
  logsafe, exceptions, `AppContext`),
  `integrations/`, `workers/`, the turn as a row and a task — launcher, run
  service, control channel (`modules/turns`) — the lane router
  (`modules/sessions`), the wiki (`modules/memory`), the ACL and the `users`
  table (`modules/identity`), the per-user `Workspace` + `WorkspaceResolver`, the
  bash and path guards, Docker.
- [Telegram gateway](components/gateway.md) — `modules/telegram`: the polling or
  webhook transport, routing, intercepted commands, topics, presenter, rich
  replies, titler, voice.
- [Extensions](components/extensions.md) — `modules/extensions`: catalog,
  registry, install, encrypted secrets, the drafts and approvals tables, compose,
  probe, OAuth connectors.
- [Learning loop](components/learning.md) — `modules/learning` (+
  `workers/curator.py`): one `LearningScope` per user since Wave 4b — observe,
  nudges, review pass (one at a time deploy-wide), usage sidecar, the curator
  sweep, wiki lint, archive, backups, the per-user skills library and API.
- [Cron](components/cron.md) — `modules/scheduler`: job repository, schedules
  (including the on-demand `manual` kind and its one wake, `wake_if_idle`), the
  claim-based tick, the queue dispatcher, ticker, executor (and the tools a
  routine can be lent), the calendar read.
- [Content plan](components/content.md) — `modules/content`: `content_items`,
  theme approval, the drafter routine, the Telegram approval card, the owner's
  content approval bound to media hashes, the `content_plan` tool in chat and in
  the deploy owner's routines, and the tick that wakes both routines.
- [Web (CladeX)](components/web.md) — turn bus over the channel bus and the
  in-flight snapshot store, the FastAPI app assembled in `http/` (the HTTP
  composition root since Step 5) from one `APIRouter` per module with `Depends`-injected collaborators and an OpenAPI
  document at `/api/docs` behind login, Login-Widget auth, REST/SSE, file store
  (`modules/files`), and the SPA — including the Calendar (`/calendar`).

## Flows

- [Message → turn → reply](flows/message-turn.md)
- [Extension install & draft→approve](flows/extension-install.md)
- [Scheduled job](flows/scheduled-job.md)
- [Content publish](flows/content-publish.md) — plan, approve the theme, draft,
  card, approve (or redraft), tick, wake, claim, publish, report, retry

## Contracts

- [PostgreSQL schema](contracts/state-db.md) — including `users` and the
  `owner_id` on every user-owned row (migration `0005`), `wiki_pages`, the
  one derived table (migration `0007`), and `content_items` (migration `0010`)
- [Catalog manifest](contracts/catalog-manifest.md)

## Decisions

- [Build on the Claude Agent SDK](decisions/claude-agent-sdk.md)
- [PostgreSQL and a modular core](decisions/postgres-and-modular-core.md)
- [Modules and consumer-side ports](decisions/modules-and-ports.md) — and, since
  Step 5, no shims: the import-linter contracts (fifteen with `content layers`), the `http/` root, the
  `core/naming.py` rule, the owner mirror's removal
- [Redis, a worker process, and what stays a singleton](decisions/redis-taskiq-scaling.md)
- [MinIO for blobs, with the on-disk tree as a mirror](decisions/minio-blobs.md)
- [Prompt caching is sacred](decisions/prompt-cache-discipline.md) — and the
  boot-time `hermes_tools` fingerprint (names, descriptions and schemas) that
  resets every lane when the toolset moves
- [The wiki is the memory; the database is an index over it](decisions/wiki-index-not-store.md)
  — `wiki_pages` as a cache, `memory_search`, and why no store, no embeddings, no
  entity extraction
- [A lane's model is lane state, chosen in one place](decisions/model-choice-is-lane-state.md)
  — the pair on the thread row, the one resolve order, config offers / catalog
  describes, the single announced fallback
- [Read-only catalog as the security boundary](decisions/readonly-catalog-boundary.md)
- [Multi-user isolation: a workspace per user, inside one container](decisions/multi-user-isolation.md) — and, since 4b, a learning scope per user; extensions stay deploy-wide (final)
- [Telegram threads are session lanes](decisions/threads-as-session-lanes.md)
- [The web is a channel, not a second application](decisions/web-as-a-channel.md)

## Quality

- [Invariants](quality/invariants.md)
- [Testing strategy](quality/testing.md)
- [Failure modes](quality/failure-modes.md)

## Maintenance

- [Operations](operations.md) — where this runs, how to upgrade it, how to check.
- [Wiki health](health.md)
- [Maintenance log](log.md)

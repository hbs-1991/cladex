---
id: glossary.root
kind: glossary
status: current
summary: Project terminology compiled from the PRD, design specs and code.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:296cb969a5de7820d00582b50ad60e99b980a2c434f952c8843502c95a720a74
evidence:
  - path: docs/PRD.md
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/modules/sessions/application/services.py
  - path: src/claude_hermes/modules/extensions/infrastructure/catalog_repository.py
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/modules/memory/infrastructure/turn_hooks.py
  - path: src/claude_hermes/modules/turns/domain/prompt.py
  - path: src/claude_hermes/modules/learning/application/services/review.py
  - path: src/claude_hermes/modules/learning/application/services/curator.py
  - path: src/claude_hermes/modules/content/domain/status.py
  - path: src/claude_hermes/modules/scheduler/domain/schedule.py
relations: []
---
# Glossary

- **Session key** — deterministic string identifying one conversation lane:
  `tg:<chat_type>:<chat_id>[:<user_id>][:t<thread_id>]`, `cron:<job_id>`, or
  `web:<user_id>:<uuid4 hex>` for a browser thread (core/session_keys.py). The
  routing/isolation primitive, and the thread id on the web wire.
- **Session lane** — one Telegram topic (or one browser thread) ↔ one
  resumable SDK session; `/new` resets the lane.
- **Channel** — which surface a turn came from: `telegram`, `web`, or `cron`.
  Carried in `turn.start`, and the reason a browser turn does not post status
  drafts into Telegram.
- **Turn event** — one typed step of a turn (`turn.start`, `text.block`,
  `tool.call`, `tool.result`, `turn.end`, …) published on the `TurnBus`;
  persisted in `turn_events` except `text.delta`. See [[component.web]].
- **Thread** — the *metadata* of a lane (title, channel, owner, archive state)
  in the `threads` table; it outlives a context reset, unlike the `sessions`
  row.
- **System append** — the custom portion of the system prompt: the static turn
  rules, the static wiki rules, a snapshot of `wiki/index.md` taken at session
  start, and the date (prompt.py `build_system_append`); frozen per session and
  reused byte-identical on resume.
- **Nudge** — a counter-triggered, out-of-band self-review pass; never
  injected text in the live conversation (nudges.py).
- **Review pass** — the background `query()` that forks the live session
  (`fork_session=True`) with a restricted toolset to write wiki pages and
  skills (learning/review.py).
- **Wiki** — the agent-owned memory tree under `agent_home/wiki/`
  (`user/`, `projects/`, `topics/`, `env/`, `raw/`, plus `index.md` and
  `log.md`); the model reads and writes it with the same Read/Glob/Grep/
  Write/Edit tools it uses on any file — there is no memory-specific MCP
  tool (wiki.py).
- **wiki/raw/** — verbatim, immutable copies of ingested sources: Edit is
  always denied and Write is denied once a file exists, so a claim can
  always be checked against its untouched origin (hooks.py
  `raw_guard_factory`).
- **Wiki lint** — the curator's read-only-by-construction health pass over
  the wiki (a dry run gets an empty write-guard root list, so every write is
  denied); runs alongside skill aging/consolidation in the weekly curator
  pass (learning/curator.py `run_wiki_lint`).
- **Provenance** — recorded origin of a skill (`created_by: agent` vs user)
  in the `.usage.json` sidecar, gating autonomous maintenance.
- **Curator** — the periodic maintenance job that ages/consolidates
  agent-created skills; archive-only, no deletion path.
- **Umbrella skill** — a class-level skill covering a family of tasks,
  preferred over many narrow single-task skills.
- **CH_HOME** — the single state directory (`/data` volume) owning all agent
  data.
- **Catalog** — reviewed extension manifests in `catalog/` (repo, read-only
  mount); the only source of truth for what an extension runs.
- **User-catalog** — chat-approved manifests under `CH_HOME/user-catalog/`;
  loses to the repo catalog on name collision.
- **Manifest** — one validated YAML describing an MCP server (command/url)
  or a plugin (repo@pinned ref); see [[contract.catalog-manifest]].
- **Draft** — an agent-proposed manifest, a row in `catalog_drafts`; inert until a
  human approves it.
- **Registry** — the `extensions` table: records *that* an extension is
  installed, never *what* it runs.
- **Probe** — a no-model-call MCP connection test via `get_mcp_status()`.
- **Scope** — where an extension is visible: `turn` (chat) and/or `cron`;
  background passes (review, curator, titler) never see extensions.
- **Lane reset** — dropping a session row so the next message starts a fresh
  SDK session (fresh cached prefix); forced by extension mutations.
- **Titler** — the one-shot haiku call that names a new topic from its first
  message.
- **Catch-up window** — `min(period/2, 2h)`: a missed cron fire older than
  this is skipped as `missed`, not replayed.
- **Content plan** — the deploy owner's `content_items`: one row per publication
  on one channel (variants share a `group_key`), drawn on the web Calendar next to
  routine fires and runs; kept by the agent through `content_plan` and by the owner
  in the UI (modules/content/).
- **Approval** (of a content item) — the owner's act that moves an item to
  `scheduled`, binding its body, channel, format, time and a sha256 per media file;
  refused for an empty body or media the format cannot publish
  (modules/content/domain/formats.py); no tool action can approve
  (modules/content/domain/status.py). Done in the calendar or with a card's
  Одобрить button.
- **Theme approval** — the owner's act that moves an `idea` with a future time to
  `planned`: the drafter may now write it. It never schedules a post
  (`theme_approved_at`).
- **Approval card** — the drafted post sent to the owner's private chat (the
  «Контент» topic) with Одобрить / Переделать / Открыть в календаре; its buttons
  carry `cnt:a|r:<id>:<revision>` and approve that revision only
  (modules/telegram/infrastructure/cards.py).
- **Drafter routine** — the on-demand routine chosen in the calendar
  (`meta.content_drafter_job_id`) that the content tick wakes
  `content_draft_lead_hours` before a `planned` item's time; one item per run,
  the only run allowed `claim_draft`/`submit`/`draft_failed`.
- **On-demand routine** — a routine with the `manual` schedule: `active` with a
  NULL `next_run_at`, fired only by `run_now` or `wake_if_idle`, back to idle after
  each claim (modules/scheduler/domain/schedule.py).
- **Publisher routine** — the on-demand routine chosen in the calendar
  (`meta.content_publisher_job_id`) that the content tick wakes when an item is
  due; the only run allowed the tool's `due`/`claim`/`published`/`failed`.

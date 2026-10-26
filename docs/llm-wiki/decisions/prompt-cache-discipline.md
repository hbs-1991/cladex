---
id: decision.prompt-cache-discipline
kind: decision
status: current
summary: The system prompt is byte-stable per session; toolset changes reset the lane — a boot-time fingerprint of every hermes tool's name, description and schema catches a deploy's, and a per-session shell stamp catches a moved shell grant.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:427e73cb41b2479f1c92c1bce48dcc654040b05507f8d4e97e3ad781ee70b92f
evidence:
  - path: docs/PRD.md
  - path: docs/superpowers/specs/2026-07-14-extensions-mcp-plugins-design.md
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/modules/memory/infrastructure/turn_hooks.py
  - path: src/claude_hermes/modules/turns/domain/prompt.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/core/model_catalog.py
  - path: src/claude_hermes/modules/sessions/application/services.py
  - path: src/claude_hermes/modules/learning/infrastructure/fork_runner.py
  - path: src/claude_hermes/http/deps.py
  - path: src/claude_hermes/modules/turns/domain/policies.py
  - path: src/claude_hermes/modules/turns/domain/value_objects.py
  - path: src/claude_hermes/modules/sessions/infrastructure/repositories.py
  - path: tests/modules/turns/test_turns_shell_stamp.py
relations:
  - quality.invariants
  - flow.message-turn
  - contract.state-db
  - decision.wiki-index-not-store
  - decision.model-choice-is-lane-state
---
# Decision: Prompt caching is sacred (P1)

## Context

Operating cost must be dominated by useful work (PRD G3, NFR-1: ≥60% of input
tokens from prompt cache in steady state). Anything that mutates past context or
the cached prefix mid-session invalidates the cache and multiplies cost.

## Decision

The system prompt is byte-stable for the life of a session: date-only timestamp,
wiki index snapshot frozen at session creation (`build_system_append`, modules/turns/domain/prompt.py,
wrapping `WikiStore.index()`, modules/turns/domain/prompt.py), per-session `system_append` stored in
the `sessions` table and reused byte-identical on every resumed turn (PRD P1, FR-8, FR-14).
State changes (skills, the frozen append itself) take effect at the next session.

Because tool schemas are part of the cached prefix, installing, enabling,
disabling or removing an extension from chat (`ext_manage`) resets the current
thread's lane — `ctx.flags.reset_lane`, applied after the session row is written,
the same effect as `/new` — and says so to the user ("This thread's context
starts fresh on your next message"; extensions design spec, "Locked Decisions").

A mutation made in the browser has no current thread, so it resets **the lanes
whose prefix it changed** (`http/deps.py::prefix_invalidator`): a connector or
plugin a user enables, disables or confirms reaches only their prefix, so it drops
that user's lanes (`runner.reset_owned(owner_id)`, the deploy owner's also taking
the unowned rows); a plugin re-fetch or delete changes a clone every enabling owner
loads, so it drops the lanes of each owner holding a row; a skill edit drops the
editing user's lanes; and approving a catalog draft — the shared catalog every
lane's prefix describes — drops every lane (`runner.reset_all()`). The rule is the
same rule, and the corollary is what the Customize cut had to fix — a mutation that
skips the reset does not "take effect next turn", it takes effect whenever the
lane happens to start a fresh session, because a resumed session never re-reads
the prefix. It applies to whatever the mutation changed: the toolset (connectors),
the plugin list, or the skill index in the frozen append. Not to a skill pin,
which changes no text the model sees.

**A tool is appended, never inserted, and the deploy that adds it resets every
lane.** `allowed_tools` is built from the `hermes` registration order and is part
of the cached prefix, so appending is the only change that leaves every existing
name where a warm prefix already has it — which is why `memory_search` is
registered after `ask_user`, and why the next tool, the content plan's
`content_plan` (2026-09-28, [[component.content]]), went after it. But a
deploy is not a mutation anybody clicks: `allowed_tools` and the MCP server are
rebuilt every turn while `system_append` is frozen on the `sessions` row, so a
new binary would otherwise swap the toolset under every live session, which is
the P1 failure exactly. `main.run()` therefore compares the toolset's
fingerprint with the `meta` key `hermes_tools` and, when it moved, calls the same
`runner.reset_all()` an extension install calls (`sync_hermes_toolset`; the
`sdk_slash_commands` write-only-when-changed shape, [[contract.state-db]]). Deploying this cut trips it once — every lane starts one
fresh session and pays one cold prefix — and every later flip of
`memory_search_enabled` is caught by the same mechanism, because a config flag
that removes a tool is a toolset change and nothing else. `content_enabled` is the
same kind of flag: `tool_names()` includes `content_plan` whenever the port is
wired, so flipping it appends or drops one name and resets every lane once at that
boot.

**The fingerprint is the whole declaration, not the names** (since the content
plan's v2, 2026-09-28). A description or a schema is prompt text in the cached
prefix exactly as a name is, so a names-only fingerprint let a reworded
description move every cached prefix with no reset to announce it — which is why
`cron_manage`'s description and schema stayed byte-identical when the scheduler
gained the `manual` kind. `SdkTurnExecutor.tool_fingerprint()` is now a sha256
over `json.dumps([{name, description, input_schema}, …], sort_keys=True)` of the
owner private-lane toolset, in registration order, with a schema's type objects
written as their names. It reads `hermes_server.hermes_tools(handlers)` — the ONE
list `build_hermes_server` also wraps — because the built-in tools' descriptions
are literals inside that function and the returned MCP server cannot be read
back. `tests/test_tool_fingerprint.py` pins that rewording a built-in tool
(`session_search`) moves it while the name list stays put. The key and the
write-only-when-changed shape are unchanged; the first boot with the new value
resets every lane once (an old comma-joined list never equals a digest), and so
did the `content_plan` description change that shipped with it. A description
still must not change in passing — now because it costs every lane a reset, not
because it slips through.

**A shell grant that moves resets that person's lanes — lazily, at each lane's
next turn** (migration `0009`, [[decision.multi-user-isolation]]). Whether a
non-owner lane gets `Bash`/`Agent`/`Task` is its owner's per-person grant, read
once per turn, so it can change between two turns of one session — and the boot
fingerprint above covers only the `mcp__hermes__*` tools, not the CLI's built-in ones.
The session row therefore records the toolset it was built with:
`sessions.shell_enabled` is stamped with the turn's `TurnContext.has_shell` on
every `bind`/`touch`, and at the start of the next turn a stamp that differs from
the lane's current `has_shell` resets the lane through the session store before
anything is read from the row, so the turn runs fresh and says so
(`SHELL_CHANGED_NOTICE`, a `text.block` before the answer and the first line of
the Telegram reply) ([[flow.message-turn]]). Nothing resets from the HTTP request
that changed the grant — a reset there could race a running turn's write-back and
resurrect the old session — and nothing is reset for a person whose shell did not
move. Because the comparison is with the lane's *effective* shell, it also
catches an edited `users_shell_enabled` default for everyone inheriting it. A
NULL stamp — a session begun before `0009` — is compared with the rule it was
started under, `is_owner_lane or users_shell_enabled`, read at the moment of
asking; the one gap is such a session left untouched across both the upgrade and
a later change to that default (sessions never idle-expire unless
`idle_reset_hours` / `daily_reset_hour` are set), which resumes once on a changed
toolset and is stamped from then on. `tests/modules/turns/test_turns_shell_stamp.py`
drives the reset over the real `SessionRepository`.

**The fingerprint has to be the list a real turn registers.** `tool_names()`
and `tool_fingerprint()` share one probe (`_owner_private_handlers()`), which
builds its handlers exactly as `execute` does for an owner's private lane, with
a synthetic `TurnContext` standing in for the lane — `send_document` and
`ask_user` exist only when there is a chat context, so a `ctx=None` probe would
have fingerprinted a list those two are missing from and quietly stopped
watching them. Every `make_*` factory only closes over the context, so building
them touches no disk and no network until a tool is actually called.

## Consequences

- A wiki write mid-session is no longer silently lost until the next session: on
  every `UserPromptSubmit`, `index_refresh_factory`
  (modules/memory/infrastructure/turn_hooks.py) compares
  `WikiStore.index_mtime()` against what this session already saw
  (`TurnService._index_seen`, modules/turns/application/turn_service.py) and, only when the index actually moved,
  re-injects the current `index.md` as `additionalContext`. This costs zero tokens
  when the wiki is unchanged, and even when it fires it does not touch the cached
  prefix — `additionalContext` is appended alongside the turn, not mixed into the
  frozen `system_append`. The byte-stable append itself is still frozen for the
  life of the session; this is a second, separate injection, not a mid-session
  prompt rebuild.
- Inbound attachments do not move any cached prefix. `_build_prompt`
  (modules/turns/infrastructure/sdk_runner.py) returns a **bare `str`** whenever nothing is attached, which is
  the shape every call site used before it existed — so text, voice, `/learn`
  and cron turns are byte-identical and their cached prefixes are untouched.
  When a photo or document *is* attached the extra content rides in the user
  turn (a stream-json message with image blocks), never in the system prompt or
  the toolset, both of which this feature leaves alone. See
  [[component.gateway]].
- The cron→topic context bridge (`_with_cron_context`,
  modules/turns/application/turn_service.py — see
  [[component.cron]]) follows the same rule: the quoted report rides in the
  user message, never in the system prompt, so a delivery does not move any
  cached prefix either.
- `include_partial_messages=True` (added for the web channel's token deltas)
  is a transport flag on `query()`, not prompt content: it changes which
  messages the SDK yields, never the system prompt, the toolset or the user
  turn, so no cached prefix moves. Turn events are likewise built *from* what
  the SDK already streams and are never fed back into the model
  ([[component.web]]).
- Extension changes cost a context reset, never a mid-session toolset swap. So
  does a changed shell grant: one fresh session per lane of that person, on
  that lane's next turn.
- `memory_search` costs two schema fields on every call and nothing else: its
  results ride in the tool result, so a wiki that changed mid-session is
  reachable without re-freezing the append the session is cached on. Whether the
  tool exists is decided by facts fixed for the LANE — a group lane and
  `memory_search_enabled` — never by who spoke, because a per-sender flip is a
  cached-prefix miss one session never recovers from
  ([[decision.multi-user-isolation]]). `content_plan` follows the same rule with
  one more lane fact: it exists only where `ctx.is_owner_lane is True`, the lane is
  not a group lane, and it has an owner id — the deploy owner's private and web
  lanes — so it too never flips between two turns of one session.
- Background passes (review, curator, titler) keep fixed narrow toolsets so
  their prompts stay cacheable too (PRD P2, P3). A consequence worth stating
  because it was once believed otherwise: a fork's toolset differs from the
  lane's, and tools are the first segment of the cached prefix, so a fork never
  reads the lane's cache whatever model it runs. That is why forks run
  `cli_id(review_model or model)` and do not follow a lane's chosen model.
- **A model switch is a cold write, never a reset**
  ([[decision.model-choice-is-lane-state]]). Caches are model-scoped, so the first
  turn on a new model — or with a new effort — re-writes the history into cache
  once. The switch is user-initiated, priced before it happens (the picker's note,
  the Telegram confirmation) and keeps the SDK session; it is the one sanctioned
  mid-session prefix change, and it touches neither `system_append` nor the
  toolset. A **changed personal default waits for the next session**: the resolver
  reads what the live session runs (`sessions.model/effort`) ahead of the owner's
  default, so a default edited in the user menu moves no live conversation.
  Clearing a lane's explicit choice is a switch like any other — the live session
  is redirected to the lane's default (`set_session_pair`) and pays one cold write
  on its next turn.

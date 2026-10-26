---
id: decision.multi-user-isolation
kind: decision
status: current
summary: A `users` row per person, an `owner_id` on every user-owned row, a workspace and learning scope per user, their own connectors, per-person shell and learning grants (0009), one shared wiki only the owner writes — isolation against accident, not a boundary against a hostile user.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:2fefa6c1bcb810607794782403b5bda97ff335adc72b5432d18b63733d0b26af
evidence:
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/core/db/migrations/versions/0005_users.py
  - path: src/claude_hermes/core/db/migrations/versions/0006_per_user_extensions.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/modules/identity/application/services.py
  - path: src/claude_hermes/modules/identity/domain/models.py
  - path: src/claude_hermes/modules/identity/presentation/schemas.py
  - path: src/claude_hermes/modules/identity/presentation/users_router.py
  - path: src/claude_hermes/core/db/migrations/versions/0009_user_grants.py
  - path: src/claude_hermes/modules/turns/application/ports.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/executor.py
  - path: tests/modules/turns/test_turns_shell_stamp.py
  - path: web/src/components/cladex/UsersSection.tsx
  - path: src/claude_hermes/modules/identity/README.md
  - path: src/claude_hermes/modules/turns/domain/policies.py
  - path: src/claude_hermes/modules/turns/domain/value_objects.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/application/execution.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/modules/turns/README.md
  - path: src/claude_hermes/integrations/claude_sdk/guards.py
  - path: src/claude_hermes/integrations/claude_sdk/options.py
  - path: src/claude_hermes/modules/files/infrastructure/roots.py
  - path: src/claude_hermes/modules/files/infrastructure/layout.py
  - path: src/claude_hermes/modules/files/README.md
  - path: src/claude_hermes/modules/sessions/domain/policies.py
  - path: src/claude_hermes/modules/sessions/README.md
  - path: src/claude_hermes/modules/scheduler/README.md
  - path: src/claude_hermes/modules/memory/README.md
  - path: src/claude_hermes/modules/extensions/domain/paths.py
  - path: src/claude_hermes/modules/extensions/application/ports.py
  - path: src/claude_hermes/modules/extensions/application/services/compose_service.py
  - path: src/claude_hermes/modules/extensions/application/services/extension_service.py
  - path: src/claude_hermes/modules/extensions/README.md
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/core/config.py
  - path: config.yaml.example
  - path: tests/test_web_isolation.py
  - path: tests/modules/turns/test_turns_two_users.py
  - path: src/claude_hermes/modules/learning/application/scopes.py
  - path: src/claude_hermes/modules/learning/wiring.py
  - path: src/claude_hermes/modules/learning/infrastructure/scoped_meta.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_slot.py
  - path: src/claude_hermes/modules/learning/application/services/loop.py
  - path: src/claude_hermes/modules/learning/application/services/review.py
  - path: src/claude_hermes/modules/learning/infrastructure/fork_runner.py
  - path: src/claude_hermes/modules/learning/presentation/dependencies.py
  - path: src/claude_hermes/modules/learning/presentation/router.py
  - path: src/claude_hermes/workers/curator.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/core/auth.py
  - path: src/claude_hermes/core/tasks.py
  - path: src/claude_hermes/modules/sessions/infrastructure/repositories.py
  - path: src/claude_hermes/modules/turns/infrastructure/tool_handlers/documents.py
  - path: src/claude_hermes/modules/identity/domain/telegram_proof.py
  - path: src/claude_hermes/modules/identity/presentation/router.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
  - path: src/claude_hermes/modules/memory/infrastructure/lint_fork.py
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/modules/memory/infrastructure/page_index.py
  - path: src/claude_hermes/modules/memory/application/services/index.py
  - path: src/claude_hermes/modules/memory/domain/prompts.py
  - path: src/claude_hermes/modules/turns/infrastructure/tool_handlers/memory.py
  - path: tests/modules/learning/test_learning_scopes.py
  - path: tests/test_core_workspace.py
  - path: tests/test_send_document.py
relations:
  - decision.readonly-catalog-boundary
  - decision.postgres-and-modular-core
  - decision.redis-taskiq-scaling
  - decision.threads-as-session-lanes
  - decision.web-as-a-channel
  - decision.prompt-cache-discipline
  - decision.minio-blobs
  - decision.wiki-index-not-store
  - contract.state-db
  - component.core-runtime
  - component.web
  - component.gateway
---
# Decision: Multi-user isolation — a workspace per user, inside one container

## Context

Until Wave 4a "who is the owner" was one `meta` row and "who else may talk" was a
list in `config.yaml`. Nobody but the owner had a record, nothing a second person
produced said whose it was, and every turn ran in the same tree: one
`CH_HOME/agent_home`, one wiki, one `inbox/`, one `CLAUDE_CONFIG_DIR`. An
allowlisted user could chat — and read the owner's files, resume into the owner's
transcript store and write the owner's wiki, because there was only one of each.

The spec's acceptance line is "two different users cannot see each other's
threads, files, drafts or workspace files, via API or via the agent's tools". This
page records what 4a does about that, and — just as important — what it
deliberately does not. Wave 4b (2026-09-18) then took the one thing 4a left as a
single tree — the learning loop — per user; its decisions are folded in below,
each marked. Migration `0009` (2026-09-24) then turned the two deploy-wide
switches that remained, a non-owner's shell and their learning loop, into
per-person grants.

## Decision

### `users.id` is the Telegram user id

```
users(id BIGINT PK, role 'owner'|'user', workspace_dir TEXT,
      display_name, username, created_at, last_seen_at, disabled_at NULL,
      default_model, default_effort NULL,        -- 0008
      shell_enabled, learning_enabled NULL)      -- 0009: the per-person grants
CREATE UNIQUE INDEX users_one_owner ON users (role) WHERE role = 'owner';
```

`CurrentUser.user_id`, `threads.owner_user_id`, `turns.user_id`, the
`web:<user_id>:` lane grammar, the ask-broker's owner check, the OAuth
pending-state binding and the session cookie all already carry the Telegram id, so
a surrogate key would buy nothing and add a translation at each of them. Identity
*is* Telegram in this product (Login Widget, Mini App). A non-Telegram login later
gets a synthetic id range; that is a 4b+ decision.

**The partial unique index is the claim arbiter**, the same shape
`turns_one_active_per_lane` uses and for the same reason: a check-then-insert is a
race. Two first private chats at once produce one owner row and one
`IntegrityError`, which `AccessControl.check` answers `"deny"`. The row is created
on first *contact*, never eagerly from `allowed_users`: the table is the record of
who actually showed up, so a typo in the allowlist creates nothing.

`meta.owner_user_id` survived as a **mirror** through 4a and 4b — `AccessControl.
owner()` fell back to it and materialised the row when the table was empty —
until Step 5 dropped it: migration `0005` materialises the row from the key at
upgrade, so every volume reaching Step 5 through `alembic upgrade head` has it,
and the state the mirror guarded (key present, table empty) is reachable only by
hand-truncating `users`. Nothing writes the key now; a stale row is inert. The
one-way door: a deploy claimed after Step 5 has its owner only as a `users` row,
so never downgrade a claimed deploy past `0005` — recovery is an `INSERT INTO
users (id, role, workspace_dir, …) VALUES (<id>, 'owner', '', …)`
([[contract.state-db]], [[decision.modules-and-ports]]).

### Every user-owned row names its owner; some tables deliberately do not

`owner_id` (nullable, `ON DELETE SET NULL`) on `sessions`, `messages`, `files`,
`jobs`, `turn_costs`, `catalog_drafts`, and the same FK on the existing
`threads.owner_user_id`. Repositories scope by owner; no handler filters ad hoc.
The per-table read/write rule is in [[contract.state-db]].

`turns.user_id` gets **no** FK: it is the *sender* of a turn, an audit field, NOT
NULL, and a group turn's sender may have no row at all. The turn's *workspace*
comes from `lane_owner`, not from that column. `turn_events` carry no owner
column either — they are read by lane, and the lane is already guarded by the
thread policy.

What stays **deploy-wide**, and why:

| deploy-wide | why |
|---|---|
| `approval_snapshots` | the snapshot a token is bound to belongs to the one approval channel, and approving is owner-only ([[decision.readonly-catalog-boundary]]) |
| the catalog itself (`catalog/` `:ro`, `user_catalog/`) | what *exists* to install is reviewed, human-approved and shared; nobody installs something nobody vetted |
| ~~`extensions`, `extension_secrets`, `oauth_tokens`~~ | **no longer deploy-wide** — keyed `(owner_id, name)` since migration `0006`; see "Connectors are per user" below. `installed_by` stays audit-only, and is now audit *within* one owner's row |
| `catalog_drafts` as a queue | the queue is deploy-wide and owner-approved; the new `owner_id` records *who proposed*, nothing more ([[decision.readonly-catalog-boundary]]) |
| `user-catalog/` | state written only through `/approve`, which only the owner can reach |
| the plugin clones | moved to `CH_HOME/plugins/<name>` (from `agent_home/plugins/`): a plugin is deploy-wide, and left inside the owner's `agent_home` a non-owner's turn could only load one if its read guard admitted the owner's whole tree |

What 4a left as the owner's tree and 4b made per user: the skills library, the
usage sidecar, the archive, the snapshots, the nudge counters and review lease,
the review forks, the curator pass and its reports — one `LearningScope` per
workspace ([[component.learning]]).

### Lane ownership is one rule, applied in one place

`core/session_keys.py::lane_owner(session_key, channel, user_id, deploy_owner)`:

| lane | owner |
|---|---|
| `web:<uid>:…` | `<uid>`, out of the **key** |
| `tg:private:<chat>[:t<n>]` | `<chat>` (chat id == user id in a private chat) |
| `tg:group\|supergroup:…` | the deploy owner |
| `cron:<job>` | the job's owner, passed in as `user_id` |

The id comes out of the key wherever the key carries one — user B opening user A's
`web:A:…` lane must not become its owner, and a group message from a stranger must
not make the stranger one. `sessions.owner_id`, `threads.owner_user_id`, the `0005`
backfill (the same rule spelled in SQL) and the workspace resolver all go through
it, which is what makes it impossible for them to disagree about a lane.

A group lane belonging to the **deploy owner** is deliberate: one lane, one
workspace, and the deploy owner is the only person in a group chat the agent has a
workspace for. The sender is still the sender (`turns.user_id`, the `ask_user`
keyboard); they just do not own the room.
`modules/sessions/domain/policies.visible(thread, user_id, is_owner)` reads the
column rather than re-deriving, with one belt: a lane that is neither `web:` nor
`cron:` is visible to the owner even if its `owner_user_id` is somehow unset, so a
group thread the backfill missed never silently disappears from the sidebar. The
belt is deliberately **not** extended to `web:` lanes ([[decision.threads-as-session-lanes]]).

### The workspace layout — the owner's tree does not move

`core/workspace.py::Workspace(home, root)`. `Workspace.owner(home)` is
`Workspace(home, home)`; `Workspace.for_user(home, workspace_dir)` is
`home / workspace_dir`, and the owner's `workspace_dir` is `""`. So for the deploy
owner `root is home` and `/data/agent_home`, `/data/.claude-cli` and `/data/logs`
are byte-identical to what they were: a live volume needs no file migration and
every SDK session keeps resuming. Everybody else gets `users/<id>`.

* **per user, off `root`**: `agent_home`, `.claude/{skills,agents}`, `wiki`,
  `inbox/`, `outbox/`, `logs/`, `tmp/` (`TMPDIR`) and `.claude-cli/`
  (`CLAUDE_CONFIG_DIR`). The last two are new and both are isolation: `/tmp` is
  shared by every lane in the container, and a shared config dir means one user's
  `--resume` replays another's conversation.
* **deploy-wide, off `home`**: `user_catalog`, `plugins`, `secrets.env`,
  `catalog-drafts/`, `pending-approvals/`. Only the owner's `ensure()` creates
  them — a second user's first message should not be what brings
  `pending-approvals/` into existence.

`WorkspaceResolver(home, workspace_dir_of=…, deploy_owner=…)` is the port every
module receives. Core may not import a module, so both lookups arrive as async
callables bound in `main.build_app` to the ACL. `for_user(id)` asks the table (the
blob mirror and the cron executor hold an `owner_id` and no key);
`for_lane(key, channel, user_id)` **reads no table at all** — it applies
`lane_owner` given the cached deploy owner, so a brand-new lane whose `sessions`
row does not exist yet resolves exactly like an old one. `ensure()` seeds the tree
lazily, once per user per process; two workers seeding the same new user is
normal. Since 4b the seeding of `settings.json`, the bundled skills and the agents
goes through one **`SeedManifest`** per tree (`agent_home/.claude/.seeded.json`,
sha256 per seeded file): a file still carrying the bytes we shipped is refreshed
when the bundle moves, a file the user patched is left alone (logged once per
tree), a file the user deleted stays deleted, and a skill sitting under
`skills/.archive/<name>` is never re-seeded — before 4b every boot copied an
archived bundled skill straight back in, so the curator archived it again a week
later, forever. `CLAUDE.md` is deliberately outside the manifest (`persona.md` is
the delivery channel), an unparseable manifest reads as empty, and `save()`
re-reads and merges immediately before its `os.replace`, so two processes seeding
one tree cost at most one extra refresh next boot ([[component.core-runtime]]).

### What a turn may touch

`TurnRunService.run` resolves the workspace **once**, from the row's key, channel
and sender, and it travels down on `TurnContext.workspace` +
`TurnContext.is_owner_lane`. Deciding it anywhere lower would mean deciding it
twice.

* `cwd` is `ws.root / "agent_home"` (`base_options(ws.root, …)`), and
  `env=workspace_env(ws)` sets `CLAUDE_CONFIG_DIR` and `TMPDIR`. The SDK forwards
  `ClaudeAgentOptions.env` to the CLI subprocess with the explicit keys winning
  over the inherited environment (verified in 4a), which is why this is enough.
  Background call sites merge rather than replace — `env={**NO_ADVISOR_ENV, …}` —
  because a replace silently re-enables the advisor tool.
* **The wiki follows the lane.** `TurnService` holds no single `WikiStore`; it
  takes `wiki_for(workspace)` and builds the lane's store per turn, and
  `WikiHooksProvider.build(..., workspace)` builds the hooks over that root.
  Without both, user B's byte-stable cached prefix would carry the owner's index
  and B's Stop gate would write the owner's wiki through a path the read guard
  never sees.
* **The blob mirror follows the row.** Both stores take a `RootLookup`
  (`async (owner_id) -> Path`, `modules/files/infrastructure/roots.py`) instead of
  a root frozen in `__init__`, and every `put`/`get`/`resolve` is given the
  **row's** `owner_id` — never the requester's. The download route re-resolves
  against whoever the row says owns the file, which is what stops a guessed or
  forged file id reading out of somebody else's tree. The S3 key grammar
  (`inbox/<lane>/…`) is unchanged; only the mirror moves ([[decision.minio-blobs]]).
* **Read confinement.** `guards.path_guard_factory(allowed_roots, cwd=…)` is a
  `PreToolUse` hook over `Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep`,
  installed for **every** lane, the owner's included, with
  `[ws.agent_home, ws.tmp_dir, ws.logs_dir, ws.plugins_dir]` plus the read-only
  catalog root when there is one (`logs_dir` is in the list deliberately: it is
  per user like the rest, and the curator writes that user's reports under their
  `logs/curator/`). Since 4b the **same hook, same roots minus the catalog**, is
  installed on every background fork — review, consolidation, wiki lint — with
  `cwd=workspace.agent_home` ([[component.learning]]). It checks
  `file_path`, `notebook_path` and `path`, resolves non-strictly so a planted
  symlink is judged by where it *points*, resolves a relative path against the
  lane's `cwd`, and denies an **absolute** `pattern`/`glob` — `Glob(pattern=
  "/data/users/*/**")` with no `path` must not walk every other tree. An empty
  root list denies everything: a call site that forgot to say where the workspace
  is gets a turn that cannot touch the disk, not one that can touch all of it.
  It fires inside a subagent too: verified empirically on CLI 2.1.273 with a
  logging hook that `PreToolUse` DOES fire for an `Agent` tool subagent's own tool
  calls, which is what lets `Agent`/`Task` ever be handed back.
* **A brand-new `CLAUDE_CONFIG_DIR` is not an interactive prompt.** Verified in
  4a: a turn against an empty config dir completes with no prompt — the CLI writes
  `.claude.json`, `policy-limits.json`, `remote-settings.json`, `projects/` and
  `sessions/` itself. A per-user config dir therefore costs a first-run write, not
  a hung turn.

### What a non-owner lane does not get

`Bash`, `Agent` and `Task`, unless the owner has granted that person a shell
(next section). `ext_manage`
keeps its own fail-closed owner gate, as before. `skill_admin` is **no longer**
withheld from a non-owner lane: since 4b it administers the lane owner's own
library, and whether it exists at all is decided by the lane, not the sender —
next section.

There is deliberately **no bash regex guard** for other workspaces: it would deny
`cd ..` and still miss `ls $X/users`, and inside one uid it cannot be a boundary
anyway. Removing the tool is the honest control.

### A shell and learning are grants, per person (0009)

Until `0009` both were deploy-wide `config.yaml` switches — `users_shell_enabled`
handed `Bash`/`Agent`/`Task` to **every** non-owner lane at once,
`users_learning_enabled` turned the learning loop on for every non-owner at once —
and both were copied into the frozen `TurnSettings` at boot, so a change needed a
restart of the agent and the worker. Trusting one person with a shell meant
trusting everyone on the allowlist. Now:

- **Two nullable columns on `users`** (`shell_enabled`, `learning_enabled`,
  [[contract.state-db]]); NULL is "no choice of their own" and inherits the config
  key, which stays as the **default** — so every existing user kept exactly the
  behaviour they had, with no backfill.
- **One rule, one function.** `Grants.of(user, defaults)`
  (`modules/identity/domain/models.py`) is the whole resolution: the owner's row →
  `(True, True)`; any other row → its column, else the default; no row → the
  defaults (fail closed, like `default_of` and `admitted`).
  `AccessControl.grants(user_id)` (one `get`, never cached) and the member wire
  both call it; no consumer reads the columns or the config keys. The defaults
  are read at call time through callables, like `allowed_users`.
- **Read per use, in whichever process runs it.** Turns asks through its
  consumer-side `LaneGrants` port once per turn, inside the turn's error envelope,
  and never substitutes a default for a failed lookup (a wrong False would reset
  the lane under a notice that lies); the scheduler's executor asks through
  `OwnerGrants` when a non-owner's routine fires and, having no session to spoil,
  fails closed to no shell and still runs; learning asks through `learning_of` per
  curator sweep (one failing id sits the sweep out) and again when a queued
  `learning_review` runs (a withdrawn grant releases the review gate and runs no
  fork). Modules stay independent: `acl` satisfies all three structurally,
  wired in `main.py` ([[decision.modules-and-ports]]).
- **A lane owned by the deploy owner never asks.** `is_owner_lane` — the owner's
  private and web lanes, every group lane, the owner's `cron:` lanes — already
  yields the full toolset and the observer. `TurnContext.shell_enabled` /
  `learning_enabled` default to **False**, and `TurnContext.has_shell`
  (`is_owner_lane or shell_enabled`) is the one value the tool policy, the session
  stamp and the reset comparison read.
- **A shell change starts a fresh session, and says so** (P1, P5). The toolset is
  part of the cached prefix, so `sessions.shell_enabled` records the `has_shell`
  a session was built with; the turn compares it at its own start and, on a
  difference, resets the lane through the store and emits "Your shell access
  (Bash, Agent, Task) was changed by the owner, so this conversation starts a
  fresh session." A NULL stamp (a session begun before `0009`) is compared with
  the rule it was started under, `is_owner_lane or users_shell_enabled`, so the
  upgrade reset nothing ([[flow.message-turn]]). Nothing resets a lane from the
  HTTP request that changed the grant — that could race a running turn's
  write-back and resurrect the old session. `tests/modules/turns/
  test_turns_shell_stamp.py` drives this over the real `SessionRepository`, so the
  column the writer stamps is proven to be the key the reader compares.
- **Only the owner sets them, only on a person who exists.** `PATCH
  /api/users/{id}` (`{"shell"?: bool, "learning"?: bool}`, strict booleans) on the
  owner-only users router; `409 owner_immutable` for the owner, `404` for an id
  with no row — including one that is only in `allowed_users`, because creating a
  row to hang a grant on would be an admission (the curator would seed their
  tree). There is no way back to "inherit": booleans only. Settings → Users shows
  a **Bash** and a **Learning** switch per non-owner row with a "default" marker
  while inherited, and asks before turning Bash on, naming what a shell reaches:
  every user's files and credentials — connector tokens, `secrets.env`, the
  Claude login ([[component.web]]).
- **Group lanes stay the owner's, and the UI says so.** Per-sender grants inside
  one shared lane would change its toolset from message to message (P1), so a
  group lane keeps the owner's full reach whoever writes in it; a permanent note
  under the Users list says that in a group chat anyone allowed to talk to the
  bot runs on the owner's lane with the owner's tools, so the switches are no
  boundary there.
- **Withdrawing does not chase what is running.** A turn in flight finishes on
  the toolset it started with; the next turn resets. A routine keeps firing,
  without the shell. A withdrawn learning grant leaves the user's nudge counters
  where they were.

### Tool presence is a property of the LANE, never of the sender (4b)

One predicate, `core/session_keys.py::is_group_lane(session_key)` (`tg:group:*`,
`tg:supergroup:*`), decides three tools at once. On a group lane `skill_admin`,
`session_search` and `cost_report` do not exist **for anybody, the deploy owner
included**: the lane's library, recall and spend are the deploy owner's
(`lane_owner`), and a room must not administer, search or bill against them. On
every other lane (`web:`, `tg:private:`, `cron:`) the sender IS the owner
structurally, so all three exist, scoped to the lane owner (`ctx.owner_id`;
`SkillAdminTool(scopes)` resolves the lane owner's scope per call).

Why the lane and not the sender: 4a gated `session_search`/`cost_report` on
"is the sender the lane's owner" (the `stranger` computation in `sdk_runner.py`),
which on a group lane flipped the MCP schema list between the owner's turn and a
member's turn of **one session** — a cached-prefix miss the lane never recovers
from (P1). That was a defect, not a feature. The prefix has to be byte-stable for
the life of a session, so anything that shapes it may depend only on facts that
are fixed for the lane. `make_handlers` (modules/turns/infrastructure/hermes_server.py)
keeps the same mechanism — a port passed
as `None` is the gate — and the runner now passes `None` for all three on a group
lane (`tests/modules/turns/test_turns_two_users.py`).

The content plan's `content_plan` (2026-09-28, [[component.content]]) is narrower
still and decided the same way: it exists only on the **deploy owner's** private
and web lanes (`ctx.is_owner_lane is True` asked explicitly, not a group lane,
`owner_id > 0`) and in the deploy owner's routines, and nowhere else — the plan is
the owner's publishing queue in v1, every `/api/content*` route is
`require_owner`, and the bundled `content-publish` skill that is also seeded into
other users' trees is inert there because the tool does not exist.

### The learning loop per user (4b)

The full mechanics are on [[component.learning]]; the decisions:

- **A scope per workspace, a registry instead of a bundle.** `LearningScopes.
  for_user(uid)` builds a `LearningScope` (sidecar, library, archive, nudges,
  gate, review, curator, admin, one `ScopedMeta`) over that user's `Workspace`,
  lazily, cached per process. `for_user(0)` *is* the owner (the resolved id is the
  cache key, so a caller carrying `0` and the owner share one sidecar over one
  `.usage.json`). Every consumer resolves its scope per call — the loop by lane
  owner, the `learning_review` task by the `owner_id` on its payload, the curator
  sweep by walking `user_ids()`, `skill_admin` and `/api/skills` by the requesting
  user.
- **Meta keys per user, the owner's byte-identical.** `ScopedMeta` appends
  `:<user_id>` to every key for a non-owner and `""` for the owner, so the seven
  pre-4b rows (`turns_since_wiki_review`, `iters_since_skill_write`,
  `curator_last_run_at`, `curator_paused`, `curator_last_report_path`,
  `curator_last_dry_run_at`, `learning_review_until`) need no migration and
  `skill_admin status` keeps reading them by literal ([[contract.state-db]]).
- **Behind a learning grant — one deploy-wide switch in 4b, per person since
  `0009`** (previous section). Without it, a non-owner lane is exactly as in 4a
  — unobserved, logged once per process — and the curator sweep skips them. With
  it, their turns are observed and reviewed and they get a weekly curator pass.
  The default (`users_learning_enabled`) is `false` because the review forks and
  the wiki lint are spend on the **owner's** account for somebody else's
  traffic, and nothing ships an owner-facing meter for background work; the cost
  is stated at the key in `config.yaml.example`. Their library, `skill_admin` and
  the Skills page are per user regardless of the grant — a library exists in
  every tree from the first turn, and reading your own is not learning.
- **One review fork at a time, deploy-wide, still.** The `learning_review_until`
  lease used to be both the per-deploy single-flight rule and the concurrency
  bound because there was one of it. With a lease per user, N users could start
  N forks on the owner's account at once, so a second bound replaces it: inline
  mode `InlineQueue(registry, {run_job: 1, learning_review: 1})`; redis mode a
  `ReviewSlot` over `core.redis.Lock("lock:review")`, TTL'd like the gate.
  `ReviewService.run` claims the user's gate, then tries the slot; a held slot
  releases the gate and returns `None` — a dropped review is the designed
  outcome, the same one a nudge gets when the gate is held, and the counters fire
  again next interval.
- **Every fork runs in ITS workspace, confined like a turn.** `cwd` and
  `CLAUDE_CONFIG_DIR` are the user's (so `resume`/`fork_session` reads *their*
  transcript store), the same `path_guard_factory` the foreground turn installs
  is on the fork, the write guard resolves relative paths against the same `cwd`
  (`write_guard_factory(cwd=)` — stacked on one matcher, the two hooks must not
  disagree about one path), and `Bash`/`Agent`/`Task` are removed by
  `disallowed_tools`, the control 4a learned is the one that means "cannot".
- **The curator sweeps users, one lock each, one sweep at a time**, owner first,
  sequentially (N passes may spawn N CLI forks in a container sized for one).
  The operator's unsuffixed `curator_paused=1` stops the **whole** sweep;
  `curator_paused:<id>` pauses one user's pass.
- **Admission is re-checked per request.** `CurrentUserMiddleware(admitted=)`
  asks `AccessControl.admitted(uid)` — the allowlist and `disabled_at`, read-only
  — on every `/api/*` request and answers 401 for a cookie whose user is no
  longer admitted. A cookie outlives the decision that issued it; before 4b a
  de-allowlisted user with a live cookie could still read, and after D6 they
  could write prompt text into a tree through `/api/skills`, so "admitted once"
  is no longer enough. One ACL read per call, which `is_owner(uid)` already cost.
- **`send_document` resolves against, and stays inside, `agent_home`.** A
  relative path is joined onto the lane owner's `agent_home` before `resolve()`
  (the seeded skills name files cwd-relative now — no `/data/agent_home` literal
  survives under `skills/`, pinned by `tests/test_bundled_skills.py`), and the
  boundary is `agent_home` rather than the workspace root: for the deploy owner
  `root == CH_HOME`, so a group member on the owner's lane could have named
  `/data/users/7/…` by absolute path and had it sent.
- **A routine created with no owner belongs to the deploy owner**
  (`SchedulerService(deploy_owner=)`, resolved at `create_job`; `0` on an
  unclaimed deploy still writes NULL). **Mini App login records the profile**:
  `verify_webapp_user` returns the id *and* the HMAC-covered `user` object in one
  function, so no standalone parser exists for the next caller to reach for
  with an unverified `initData`.

### One shared wiki namespace, written by the owner alone

Per-user trees answer "nobody reads anyone else's". They do not answer "the
household has facts in common" — the flat, the kids' school, the car — which
before this had to be written twice, once into each wiki, and drift apart.

The namespace is **`shared/` inside the OWNER's wiki**
(`Workspace.shared_wiki_root` = `CH_HOME/agent_home/wiki/shared`, deploy-wide, so
it hangs off `home` and not off `root`). Inside the owner's tree on purpose:
the owner edits it with the same native `Write`/`Edit` and the same
`index.md` discipline as any other page, their guards already admit it, and
nothing new is mounted. The owner's `ensure()` creates it and seeds
`shared/index.md` once, idempotently; after that the owner owns every byte.

**Everyone else reads it and cannot write it.** A non-owner's turn and both of
their background forks get `ws.shared_wiki_root` as an extra read root — the one
path outside their own tree they may read — and a `WRITE_TOOLS` matcher with
`readonly_guard_factory([ws.shared_wiki_root, ws.wiki_root / "shared"],
cwd=ws.agent_home)` beside it. `WRITE_TOOLS` and not `Write|Edit` everywhere,
the lint fork included: a fork's `tools=` list already leaves `MultiEdit` and
`NotebookEdit` out, but under `bypassPermissions` that list is not a toolset, so
only a matcher that names them is a fence if a CLI build offers them anyway.
**Both** spellings are fenced, and the second is
the one that matters: a model told a read-only shared wiki exists will try
`Write("wiki/shared/x.md")`, a relative path that resolves into its OWN tree,
where it would quietly succeed as a private page nobody else ever sees. The deny
message is what teaches it the difference. The owner gets no fence: for them the
two paths are one path, inside `agent_home`, and it is theirs.

**In the index the namespace is one boolean.** `WikiIndexService` writes
`shared = true` for a row whose `path` starts with `shared/` **and only when the
tree being indexed is the owner's**, so a non-owner who invents their own
`shared/` folder keeps it private. A non-owner's search predicate is
`owner_id = :me OR shared`, and identity is `(owner_id, path)` everywhere,
because both users may hold a `shared/home` ([[contract.state-db]]). The
`memory_search` handler prints `[shared]` on a foreign row and prints that row's
outgoing links only when they name a page already in the same result set: a
shared page linking `[[user/health]]` must not tell every admitted user which
pages the owner keeps.

**In the prompt it is a quarter, not a double.** A non-owner's `WikiStore` is
built with `shared_root=ws.shared_wiki_root` and appends a
`## Shared wiki (read-only; on disk at <path>)` section after their own index,
truncated at `index_max_chars // 4` with the same `TRUNCATION_NOTE`;
`index_mtime()` becomes the max of the two, so the refresh hook fires when the
owner edits `shared/index.md`. The owner's store passes `shared_root=None` and
its output is byte-identical to the pre-shared one — it is a cached prefix, and a
feature for other users may not move one byte of it
([[decision.prompt-cache-discipline]]). The non-owner's lint fork is told in one
sentence that `shared/` is read-only, so it does not burn `curator_max_turns` on
denied writes.

**The known gap: the owner is never told the namespace exists.** `WIKI_RULES` is
frozen prompt text and may not grow, so the seeded `shared/index.md` is the only
thing that carries the news — and only to a turn that happens to read it. The
delivery channel that reaches an already-live volume is `persona.md`
(`write_persona` rewrites it every boot, which is exactly why it is the channel
a `_copy_missing` seed cannot be); one sentence there is the follow-up,
deliberately not part of this cut.

### Connectors are per user; the catalog and the clones are not

**This supersedes "Extensions stay deploy-wide — final (4b, D9)".** That decision
was right about what it was defending and wrong about what had to follow from it,
so the old argument is kept below rather than deleted — it is still what a reader
needs to understand why the split falls where it does.

The line is **credentials and enablement per user; everything that says what
*exists* deploy-wide**:

| per user, keyed `(owner_id, name)` | deploy-wide |
|---|---|
| `extensions` — who has it enabled, and their `state`, `last_error`, `tool_count`, `tool_names` | the reviewed catalog (`catalog/` `:ro`) and `user_catalog/` |
| `extension_secrets` — their API keys, still one `SecretBox` | `catalog_drafts` as a queue, and `approval_snapshots` |
| `oauth_tokens` — their sign-in with their own account | the plugin **clones** under `CH_HOME/plugins/<name>` |

Migration `0006` moves the three tables from a `name` primary key to
`PRIMARY KEY (owner_id, name)`, with `owner_id BIGINT NOT NULL REFERENCES
users(id) ON DELETE CASCADE` — **not** the nullable `SET NULL` of `0005`. There an
ownerless row was harmless history; here it is a decryptable credential belonging
to nobody, and therefore to everybody ([[contract.state-db]]).

**Every extensions port takes `*, owner_id: int` — required, keyword-only, no
default.** That is the enforcement, not a convention: a call site that forgets one
is a `TypeError` at the seam rather than a silent read of the deploy owner's
credentials. `owner_id: int = 0` anywhere in this module would give back exactly
the failure mode the wave exists to remove. `ComposeService.for_scope(scope, *,
owner_id)` is fed from the two places that already know the owner — `ctx.owner_id`
on a turn (the **lane** owner, never the sender, for the same reason `cwd` is the
lane owner's tree) and `job["owner_id"]` on a routine. The OAuth refresh lock is
`lock:oauth:<owner>:<name>` for the same reason ([[component.extensions]]).

**What answered D9's three objections:**

- *Per-user secrets need a second key scheme with no owner to hold it (P7).* They
  do not: one `SecretBox` and one `CH_SECRETS_KEY` still seal every row. The key
  was never per owner; only the row's **key** changed, from `name` to
  `(owner_id, name)`, so two people's `NOTION_TOKEN` are two rows under one box.
- *Per-user compose and approval snapshots are a second extensions module (P2).*
  Compose was already one function of the owner's rows; giving it an argument made
  it per user. Approval snapshots did **not** follow — approving is still one
  owner-only channel over a shared catalog, which is why the table is in the
  deploy-wide column above.
- *N per-user install states multiply prefix churn (P1).* The cached prompt prefix
  has been **one per user since 4a** — `allowed_tools`, `cwd` and the wiki index
  already differ by lane owner ("Costs", below). Per-user connectors change how
  much of one user's prefix moves, not how many prefixes exist, and the flag reset
  after a mutation resets that owner's lanes rather than every lane.

**What did not change, and is the same argument D9 made:** a **plugin is code the
SDK loads and runs**. `git clone` and deleting a clone stay owner-only, the clone
stays shared under `CH_HOME/plugins/<name>`, and the read guard still admits one
deploy-wide plugin root rather than N. A second user *adopts* an already-cloned
plugin — enabling with no row of their own writes one — so what they gain is an
enablement row, never a new executable. Adding a catalog entry (`POST
/api/connectors`, `/approve`) stays owner-only too: **there is no "shared"
connector**, no `owner_id IS NULL` meaning everyone's, and `ext_manage` mutations
stay owner-gated and fail closed — only its *reads* became per-lane-owner.

**The control is `disallowed_tools`, not `allowed_tools`.** Every turn runs under
`permission_mode="bypassPermissions"`, where `allowed_tools` is an *auto-approve*
list rather than a toolset — the live 4a smoke watched a non-owner lane run `Bash`
with `Bash` absent from `allowed_tools`. `policies.denied_tools` fills
`ClaudeAgentOptions.disallowed_tools` (the CLI's `--disallowedTools`) and is what
actually removes the tool; `policies.builtin_tools` only keeps the approve list in
`_BUILTIN_TOOLS` order so the cached prefix stays byte-stable.

## What this is NOT

**Inside one container this is isolation against accident and against a
well-behaved model — not a boundary against a hostile user.** Every turn runs as
the same uid. A shell reaches every other user's tree whatever a `PreToolUse` hook
says, and the hook itself is TOCTOU-racy: a symlink swapped between the hook's
`Path.resolve()` and the tool's own open is not caught. This is the same posture
`plugin_root` and the env-scrubbing already have — defence in depth, paid for
because it is cheap.

**The `:ro` catalog mount stays the only hard boundary**
([[decision.readonly-catalog-boundary]]), and **a container per user is the
follow-up** (spec "Out of scope"). Anyone granted a shell — their **Bash**
switch in Settings → Users, or the `users_shell_enabled` default — should be
treated as trusted with every other user's data and credentials; the
confirmation in Settings and `config.yaml.example` at the key both say so. And in
a group chat anyone allowed to talk to the bot runs on the owner's lane with the
owner's tools, the shell included, so the grants are no boundary there at all.

## Costs

- **The cached prompt prefix is now per user.** `allowed_tools`,
  `disallowed_tools`, `cwd` and the wiki index in `system_append` all differ by
  lane owner, so the prefix that used to be shared by the whole deploy is now one
  per user. It is still byte-stable *per session* — the toolset is decided once
  per turn from `has_shell`, and since `0009`, when a shell grant moves between
  two turns of a lane, the turn starts a fresh session rather than change the
  toolset under a live one — so P1 holds, but the NFR-1 cache-hit target is now
  measured against N prefixes rather than one, and a grant change costs that
  person's lanes one cold start each ([[decision.prompt-cache-discipline]]).
- **Same-uid TOCTOU**, above.
- **One more table read at boot** for the deploy owner, cached in the resolver
  (and deliberately *not* cached while it is still `0`, or a process that booted
  before the deploy was claimed would resolve group and cron lanes to nobody
  forever).
- **Per-user learning is N × the owner's learning spend** (4b): a review fork per
  `wiki_nudge_interval` turns without a wiki write and per `skill_nudge_interval`
  tool uses, per user, plus a weekly curator pass whose wiki lint is an LLM fork
  of up to `curator_max_turns` — all billed to the owner's account, with no
  per-user meter and no per-user quota on skills or wiki (a skill is ≤ 64 KiB on
  the backed-up volume) — per user who holds a learning grant. This is why the
  default is off. The one bound
  that holds either way is one review fork at a time deploy-wide, so heavy
  traffic degrades into dropped nudges, never into N concurrent CLI processes.
- **One ACL read per API request** for the per-request admission check —
  `admitted()` is read-only (it does not stamp `last_seen_at` or materialise a
  row, which `check()` does), so it is a SELECT and not an UPDATE per call.
- **The seed manifest is new state in every tree.** An existing volume reads as
  "legacy": on its first 4b boot a seeded file that still matches the current
  bundle is recorded, a pristine copy of an *older* bundle
  (`_PREVIOUSLY_SHIPPED_SEEDS`, the `_SHIPPED_PERSONA_SHA256` idea) is refreshed
  and recorded there and then, and a file that matches no shipped version is
  left alone and said once at INFO — never silently frozen. The four skill files
  4b itself rewrites are in that set, so the `/data/agent_home` literals leave
  every untouched live volume on the first boot.
- **A rolling deploy across 4a → 4b must drain the queue** ([[system.operations]]).
- **So must the one that brings per-user connectors**, for a different reason: the
  OAuth refresh lock name changes from `lock:oauth:<name>` to
  `lock:oauth:<owner>:<name>`, so an old agent and a new worker alive together take
  different keys for one connector and two refreshes can run at once — which a
  provider that rotates refresh tokens (Notion) answers by refusing the loser and
  leaving `needs_reauth` on a connector that had just refreshed
  ([[system.operations]]).
- **And so should the one that brings per-user grants** (`0009`): an old worker
  left consuming the queue beside the new agent runs every turn under the old
  deploy-wide rule and ignores the grants ([[system.operations]]).
- **`0006`'s downgrade is destructive.** A primary key on `name` alone cannot hold
  two people, so going back keeps the deploy owner's rows and DELETES everybody
  else's; their OAuth tokens do not come back, because `CH_SECRETS_KEY` protects a
  dump and not a deleted row. Dump the three tables before the upgrade
  ([[contract.state-db]]).

## What a non-owner user gets after 4a, 4b and per-user connectors

Their own threads, uploads and message search; their own workspace (wiki,
`CLAUDE.md`, the bundled skills and agents — seeded and, since 4b, refreshed
while untouched — `inbox/`/`outbox/`, `tmp/`); a turn with
Read/Write/Edit/Glob/Grep confined to it, plus WebSearch/WebFetch/TodoWrite and
the hermes tools `ask_user`, `session_search` (their messages), `cron_manage`
(their routines), `send_document` (files under their `agent_home`, relative
paths welcome), `cost_report` (their spend) and `memory_search` (their own wiki
index plus the shared namespace). Read access to `shared/` — as a root, as a
section of their prompt index and as searchable rows — is the one thing they get
out of the owner's tree, and they cannot write it ("One shared wiki namespace",
above).

Since 4b, also their own: the skills library; `skill_admin` on their private and
web lanes, over that library; the Skills page and `/api/skills` over that library,
read **and** write; snapshots and rollback of their own tree. `/learn` already
landed in their tree (the prompt says `.claude/skills/`, relative, cwd = their
`agent_home`); a skill made that way has no sidecar entry, reads as `source:
user` and is never aged, so P4 holds. With a learning grant: the usage
sidecar, the nudge counters, review forks over their own conversations (their
wiki and skills grow), a weekly curator pass — aging, consolidation, wiki lint —
over their tree, and curator reports under `users/<id>/logs/curator/`.

Since per-user connectors, also their own: the connectors page and the Connectors
tab, over the **shared** catalog with **their** state on it (a connector the owner
uses reads as available and not connected — the list is the catalog plus the
caller's own rows, never a disclosure about who else enabled what); their own API
keys and their own OAuth sign-in; enabling, disabling and removing their own row;
*adopting* an already-cloned plugin; and MCP servers composed into their turns
from their credentials alone. `ext_manage`'s **read** actions (`catalog`, `list`,
`drafts`) answer about the lane owner's rows.

Still not theirs: `Bash`/`Agent`/`Task` (unless the owner grants them a shell),
`ext_manage`'s **mutations** (still owner-gated, still failing closed), installing
or deleting a plugin **clone**, adding a catalog entry (`POST /api/connectors`,
`/approve`, the drafts pages), the memory routes, other people's anything. On a
group lane nobody — the owner included — has `skill_admin`, `session_search` or
`cost_report`; a group lane composes the **deploy owner's** connectors, because a
group lane is the deploy owner's lane. The plan
said the SPA never gated the Skills page by role; it did (`SkillsTab.tsx`,
`SkillPage.tsx` hid *New skill* and the edit/archive controls behind
`me.role === "owner"`), and the gate was removed in the same wave so the
buttons follow the API: the page is always the caller's own library.

## Debts (paid in 4b and Step 5; what is still open)

Paid in 4b, kept here so the reasoning stays findable:

- ~~**Learning is owner-only.**~~ Per user behind `users_learning_enabled` in 4b,
  behind each person's own learning grant since `0009` ("The learning loop per
  user", above). `TurnService` observes a lane when `ctx.is_owner_lane or
  ctx.learning_enabled`, with `owner_id=ctx.owner_id`;
  `MessageActivity` is built per scope with that user's id, so a neighbour
  chatting at 3am holds nobody else's pass off.
- ~~**The seeded skills hardcode `/data/agent_home` paths.**~~ Every literal is
  now cwd-relative (12 occurrences in `gpt-image-2`, `linkedin-post`;
  `skill-authoring` names `.claude/skills/<name>/SKILL.md` under the agent's
  home), and `send_document` resolves the same way. `gpt-image-2` runs `gen.sh`
  through `Bash`, so it still does not work on a non-owner lane without a shell
  grant — its body says so rather than implying the path fix makes it work.
- ~~**Mini App and Login Widget admission passes no profile.**~~ Both `_admit`
  calls pass `Profile.of(...)`; the Mini App's comes out of
  `verify_webapp_user`, inside the HMAC-checked string.
- ~~**`_copy_missing` never overwrites.**~~ `SeedManifest` ("The workspace
  layout", above): refresh-if-untouched, deletion respected, archive respected.
  `manifest=None` keeps the old never-overwrite path for a caller without one.
- ~~**A job created by a lane-less fork has no owner.**~~ `create_job` with
  `owner_id=None` resolves `await deploy_owner()`.

Paid in Step 5 (commit `12a3460`, [[decision.modules-and-ports]]):

- ~~**`meta.owner_user_id` is still mirrored.**~~ Dropped; the `users` table is
  the only owner record ("`users.id` is the Telegram user id", above) and the one-way
  door past `0005` is the price.
- ~~**The legacy `runner.AgentRunner` head stays owner-only.**~~ Deleted with the
  shims: every `after_turn` carries `owner_id`, every fork `workspace=`.
- ~~**A `LaneWorkspace` without a resolver writes into the deploy owner's
  inbox.**~~ `LaneWorkspace(lane_dir, resolver)` requires the resolver; the
  bare-`home` branch and the `gateway.telegram` entry point it served are gone.
- ~~**`thread_owner` beside `lane_owner`.**~~ One rule now; the older one that
  answered `None` for a group lane went.
- ~~**The browser's Skills page role gate.**~~ Removed in 4b (above); the buttons
  follow the API.

Paid with `0009`:

- ~~**A shell and learning are all-or-nothing, and frozen at boot.**~~ Per-person
  grants read per use ("A shell and learning are grants, per person", above); the
  two config keys are the defaults.

Still open:

- **No per-user quotas.** Per-user learning is N × the owner's spend with no
  per-user meter and no cap on skills or wiki size ("Costs", above); the
  learning grant, off by default, is the only brake.
- **The memory routes stay owner-only.** `/api/wiki/*` reads and writes the
  deploy owner's wiki; a user's own wiki is reached only through their turns and
  forks. The index adds no HTTP surface either.
- **The content plan is the deploy owner's alone.** `content_items.owner_id` is on
  every row, so a plan per user needs no migration — only the tool gate and the
  routes' `require_owner` opened. Its Telegram approval cards go to the plan
  owner's private chat and their buttons and replies answer only `acl.is_owner`. Everybody's routines are on the calendar
  (`GET /api/routines/calendar` is scoped to the asking user).
- **Nothing tells the owner that `shared/` exists.** The seeded `shared/index.md`
  is the only carrier, because `WIKI_RULES` is frozen prompt text; the follow-up
  is one sentence in `persona.md` ("One shared wiki namespace", above).
- **A Telegram reply is not re-sent after an agent restart.** The turn's row and
  events survive in the worker, but the presenter that would have posted the
  final message died with the process; the reply is in the web thread, not the
  chat.
- **The presenter binds on a busy lane** to the first `turn.start` it sees, so a
  second message's status draft can follow the first turn (`modules/turns/
  README.md`, known debts; the fix is an `on_started(turn_id)` callback).
- **The reap window.** `TurnLauncher.reap` marks an expired active row `lost` on
  the scheduler's tick, so a worker that died mid-turn holds its lane until the
  lease runs out.

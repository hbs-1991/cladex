---
id: component.learning
kind: component
status: current
summary: Self-improvement, one scope per user since Wave 4b — nudges, a per-user review lease, the queued review pass behind one deploy-wide slot, the usage sidecar, the curator sweep behind Redis locks, archive and backups.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:c0366a70c991372e136008c96ea0555048306f1efb4a547fe3de33ff9cd282da
evidence:
  - path: src/claude_hermes/modules/learning/domain/constants.py
  - path: src/claude_hermes/modules/learning/domain/usage.py
  - path: src/claude_hermes/modules/learning/domain/prompts.py
  - path: src/claude_hermes/modules/learning/domain/exceptions.py
  - path: src/claude_hermes/modules/learning/application/ports.py
  - path: src/claude_hermes/modules/learning/application/services/nudges.py
  - path: src/claude_hermes/modules/learning/application/services/loop.py
  - path: src/claude_hermes/modules/learning/application/services/review.py
  - path: src/claude_hermes/modules/learning/application/services/library.py
  - path: src/claude_hermes/modules/learning/application/services/aging.py
  - path: src/claude_hermes/modules/learning/application/services/consolidation.py
  - path: src/claude_hermes/modules/learning/application/services/curator.py
  - path: src/claude_hermes/modules/learning/application/services/skill_admin.py
  - path: src/claude_hermes/modules/learning/infrastructure/usage_sidecar.py
  - path: src/claude_hermes/modules/learning/infrastructure/skill_archive.py
  - path: src/claude_hermes/modules/learning/infrastructure/skill_repository.py
  - path: src/claude_hermes/modules/learning/infrastructure/guards.py
  - path: src/claude_hermes/modules/learning/infrastructure/fork_runner.py
  - path: src/claude_hermes/modules/learning/infrastructure/report.py
  - path: src/claude_hermes/modules/learning/infrastructure/dispatch.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_gate.py
  - path: src/claude_hermes/modules/learning/application/scopes.py
  - path: src/claude_hermes/modules/learning/wiring.py
  - path: src/claude_hermes/modules/learning/infrastructure/scoped_meta.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_slot.py
  - path: src/claude_hermes/modules/learning/presentation/dependencies.py
  - path: src/claude_hermes/modules/learning/presentation/router.py
  - path: src/claude_hermes/modules/memory/infrastructure/lint_fork.py
  - path: src/claude_hermes/integrations/claude_sdk/guards.py
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/modules/sessions/infrastructure/repositories.py
  - path: src/claude_hermes/main.py
  - path: config.yaml.example
  - path: tests/modules/learning/test_learning_scopes.py
  - path: src/claude_hermes/core/tasks.py
  - path: src/claude_hermes/core/redis.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/modules/learning/README.md
  - path: src/claude_hermes/workers/curator.py
  - path: src/claude_hermes/core/snapshots.py
  - path: src/claude_hermes/core/events.py
  - path: src/claude_hermes/modules/turns/application/observation.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/modules/memory/application/services/lint.py
  - path: src/claude_hermes/modules/memory/infrastructure/turn_hooks.py
  - path: src/claude_hermes/modules/memory/application/services/index.py
  - path: src/claude_hermes/modules/memory/domain/prompts.py
  - path: src/claude_hermes/modules/learning/infrastructure/fork_usage.py
  - path: src/claude_hermes/core/model_catalog.py
  - path: src/claude_hermes/core/config.py
relations:
  - decision.model-choice-is-lane-state
  - component.core-runtime
  - quality.invariants
  - decision.modules-and-ports
  - decision.redis-taskiq-scaling
  - decision.multi-user-isolation
  - decision.wiki-index-not-store
---
# Learning Loop

Self-improvement runs after the user's reply is delivered, out-of-band, with a
restricted toolset (PRD P3). Memory is written as plain files under `wiki/`,
reached with Read/Glob/Grep/Write/Edit — no MCP tool writes it anymore
(`memory_save` and its server are gone; `memory_search` only reads the derived
index, [[decision.wiki-index-not-store]]); skills sediment through the review
pass; the curator does library hygiene, not extraction.

## Per-turn observation → nudges → review

**Since Wave 4b the whole of this runs per user, as a `LearningScope` per
workspace.** Until 4b the loop was one set of objects over one tree — the deploy
owner's sidecar, library, counters, gate and curator — and 4a had simply skipped
`after_turn` for non-owner lanes. Now `application/scopes.py` defines
`LearningScope(user_id, workspace, is_owner, skills_dir, logs_dir, meta, sidecar,
archive, library, admin, nudges, gate, review, curator)` and the `LearningScopes`
registry that builds one lazily per user (`for_user(uid)`, cached per process
after a successful build; `owner()`; `user_ids()`; `observes(uid)`), with
`wiring.build_scopes(cfg, workspaces=, meta=, sessions=, activity_for=,
wiki_lint_for=, fork=, snapshots=, users=, learning_of=, review_slot=)` as the
module's composition — it sits outside the four
layers like `extensions/wiring.py`, because a scope holds application services
*and* the infrastructure adapters that satisfy their ports, and `main.build_app()`
calls it once. Two rules there are load-bearing: **`for_user(0)` is the owner**
(the resolved id is the cache key, so a caller carrying `0` and a caller
naming the owner share one object — two sidecars over one `.usage.json` would
overwrite each other's counts; on an unclaimed deploy the scope is built over
`Workspace.owner` and *not* cached, for the reason the resolver never caches a
zero owner), and **`user_ids()` honours each user's learning grant** (since
`0009`): the owner first and unconditionally — their pass never waits on a
lookup — then every id from `AccessControl.active_user_ids()` (every non-disabled
user, **no limit**, because a `list_users(limit=100)` there would have silently
dropped user 101 from every curator sweep) for which `learning_of(uid)` answers
true, deduplicated. `learning_of` is identity's effective grant —
`users.learning_enabled`, else the `users_learning_enabled` default, resolved by
`Grants.of` — passed in by `main.py` as `(await acl.grants(uid)).learning` and
asked per sweep, never cached; it is a required argument, so a composition that
forgot it fails at boot rather than quietly learning over somebody. A lookup
that raises for one id is logged and leaves THAT id out of this sweep (the next
tick asks again). `observes(user_id)` asks the same grant for one id — the owner
and `0` are always observed without a lookup — and lets a failure raise, because
its caller holds a gate and decides what a failure means.

Every consumer resolves its scope **per call** and holds none across calls,
because which one it wants is decided per call: `LearningLoop.after_turn` by the
lane owner, the `learning_review` task by the `owner_id` on its payload, the
curator sweep by walking `user_ids()`, `core.app_context.SkillAdminTool(scopes)`
and the skills API by the requesting user. `LearningBundle` keeps its owner-level
fields (`skills_dir … gate`) populated from the owner scope for the tests that
read them by name, and gains `scopes` — since Step 5 `WebDeps` carries `learning`
(the registry) and none of those fields, so the skills router mounts on the
registry alone.

**Meta keys per user, the owner's untouched.** `infrastructure/scoped_meta.py::
ScopedMeta(meta, suffix)` satisfies the `MetaStore` port and appends `suffix` to
every key on the way in: `""` for the owner — decided by the *workspace*
(`root == home`), so an unclaimed deploy's tree keeps them too — and `:<user_id>`
for everyone else. The seven pre-4b rows (`turns_since_wiki_review`,
`iters_since_skill_write`, `curator_last_run_at`, `curator_paused`,
`curator_last_report_path`, `curator_last_dry_run_at`, `learning_review_until`)
are therefore byte-identical to 4a's: no migration, `skill_admin status` keeps
reading three of them by literal, `scripts/import_sqlite.py` copies the suffixed
rows for free. `build_scope` constructs **one** `ScopedMeta` per scope and hands
that instance to all four readers (`NudgeTracker`, `MetaReviewGate`,
`SkillAdminService`, `CuratorService`) — four separately built instances would
agree today, and the day one is built with the wrong suffix a user's
`curator_paused:7` would pause the owner's pass ([[contract.state-db]]).

**The grant.** `TurnService` calls `observer.after_turn(session_key, stats,
owner_id=ctx.owner_id)` when `ctx.is_owner_lane or ctx.learning_enabled`
(modules/turns/application/turn_service.py) — the lane owner's effective
learning grant, which turns resolves once per turn through identity inside the
turn's envelope ([[flow.message-turn]]); otherwise `_say_unobserved()` logs once
per process ("a non-owner lane without a learning grant is not observed by the
learning loop"). The owner sets it per person in Settings → Users; a person with
no choice of their own inherits `users_learning_enabled`, which defaults to
**false** because the review forks and the weekly lint run on the owner's account
for somebody else's traffic and nothing meters it per user — the cost is spelled
out at the key in `config.yaml.example`. A withdrawn grant stops the next turn's
observation, the next sweep's pass and any review of theirs still queued (below);
their nudge counters are left where they were, so a re-granted user's first
review may come a little early. A non-owner's library, `skill_admin` and Skills
page are theirs regardless of the grant — what it withholds is the background
work, and nothing in their reply may claim it happened (P5)
([[decision.multi-user-isolation]]). Verified live on 2026-09-18, before `0009`,
through a real taskiq worker with the deploy-wide switch on and
`wiki_nudge_interval: 1`:
user 2's web turn fired the wiki nudge, `learning_review` ran with `owner_id=2`,
the lease was `learning_review_until:2` and the counter `turns_since_wiki_review:2`,
and the fork ran in `users/2/.claude-cli` (the only project dir there is user 2's
`agent_home`) and released the lease.

Wave 2 moved this component into `src/claude_hermes/modules/learning/`; paths
below are relative to that module root unless they name another one. Two pieces
deliberately left: `TurnStats` is `core/events.py` and `observe_block` is
`modules/turns/application/observation.py` (the runner parses the SDK blocks),
and the wiki-lint pass is memory's ([[decision.modules-and-ports]]).

- `core/events.py` + `modules/turns/application/observation.py` — `TurnStats` (core/events.py) accumulated per `ToolUseBlock`
  by `observe_block` (modules/turns/application/observation.py): tool count, `wrote_wiki` (a Write/Edit
  path containing `/wiki/`), writes under `.claude/skills/` (skill patches),
  `Skill` invocations.
- `application/services/nudges.py` — `NudgeTracker.observe_turn` (modules/learning/application/services/nudges.py): counters in DB
  meta (survive restarts). Wiki counter +1 per turn, reset on a wiki write;
  emits `"wiki"` at `wiki_nudge_interval` (4). Skills counter +`tool_uses`,
  reset on a skill write; emits `"skills"` at `skill_nudge_interval` (15).
  Counters, never injected text — cache-safe.
- `application/services/loop.py` — `LearningLoop(scopes, dispatcher)` is a
  **router over scopes** since 4b: `after_turn(session_key, stats, *, owner_id=0)`
  resolves `scopes.for_user(owner_id)`, bumps *that* sidecar's
  `use_count`/`patch_count`, asks *that* tracker (counters under that user's meta
  keys), and — if *that* `ReviewGate` says no review is in flight — claims the
  gate and calls `ReviewDispatcher.dispatch(session_key, kinds,
  owner_id=scope.user_id)` (`scope.user_id`, not the raw `owner_id`, because `0`
  has been normalised to the resolved owner and the worker must hit the same
  cache entry). A nudge arriving while one runs is dropped, not queued; a failed
  enqueue releases the gate. Since Wave 3a the dispatcher is
  `QueueReviewDispatcher` over the process's `TaskQueue`
  (modules/learning/infrastructure/dispatch.py): inline it is the same
  `asyncio.Task` as before, in redis mode the `learning_review` task runs in a
  worker, and neither this module nor the loop knows which
  ([[decision.redis-taskiq-scaling]]). The task (workers/tasks.py) asks
  `scopes.observes(owner_id)` **again at run time** (`0009`): a review that sat
  on the queue while the owner withdrew that user's learning grant releases the
  scope's gate — the loop claimed it before enqueueing and only `ReviewService.run`
  would otherwise release it, so an early return would block that user's nudges
  and curator pass for `review_timeout_seconds` — logs once and returns without a
  fork; a lookup that raises releases the gate too and propagates. `kinds` travels as a **sorted `list[str]`**,
  never the tracker's `set`, and `owner_id` as an int — the payload has to survive
  being JSON on a Redis list. A payload with no `owner_id` (queued by a 4a agent,
  drained by a 4b worker) is the owner's; the other direction is a `TypeError`
  and a lost review, which is why a multi-container upgrade drains the queue
  first ([[system.operations]]). The Wave 2 `reviewer=` keyword and its
  `_DirectDispatcher` shim are gone with the constructor they hung off.
- `infrastructure/review_gate.py` — `MetaReviewGate` is the per-user single-flight
  answer now that the review may run in another process. It is a **lease, not a
  flag**: the meta key `learning_review_until[:<user_id>]` holds the instant a
  claim expires (`cfg.review_timeout_seconds`, default 900 s), `ReviewService`
  claims it at the start of a pass and clears it in a `finally`, and both that
  user's curator gate and `TurnService` read `running()`. A boolean would have
  stayed true forever the first time a worker was OOM-killed mid-pass, silently
  disabling the curator; an unreadable row reads as free for the same reason.
  Wave 2's `review_running` *property* is gone — a property returning a coroutine
  is truthy in every `if` that forgets to await it.
- `infrastructure/review_slot.py` — **one review fork at a time, deploy-wide,
  still.** With a lease per user the gate no longer bounds concurrency (N users
  could each hold their own), so a second bound replaces it, one per queue mode:
  inline, `InlineQueue(registry, {run_job: 1, learning_review: 1})` in `main.py`
  (`core/tasks.py`'s docstring says the bound is the limit now, not the lease);
  redis, `RedisReviewSlot` — the `ReviewSlot` port (`acquire() -> bool`,
  `release()`) over `core.redis.Lock("lock:review", ttl_ms=review_timeout_seconds
  *1000)`, a fresh `Lock` per claim because a taskiq worker runs several tasks in
  one process and a shared `Lock` object's `held` flag would be flipped by the
  loser's failed `acquire`, leaving the winner unable to release. `ReviewService.
  run` claims the gate first, then the slot; a held slot **releases the gate and
  returns `None`** — a dropped review is the designed outcome, and the counters
  fire again next interval. `slot=None` (inline, tests) means always free.
- `application/services/review.py` — `ReviewService.run` (modules/learning/application/services/review.py,
  forking through `SdkForkRunner.run_review`, modules/learning/infrastructure/fork_runner.py):
  resumes the session with
  **`fork_session=True`** (sees the context, never corrupts the live
  session), `max_turns=16`, tools = Read/Glob/Grep/Write/Edit only — no MCP
  server at all (`REVIEW_TOOLS`, modules/learning/domain/constants.py).
  **Which model, and why not the lane's.** Every fork runs
  `cli_id(cfg.review_model or cfg.model)` — curator consolidation and the wiki
  lint `cli_id(cfg.curator_model or cfg.review_model or cfg.model)`, as before —
  with **no effort**, whatever model the lane it reviews was switched to
  ([[decision.model-choice-is-lane-state]]). The old "same model reuses the warm
  cache" rationale was false and is gone from `config.py` and
  `config.yaml.example`: a fork's toolset differs from the lane's and tools are
  the first segment of the cached prefix, so a fork never reads the lane's cache
  at all, and following a Fable lane would only make every review a Fable review.
  What does matter is the window — the fork resumes the lane's whole history —
  which is why `offered()` warns at boot when an offered model outgrows
  `review_model or model`. `CLAUDE_CODE_NO_MODEL_FALLBACK=1` reaches every fork
  through `workspace_env`, and each fork logs the model that answered and the
  cache-read share of its input (`fork_usage.drain_fork`; the lint fork carries
  its own copy of that loop, since a module may not import another's).
  **Since 4b every fork runs in ITS workspace, confined like a turn**
  (`run_review(..., workspace=)`, `run_consolidation(..., workspace=)`,
  `WikiLintOptions(..., workspace=)` / `build_wiki_lint_service(...,
  workspace=)`; `None` means `Workspace.owner(home)`, the deploy owner's tree): the
  fork gets `base_options(workspace.root)` and `env={**NO_ADVISOR_ENV,
  **workspace_env(workspace)}` — the user's `CLAUDE_CONFIG_DIR`, so `resume`/
  `fork_session` reads *their* transcript store, and their `TMPDIR`; a
  `PreToolUse` `HookMatcher(PATH_TOOLS, path_guard_factory([agent_home, tmp_dir,
  logs_dir, plugins_dir], cwd=agent_home))` — the **same** hook a foreground turn
  gets, for one rule (`plugins_dir` is in the list for that reason only, no fork
  loads a plugin; the catalog root is *not*, no fork reads a manifest;
  `fork_roots`/`fork_path_guard` in fork_runner.py, the same list spelled inline
  in memory's `lint_fork.py`); and `disallowed_tools=["Bash", "Agent", "Task"]`
  (`FORK_DISALLOWED_TOOLS`) — the forks' `tools=` already omitted them, but under
  `bypassPermissions` only `disallowed_tools` takes a tool away, the lesson of
  4a's live smoke. `write_guard_factory`
  (integrations/claude_sdk/guards.py) is a `PreToolUse` hook confining Write/Edit to an explicit
  root list — an **empty** list denies everything, which is how a dry-run
  lint pass is read-only by construction; the review pass itself passes
  `[skills_dir, wiki_root]` and still protects `.usage.json`/`.archive`,
  failing closed on an unparseable path. It gained **`cwd=`** in 4b and every fork
  passes `workspace.agent_home`: the path guard beside it on the same matcher
  resolves a relative `file_path` against the lane's `cwd`, and left to resolve
  against the *process* cwd — a different directory in every worker — the two
  would disagree about one path, so a relative `.claude/skills/x/SKILL.md` would
  be allowed by one hook and denied by the other (`tests/test_review.py`: a
  relative `skills/` write passes both, a relative `logs/` write fails).
  `skills_write_guard_factory`
  (modules/learning/infrastructure/guards.py) is the same guard narrowed to the skills dir alone, kept
  for the curator's consolidation pass. The fork also wires
  `raw_guard_factory` (modules/memory/infrastructure/turn_hooks.py, `wiki/raw/` is immutable) into the same
  `PreToolUse` matcher, and `page_touch_factory` (modules/memory/infrastructure/turn_hooks.py) +
  `stop_gate_factory` (modules/memory/infrastructure/turn_hooks.py) on `PostToolUse`/`Stop` via a per-review
  `WikiTurnState` (modules/memory/infrastructure/turn_hooks.py) — a page written without an index/log
  update blocks the fork's own Stop once, then lets it finish if the model
  ignores the nudge (`stop_hook_active`). Newly created skill dirs are
  marked `created_by="agent"` in the sidecar.

  **The review fork flushes what it wrote into the wiki index, under the
  SCOPE's user id.** `run_review(..., workspace=, owner_id=)` appends
  `guards.index_flush(state, owner_id, workspace)` to the `Stop` list after the
  gate: the gate is what makes the fork update `index.md`, the flush is what
  makes the rows match the tree it just changed. It is on that list because
  curation runs out of band (P3) and the fork is therefore the wiki's main
  rewriter — without it `memory_search` would quote a snippet the fork had
  already rewritten, as memory, until the next lint or boot
  ([[decision.wiki-index-not-store]]). `owner_id` travels from the scope because
  a workspace does not name its owner, and `0` means "do not index";
  `run_consolidation` writes skills only and gets no flush. Flushing twice is
  idempotent, so nothing depends on whether the SDK runs later hooks after a
  `block`. A **non-owner's** fork — review and lint alike — gets
  `ws.shared_wiki_root` as an extra read root and the `WRITE_TOOLS` fence over
  both spellings of `shared/` beside it — `WRITE_TOOLS`, not `Write|Edit`,
  because `tools=` is an auto-approve list under `bypassPermissions` and only a
  matcher naming `MultiEdit`/`NotebookEdit` fences them — and
  `build_wiki_lint_prompt(dry_run, *, shared_readonly=True)` adds one sentence
  saying so, so the pass does not burn `curator_max_turns` on writes it will be
  denied ([[decision.multi-user-isolation]]).
- `domain/prompts.py` — authoring standards (description ≤60 chars, fixed section
  order), `build_review_prompt` (modules/learning/domain/prompts.py, wiki section when `"wiki"`
  is due, skills section when `"skills"` is due — patch-existing-umbrella-
  first, never capture env-dependent failures), `/learn` prompt, curator
  prompt, and `build_wiki_lint_prompt` (modules/learning/domain/prompts.py) for the curator's
  wiki-lint pass.

## The library — one implementation, two channels

`library.py` holds every read and every file write of the skills library:
`list_skills`, `read_skill`, `write_skill`, `create_skill`, `etag_of`,
`parse_front_matter`. Both `skill_admin` (chat, its `list` action refactored onto
`list_skills`) and [[component.web]]'s skills router call it, so the two channels
cannot drift on what "stale", "bundled" or "archived" means — the
`extensions/manage.py` precedent, for the same reason. What it deliberately leaves
to its callers is the bookkeeping the two do differently: the snapshot, the patch
count and the session reset.

**Since 4b the library is per user, on both channels.** Each scope holds its own
`SkillLibraryService(SkillRepository(workspace.skills_dir), sidecar, archive)`.
In chat, `SkillAdminTool(scopes).handle(args, ctx)` resolves
`scopes.for_user(ctx.owner_id)` — the **lane owner's** library, never the
sender's and never the deploy owner's by default — and the tool exists on every
private, web and cron lane, and on **no** group lane for anybody (the
lane-shaped rule, `core/session_keys.is_group_lane`;
[[decision.multi-user-isolation]]). In the browser, `presentation/dependencies.py`'s
`get_scope` resolves `deps.learning.for_user(user.user_id)` and `get_library`,
`get_usage`, `get_skills_dir`, `get_logs_dir` read off that scope, so every
`/api/skills` route is the **requesting user's own library** — reads and writes
alike; there is no `require_owner` on the mutations any more, because the scope
*is* the authorization: no route can name another user's library, and a name from
somebody else's is a plain 404. In 4a `GET /api/skills` served the owner's library
to any logged-in user; this closes that. Verified live: `GET /api/skills` as each
user returned that user's library, user 2's `POST /api/skills` created
`users/2/agent_home/.claude/skills/smoke-user2`, and the owner got 404 on
`/api/skills/smoke-user2`. The **invalidation after a mutation is
`runner.reset_owned(scope.user_id, include_unowned=scope.is_owner)`**, not
`reset_all()`: a user's skill index reaches nobody else's cached prefix, so
evicting every other user's warm session for one person's edit would be the
NFR-1 regression the per-user prefix was meant to avoid. `SessionRepository.
reset_owned` is `DELETE FROM sessions WHERE owner_id = :o`, plus `OR owner_id IS
NULL` only when the caller is the deploy owner — a lane bound while the deploy
owner was still `0` wrote NULL, and those lanes are the owner's. `reset_all()`
stays for the extensions mutations, which really do change every lane's prefix —
and since Step 5 only extensions' `PromptCacheInvalidator` declares it; learning's
port declares `reset_owned` alone.

- **The listing is a union of three sources that can disagree** — live
  directories, `.archive/`, and `.usage.json` — because a skill missing from any
  one of them is still real: a directory the sidecar never saw predates telemetry,
  a sidecar key with no directory is one somebody moved by hand. The **directory**
  decides `state`: no live directory means `archived` whatever the sidecar says,
  since `state` describes what the model can load this turn. Likewise the source
  tree, not the sidecar, decides `source`: a name under the bundled skills dir
  (`paths.bundled_skill_names`) lists as `bundled` even if the sidecar recorded
  `created_by: agent`.
- **A broken skill still lists**, with `broken: true` and an empty description,
  rather than raising — hiding it is how one stays broken for weeks. `broken`
  covers both unparseable frontmatter and a missing description, because the
  prompt index cannot tell them apart either: neither routes.
- **Containment on every path, reads included.** Every directory is built as
  `skills_dir / name` and then re-checked with `resolved.is_relative_to(root)` —
  the `FileStore.resolve` rule. The name regex guards writes, but a read takes
  whatever the URL carried, so the check is on the resolved path and a name that
  survives the regex by accident still cannot escape.
- **`write_skill` is guarded by an etag**, `sha256` of the exact bytes on disk.
  The review pass above writes these same files after most turns; without the
  precondition an owner's Save would silently discard whatever the agent had just
  learned. It does not serialise the two — a write landing mid-pass still wins —
  it makes the loss *visible*: `StaleEdit` carries the current text so the caller
  can show it instead of sending the owner to fetch it. The write itself is
  `os.replace` over a temp file in the same directory, because the review pass may
  be reading the file right now and a truncate-then-write would hand it half a
  skill. `before_write` is the hook the web router uses to place its snapshot
  after the etag and the frontmatter are known good and before the replace —
  outside the call, every refused save would cost a slot in a keep-5 store.

## Provenance and lifecycle

- `infrastructure/usage_sidecar.py` + `domain/usage.py` — `.usage.json` sidecar per skill: `created_by`, timestamps,
  `use_count`, `patch_count`, `state` (active/stale/archived), `pinned`.
  Atomic best-effort writes. `last_activity` (modules/learning/domain/usage.py) anchors aging.
- `infrastructure/skill_archive.py` — `archive_skill` moves one skill into
  `skills/.archive/<name>`; `restore_skill` reverses. No deletion path
  exists (PRD P4).
- `core/snapshots.py` — `snapshot` (core/snapshots.py): tar.gz of a source tree into
  `logs/<kind>_backups/<utc-id>/` + manifest, retention `backup_keep` (5).
  `kind` (`"skills"` | `"wiki"` | `"skill_edits"`, `_KINDS` map at core/snapshots.py)
  picks the backups root and archive name, so each writer gets an independent
  snapshot store that `backup_keep` prunes on its own. `skill_edits`
  (`logs/skill_edit_backups/`) exists for exactly that isolation: a browser save
  snapshots the skills tree too, and reusing `kind="skills"` would let five
  owner edits evict the `pre-curator-run` snapshot the curator's own rollback
  instructions point at. `rollback` (core/snapshots.py) takes a protective
  pre-rollback snapshot (same `kind`) and rejects unsafe tar members.

## Curator

`CuratorService.run_pass()` (modules/learning/application/services/curator.py) is
**one pass over ONE scope's tree** since 4b — constructed per scope by
`wiring.build_scope` with that scope's `ScopedMeta`, sidecar, archive,
consolidation service, `ReportWriter(logs_dir)`, the memory module's wiki lint
over that tree (`wiki_lint_for(workspace)`) and that user's `MessageActivity`
(built per scope with an **async** `owner()` callable returning the user's id —
the adapter awaits it, and a bare `lambda: uid` would `TypeError` inside the
swallowed pass). `logs_dir=` replaced the old `home=` parameter on both
`CuratorService` and `ReportWriter`: the only thing either did with `home` was
spell `home / "logs"`, the scope already hands that directory to the admin tool
and the snapshots, so it is one fact and one parameter — byte-identical
`CH_HOME/logs` for the owner, `users/<id>/logs` for everyone else, where their
read guard can see it. It runs
behind sequential gates: enabled → not paused (*this scope's* `curator_paused
[:<id>]`) → no review in flight (*this scope's* `ReviewGate`) → first-run
deferral → interval (168h) → chat idle ≥2h (*this user's* `messages` max ts, so a
neighbour chatting at 3am holds nobody else's pass off). A new user's library
therefore gets its full interval of grace before the first aging pass. A real
(non-dry-run) pass first snapshots the skills
tree (`pre-curator-run`, `kind="skills"`), then:

- **Aging** (`apply_aging`, modules/learning/application/services/aging.py): agent-created, unpinned skills
  only — active → stale after 30 idle days, stale → archived after 90;
  recent use/patch reactivates.
- **Consolidation** (`run_consolidation`, modules/learning/infrastructure/fork_runner.py, opt-in
  `curator_consolidate`, default off): fresh-context LLM pass with
  Read/Glob/Grep/Write/Edit and the skills-only write guard; the model
  merges content but **cannot archive** (guard blocks `.archive/`) — the
  code archives from the required trailing YAML block, re-checking
  eligibility.
- **Wiki lint** (`run_wiki_lint`, modules/learning/application/services/curator.py, gated by
  `wiki_lint_enabled`, default on): unless dry (`curator_dry_run` or
  `wiki_lint_dry_run`), snapshots `wiki_root` first (`kind="wiki"`, reason
  `pre-wiki-lint`), then forks a fresh-context pass — no resume, nothing to
  fork from — with Read/Glob/Grep/Write/Edit. `write_guard_factory` gets an
  **empty** root list on a dry run, so every write is denied and the
  "report only" pass is read-only by construction; a real pass gets
  `[wiki_root]` plus `raw_guard_factory` protecting `wiki/raw/`.
  `build_wiki_lint_prompt` hunts contradictions between pages, claims that
  drifted from their `raw/` source, orphan pages, broken `[[links]]`, an
  overgrown index, and pages still tagged `migrated` from the old flat-
  memory carry-over. A **wet** pass that returned a result then awaits
  `after_lint()` — `main.build_app()`'s closure over `indexer.reindex(user_id,
  ws)`, so the whole tree is re-indexed after the one pass that rewrites pages
  wholesale. It is awaited inside the same `try`, so a failing callback is
  logged rather than raised: a pass that fixed the wiki still reports what it
  did ([[decision.wiki-index-not-store]]).

Reports land in that scope's `logs/curator/<ts>/` — `CH_HOME/logs/curator/` for
the owner, `users/<id>/logs/curator/` for everyone else (`run.json` + `REPORT.md`
with rollback instructions and, now, a `## Wiki lint` section — `_write_report`,
modules/learning/application/services/curator.py); the result dict gained `wiki_lint` and `wiki_snapshot_id`.
State lives in that scope's meta keys `curator_last_run_at`, `curator_paused`,
`curator_last_report_path` (plus `curator_last_dry_run_at`, and the review gate's
`learning_review_until`) — unsuffixed for the owner, `:<id>`-suffixed otherwise.

**The sweep: every user's pass, owner first, one at a time.** `workers/curator.py`
holds `run_once(service, lock=None)` (one scope's pass, under its lock in redis
mode, swallowing and logging every exception — a curator that dies on one bad
pass stops ageing the library for the life of the process), and since 4b
`run_all(scopes, lock_for=None) -> list[dict]`: it reads the **unsuffixed**
`curator_paused` through the owner scope's meta *first* and, if it is `"1"`,
skips the whole sweep with `[{"ran": False, "reason": "paused"}]` — that row is
the operator's switch, `domain/constants.py` promises nothing in this code writes
it, and it has to stop *all* of it; a user's own `curator_paused:<id>` is what
their own pass reads. Then for each id in `scopes.user_ids()` it resolves the
scope (a `users` row whose tree fails to seed is logged and skipped, so one bad
user does not stop everyone else's library from ageing) and runs `run_once(scope.
curator, lock_for(uid))`, **sequentially** — N passes may each spawn a CLI fork,
and N at once is N CLI processes in a container sized for one. Each result
carries `user_id`. Verified live: the sweep ran the owner's pass, skipped user 2
with `reason: review running` while user 2's own review lease was held, ran user
2's pass on the next sweep (report under `users/2/logs/curator/`, meta
`curator_last_run_at:2`), and an unsuffixed `curator_paused=1` made it return the
single `paused` result.

**Two ways to drive the sweep, one body.** `curator_loop(scopes, tick_seconds)` is
inline mode's cadence — one process, one loop inside `main.run`, tick 900 s, no
locks because there is nobody to race — and `curator_tick` in `workers/tasks.py`
is redis mode's: `run_all` inside an outer `core.redis.Lock` on
**`lock:curator:sweep`** (TTL `curator_max_turns * 60 s * max(1, N users)`) so two
ticks never run two sweeps at once — a sweep of N users outlives the 15-minute
tick, and without the outer lock the next tick would start a second sweep behind
the first; the per-user locks would keep them off the same tree but not off the
same account — and `lock_for(uid)` building a per-pass `Lock` on **`lock:curator`**
for the owner (the unsuffixed key) or **`lock:curator:<id>`** for anyone else (TTL
`curator_max_turns * 60 s`), which is what still keeps an inline `curator_loop`
and a worker off one tree, and what holds when a sweep outlives its outer lock's
TTL. A killed worker's lock expires rather than being released by hand. The
pass's own interval and idle gates are unchanged — the locks only decide how many
processes are asking. The `skill_admin` tool (`SkillAdminService`, modules/learning/application/services/skill_admin.py,
  reached through turns' delegating handler and `SkillAdminTool(scopes)`) exposes
list/pin/unpin/archive/restore/snapshots/rollback in chat — for the lane owner's
skills store only; the wiki has no chat-facing admin tool, only the lint pass.
Verified live: `skill_admin list` on user 2's private web lane listed user 2's
seeded library.

**The seeds are core's, but one fix here is learning's to know about.** Before
4b `core/workspace.py::seed_skills` copied every bundled skill that was *absent*
from `skills/` on every boot — including one the curator's aging pass or
`skill_admin archive` had just moved into `skills/.archive/<name>`, so an archived
bundled skill was resurrected on every restart and archived again a week later,
forever. `seed_skills` now skips any bundle name that exists under `.archive/`,
whatever the `SeedManifest` says, and the manifest itself makes deletion stick
and refreshes a seed the user never touched ([[component.core-runtime]]).

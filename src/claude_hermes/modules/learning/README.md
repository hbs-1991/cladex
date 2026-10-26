# `modules/learning`

## Purpose

The agent's procedural memory and the machinery that keeps it honest: the skills
library (list / read / edit under an etag / create / archive / restore), the usage
sidecar that records who made a skill and when it was last used, the post-turn
nudge counters, the out-of-band **review** fork that writes down what a turn
taught, and the **curator** — a gated, reporting, unattended pass that ages skills
out, asks a fork to merge narrow ones into umbrellas, and lints the wiki through
memory.

Owns no table. Its state is on disk (`.claude/skills/**`, `.usage.json`,
`logs/*_backups`, `logs/curator/**`) plus seven `meta` rows — **per workspace**
since Wave 4b: the owner's rows keep their pre-4b names, every other user's are
the same names with a `:<user_id>` suffix, and the on-disk state sits in that
user's tree (`users/<id>/agent_home/.claude/skills`, `users/<id>/logs`).

## Layout

```
domain/          constants (the seven meta keys, tool sets, caps, layout)
                 exceptions (SkillError, StaleEdit -> ApplicationException)
                 models (SkillInfo, etag_of, parse_front_matter, validate_content)
                 usage (new_entry, last_activity)
                 prompts (review / learn / curator / wiki-lint builders, VERBATIM)
application/     ports.py
                 scopes.py   LearningScope (one user's objects) + LearningScopes
                             (the registry: owner(), for_user(id), user_ids())
                 services/ nudges, loop, review, library, aging,
                           consolidation, curator, skill_admin
infrastructure/  usage_sidecar, skill_archive, skill_repository,
                 guards (skills_write_guard_factory), fork_runner (the SDK),
                 fork_usage (drain_fork: one fork's stream + what answered it),
                 report (the curator's run.json + REPORT.md),
                 review_gate (MetaReviewGate), dispatch (QueueReviewDispatcher),
                 scoped_meta (ScopedMeta — the per-user key suffix),
                 review_slot (RedisReviewSlot over `lock:review`)
presentation/    router (router: APIRouter, 8 routes), dependencies (7 providers),
                 schemas (row_of, stale_edit_body, SkillCreate, SkillSave)
wiring.py        build_scope / build_scopes — the module's composition, outside
                 the four layers like extensions' `wiring.py`; `main.build_app()`
                 calls it once
```

**A scope per workspace (Wave 4b, D1).** `LearningScope` is one user's full set
— `skills_dir`, `logs_dir`, `meta` (a `ScopedMeta`), `sidecar`, `archive`,
`library`, `admin`, `nudges`, `gate`, `review`, `curator` — built lazily by
`wiring.build_scope` over that user's `Workspace` and cached per process.
`LearningScopes` is what every consumer holds: `LearningLoop.after_turn` asks for
the lane owner's scope, the `learning_review` task for the one the nudge was
dispatched with, the curator sweep walks `user_ids()`, and `skill_admin` and
`/api/skills` ask for the requesting user's. Two rules are load-bearing:
`for_user(0)` IS the owner (the cache key is the resolved id, so a 4a payload and
an `owner_id=0` caller land on the same object — two sidecars over one
`.usage.json` would overwrite each other), and `user_ids()` honours each
user's **learning grant** (since 0009): the owner first and unconditionally,
then every non-disabled user from `AccessControl.active_user_ids()` (no limit)
for whom `learning_of(uid)` answers true, deduplicated. `learning_of` is
identity's effective grant — the user's own `users.learning_enabled`, else the
`users_learning_enabled` default — injected by `main.py` as a callable and asked
per sweep, never cached; a lookup that raises for one id is logged and leaves
THAT id out of this sweep, never the owner's pass or anybody else's.
`observes(user_id)` asks the same grant for one id (the owner and `0` are always
observed without a lookup) and, unlike the sweep, lets a failure raise.
`core.app_context.LearningBundle` carries `scopes` plus the deploy owner's scope
objects, built eagerly (the curator and review tests read them by name).

`workers/curator.py` holds the three ways `CuratorService.run_pass()` is driven:
`run_once(service, lock)` (ONE scope's pass, holding that scope's lock in redis
mode), `run_all(scopes, lock_for)` (the sweep: reads the unsuffixed
`curator_paused` first, then every id from `scopes.user_ids()`, owner first,
sequentially — N users is N passes and each may spawn a CLI fork), and
`curator_loop(scopes, tick_seconds)` (inline mode's cadence over the sweep). All
swallow and log every exception: a curator that dies on one bad pass stops
ageing the library for the life of the process, and one user whose tree fails to
resolve must not stop every other user's library from ageing.

The review takes the same shape. `LearningLoop.after_turn` enqueues
`learning_review(session_key, kinds, owner_id)` through `QueueReviewDispatcher`;
`core.tasks.InlineQueue` runs it here and `TaskiqQueue` runs it in a worker, and
neither this module nor the loop knows which. The worker resolves
`scopes.for_user(owner_id)` and runs THAT scope's `ReviewService` — after asking
`scopes.observes(owner_id)` again (0009): a review that sat on the queue while
the owner withdrew that user's learning grant releases the scope's review gate
(the enqueuing turn claimed it, and only `ReviewService.run` would otherwise
release it), logs once and returns without a fork. A lookup that raises releases
the gate too and propagates.

## Public surface

### Ports consumed (`application/ports.py`)

| Port | Methods | Provided by |
|---|---|---|
| `SkillUsagePort` | `mark_created`, `bump_use`, `bump_patch`, `set_archived`, `set_state`, `set_pinned`, `get`, `all`, `list_skill_dirs` | own `UsageSidecar` |
| `SkillArchivePort` | `archive_skill(name, now_iso)`, `restore_skill(name, now_iso)`, `list_archived()` | own `SkillArchive` |
| `SkillRepositoryPort` | `skills_dir`, `resolve(name, *, archived=False)`, `locate(name)`, `placement(name, live)`, `info(...)`, `detail(...)`, `read_bytes`, `write_atomic`, `create_dir`, `exists` | own `SkillRepository` |
| `MetaStore` | `async get(key)`, `async set(key, value)` | `core.meta.MetaStore`, wrapped per scope in own `ScopedMeta(meta, suffix)` — `""` for the owner, `":<user_id>"` for everybody else; one instance per scope handed to all four readers (nudges, gate, admin, curator) |
| `ReviewDispatcher` | `async dispatch(session_key, kinds: list[str], owner_id: int = 0)` | own `QueueReviewDispatcher` over `core.tasks.TaskQueue`; `owner_id` rides the payload as a primitive because the session key alone does not say whose scope a `cron:` lane is |
| `ReviewGate` | `async running()`, `async claim()`, `async release()` | own `MetaReviewGate` over that scope's `ScopedMeta` — one lease per user |
| `ReviewSlot` | `async acquire() -> bool`, `async release()` | own `RedisReviewSlot` over `core.redis.Lock("lock:review")` in redis mode; `None` inline (the `InlineQueue` limit `{learning_review: 1}` is the bound there) |
| `SessionSnapshotPort` | `async get(session_key) -> {sdk_session_id, system_append} \| None` | **sessions** |
| `ActivityPort` | `async last_message_ts() -> str \| None` | **sessions**, through `core.app_context.MessageActivity(messages, owner=<async callable>)` — one per scope, so a user's quiet hours are their own |
| `SnapshotStorePort` | `snapshot(dir, logs, reason, keep=, kind=)`, `list_snapshots(logs, kind=)`, `rollback(dir, logs, id, keep=, kind=)` | `core.snapshots` |
| `WikiLintPort` | `async lint(dry_run=False) -> str \| None` | **memory** (`WikiLintService`) |
| `WikiGuardsPort` | `new_turn_state()`, `raw_guard(wiki_root)`, `page_touch(state, wiki_root)`, `stop_gate(state)`, `index_flush(state, owner_id, workspace) -> hook \| None` | **memory**, through `core.app_context.WikiForkGuards` over the hook factories. `index_flush` answers `None` when no wiki index is wired, and `fork_runner` reads it with `getattr` — a composition older than the index is a fork that writes no rows, not a review that dies building its options |
| `ForkRunner` | `async run_review(*, prompt, system_append, resume, skills_dir, wiki_root, touched, workspace=None, owner_id=0)`, `async run_consolidation(*, prompt, skills_dir, workspace=None)` | own `SdkForkRunner`. `workspace` is the tree the fork runs in (its `cwd`, its `CLAUDE_CONFIG_DIR`, its guard roots); `None` keeps the deploy owner's. `owner_id` is the SCOPE's user id — whose wiki index the fork's Stop hook writes into — because a workspace does not name its owner; `0` means "do not index" |
| `PromptCacheInvalidator` | `async reset_owned(owner_id, *, include_unowned=False)` | **turns** (`TurnService.reset_owned` — one user's lanes, B-4; extensions' own invalidator is `reset_all`, because an install changes every lane's prefix). `dependencies.get_invalidate` binds it per request to the requesting user |
| `ReportWriterPort` | `write(result) -> Path` | own `ReportWriter` |
| `Clock` | `__call__() -> datetime` | **declared, nothing binds it yet** — every service still takes `now` as an argument |

### Ports provided

* `SkillLibraryPort` — `list_skills`, `read_skill`, `write_skill`, `create_skill`
  (plus `archive_skill` / `restore_skill`), satisfied by `SkillLibraryService`.
  Consumed by the turns module and the skills router.
* `SkillAdminService.handle(action, *, name="", backup_id=None) -> str` — the
  `mcp__hermes__skill_admin` body: `list | pin | unpin | archive | restore |
  status | snapshots | rollback`, over ONE scope's library, sidecar, meta and
  `logs_dir`. Turns reaches it through `core.app_context.SkillAdminTool(scopes)`,
  whose `handle(args, ctx)` resolves `scopes.for_user(ctx.owner_id or 0)` — the
  LANE owner's library, never the sender's and never the deploy owner's by
  default.
* `LearningScopes` — `owner()`, `for_user(user_id)`, `user_ids()`,
  `observes(user_id)` — what the
  `learning_review` task, the curator sweep, `SkillAdminTool` and the skills
  router hold (`http.deps.WebDeps.learning`).
* `LearningLoop(scopes, dispatcher).after_turn(session_key, observation, *,
  owner_id=0)` + `async review_in_flight(owner_id=0)` — the runner's one call
  into learning. Takes `core.events.TurnObservation`; resolves the owner's scope,
  bumps ITS sidecar, asks ITS tracker, claims ITS gate and dispatches with
  `scope.user_id`. Wave 2's `review_running` *property* is gone: the answer is a
  row now, and a property returning a coroutine is truthy in every `if` that
  forgets to await it. The `reviewer=` keyword and its `_DirectDispatcher` shim
  went with the constructor they hung off (R-3).
* `ReviewService(..., gate=, workspace=, slot=, owner_id=).run(session_key, kinds)` —
  claims the per-user gate, then the deploy-wide slot; a held slot releases the gate and
  returns `None` (a dropped review, the same designed outcome as a dropped nudge).
  `owner_id` is the scope's user id and travels to `run_review` so the fork's
  Stop hook can flush the pages it wrote into that user's wiki index.

**The forks and the shared wiki namespace.** `fork_roots(ws)` gives a non-owner's
fork `ws.shared_wiki_root` as an extra read root — the deploy owner's
`wiki/shared/`, the one tree outside their own they may read — and
`fork_readonly_fence(ws)` installs the `WRITE_TOOLS` matcher that makes it
read-only, in **both** spellings (`ws.shared_wiki_root` and
`ws.wiki_root / "shared"`). Both forks carry the fence; the owner gets neither,
because for them the two paths are one path and it is theirs. The review fork's
own `write_guard_factory([skills_dir, wiki_root], …)` already excludes the first
path — it is not under this user's `wiki_root` — and admits the second, which is
exactly the silent private copy the fence refuses.

**The review fork flushes what it wrote.** Curation runs out of band (P3), so the
review fork is the wiki's main rewriter: without a flush, `memory_search` would
quote a snippet the fork had already rewritten, as memory, until the next lint or
boot. `run_review` appends `guards.index_flush(state, owner_id, workspace)` to the
`Stop` list **after** the gate — the gate is what makes the fork update
`index.md`, the flush is what makes the rows match the tree it just changed, and
flushing twice is idempotent so nothing here depends on whether the SDK runs later
hooks after a `block`. `run_consolidation` writes skills only and gets no flush.
* `CuratorService(cfg=, logs_dir=, skills_dir=, wiki_root=, ..., gate=).run_pass()
  -> dict`, driven per scope by `workers.curator.run_all(scopes, lock_for)` — from
  `curator_loop(scopes, tick)` inline and from the `curator_tick` task in redis
  mode. `logs_dir=` replaced `home=` (S-1): the only thing the class did with
  `home` was spell `home / "logs"`, and for a non-owner that is
  `users/<id>/logs`, where their snapshots, reports and read guard already are.

### Dependencies

`presentation/dependencies.py`, built on `core.di.provide`. A handler writes
`library: SkillLibraryService = Depends(get_library)` and never sees the bundle; a
test swaps one collaborator with `app.dependency_overrides[get_library]`
(`tests/modules/learning/test_learning_router.py`).

**Every provider is the REQUESTING user's (Wave 4b, D6).** `get_scope` resolves
`deps.learning.for_user(user.user_id)` — the caller's own `LearningScope` — and
the rest read off it, never off the bundle (which has carried no owner-scope
object since Step 5). The scope IS the authorization: no route can name another
user's library.

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_scope` | `learning` (`LearningScopes`) + `CurrentUser` | the caller's `LearningScope`, cached per user on the registry |
| `get_library` | `scope.library` | `SkillLibraryService` |
| `get_usage` | `scope.sidecar` | the `UsageSidecar` the turn loop bumps for this user, so a browser read sees a fresh count |
| `get_skills_dir` | `scope.skills_dir` | `Path` — the snapshot *source* for a save |
| `get_logs_dir` | `scope.logs_dir` | `Path` — where this user's `skill_edits` snapshot store lives |
| `get_backup_keep` | `cfg.backup_keep` | `int` — the number, not the whole `Config` |
| `get_invalidate` | `runner` | zero-argument coroutine: `runner.reset_owned(scope.user_id, include_unowned=scope.is_owner)` — this user's lanes only (B-4); the deploy owner also sweeps the `owner_id IS NULL` rows, which are lanes bound before the deploy was claimed |

The bundle attributes this module reads are exactly `learning`, `runner` and
`cfg`. `library is None → no routes` used to live inside a `routes(deps)` closure;
**mounting is the shell's decision** now (`deps.learning is not None`), so the
module states what it needs rather than whether it exists.

### Routes

`router: APIRouter(tags=["learning"])`. **No DELETE** — the app answers 405 there and
that is the feature (PRD P4).

| Method | Path | `operation_id` | Notes |
|---|---|---|---|
| GET | `/api/skills` | `learning_list_skills` | the caller's library |
| POST | `/api/skills` | `learning_create_skill` | 201, body `SkillCreate` |
| POST | `/api/skills/{name}/pin` | `learning_pin_skill` | |
| POST | `/api/skills/{name}/unpin` | `learning_unpin_skill` | |
| POST | `/api/skills/{name}/archive` | `learning_archive_skill` | |
| POST | `/api/skills/{name}/restore` | `learning_restore_skill` | |
| GET | `/api/skills/{name}` | `learning_get_skill` | |
| PUT | `/api/skills/{name}` | `learning_save_skill` | body `SkillSave` |

The fixed segments are registered before `{name}` so a verb can never be read as a
skill name. **There is no owner gate any more** (Wave 4b): every route is the
requesting user's own library, a mutation edits their own skills, and a name
from somebody else's library is a plain 404. `require_owner` used to sit on the
mutations because the one library was the owner's; in 4a `GET /api/skills` served
that library to any logged-in user, and this is what closes it. Admission itself
is re-checked per request by `core.auth.CurrentUserMiddleware(admitted=)` (R-8):
a cookie whose user was de-allowlisted or disabled gets 401, because after 4b a
logged-in user writes prompt text into a tree.

`SkillCreate` and `SkillSave` are `Any`-typed with defaults on purpose:
`SkillLibraryService` owns the validation and answers with the `SkillError` codes the
frontend switches on (`bad_name`, `bad_frontmatter`, `too_large`, `name_taken`), so a
Pydantic 422 in front of it would replace a status and a code
`tests/test_web_skills.py` pins. The one body that does not render through the shared
`ApplicationException` handler is still the 409 `stale_edit`, because it carries the
current text and etag as well.

### Events

None emitted. `core.events.TurnStats` / `TurnObservation` is consumed.

## Invariants

* **Both forks run the DEPLOY's fork model, never the lane's.** `run_review` uses
  `cli_id(cfg.review_model or cfg.model)` and `run_consolidation`
  `cli_id(cfg.curator_model or cfg.review_model or cfg.model)`, with **no
  effort**: a review is background work nobody is waiting on, and a lane on an
  expensive model must not silently make its own review expensive too. `cli_id`
  is the one spelling a model reaches the CLI in, here as in the foreground turn.
  The forks' env carries `CLAUDE_CODE_NO_MODEL_FALLBACK=1` through
  `workspace_env`, so the CLI's own availability chain cannot answer as something
  else. `infrastructure/fork_usage.drain_fork` drives each fork's message stream
  and logs `fork_ran {fork, model, cache_read_share}` — which model actually
  answered (a substitution shows up nowhere else) and
  `cache_read / (input + cache_read + cache_creation)` over the MAIN-LOOP calls
  only, because a `ResultMessage`'s usage is the sum over every call of the fork.

* **Wave 4b: the loop runs per user; since 0009, behind each user's learning
  grant.** `TurnService` calls
  `observer.after_turn(session_key, stats, owner_id=ctx.owner_id)` when
  `ctx.is_owner_lane or ctx.learning_enabled` — the lane owner's effective grant,
  resolved once per turn by turns through identity (their own column, else the
  `users_learning_enabled` default); otherwise it logs once per process ("a
  non-owner lane without a learning grant is not observed by the learning loop")
  and the non-owner lane is exactly what 4a made it — no observation, no
  counters, no review. With the grant on,
  a non-owner's skill invocations bump THEIR sidecar, their counters live under
  THEIR suffixed meta keys, and a due nudge dispatches a review that resumes
  THEIR session in THEIR `CLAUDE_CONFIG_DIR`, `cwd` = their `agent_home`, writes
  confined to their `skills/` and `wiki/`, reads confined to their tree by the
  same `path_guard_factory` a foreground turn gets, `Bash`/`Agent`/`Task` removed
  by `disallowed_tools`. The curator sweeps one pass per user, owner first, each
  with its own lock, meta keys, quiet-hours check (`MessageActivity` per scope:
  a neighbour chatting at 3am holds nobody else's pass off), snapshots and
  reports under `users/<id>/logs/` — for every user whose grant is on. The
  owner sets it per person in Settings → Users; the config default is **false**,
  because every one of those forks is spend on the OWNER's account for somebody
  else's traffic and nothing meters it per user yet. A withdrawn grant stops the
  next turn's observation, the next sweep's pass and any review still queued;
  the user's nudge counters are left as they were. `skill_admin` and
  `/api/skills` are per user regardless of the grant — a library exists in every
  tree from the first turn, and reading your own is not learning.
* **One review at a time, deploy-wide, still.** The per-user gate stopped
  bounding concurrency the moment there was one lease per user; the `ReviewSlot`
  (`lock:review`, redis) and the inline queue limit `{learning_review: 1}` are
  the bound that replaced it (B-1). `ReviewService.run` claims the gate, then
  the slot; a held slot releases the gate and returns `None`.
* **The seven meta keys are never renamed**: `turns_since_wiki_review`,
  `iters_since_skill_write`, `curator_last_run_at`, `curator_paused`,
  `curator_last_report_path`, `curator_last_dry_run_at`, `learning_review_until`.
  The owner's `ScopedMeta` has the empty suffix, so those rows are byte-identical
  to 4a's (no migration; `scripts/import_sqlite.py` copies suffixed rows for free
  as part of the whole table); a non-owner's are `<key>:<user_id>`. The
  unsuffixed `curator_paused` is the operator switch nothing in this code writes:
  `run_all` reads it FIRST and skips the whole sweep (B-2); `curator_paused:<id>`
  pauses one user's pass. `domain/constants.py` spells the keys once and
  `infrastructure/scoped_meta.py` adds the suffix.
* **Nothing deletes a skill.** Archive moves a directory into `.archive/`;
  restore moves it back and refuses rather than merging.
* **`SkillRepository.resolve` containment check moved verbatim** — the resolved
  path must stay inside the library, because reads take the name straight off the
  URL.
* **The etag precondition on save** is what makes a lost update loud; the
  `before_write` snapshot fires only once the content and etag are known good, so
  a refused save leaves no snapshot behind.
* **The consolidation fork cannot archive anything itself** — its write guard
  denies `.archive/` and `.usage.json`; the code archives from the fork's report,
  and only for unpinned, agent-created, still-on-disk skills.
* **A review in flight blocks the whole curator pass**, and a second nudge while
  a review runs is dropped, not queued. Both read the same `ReviewGate`, so the
  rule survives the review moving to a worker process.
* **The review gate is a lease, not a flag.** `learning_review_until` holds the
  instant a claim expires (`cfg.review_timeout_seconds`), `ReviewService` claims
  it at the start of a pass and clears it in a `finally`. A boolean would stay
  true forever the first time a worker is OOM-killed mid-pass, silently disabling
  the curator; an unreadable row reads as free for the same reason.
* **Task arguments are primitives.** `dispatch(session_key, kinds, owner_id)`
  takes a sorted `list[str]` and an `int`, never the tracker's `set` and never a
  service: the payload has to survive being JSON on a Redis list. A payload with
  no `owner_id` (queued by a 4a agent, drained by a 4b worker) is the owner's;
  the other direction is a `TypeError` in a 4a worker and a lost review, which
  is why a multi-container upgrade drains the queue first (`docs/deploy.md`).
* **First run defers**, dry runs throttle on their own key, and the idle gate
  reads "no message ever recorded" as infinitely idle.
* `run_consolidation` and the wiki lint swallow their exceptions on purpose: an
  unattended pass must survive a broken fork.
* Prompt text in `domain/prompts.py` is a cache key — the review fork resumes the
  turn's own session with a byte-identical `system_append`.
* Snapshot ids are colon-free (`%Y-%m-%dT%H-%M-%SZ`): Windows-safe directory names.

## Known debts

Step 5 deleted the `learning/` legacy package (the re-export shims and the
`curator` / `library` adapters, `CuratorService(learning=)` with them) and the
`PromptCacheInvalidator.reset_all` declaration — the port says `reset_owned` and
nothing else. Since Wave 4b `main.build_app()` calls
`wiring.build_scopes(cfg, workspaces=, meta=, sessions=, activity_for=,
wiki_lint_for=, fork=, snapshots=, users=acl.active_user_ids, learning_of=,
review_slot=)` (`learning_of` since 0009: `(await acl.grants(uid)).learning`,
required — a composition that forgot it fails at boot) once and builds `LearningLoop(scopes, QueueReviewDispatcher(queue))` over it;
the owner scope's objects populate `LearningBundle`'s owner fields. Per scope:
`NudgeTracker` over a `ScopedMeta`, `ReviewService` over the sessions module's
`SessionRepository` with that scope's `workspace`, and `CuratorService` with
memory's `WikiLintService` over that workspace as its `WikiLintPort` and a
`MessageActivity(MessageRepository, owner=)` as its `ActivityPort`. The
`WikiGuardsPort` both forks install is `core.app_context.WikiForkGuards` over
memory's four hook factories.

| Debt | Where | Paid by |
|---|---|---|
| ~~`web/skills.py`, its `routes(deps)`, and `WebDeps.{library,skills_dir,sidecar}` for it.~~ **Deleted in Step 5.** `build_web_app` mounts the `APIRouter` when `deps.learning is not None`, and every collaborator comes off `WebDeps.learning` (the `LearningScopes`) through this module's providers. | done |
| ~~`tools._skill_admin` and its `_OneScope`.~~ **Deleted in Step 5** with `tools.py`; `SkillAdminService` is reached only through `core.app_context.SkillAdminTool(scopes)`. | done |
| ~~Non-owner lanes are not observed at all~~ — **paid in Wave 4b**: observed in their own scope behind a learning switch — deploy-wide `users_learning_enabled` in 4b, a per-user grant since 0009 (see Invariants). | done | done |
| ~~`domain/prompts.py` duplicated memory's `build_wiki_lint_prompt`.~~ **Deleted in Step 5**; memory's copy is the only one. | done |

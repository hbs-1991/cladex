# Wave 4b — the learning loop per user

Status: plan, 2026-09-18, advisor amendments folded in (B-1…B-9, R-1…R-11, S-1…S-5;
see the table at the end). Follows Wave 4a (ce75fdf, 50a8038, ac97c8b, eddca86).
Spec: `docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`,
Step 4 (req. 24, 25, 27, 28). Decision page: `docs/llm-wiki/decisions/multi-user-isolation.md`
("Debts (4b and Step 5)").

After 4a every user has a tree (`users/<id>/agent_home`, wiki, skills, `.claude-cli`,
`tmp`, `logs`) and every turn runs confined to the lane owner's tree. What 4a left as
one tree — the deploy owner's — is the whole learning loop: `after_turn` is skipped
for non-owner lanes, the review fork, the usage sidecar, the skills library,
`skill_admin`, the curator (aging, consolidation, wiki lint), the snapshots and the
curator reports all take `workspace.skills_dir`/`logs_dir` of the OWNER workspace.
`/api/skills` reads are open to any logged-in user and serve the owner's library, and
on a group lane `is_owner_lane` is true, so any group member can run
`skill_admin archive` against the owner's library today.

## Goal

After 4b:

- A non-owner's turns are observed (when the switch is on): their skill
  invocations bump THEIR sidecar, their nudge counters live under THEIR meta keys,
  and a due nudge dispatches a review fork that resumes THEIR session in THEIR
  `CLAUDE_CONFIG_DIR`, with `cwd` = their `agent_home`, writes confined to their
  `skills/` and `wiki/`, reads confined to their tree (the same
  `path_guard_factory` a foreground turn gets).
- The curator runs one pass per user (owner first), each behind its own lock,
  its own meta keys, its own quiet-hours check, its own snapshots and reports
  under `users/<id>/logs/`. The operator's `curator_paused` still pauses ALL of it.
- `skill_admin` exists on every private/web/cron lane and administers the lane
  owner's library; it does not exist on group lanes (nor do `session_search` and
  `cost_report` there — see D6). `/api/skills` serves the REQUESTING user's
  library, and a user may edit their own; nobody sees anyone else's.
- The seeded skills work in a non-owner tree (no `/data/agent_home` literals),
  and `send_document` resolves a relative path against the lane's `agent_home`
  and refuses anything outside it.
- Bundled seeds refresh when the user never touched them; a seed the user deleted
  or archived stays gone (the `_copy_missing` defect, both halves, plus the live
  "archived skill resurrected on every boot" defect the advisor reproduced).
- The Mini App login records the profile; a routine created with no owner
  belongs to the deploy owner rather than to nobody.
- Per-user extensions are DECIDED (not built): they stay deploy-wide.

One switch, `users_learning_enabled` (default **false**): off, non-owner lanes are
exactly as in 4a (unobserved, logged once) and the curator runs the owner's pass
only. `skill_admin` and `/api/skills` are per user regardless of the switch — a
library exists in every tree from the first turn, and reading your own is not
learning. Default false because the review forks and the weekly lint are spend on
the owner's account for somebody else's traffic, and nothing in this wave ships
an owner-facing meter for background work (advisor check 8). Turning it on is a
`config.yaml` edit.

Out of scope: per-user extensions (decided against, D9), Step 5 shim removal in
general (one exception: the `reviewer=`/`_DirectDispatcher` shim in
`LearningLoop` is deleted here, because the constructor it hangs off is being
replaced — its test `tests/test_learning_loop.py::…legacy…` goes with it), the
legacy `runner.AgentRunner` head (stays owner-only; `after_turn` without
`owner_id` and forks without `workspace` default to the owner — verified safe),
a per-user quota on skills/wiki (R-9: accepted and documented in `docs/deploy.md`;
a skill is ≤ 64 KiB and the tree is on the backed-up volume), the memory routes
of the SPA (owner-only, unchanged).

## Locked decisions

### D1. A learning SCOPE per workspace; the bundle becomes a registry

`modules/learning/application/scopes.py`:

```python
@dataclass(frozen=True)
class LearningScope:
    user_id: int
    workspace: Workspace
    is_owner: bool    # workspace.root == workspace.home
    skills_dir: Path
    logs_dir: Path
    meta: Any         # ScopedMeta (D2) — THE store nudges/gate/curator/admin read
    sidecar: Any      # UsageSidecar
    archive: Any      # SkillArchive
    library: Any      # SkillLibraryService
    admin: Any        # SkillAdminService (meta=scope.meta, B-6)
    nudges: Any       # NudgeTracker (meta=scope.meta)
    gate: Any         # MetaReviewGate (meta=scope.meta)
    review: Any       # ReviewService
    curator: Any      # CuratorService (meta=scope.meta, logs_dir=scope.logs_dir)


class LearningScopes:
    def __init__(self, *, workspaces, build, users, users_enabled): ...
    async def owner(self) -> LearningScope
    async def for_user(self, user_id: int) -> LearningScope
    async def user_ids(self) -> list[int]
```

- `build(user_id, workspace) -> LearningScope` lives in
  `modules/learning/wiring.py::build_scopes(cfg, *, workspaces, meta, sessions,
  activity_for, wiki_lint_for, fork, snapshots, users, review_slot) -> LearningScopes`
  (the `extensions/wiring.py` precedent). `main.py` calls one function.
- `for_user(0)` resolves `await workspaces.owner_id()` and **normalises**: the
  scope is cached under the resolved id, so `for_user(0)` and
  `for_user(<owner>)` are one object (R-6). On an unclaimed deploy (owner 0)
  the scope is built over `Workspace.owner(home)` and NOT cached.
- `users` is an async callable returning **every non-disabled user id, no
  limit** — a new `UserRepository.active_ids()` / `AccessControl.active_user_ids()`
  (R-5: `list_users(limit=100)` would silently drop user 101 from the sweep).
  `user_ids()` returns `[owner, *others]` deduplicated (the owner is a `users`
  row too), and with `users_enabled()` false returns the owner alone.
- `LearningBundle` KEEPS its fields (`skills_dir, logs_dir, sidecar, library,
  admin, nudges, loop, curator, review, gate`) as the OWNER scope's objects —
  `tests/test_curator*.py`, `test_review.py`, `test_main.py`, `web/deps.py`
  read them — and gains `scopes: LearningScopes`. Every new consumer goes
  through `scopes`.

### D2. Meta keys per user, owner keys unchanged; the operator's pause is global

`modules/learning/infrastructure/scoped_meta.py::ScopedMeta(meta, suffix)`
satisfies the `MetaStore` port (`get`/`set`) and appends `suffix` to every key.
The owner's scope gets `suffix=""` — the seven legacy keys
(`turns_since_wiki_review`, `iters_since_skill_write`, `curator_last_run_at`,
`curator_paused`, `curator_last_report_path`, `curator_last_dry_run_at`,
`learning_review_until`) are byte-identical, no migration, `skill_admin status`
keeps working, `scripts/import_sqlite.py` copies suffixed keys for free (whole
`meta` table). A non-owner scope gets `suffix=f":{user_id}"`. `build()`
constructs the `ScopedMeta` once and hands that same instance to all four
readers (B-6); the scope exposes it as `meta`.

`curator_paused` (unsuffixed) is the operator switch `domain/constants.py`
promises nothing in this code writes: `run_all` reads it FIRST and skips the
whole sweep with `reason: paused` (B-2). `curator_paused:<id>` pauses one
user's pass (their own scope reads its own key).

The review lease (`learning_review_until[:<id>]`) is per user. What it no
longer bounds is CONCURRENCY — see D3.

### D3. The review is dispatched WITH its owner; one review at a time, still

- `ReviewDispatcher.dispatch(session_key, kinds, owner_id: int = 0)`;
  `QueueReviewDispatcher` enqueues `owner_id` as a primitive.
- `learning_review(*, session_key, kinds, owner_id: int = 0)` in `workers/tasks.py`:
  `scope = await ctx.learning.scopes.for_user(owner_id)`; `scope.review.run(...)`.
  A payload without `owner_id` (queued by a 4a agent, drained by a 4b worker) is
  the owner's. The other direction — a 4b agent's payload drained by a 4a
  worker — is a `TypeError` and a lost review (R-10): a single-container deploy
  restarts everything together; a multi-container operator drains the queue
  before the upgrade (`docs/deploy.md`).
- `LearningLoop(scopes, dispatcher)` with `after_turn(session_key, stats, *,
  owner_id: int = 0)`: pick the scope, bump its sidecar, ask its tracker,
  read/claim ITS gate, dispatch with `owner_id`. Drop-not-queue and
  claim-at-dispatch/release-on-failed-enqueue move unchanged. The
  `reviewer=`/`_DirectDispatcher` shim is deleted (see Out of scope);
  `tests/modules/learning/test_learning_ports.py` and `test_learning_queue.py`
  construct the loop and are updated (R-4).
- **Deploy-wide review concurrency (B-1).** `core/tasks.py` says the unbounded
  `learning_review` was safe because the lease allowed one at a time; per-user
  leases end that. Two bounds replace it, one per mode:
  - inline: `InlineQueue(registry, {RUN_JOB_TASK: 1, LEARNING_REVIEW_TASK: 1})`
    (`main.py`), and the `core/tasks.py` docstring is rewritten to say so;
  - redis: a `ReviewSlot` port (`acquire() -> bool`, `release()`), satisfied by
    `core.redis.Lock(redis, "lock:review", ttl_ms=review_timeout_seconds*1000)`.
    `ReviewService.run` claims the per-user gate, then tries the slot; if the
    slot is held it RELEASES the gate and returns `None` — a dropped nudge is
    the designed behaviour, and the counters fire again next interval. `None`
    slot (inline, tests) means "always free".
- turns' `TurnObserver.after_turn(session_key, stats, *, owner_id: int = 0)`.
  `TurnService` calls `observer.after_turn(session_key, stats, owner_id=ctx.owner_id)`
  when `ctx.is_owner_lane or settings.users_learning_enabled`; otherwise
  `_say_unobserved()` (text: "users_learning_enabled is false"). `TurnSettings`
  gains `users_learning_enabled`. Observer fakes in tests accept the keyword.

### D4. Every fork runs in ITS workspace, confined like a turn

`ForkRunner.run_review(..., workspace: Workspace | None = None)` and
`run_consolidation(..., workspace=None)`; `WikiLintOptions.build` /
`build_wiki_lint_service(cfg, home, wiki_root, workspace=None)` likewise. With a
workspace the fork gets:

- `base_options(workspace.root, ...)` and `env={**NO_ADVISOR_ENV, **workspace_env(workspace)}`
  (the user's `CLAUDE_CONFIG_DIR`, so `resume`/`fork_session` reads THEIR
  transcript store, and their `TMPDIR`);
- a `PreToolUse` `HookMatcher(matcher=PATH_TOOLS, hooks=[path_guard_factory(
  [agent_home, tmp_dir, logs_dir, plugins_dir], cwd=agent_home)])` — the SAME
  hook the foreground turn gets (4a D6), for one rule; `plugins_dir` is in the
  list for that reason only (no fork loads plugins), the catalog root is NOT
  (no fork reads a manifest; S-2/S-3);
- **`write_guard_factory(..., cwd=workspace.agent_home)`** — the write guard
  resolves a relative `file_path` against the process cwd today
  (`guards.py:78`) while the path guard resolves against `cwd=`; stacked on
  one matcher they would disagree and a relative write under `skills/` would be
  allowed by one and denied by the other (B-8). `write_guard_factory` gains
  `cwd: Path | None = None` (None = today's behaviour) and all three call
  sites pass it. Test: a relative `skills/x/SKILL.md` write is allowed by BOTH
  hooks, a relative `logs/x` write is denied;
- `disallowed_tools=["Bash", "Agent", "Task"]` (the forks' `tools=` already
  omits them; this is the belt 4a learned is the one that means "cannot").

`None` (the legacy head and the existing tests) keeps `Workspace.owner(home)`.
`ReviewService`, `ConsolidationService`, `WikiLintService` take `workspace` at
construction. `CuratorService(logs_dir=...)` and `ReportWriter(logs_dir)`
replace the `home` parameter: both only ever spelled `home / "logs"`, and the
scope hands `logs_dir` to `SkillAdminService` and the snapshots already — one
fact, one parameter (S-1). Owner: byte-identical paths.

### D5. The curator sweeps users, one lock each, one sweep at a time

- `workers/curator.py::run_all(scopes, lock_for=None) -> list[dict]`: reads the
  unsuffixed `curator_paused` (B-2) via the owner scope's meta, then for each id
  in `await scopes.user_ids()` (owner first) → `run_once(scope.curator,
  lock_for(user_id) if lock_for else None)`. Sequential; a failing user's pass
  is logged and the sweep continues (`_pass` already swallows).
- `curator_loop(scopes, tick_seconds)` calls `run_all` every tick (inline).
  **`main.py` `run()` (`curator_loop(ctx.learning.curator, …)` today) is
  updated to pass `ctx.learning.scopes`** (R-2).
- `curator_tick` task: `run_all(ctx.learning.scopes, lock_for)` inside an outer
  `Lock(redis, "lock:curator:sweep", ttl_ms=curator_max_turns*60_000 * max(1, N))`
  so two ticks never run two sweeps at once (R-1: a sweep of N users outlives
  the 15-minute tick). `lock_for(uid)` is `Lock(redis, "lock:curator" if
  owner else f"lock:curator:{uid}", ttl_ms=curator_max_turns*60_000)` — the
  owner keeps the legacy key; the per-user lock is for the inline-loop-vs-worker
  case and for a sweep that lost its outer lock to a TTL.
- `MessageActivity(messages, owner=<async callable returning uid>)` per scope
  (B-7: the adapter awaits `owner()`; a bare `lambda: uid` would `TypeError`
  inside the swallowed pass). `activity_for(uid)` in wiring builds it.
- First-run deferral, interval, dry-run keys: per scope via D2, so a new user's
  library gets its full interval of grace before the first aging pass.

### D6. Lane-shaped tools; `/api/skills` per requesting user

- **Tool presence is a property of the LANE, never of the sender** (P1: the
  prefix is byte-stable for the life of a session; B-5). One predicate in
  `core/session_keys`: `is_group_lane(session_key)` (`tg:group:*`,
  `tg:supergroup:*`). On a group lane `skill_admin`, `session_search` and
  `cost_report` do not exist for anybody — the lane's library, recall and
  spend are the deploy owner's, and a per-sender flip (4a's `stranger` rule)
  changed the MCP schema list mid-session. On every other lane (`web:`,
  `tg:private:`, `cron:`) the sender IS the owner structurally, and all three
  exist, scoped to the lane owner. The `stranger` computation in
  `sdk_runner.py` is replaced by this predicate.
- turns' `SkillAdminPort.handle(args, *, ctx=None)`; `make_skill_admin(skills, ctx)`.
  `core.app_context.SkillAdminTool(scopes)`: `handle(args, ctx)` resolves
  `scope = await scopes.for_user(ctx.owner_id or 0)` and delegates to
  `scope.admin.handle(...)`.
- `modules/learning/presentation/dependencies.py`: every provider resolves the
  scope of `CurrentUser` through `deps.learning` (the `LearningScopes`), so
  `get_library`/`get_usage`/`get_skills_dir`/`get_logs_dir` are that user's.
  `require_owner` on the mutations is replaced by "your own library" — the
  scope IS the authorization; no route can name another user's library.
  `WebDeps` gains `learning`; its `library/skills_dir/sidecar` fields stay for
  the legacy readers.
- `invalidate` after a mutation: **`reset_owned(scope.user_id)` for everyone,
  owner included** (B-4). After 4b the owner's skills reach nobody else's
  prefix, and `reset_all()` would evict every other user's warm cache (NFR-1).
  `SessionRepository.reset_owned(owner_id, *, include_unowned=False)`:
  `WHERE owner_id = :o`, plus `OR owner_id IS NULL` when the caller is the
  deploy owner — a lane bound while `deploy_owner()` was 0 wrote NULL. Exposed
  as `SessionLane.reset_owned` → `TurnService.reset_owned`. `reset_all()`
  stays for extensions only.
- **Admission is re-checked per request** (R-8). `core.auth.CurrentUserMiddleware`
  takes a second callable `admitted(uid) -> bool` (bound to
  `acl.check(uid, "web") == "allow"` — the allowlist and `disabled_at`) and
  answers 401 for a cookie whose user is no longer admitted. Today a
  de-allowlisted user with a live cookie could read; after D6 they could write
  prompt text into a tree, so the check moves from "at login" to "per request"
  — one ACL round trip, which `is_owner(uid)` already costs.
- The SPA is unchanged: it never gated the Skills page by role, and the API now
  answers with the caller's own library. This closes the 4a leak.

### D7. `send_document` resolves relative to, and stays inside, `agent_home`

`make_send_document`: a `Path(raw)` that is not absolute is joined onto
`workspace.agent_home` before `resolve()`; the boundary becomes
`workspace.agent_home` rather than `workspace.root` (S-5: for the deploy owner
`root == /data`, so a group member on the owner's lane could name
`/data/users/7/…` by absolute path and have it sent). The resolve-then-
`is_relative_to` order is kept, so `..` and symlinks stay caught. Absolute
paths inside `agent_home` are unchanged; `secrets.env` stays refused.

### D8. Seeds: a manifest, refresh-if-untouched, respect deletion and archive

`core/workspace.py`: the manifest is **`agent_home/.claude/.seeded.json`**, keys
**relative to `agent_home`** (`.claude/settings.json`, `.claude/skills/foo/SKILL.md`,
`.claude/agents/refuter.md`), values the sha256 at seed time. `CLAUDE.md` is
**excluded** — `seed_claude_md` and `_SHIPPED_PERSONA_SHA256` are a finished
migration and `persona.md` is the delivery channel (B-9). An unparseable
manifest reads as empty (the file lives where the agent can write it) and never
raises.

`seed_skills`, `seed_agents`, `seed_settings` gain `manifest: SeedManifest | None
= None`; `None` is today's never-overwrite path, so the four
`tests/test_paths_config.py` callers stay green untouched. `Workspace.ensure()`
opens one manifest per tree and passes it to all three. Per file:

| dest | manifest record | rule |
|---|---|---|
| absent | none | copy, record (a new bundle file, or first boot) |
| absent | present | **leave absent** — the user deleted it on purpose |
| absent, and `skills/.archive/<name>` exists | any | **leave absent** — archived, not deleted (B-3; also fixes today's resurrection on every boot) |
| present | none | legacy: `sha(dest) == sha(src)` → record it (pristine); else leave alone (their edits) |
| present | `== sha(dest)`, `!= sha(src)` | **refresh**: overwrite atomically, record the new hash |
| present | `!= sha(dest)` | user-modified: leave, log once at INFO per tree |

Skills and agents are seeded file by file with the same table, so a bundled
skill the user patched keeps their patch while its untouched siblings refresh.
Only names that exist in the bundle are ever visited. Concurrency (R-11): the
manifest is loaded at the start of `ensure()`, entries are updated in memory,
and **immediately before the `os.replace` it is re-read and merged** (disk
entries win for paths this process did not touch); the temp name is
pid-unique. A lost race then costs at most one extra refresh on the next boot,
never a permanently "user-modified" classification.

### D9. Extensions stay deploy-wide — decided

An install changes the cached prompt prefix and the MCP server set of every turn
the deployment composes, runs under the OWNER's credentials (`extension_secrets`
sealed by one `SecretBox`, OAuth tokens), and lands a clone in `CH_HOME/plugins`
that every tree loads — 4a moved the clones there precisely so a deploy-wide
plugin is loadable from a non-owner tree; a per-user install would force the
read guard to admit a per-user plugin root. Per-user installs would need
per-user secrets (a second key scheme with no owner to hold it, P7), per-user
compose and approval snapshots (a second extensions module, P2), and N
per-user install states multiplying prefix churn (P1). The per-user
customisation channel is the skills library (D6) and the wiki, both already in
the user's tree. `ext_manage` stays owner-gated (fails closed). Recorded on the
decision page as final for this iteration; `installed_by` remains audit-only.

### D10. Mini App admission carries the profile

`domain/telegram_proof.py`: **`verify_webapp_user(init_data, key, now, max_age)
-> tuple[int, Mapping]`** — parsing and HMAC verification stay one function
(R-7: a standalone parser is the function the next caller uses alone, and the
display name becomes attacker-supplied). `verify_webapp` becomes the thin
wrapper returning `[0]`. `webapp_login` calls `_admit(..., profile=Profile.of(user))`.

### D11. A routine with no owner belongs to the deploy owner

`SchedulerService(..., deploy_owner: Callable[[], Awaitable[int]] | None = None)`;
`create_job` with `owner_id is None` resolves `await deploy_owner()` (0 → still
NULL, the unclaimed-deploy case). `main.py` passes `workspaces.owner_id`.

### D12. Config

- `users_learning_enabled: bool = False` (`config.py`, `config.yaml.example`,
  `TurnSettings`). The example states the cost at the key: one review fork per
  `wiki_nudge_interval` (4) turns without a wiki write and per
  `skill_nudge_interval` (15) tool uses, per user; one weekly curator pass per
  user whose wiki lint is an LLM fork of up to `curator_max_turns`; all of it on
  the owner's account. The second dial is the review bound (D3): one review at
  a time deploy-wide.
- `core/tasks.py` docstring: the `learning_review` bound is the queue limit
  now, not the lease.

### D13. Seeded skills without `/data/agent_home`

`skills/gpt-image-2/SKILL.md`, `skills/linkedin-post/SKILL.md`,
`skills/linkedin-post/references/visuals.md` (the complete list — 12
occurrences): every `/data/agent_home/<x>` becomes the relative `<x>` (cwd is
`agent_home` in every fork and turn; D7 makes `send_document` agree).
`skills/skill-authoring/SKILL.md` names `<CH_HOME>/agent_home/.claude/skills/`:
change to "`.claude/skills/<name>/SKILL.md` under the agent's home (cwd)".
`gpt-image-2` runs `gen.sh` through `Bash`, so it stays unusable on a non-owner
lane unless `users_shell_enabled` — say so in its body rather than implying the
path fix makes it work. The assertion "no `/data/agent_home` literal under
`skills/`" goes into the existing `tests/test_bundled_skills.py`, scoped to the
whole tree, not to its `CHECKED` tuple (`catalog-manifest` stays excluded there
for the reason its docstring gives).

## Phases

### Phase L — learning scopes (one agent)

Files: `modules/learning/application/{scopes.py (new), ports.py (ReviewSlot,
ReviewDispatcher owner_id, ForkRunner workspace, ActivityPort unchanged),
services/loop.py, services/review.py, services/consolidation.py,
services/curator.py (logs_dir=)}`, `modules/learning/infrastructure/{scoped_meta.py
(new), review_slot.py (new, over core.redis.Lock), dispatch.py, fork_runner.py,
report.py (logs_dir)}`, `modules/learning/wiring.py (new)`,
`modules/memory/infrastructure/lint_fork.py`, `modules/memory/application/services/lint.py`,
`integrations/claude_sdk/guards.py` (`write_guard_factory(cwd=)`),
`workers/{tasks.py, curator.py}`, `core/app_context.py` (`LearningBundle.scopes`,
`SkillAdminTool` over scopes), `core/config.py`, `core/tasks.py` (docstring),
`modules/identity/{infrastructure/repositories.py, application/services.py}`
(`active_ids`/`active_user_ids`), `main.py` (learning block, inline queue
limits, `SchedulerService(deploy_owner=)`, the `curator_loop` call in `run()`),
`config.yaml.example`.
Tests: `tests/modules/learning/test_learning_scopes.py` (new: two users → two
sidecars, two meta suffixes, owner keys unchanged, `for_user(0)` is the owner's
object, `user_ids` dedupes and honours the switch, a non-owner's `skill_admin
status` reads `curator_last_run_at:<id>`), `test_review.py` (fork gets the
workspace; the path guard denies the owner's tree; a relative `skills/` write
passes both hooks and a relative `logs/` write fails; `disallowed_tools`; the
slot: held → gate released, `None` returned), `test_curator*.py` (`run_all`
sweeps owner first, per-user locks, outer sweep lock, unsuffixed pause stops
everything, per-user activity), `test_learning_loop.py` (`after_turn(owner_id=)`
routes to the right scope; legacy payload runs the owner's review; the legacy
`reviewer=` test is deleted), `test_learning_ports.py`, `test_learning_queue.py`,
`test_workers_tasks.py`, `test_main.py`, `test_core_tasks.py` (inline limit).

### Phase T — turns, sessions, auth and web (one agent, in parallel with L)

Files: `core/session_keys.py` (`is_group_lane`), `core/auth.py` (`admitted`),
`modules/turns/application/{ports.py (TurnObserver, SkillAdminPort,
TurnSettings.users_learning_enabled, SessionLane.reset_owned), turn_service.py
(gate, reset_owned)}`, `modules/turns/infrastructure/{sdk_runner.py (lane
predicate replaces `stranger`), hermes_server.py, tool_handlers/delegating.py,
tool_handlers/documents.py (D7)}`, `modules/sessions/{application/services.py,
infrastructure/repositories.py}` (`reset_owned`), `modules/learning/presentation/
{dependencies.py, router.py}`, `web/deps.py` (`WebDeps.learning`), `web/app.py`
(middleware `admitted=`). Tests: `tests/modules/turns/test_turns_two_users.py`
(non-owner lane observed with `owner_id` when the switch is on, unobserved when
off; group lane offers none of the three tools to anybody; private lane offers
all three), `tests/test_send_document.py` (relative path → agent_home; absolute
path under `root` but outside `agent_home` refused), `tests/modules/learning/
test_learning_router.py` + `tests/test_web_isolation.py` (B lists only B's
skills, B edits B's skill, B cannot reach A's by name → 404; a de-allowlisted
cookie → 401), `tests/test_sessions.py` (`reset_owned` incl. the NULL belt),
`tests/test_core_session_keys.py`. **Does not edit `main.py` or
`core/app_context.py`** — `SkillAdminTool.handle(args, ctx)` is L's; T calls it.

Contract between L and T (both build against it): `LearningScopes.for_user(uid)`,
`LearningScope` fields as in D1, `WebDeps.learning: LearningScopes`,
`SkillAdminPort.handle(args, *, ctx=None)`, `TurnObserver.after_turn(session_key,
stats, *, owner_id=0)`, `TurnService.reset_owned(owner_id)`.

### Phase S — seeds, skills, identity, scheduler (one agent, in parallel)

Files: `core/workspace.py` (D8), `skills/**` (D13), `modules/identity/domain/
telegram_proof.py` + `presentation/router.py` (D10), `modules/scheduler/application/
service.py` (D11; `main.py`'s one line is L's). Tests: `tests/test_core_workspace.py`
(the six manifest rows, per-file skill refresh, archived skill not resurrected,
deletion respected, legacy pristine record, concurrent ensure keeps both
processes' entries, unparseable manifest reads as empty, `CLAUDE.md` never in
the manifest), `tests/test_paths_config.py` (untouched and green),
`tests/test_bundled_skills.py` (extend: no `/data/agent_home` literal), identity
webapp profile test, scheduler fallback test.

### Phase V — verification (me)

Full suite, `ruff`, `lint-imports`, then a live two-user smoke through a real
worker (`scratchpad/smoke4`, switch on): user 2's turns hit the nudge interval →
a `learning_review` task with `owner_id=2` → the fork's `cwd` and
`CLAUDE_CONFIG_DIR` are `users/2/...`, a `Read` of the owner's `CLAUDE.md` is
denied, user 2's wiki gets a page; `skill_admin list` on user 2's lane shows
user 2's library; `/api/skills` as user 2 lists user 2's; the curator sweep
writes `users/2/logs/curator/`; the owner's `curator_paused=1` stops the
sweep. Then the implementation-advisor code pass.

### Phase D — docs and wiki (me)

`docs/llm-wiki/decisions/multi-user-isolation.md` (debts closed, D9 final, the
lane-shaped tools rule), `components/learning.md`, `components/core-runtime.md`,
`components/turns.md`, `components/web.md`, `components/identity.md`,
`system.md`, `operations.md` (curator per user, locks, drain-before-upgrade),
`contracts/state-db.md` (meta suffixes), module READMEs (learning, turns,
memory, identity, scheduler, sessions, workers), `README.md`, `docs/deploy.md`
(the switch and its cost, `users/<id>/logs/curator`, no quota), `config.yaml.example`.
Re-stamp, validate, receipt.

## What a non-owner user gets after 4b

Their own: skills library (seeded, refreshed while untouched), `skill_admin` on
their private/web lanes, the Skills page over their library, snapshots and
rollback of their own tree. `/learn` already lands in their tree (the prompt
says `.claude/skills/`, relative, cwd = their `agent_home`); a skill made that
way has no sidecar entry, reads as `source: user` and is never aged — P4 holds
(S-4). With `users_learning_enabled`: usage sidecar, nudge counters, review
forks over their own conversations (their wiki and skills grow), a weekly
curator pass with aging/consolidation/wiki-lint over their tree, curator
reports under their `logs/`.

Still not theirs: `Bash`/`Agent`/`Task` (unless `users_shell_enabled`),
`ext_manage` and every extensions page, the memory routes, routines and
connectors pages, other people's anything. On a group lane nobody — the owner
included — has `skill_admin`, `session_search` or `cost_report`.

## Risks named

- **Cost.** N users × (review forks + weekly lint). Default off; the review
  bound is one at a time deploy-wide; no per-user meter ships (R-9 accepted).
- **A review fork resumes with the user's `system_append`**, which carries the
  user's wiki index — byte-identical to the foreground turn's, warm cache.
- **The seed manifest is new state** in every tree; an existing volume behaves
  as "legacy" until the first boot records the pristine files, so the first
  refresh happens on the SECOND boot after a bundle change. Documented.
- **Rolling deploys** must drain the queue (R-10).

## Advisor amendments (plan pass)

| id | change |
|---|---|
| B-1 | inline `LEARNING_REVIEW_TASK: 1`; redis `lock:review` slot inside `ReviewService.run`; `core/tasks.py` docstring |
| B-2 | unsuffixed `curator_paused` pauses the whole sweep |
| B-3 | `seed_skills` skips names present under `.archive/` |
| B-4 | `reset_owned` for everyone; NULL-owner belt for the deploy owner; `reset_all` only for extensions |
| B-5 | tool presence by LANE (`is_group_lane`), never by sender; `session_search`/`cost_report` folded in |
| B-6 | `LearningScope.meta`; one `ScopedMeta` per scope for all four readers |
| B-7 | `MessageActivity.owner` is an async callable |
| B-8 | `write_guard_factory(cwd=)` |
| B-9 | manifest anchored at `agent_home`, `CLAUDE.md` excluded, `manifest=None` legacy path, unparseable → empty |
| R-1 | outer `lock:curator:sweep` |
| R-2 | `main.run()`'s `curator_loop` call |
| R-3 | the `reviewer=` shim and its test are deleted; Out-of-scope says so |
| R-4 | the two extra loop-constructing tests |
| R-5 | `active_ids()` without a limit |
| R-6 | scope cache normalised on the resolved id; `user_ids` dedupes |
| R-7 | `verify_webapp_user` — no standalone parser |
| R-8 | per-request `admitted(uid)` in the middleware |
| R-9 | no quota; documented |
| R-10 | drain before a multi-container upgrade |
| R-11 | re-read-and-merge before the manifest replace; pid-unique temp |
| S-1 | `logs_dir=` replaces `home=` in `CuratorService`/`ReportWriter` |
| S-2/3 | `plugins_dir` kept for one rule; no catalog root in fork roots |
| S-4 | `/learn` named |
| S-5 | `send_document` boundary is `agent_home` |
| check 8 | default `false` |

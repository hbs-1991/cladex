# Wave 4a — `users` table, `owner_id` everywhere, per-user workspaces, Telegram webhook mode

Status: plan, 2026-09-17, revised the same day after the implementation-advisor
pass (seven blockers and the risks are folded in; the "Advisor amendments" section
at the end lists what changed and why). Follows Wave 3b (1400298..0c6ef7e). Spec:
`docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`
req. 12, 21, 24, 25, 27, 28, 29, 34 and the acceptance lines "Telegram webhook mode
works behind the existing Caddy setup" and "Two different users cannot see each
other's threads, files, drafts or workspace files, via API or via the agent's
tools".

Step 4 is cut in two. **4a (this plan)**: the data model, the ACL over it, the
per-user workspace a turn runs in, the tool confinement, and the webhook transport.
**4b (next)**: the learning loop per user (skills library, usage sidecar, review
forks, curator passes and backups are still one tree — the deploy owner's — after
4a) and the per-user extension decisions that follow from it.

## Goal

After 4a:

- **A `users` row exists for every person the agent talks to.** The row is created
  on first contact — the first private Telegram chat claims the deploy as before,
  an allowlisted user gets a row on their first message or first web login — and
  `AccessControl` reads the table, not one `meta` key.
- **Every user-owned row names its owner.** `owner_id` on `sessions`, `messages`,
  `files`, `jobs`, `turn_costs`, `catalog_drafts`; `threads.owner_user_id` gains the
  FK (`turns.user_id` is the sender, an audit field, and gets none). Group Telegram lanes belong to the deploy owner.
  Repositories scope by owner; no handler filters ad hoc.
- **A turn runs in its lane owner's workspace.** `cwd`, the wiki (its index in the
  system append, its hooks), `inbox/`/`outbox/`, the blob mirror, `CLAUDE.md`,
  `.claude/{settings.json,skills,agents}`, `tmp/` and the CLI's own transcript store
  are per user. Read/Write/Edit/Glob/Grep/NotebookEdit are confined to that
  workspace (plus the read-only catalog and the shared plugins tree) by a
  `PreToolUse` hook. **Non-owner lanes get no `Bash`, `Agent` or `Task`** unless
  `users_shell_enabled: true`: inside one container every turn runs as the same uid,
  so a regex over a shell command would imply a boundary it cannot provide. What 4a
  builds is isolation against accident and a well-behaved model; the `:ro` catalog
  mount stays the only hard boundary, and a container per user is the follow-up
  (spec "Out of scope"). The security decision page says exactly that.
- **The owner's workspace is where it already is.** `users.workspace_dir` is `""`
  for the owner (root = `CH_HOME`, so `/data/agent_home`, `/data/.claude-cli` and
  `/data/logs` do not move) and `users/<id>` for everyone else. A live volume needs
  no migration of files; SDK sessions keep resuming.
- **`telegram_mode: webhook` works.** `main.run` registers the webhook URL with
  Telegram, blocks on the web server instead of polling, and N agent instances
  behind Caddy or the tunnel all accept updates. Polling stays the default and the
  local-dev mode. The singleton page is updated: none in webhook mode.

Out of scope: the learning loop per user (4b), per-user extensions (installs stay
deploy-wide and owner-managed, see Locked Decisions), removing shims (Step 5), the
SPA (no wire change is needed; `/api/me` gains two additive keys).

## Wire contract (frozen, with additive changes only)

- `GET /api/me` → `{"user_id", "role"}` unchanged, **plus** `"display_name"` and
  `"username"` (both `str`, possibly `""`). Every other route, status and error
  `code` is byte-identical. Session cookie format unchanged.
- `GET /healthz` → `200 {"ok": true}` (new, public, outside `/api/`). Compose
  healthchecks and Caddy may probe it; nothing depends on it.
- `POST /telegram/webhook/{secret}`: the path secret **and** the
  `X-Telegram-Bot-Api-Secret-Token` header must both equal the configured secret
  (compare_digest); a missing or wrong header is 401. Telegram always sends the header
  once `set_webhook(secret_token=…)` is used, and a proxy access log holds the path,
  so the header is the credential that never reaches a log. The route answers 200
  immediately and dispatches the update in the background; a repeated `update_id`
  (Telegram retries on timeout) is dropped.
- `config.yaml.example` gains `users_shell_enabled: false` (whether non-owner lanes
  get `Bash`/`Agent`/`Task`).
- Session key grammar (`tg:…`, `web:<user_id>:…`, `cron:<job_id>`) unchanged.
  `user_id` **is** the Telegram user id everywhere it was before.
- `config.yaml.example`: `telegram_mode` documented as live; `.env.example` gains
  `CH_TELEGRAM_WEBHOOK_SECRET`. No key renamed.
- Telegram behaviour unchanged from the user's side in both modes.
- Import script `scripts/import_sqlite.py`: unchanged; migration `0005` backfills
  the imported rows to the owner.

## Locked Decisions

### D1. `users.id` is the Telegram user id

```
users(
  id            BIGINT PRIMARY KEY,        -- the Telegram user id
  role          TEXT NOT NULL,             -- 'owner' | 'user'
  workspace_dir TEXT NOT NULL,             -- relative to CH_HOME: '' (owner) | 'users/<id>'
  display_name  TEXT NOT NULL DEFAULT '',
  username      TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  disabled_at   TEXT NULL
)
CREATE UNIQUE INDEX users_one_owner ON users (role) WHERE role = 'owner';
```

Why not a serial id plus `telegram_id`: `CurrentUser.user_id`, `threads.owner_user_id`,
`turns.user_id`, the `web:<user_id>:` lane grammar, the ask-broker owner check, the
OAuth pending-state binding and the session cookie all carry the Telegram id today,
and spec req. 29 ("a Telegram user id maps to exactly one user") is satisfied by the
primary key. Identity *is* Telegram in this product (Login Widget, Mini App). A
non-Telegram login later gets a synthetic id range; that is a 4b+ decision and is
noted in the decision page. `users_one_owner` is the claim race's arbiter: two first
private chats at once produce one owner and one `IntegrityError` → `"deny"`.

### D2. Migration `0005_users.py`

1. `CREATE TABLE users` as above.
2. If `meta.owner_user_id` exists: `INSERT INTO users (id, role, workspace_dir, …)
   VALUES (:owner, 'owner', '', …)`. The `meta` key is **kept and mirrored** (the
   `acl.py` shim, `tests/test_acl.py` and the curator's meta reads still see it);
   `AccessControl.check` writes both on a claim. Step 5 drops the mirror.
3. `owner_id BIGINT NULL REFERENCES users(id) ON DELETE SET NULL` added to
   `sessions`, `messages`, `files`, `jobs`, `turn_costs`, `catalog_drafts`; the same
   FK added to `threads.owner_user_id` (nullable, existing). **`turns.user_id` gets
   no FK**: it is the sender, an audit field, NOT NULL, and every test that inserts
   a turn with an arbitrary id would start failing for nothing. The cron `0`
   sentinel is still replaced by the job owner (D6). Indexes: `ix_sessions_owner`,
   `ix_files_owner`, `ix_jobs_owner`, `ix_messages_owner_ts (owner_id, ts)`.
4. **Backfill derives the owner from the key, never blanket-assigns it.** The rule
   is `lane_owner` (D3) spelled in SQL, applied to `sessions`, `messages`, `files`,
   `turn_costs` (all carry `session_key`) and to `threads.owner_user_id WHERE NULL`:
   ```
   web:<uid>:…            -> split_part(session_key, ':', 2)::bigint
   tg:private:<chat>[…]   -> split_part(session_key, ':', 3)::bigint
   tg:group|supergroup:…, cron:… -> the deploy owner (meta.owner_user_id)
   ```
   Before the backfill the migration **inserts a `users` row (`role='user'`,
   `workspace_dir='users/<id>'`) for every distinct non-owner id the keys yield**,
   so the FK holds; `jobs` and `catalog_drafts` → the deploy owner (owner-only
   routes today). A blanket "NULL → owner" would hand an allowlisted user's private
   lanes, messages and files to the owner and resolve their lanes to the owner's
   workspace forever, silently. `sessions.sdk_session_id` is set to NULL for every
   session whose derived owner is not the deploy owner: the lane's cwd and config
   dir change, so the transcript is unresumable anyway, and this saves one dead CLI
   launch per lane. When no owner exists (a fresh database) there is nothing to
   backfill; a database with rows and no owner is not a state the app produces.
5. `messages.owner_id` is denormalised on purpose: search is one GIN query and must
   stay one query (`WHERE owner_id = :uid AND content_tsv @@ …`).
6. `users_one_owner` is declared in the ORM as
   `Index("users_one_owner", "role", unique=True, postgresql_where=text("role = 'owner'"))`
   so `tests/test_migrations.py::compare_metadata` renders it identically; run that
   test first.

Nullable rather than NOT NULL so an old row survives; **code always writes a
non-NULL owner** and a NULL row is visible to nobody. `tests/test_migrations.py`
keeps comparing `upgrade head` against the ORM metadata, and gains the derived-owner
case: seed through 0004 a `tg:private:<B>` session with messages and a file, a
`web:<A>:…` thread, a group thread, and `meta.owner_user_id = A`; upgrade; assert
B's rows carry `owner_id = B`, a `users` row for B exists, the group thread's owner
is A, and B's `sdk_session_id` is NULL.

### D3. Lane ownership is one rule, in `core/session_keys.py`

`lane_owner(session_key, channel, user_id, deploy_owner) -> int`:
`web:` → the creating user; `tg:private` → the chat id; `tg:group|supergroup` → the
deploy owner; `cron:` → the job's owner (passed in). This replaces `thread_owner`,
which returned `None` for groups; `thread_owner` stays as a shim over it. Every
place that creates a lane (turns router, Telegram router, scheduler's
`ThreadEnsure`) passes the owner through explicitly — `sessions` never imports
identity. `modules/sessions/domain/policies.visible(thread, user_id, is_owner)`
becomes: cron lanes hidden; `thread["owner_user_id"] == user_id`; **or** `is_owner`
for a lane that is neither `web:` nor `cron:` — the belt for a group thread the
backfill somehow missed, so the owner never silently loses a thread from the sidebar.
The owner sees group lanes either way because the lane's owner *is* the owner.
Since `sessions.owner_id`, `threads.owner_user_id` and the resolver all apply the
one rule, they cannot disagree about a lane.

### D4. Identity module

- `domain/models.py`: `@dataclass(frozen=True) User(id, role, workspace_dir,
  display_name, username, created_at, last_seen_at, disabled_at)`; `Profile(display_name,
  username)` value object built from a Telegram `from_user` or a Login Widget payload.
- `application/ports.py`: `UserStore` — `async get(user_id) -> User | None`,
  `async owner() -> User | None`, `async create(user_id, role, workspace_dir, profile,
  now_iso) -> User` (raises `OwnerExists` on the partial-index conflict), `async touch(user_id,
  profile, now_iso) -> None`, `async list(limit) -> list[User]`. `OwnerStore` stays
  (the mirror).
- `application/services.py`: `AccessControl(users, owner_mirror, allowed_users)`.
  `owner() -> User | None` reads the table and, on a miss, **falls back to the
  mirror and materialises the owner row from it** (`create` with
  `ON CONFLICT DO NOTHING`, then re-read): "claimable" means *neither* source is
  set. A restored pre-0005 dump or a truncated table must not let the next private
  message take the deploy — `tests/test_acl.py::test_allowed_users_config` sets only
  the meta key and expects `"allow"` for an allowlisted user, and keeps passing.
  `check(user_id, chat_type, profile=None) -> "claimed" | "allow" | "deny"`:
  no owner anywhere + private → `users` INSERT **first** (the partial index is the
  arbiter; the loser's `IntegrityError` is caught, the transaction rolled back, and
  the answer is `"deny"`), then the mirror write → `"claimed"`; row exists and not
  disabled → `touch` → `"allow"`; no row and `user_id in allowed_users` → create
  `role='user'`, `workspace_dir=f"users/{user_id}"` → `"allow"`; else `"deny"`.
  `is_owner(user_id)` → the row's role (one query, cached per request by the
  middleware as today). New: `user(user_id) -> User | None` and
  `workspace_dir_of(user_id) -> str` (raises `UnknownUser`) — what the workspace
  resolver asks.
- `infrastructure/repositories.py`: `UserRepository(engine)` (SQLAlchemy Core,
  `INSERT … ON CONFLICT (id) DO UPDATE SET last_seen_at, display_name, username` for
  `touch`); `MetaOwnerRepository` unchanged.
- `presentation/router.py`: `/api/me` adds the two keys (`schemas.user_wire` gains
  them); `_admit` passes the widget `first_name`/`last_name`/`username` as the profile.
- `acl.py` shim: `AccessControl(db, cfg)` builds `UserRepository(db.engine)` +
  `_FacadeOwnerStore`; `tests/test_acl.py` keeps passing unchanged.
- `tests/web_helpers.make_client(owner=, allowed=)` inserts `users` rows for the owner
  and every allowed id (role `user`, workspace `users/<id>`) — that is what makes the
  FKs hold in every router test without touching the tests themselves. A new
  `tests/conftest.py` helper `ensure_users(engine, *ids, owner=None)` serves the
  repository tests that insert rows directly.

### D5. Per-user workspace (`core/workspace.py`)

```python
@dataclass(frozen=True)
class Workspace:
    home: Path                        # CH_HOME
    root: Path                        # the user's root; the owner's is `home` itself
```

- `Workspace(home)` alone is gone: `Workspace.owner(home)` = `Workspace(home, home)`
  and `Workspace.for_user(home, workspace_dir)` = `Workspace(home, home /
  workspace_dir)` (`""` → the owner's). One representation; `ws.root` is never
  `None`. Every path property that is *per user* derives from `root`: `agent_home`,
  `claude_dir`, `skills_dir`, `agents_dir`, `wiki_root`, `inbox_dir`, `outbox_dir`,
  `tmp_dir` (`root / "tmp"`, new), `cli_config_dir` (`root / ".claude-cli"`, new),
  `logs_dir`. Deploy-wide paths derive from `home`: `user_catalog`, `plugins_dir`
  (**new**: `home / "plugins"`, see D8), `secrets_path`, `drafts_dir`,
  `pending_approvals_dir`. For the owner `root == home` and every path is
  byte-identical to today (`/data/agent_home`, `/data/.claude-cli`, `/data/logs`).
- `Workspace.ensure()` creates and seeds `root`'s tree (what `get_home()` does today
  for the one tree): `agent_home/.claude/{skills,agents}`, `settings.json`,
  `persona.md`, `CLAUDE.md`, the wiki seed, `logs`, `tmp`, `.claude-cli`.
  Idempotent; called lazily by the resolver on first use of a user, and by
  `main.build_app` for the owner as today. Two workers ensuring the same new user
  at once: `_copy_missing` already swallows the loser's `FileExistsError` into a
  warning — the message is changed to say "seeded concurrently" when the
  destination exists afterwards. A user's tree is seeded from the bundle shipped
  when they first spoke; `write_persona` self-heals on every `ensure`, skills and
  `settings.json` do not (the known `_copy_missing` defect, now per user; 4b).
- `core/workspace.py::WorkspaceResolver(home, *, workspace_dir_of, deploy_owner)` —
  the port every module receives; both lookups are async callables bound by the
  composition root, so core stays ignorant of identity:
  - `async for_user(user_id) -> Workspace`: `workspace_dir_of(user_id)` → build →
    `ensure()` once per process (cached by user id).
  - `async for_lane(session_key, channel, user_id) -> Workspace`: **no table read**
    — `lane_owner(session_key, channel, user_id, await deploy_owner())` is pure given
    the deploy owner (cached), so a brand-new lane whose `sessions` row does not
    exist yet resolves the same way as an old one, and nothing can disagree with
    `sessions.owner_id`, which is written from the same rule.
- **The owner is the deploy owner.** The curator, the review forks, the skill
  library, `SkillAdminTool`, `UsageSidecar`, `snapshots` and `ReportWriter` keep
  taking `workspace.skills_dir` / `logs_dir` of the owner workspace, unchanged (4b).

### D6. Which workspace a turn runs in, and what it may touch

Every process-wide object that holds `home` today is named here, because each one
left behind is a silent cross-user path:

- **Resolution.** `TurnRunService.run` resolves `ws = await resolver.for_lane(
  row["session_key"], row["channel"], row["user_id"])` and passes it to
  `runner.run_turn(..., workspace=ws)`; `TurnService` threads it into
  `TurnContext.workspace` + `TurnContext.is_owner_lane`. The legacy head with no
  `workspace=` resolves the owner's (the five runner test modules keep working).
- **`cwd`, env.** `SdkTurnExecutor.execute` calls `base_options(ws.root, …)` —
  `base_options(home, …)` keeps its signature, the argument is now the user's root —
  and passes `env={"CLAUDE_CONFIG_DIR": str(ws.cli_config_dir), "TMPDIR":
  str(ws.tmp_dir)}` so the CLI's transcript store (and therefore `resume`) and the
  temp directory are per user. Every background call site that already passes
  `env=NO_ADVISOR_ENV` (cron executor, fork runner, lint fork, MCP prober) merges:
  `env={**NO_ADVISOR_ENV, …}` — a replace would silently re-enable the advisor.
- **The wiki, in the prompt and in the hooks.** `TurnService` no longer holds one
  `WikiStore`: it takes `wiki_for: Callable[[Workspace], WikiPort]` and builds the
  lane's store per turn (a thin path object; `index()` reads one file), so
  `build_system_append(wiki.index(), …)` and `_index_seen` read *that* user's index.
  `WikiHooksProvider.build(session_key, index_seen, workspace)` builds `wiki_hooks`
  over `workspace.wiki_root`. Without both, user B's byte-stable cached prefix
  carries the owner's index and B's Stop gate writes the owner's wiki through a
  path the read guard never sees. `sessions.index_mtime` now means "mtime of that
  lane's owner's index" (contract page).
- **The blob mirror, both ways.** `LocalBlobStore` / `S3BlobStore` freeze one root
  in `__init__` today; they take `roots: Callable[[int], Awaitable[Path]]`
  (`resolver.mirror_root(owner_id)` = `for_user(owner_id).agent_home`) and every
  `put`/`get`/`resolve` receives the **row's `owner_id`** — never the requester's,
  never a process global. `FileService.put_upload(..., owner_id)`, `publish(...,
  owner_id)`, `attachments_for(...)` and the download route pass the row's owner
  through; `resolve_within` keeps its containment check against that root. The S3
  key grammar is unchanged (`inbox/<lane>/…`); only the mirror moves. The test writes
  through the turn as B and reads through the download route as B and asserts the
  bytes — never the path through the function that produced it.
- **Telegram intake.** `modules/telegram/application/services/intake.py` downloads
  into `WorkspacePort.lane_dir(session_key)`; the binding (`LaneWorkspace` in
  `core/app_context.py`, `gateway/telegram._Workspace`) becomes
  `lane_dir(session_key, channel, user_id)` over the resolver, so a non-owner's photo
  lands in their own `inbox/`.
- **`send_document`** containment root = `ws.root`, through `make_handlers(workspace=)`.
- **Cron turns run as the job's owner.** `jobs.owner_id` is set at creation (the
  turn's owner through `CronToolPort`, the request user through the routines API);
  `SdkJobExecutor.run(job)` resolves `for_user(job["owner_id"])` and the cron
  `turn.start` payload carries `"user_id": job["owner_id"]` instead of `0`.
- **Toolset per lane.** `allowed_tools` is already per call site and CLI-enforced,
  so it is the real control. Non-owner lanes (`not is_owner_lane`) get
  `_BUILTIN_TOOLS` minus `Bash`, `Agent`, `Task` unless `cfg.users_shell_enabled`,
  and **no `skill_admin`** (`make_handlers(skills=None)` for those lanes — the skills
  library is the owner's tree in 4a and `SkillAdminService` has no owner gate).
  `ext_manage` keeps its own fail-closed owner gate. The toolset is decided once per
  turn and nothing in 4a changes a user's role, so a lane's cached prefix stays
  byte-stable (P1); the cost is that the prefix is now per user rather than shared,
  recorded next to the NFR-1 target. **There is no bash regex guard for other
  workspaces**: it would deny `cd ..` and miss `ls $X/users`, and it cannot be a
  boundary inside one uid.
- **Read confinement** — `integrations/claude_sdk/guards.py::path_guard_factory(
  allowed_roots)` returns a `PreToolUse` hook for
  `Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep` that denies when any of
  `file_path`, `notebook_path`, `path` (Glob/Grep, optional, defaults to cwd) resolves
  (`Path.resolve()`, non-strict, so a planted symlink is followed) outside every
  allowed root, and denies an **absolute `pattern`/`glob`** for Glob/Grep the same
  way (`Glob(pattern="/data/users/*/**")` with no `path` must not pass). Installed
  for every lane, owner included, with `allowed=[ws.agent_home, ws.tmp_dir,
  catalog_root, ws.plugins_dir]`. Same-uid TOCTOU remains and the decision page says
  so, in the words `plugin_root` already uses: defence in depth, not a boundary. The
  legacy `write_guard_factory` stays for the forks.
- **Subagent hooks.** Whether the CLI fires `PreToolUse` for an `Agent`/`Task`
  subagent's tool calls is verified empirically in Phase A (a logging hook plus one
  `Agent` turn, `scratchpad/`). If it does not fire, `Agent`/`Task` leave the
  non-owner toolset regardless of `users_shell_enabled`, and the decision page says
  why.
- **Learning after a turn**: `TurnService` calls `observer.after_turn` only when
  `is_owner_lane`; other lanes are not observed in 4a (4b debt, logged once per
  process at INFO). Nothing in a non-owner's reply may claim a lesson was learned
  or a skill was saved (P5): the tools that would say so are simply not offered.

### D7. Owner scoping in repositories

| table | write | read |
|---|---|---|
| `sessions` | `bind`/`touch` carry `owner_id` (from `lane_owner`) | `SessionRepository.owner_of(session_key)` for the resolver |
| `threads` | `touch(..., owner_user_id=lane_owner(...))` | `list_visible`/`get_visible` unchanged API, policy per D3 |
| `messages` | `MessageRepository.add(..., owner_id)` | `search(query, owner_id, …)` and `recent(session_key, …)`; the `recall` tool passes the turn's owner |
| `files` | `put_upload(..., owner_id)`, `publish(..., owner_id)` | `get(file_id)` unchanged; `attachments_for` also checks `owner_id`; `LaneGuard` unchanged (the thread policy already decides) |
| `jobs` | `create(..., owner_id)` | `list(owner_id)`, `get(id, owner_id)`; the routines API is still `require_owner`, so `owner_id = user.user_id` there; the agent tool passes the turn's owner |
| `turn_costs` | `record(..., owner_id)` | `cost_by_day(owner_id)`; the report is owner-only today and stays so, scoped to the owner's own turns |
| `catalog_drafts` | `propose(..., owner_id)` | listing unchanged (owner-only routes); the row records who proposed |

`turn_events` are read by lane and the lane is guarded by the thread policy, so they
carry no column (documented in the contract page). `extensions`, `oauth_tokens`,
`extension_secrets`, `approval_snapshots` are deploy-wide (D8).

### D8. What stays deploy-wide, and why

- **Extensions, their secrets and OAuth tokens.** An install changes the cached
  prompt prefix and the MCP servers every turn composes; it is the owner's decision
  and the owner's credentials. Per-user extensions are a 4b decision, taken together
  with per-user learning. `installed_by` stays audit-only.
- **Plugin clones** move from `agent_home/plugins/<name>` to **`CH_HOME/plugins/<name>`**
  (`modules/extensions/domain/paths.PLUGINS_SUBDIR` resolves against `home`, not
  `home/agent_home`). `Workspace.ensure()` for the owner performs the one-time
  `os.replace(home/"agent_home"/"plugins", home/"plugins")` when the old directory
  exists and the new one does not, logged at WARNING with both paths and the entry
  count. Every process (agent, worker, scheduler) runs `ensure()` for the owner at
  boot, so the `OSError` of the losing concurrent `os.replace` is caught and the
  destination re-checked. This is what lets a non-owner turn load a plugin without
  its read guard admitting the owner's `agent_home`. Phase E's smoke asserts a
  plugin still composes after the move; nothing in the seeded skills or persona
  names the host-side `agent_home/plugins` path (checked in Phase B).
- **The skills library, usage sidecar, archive, backups, curator reports**: the
  owner's workspace, unchanged (4b).
- **`user-catalog/`**: deploy-wide state, written only through `/approve` (owner).

### D9. Telegram webhook mode

- `main.run`: after the web server task is started (that is ~100 lines after the
  secret pops; the `Bot` object holds the token and nothing spawns a subprocess in
  between), `if cfg.telegram_mode == "webhook"`: require `cfg.web_enabled` and
  `cfg.web_public_origin` (else `SystemExit` with the reason), then
  `await bot.set_webhook(url=f"{origin}/telegram/webhook/{secret}",
  secret_token=secret, allowed_updates=ctx.telegram.dispatcher.resolve_used_update_types(),
  drop_pending_updates=False)` and `await web_task` — the web server is the
  process's main loop. `allowed_updates` is **not** a hardcoded list: aiogram's
  `start_polling` defaults to `resolve_used_update_types()`, and a hardcoded list
  would make the two modes diverge the day a handler is added. `else:` `await
  bot.delete_webhook(drop_pending_updates=False)` and `await
  ctx.telegram.run_polling()` as today — **load-bearing**, not documentation:
  verified against aiogram 3.29.1, `start_polling` does not remove a set webhook,
  and `getUpdates` behind one answers 409. The literal `run_polling` stays in
  `run()`'s source (`tests/test_main.py` anchors on it).
- `set_webhook` with the same URL and secret from N instances is an idempotent
  `ok: true`; the one hazard is a rolling deploy whose instances disagree about
  `web_public_origin` (`docs/deploy.md`).
- Both calls go through `Bot`, which is closed on shutdown from now on
  (`delivery.aclose()` also `await bot.session.close()`).
- `CH_TELEGRAM_WEBHOOK_SECRET` is popped from `os.environ` with the other secrets in
  both `run` and `worker_startup` (the route reads `cfg`, loaded before the pop).
- `presentation/webhook.py::deliver`: the `X-Telegram-Bot-Api-Secret-Token` header
  is **required** and compared with `compare_digest` in addition to the path.
  **At-least-once delivery**: Telegram retries an `update_id` it did not get a 2xx
  for within its timeout, and the handler today runs intake (a file download and a
  MinIO put) and voice STT inline — one slow document was two turns and two charges.
  So `deliver` (a) drops a repeated `update_id` through an `UpdateDedup` port
  (`SETNX hermes:tg:update:<id> EX 600` in redis mode, an LRU set inline — the
  point is cross-instance), (b) hands the update to a background task held in a
  strong-reference set and answers `200` immediately, accepting that a crash loses
  an update rather than duplicating one. The docstring stops claiming a malformed
  body yields 400 when `Update.model_validate` raises inside `handle_update`
  (it is caught and answered 200); `webhook.py` and `modules/telegram/README.md`
  stop claiming the path needs `PUBLIC_PATHS` (the middleware guards `/api/*` only).
- `GET /healthz` in `web/app.py`, registered before the `SpaStaticFiles` catch-all.
- `docker-compose.yml`: comment on `agent` that `--scale agent=N` needs **both**
  `telegram_mode: webhook` and `CH_REDIS_URL` (inline mode starts a `CronTicker`
  and the curator loop in every instance — N clocks). `docker-compose.prod.yml`
  healthcheck for `agent` becomes `curl -fsS http://localhost:8080/healthz` when
  `web_enabled` (documented; the asyncpg probe stays the default because
  `web_enabled` is a config.yaml key). `.env.example` gains
  `CH_TELEGRAM_WEBHOOK_SECRET=` with a one-line comment; `docs/deploy.md` gets a
  "Webhook mode" section: generate the secret, set `telegram_mode: webhook`, both
  Caddy and the tunnel already forward every path, the proxy access log holds the
  path secret (hence the required header), `getWebhookInfo` as the check, the 409
  note becomes "polling only".
- Singletons decision (`decisions/redis-taskiq-scaling.md`): in webhook mode the
  agent is no longer a singleton; the Titler's task set and the presenter's typing
  task are per-instance and per-message and not correctness (recorded).

### D10. What a user is in a worker

`TurnRunService` and `SdkJobExecutor` resolve workspaces through the same
`WorkspaceResolver` the agent has; `AppContext` gains `users: AccessControl`
(already `acl`) and `workspaces: WorkspaceResolver`. Nothing in a worker reads
`CH_HOME/agent_home` directly any more except the learning services (4b).

## Phases

Phase A must land first (the table and the models everything else references). B, C
and D are independent of each other and run in parallel on top of A. E integrates
and documents.

### Phase A — identity: the table, the ACL, the resolver seam (one implementer)

Files: `core/db/migrations/versions/0005_users.py`; `modules/identity/domain/models.py`
(+ ORM `UserRow`, registered in `core/db/registry.py`); `application/ports.py`,
`application/services.py`; `infrastructure/repositories.py`; `presentation/{router,
schemas}.py`; `acl.py` shim; `core/session_keys.py::lane_owner`; ORM columns on
`sessions`, `messages`, `files`, `jobs`, `turn_costs`, `catalog_drafts`
(`modules/*/domain/models.py`) so `test_migrations` matches; `core/workspace.py`
(D5: `root`, `for_user`, `ensure`, `cli_config_dir`, `plugins_dir`, `WorkspaceResolver`);
`tests/web_helpers.py`, `tests/conftest.py::ensure_users`; `main.build_app` wiring
(`AccessControl(UserRepository(engine), MetaOwnerRepository(meta), lambda: cfg.allowed_users)`,
`WorkspaceResolver(...)` on `AppContext`).

Tests: `tests/modules/identity/test_identity_users.py` (claim creates the owner row
and the mirror; the second private user is denied; an allowlisted user gets a `user`
row with `users/<id>`; `touch` updates the profile; two concurrent claims → one owner;
disabled user denied), `test_identity_router.py` (`/api/me` keys), `tests/test_migrations.py`
(0005 upgrade on a database seeded through 0004 with a `meta` owner and rows in every
table backfills every `owner_id`), `tests/test_core_workspace.py` (owner paths are
byte-identical; a user root's paths; `ensure` seeds and is idempotent; the plugins
move), `tests/test_session_keys.py` (`lane_owner`).

### Phase B — the workspace in the turn (one implementer, after A)

Files: `integrations/claude_sdk/{options,guards}.py` (`env=`, `path_guard_factory`);
`modules/turns/{domain/value_objects (TurnContext.workspace, is_owner_lane),
domain/policies (the non-owner toolset), application/turn_service (wiki_for, the
observer gate), application/execution, application/ports, infrastructure/sdk_runner,
infrastructure/hermes_server (make_handlers(workspace=, skills=None per lane)),
infrastructure/tool_handlers/documents}.py`; `modules/memory/infrastructure/
{wiki_store (per root), turn_hooks}.py` + `WikiHooksProvider.build(..., workspace)`,
`presentation/dependencies.py` (`WikiGraphService` over the owner's root through the
resolver — memory routes are `require_owner`); `modules/files/infrastructure/{layout,
local_blob_store,s3_blob_store}.py` + `application/service.py` (`roots` per owner,
`owner_id` through every call); `modules/telegram/application/services/intake.py` +
the `WorkspacePort` bindings (`core/app_context.LaneWorkspace`, `gateway/telegram._Workspace`);
`modules/scheduler/infrastructure/executor.py` (job owner's workspace, `user_id` in
the cron `turn.start`, `{**NO_ADVISOR_ENV, …}`); `modules/learning/infrastructure/
fork_runner.py`, `modules/memory/infrastructure/lint_fork.py`, `modules/extensions/
infrastructure/mcp_prober.py` (env merge only — they stay on the owner's tree);
`modules/extensions/domain/paths.py` (plugins under `home`); `core/config.py` +
`config.yaml.example` (`users_shell_enabled: false`); `main.py` (`wiki_for`, the
blob stores over the resolver, `WikiHooksProvider`).

Tests: `tests/test_guards.py` (path guard: inside/outside/symlink escape/every tool
name/absolute `pattern` and `glob`/`path` absent), `tests/modules/turns/test_turns_execution.py`
(the run service resolves the lane owner's workspace and passes it down),
`tests/modules/turns/test_turns_workspace.py` (executor options for an owner lane
and a user lane: `cwd`, `env`, the toolset, `skill_admin` absent, hooks carry the guard
for the user root), the two-user test in `tests/modules/turns/test_turns_two_users.py`
that runs the fake executor for user A and user B and asserts the two `cwd`s differ,
**B's `system_append` contains none of A's wiki page titles**, and neither guard admits
the other's root; `tests/modules/files/test_files_layout.py` (mirror root per owner,
S3 key unchanged; write as B through the service, read as B through the route, bytes
equal); `tests/modules/telegram/test_telegram_intake.py` (a non-owner's download
lands under their root); `tests/modules/scheduler/test_scheduler_executor.py` (cron
turn runs as the job owner, advisor env preserved).

### Phase C — owner scoping in repositories and routers (one implementer, after A)

Files: `modules/sessions/{domain/policies, application/services, infrastructure/
repositories, presentation/router}.py`; `modules/turns/{application/turn_service
(messages.add owner, costs.record owner, recall tool owner), presentation/router (lane
owner on create)}.py`; `modules/files/{application/service, infrastructure/repository,
presentation/router}.py`; `modules/scheduler/{application/service, infrastructure/
repository, presentation/router}.py` + `CronToolPort` (owner from the turn);
`modules/extensions/infrastructure/repositories/pg_drafts.py` (+ the proposer);
`modules/telegram/application/services/routing.py` (lane owner into the thread touch,
`profile` into `acl.check`); `modules/learning` `MessageActivity` (owner-scoped where it
reads messages); `db.py` facade signatures where they delegate.

Tests: `tests/modules/sessions/test_sessions_policies.py` (the new rule: owner sees
group lanes, user A never sees user B's web lane), the two-user isolation test in
`tests/test_web_isolation.py` — user A and user B through `make_client`: A's threads,
files, uploads, messages search, routines and drafts are not visible to B via any route
(threads list, thread get, events, stream, uploads GET, `/api/routines`, search), and the
recall tool scoped to A returns nothing of B; `tests/modules/scheduler/test_scheduler_repository.py`
(`owner_id` on create/list), `tests/test_search.py` (search scoped by owner).

### Phase D — webhook mode (one implementer, independent of A)

Files: `main.py` (`run`: the branch, `set_webhook`/`delete_webhook`, the pop, bot
session close), `workers/taskiq_app.py` (the pop), `modules/telegram/presentation/
{webhook,dependencies}.py` (required header, `UpdateDedup` port, background dispatch,
docstrings), `modules/telegram/infrastructure/update_dedup.py` (`RedisUpdateDedup`,
`MemoryUpdateDedup`), `modules/telegram/README.md`, `web/app.py` (`/healthz`),
`web/deps.py` (`update_dedup`), `.env.example`, `config.yaml.example`,
`docker-compose.yml`, `docker-compose.prod.yml`, `docs/deploy.md`,
`docs/llm-wiki/decisions/redis-taskiq-scaling.md` (singletons).

Tests: `tests/test_main.py` (source-anchored: the webhook branch is after
`events_bus.start()` and after the pops; the `run_polling` literal is present;
`delete_webhook` precedes `run_polling`; `CH_TELEGRAM_WEBHOOK_SECRET` in both pop
lists), `tests/modules/telegram/test_telegram_webhook.py` (header missing → 401,
header wrong → 401, header + path right → 200 and the update is fed; the same
`update_id` twice → fed once; a slow gateway does not delay the 200),
`tests/modules/telegram/test_telegram_update_dedup.py` (fakeredis SETNX + TTL, the
memory LRU), `tests/test_web_app.py` (`/healthz` public, no cookie, not under the
transport policy, not swallowed by the SPA mount), a `tests/test_main_webhook.py`
that drives a `run()`-shaped helper `start_transport(cfg, bot, gateway, web_task)`
extracted from `run()` with a fake bot recording `set_webhook`/`delete_webhook` calls
in both modes (`allowed_updates` taken from the dispatcher, `web_enabled`/origin
refusals).

aiogram 3.29.1, verified: `start_polling` does **not** delete a set webhook (the
explicit call is load-bearing); `set_webhook(secret_token=)` exists; the default
`allowed_updates` is `resolve_used_update_types()`.

### Phase E — integration, docs, wiki (me, after B/C/D)

Full suite, ruff, lint-imports (contracts unchanged in count; the resolver in core
imports nothing from modules), `docker compose config/build`, live smoke: two users
(the owner and an allowlisted id) each start a web turn against a real worker; assert
the two `cwd`s in the worker log, the second user's `GET /api/threads` shows only
their own lane, the owner's `Read` of `/data/users/<id>/…` is denied by the hook,
`ls /data/users` from the second user's Bash is denied by the guard. Webhook smoke:
`telegram_mode: webhook` with a fake public origin against a local `curl` POST of an
update to `/telegram/webhook/<secret>` → the message reaches the router (log line).

Wiki: `decisions/multi-user-isolation.md` (new; security, next to
`readonly-catalog-boundary`), `contracts/state-db.md` (0005, `users`, the owner columns,
what stays deploy-wide), `components/{core-runtime,web,gateway,extensions}.md`,
`flows/message-turn.md` (the workspace resolution step), `decisions/redis-taskiq-scaling.md`
(singletons: none in webhook mode), `operations.md` (webhook check), `system.md`;
module READMEs `identity`, `turns`, `files`, `sessions`, `scheduler`, `telegram`,
`extensions`, `memory`, `learning` (4b debts); `README.md` (multi-user paragraph,
webhook mode), `docs/deploy.md`. `.llm-wiki/llm-wiki validate` clean.

## What a non-owner user gets after 4a

Stated here so the first real second user is not a surprise: their own threads,
uploads and search; their own workspace (wiki, `CLAUDE.md`, bundled skills and
agents seeded once, `inbox/`/`outbox/`, `tmp/`); a turn with Read/Write/Edit/Glob/
Grep confined to it, WebSearch/WebFetch/TodoWrite, and the hermes tools
`ask_user`, `session_search` (their messages), `cron_manage` (their routines),
`send_document`, `cost_report` (their spend). They do **not** get `Bash`, `Agent`,
`Task` (unless `users_shell_enabled`), `skill_admin`, `ext_manage` (owner-gated as
before), the memory/skills/routines/connectors/drafts pages of the SPA (all
`require_owner`, unchanged), nudges, review passes, or curated skills from their
conversations (4b).

## Open questions, defaulted

1. **Should allowlisted users be created eagerly at boot from `allowed_users`?** No —
   on first contact, so a typo in the allowlist creates nothing. The row is the
   record of who actually showed up.
2. **`display_name` from Telegram's `first_name + last_name`** — yes, joined by one
   space, trimmed. Not shown anywhere but `/api/me` yet.
3. **A disabled user** — `disabled_at` exists, nothing sets it in 4a (the owner has no
   UI for it); `check` refuses when it is set, so an operator can set it in SQL.
4. **Group lanes and non-owner senders**: the turn's `user_id` is the sender and the
   workspace is the owner's (the lane's). The sender's `ask_user` keyboard still binds
   to the sender. Unchanged behaviour, now stated.
5. **Web uploads by user B into a lane of user A** are already refused by `LaneGuard`
   through the thread policy; the new `owner_id` on `files` is defence in depth for
   the worker's re-resolution.

## Advisor amendments (2026-09-17)

Verdict was APPROVE-WITH-CHANGES. What changed in this plan and why:

| # | Blocker / risk | Change |
|---|---|---|
| B1 | Per-user wiki missed its two consumers: `TurnService._wiki` (the system append) and `WikiHooksProvider` (the hooks) — the owner's index would sit in another user's cached prefix and their Stop gate would write the owner's wiki. | D6: `TurnService` takes `wiki_for(workspace)`; `WikiHooksProvider.build(..., workspace)`; the two-user test asserts on the append string. |
| B2 | Blanket `owner_id = owner WHERE NULL` backfill would hand an allowlisted user's lanes, messages and files to the owner and resolve their lanes to the owner's workspace forever. | D2: backfill derives the owner from `session_key` (the `lane_owner` rule in SQL), inserts `users` rows for the non-owner ids it finds, nulls those users' `sdk_session_id`; the migration test covers the derived case. |
| B3 | Two sources of truth for the owner: an empty `users` table with the `meta` mirror set would let the next private message claim the deploy (`tests/test_acl.py` already encodes that state). | D4: `owner()` reads the table, falls back to the mirror and materialises the row; claim = users INSERT first, loser's `IntegrityError` rolled back → `"deny"`. |
| B4 | Blob store root frozen in `__init__`; a per-user write path with a process-global read path either raises in `resolve_within` or hands the model a path its `Read` is denied. | D6: stores take `roots(owner_id)`; every call carries the row's `owner_id`; the test reads back through the route as the other user. |
| B5 | `telegram/.../intake.py` was missing — a non-owner's photo would land in the owner's tree. | D6 + Phase B file list: `lane_dir(session_key, channel, user_id)` over the resolver. |
| B6 | `skill_admin` has no owner gate and the skills library stays the owner's tree in 4a. | D6: `make_handlers(skills=None)` for non-owner lanes. |
| B7 | Webhook delivery is at-least-once and the handler ran intake and STT inline in the request — one slow document was two turns. | D9: required secret header, `UpdateDedup` (Redis SETNX / memory LRU), background dispatch with an immediate 200. |
| R | Bash regex guard: denies `cd ..`, misses `ls $X/users`, and cannot be a boundary inside one uid. | Dropped. Non-owner lanes get no `Bash`/`Agent`/`Task` unless `users_shell_enabled`; the decision page calls 4a "isolation against accident", not a boundary. |
| R | `turns.user_id` FK: an audit field, NOT NULL, would break every test inserting a turn. | No FK on it. |
| R | Path guard gaps: Glob/Grep `pattern`/`glob` may be absolute with no `path`; `/tmp` is shared. | Absolute patterns denied; `tmp_dir` per workspace with `TMPDIR` in the env. |
| R | Subagent hooks: unknown whether the CLI fires `PreToolUse` inside `Agent`/`Task`. | Verified empirically in Phase A; decides whether `Agent`/`Task` can ever be granted to non-owners. |
| R | `allowed_updates` hardcoded would diverge from polling; `start_polling` does not delete a webhook. | From `resolve_used_update_types()`; explicit `delete_webhook` is load-bearing. |
| R | `--scale agent=N` also needs redis mode (inline mode starts N clocks). | Compose comment says both. |
| R | Env replace at background call sites re-enables the advisor. | `{**NO_ADVISOR_ENV, …}`. |
| R | Plugins move raced by three processes. | `OSError` caught, destination re-checked. |
| R | Visibility rule without `is_owner` would hide a group thread the backfill missed. | The belt stays for non-web, non-cron lanes. |
| R | `root: Path \| None` gives two representations of the owner. | `root: Path`, `Workspace.owner(home)`. |
| R | Fresh `CLAUDE_CONFIG_DIR` first-run behaviour; per-user cached prefix cost. | Phase E smoke runs a turn in a brand-new config dir; NFR-1 note in the decision page. |

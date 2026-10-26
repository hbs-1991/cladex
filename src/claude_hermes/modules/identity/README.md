# `identity`

## Purpose

Who is asking, whether they may, and **where their workspace is**. Three
responsibilities: verifying a Telegram sign-in (Login Widget or Mini App) and
minting/reading the signed session cookie from it; the access policy — the first
private-chat user claims ownership of an unclaimed deploy, after which only the
owner and `Config.allowed_users` get in, with `is_owner` as the stricter gate for
anything that changes what the agent *is*; and, since Wave 4a, the `users` table
that records everyone who actually showed up.

`users.id` **is the Telegram user id** (D1). `CurrentUser.user_id`,
`threads.owner_user_id`, `turns.user_id`, the `web:<user_id>:` lane grammar, the
ask-broker's owner check and the session cookie all carry that number already, so a
surrogate key would only add a translation at every one of them; spec req. 29 ("a
Telegram user id maps to exactly one user") is then the primary key itself. A
non-Telegram login later gets a synthetic id range (4b+).

## Layout

```
domain/
  exceptions.py     LoginError(ValueError), UserExists, OwnerExists, UnknownUser,
                    OwnerImmutable
  models.py         User, Grants, Profile, Role, CurrentUser  (pure - no sqlalchemy)
  tables.py         UserRow - the declarative `users` model
  session_token.py  COOKIE, sign_session, read_session          (pure hmac)
  telegram_proof.py login_key, webapp_key, verify_login, verify_webapp_user,
                    verify_webapp (the id-only wrapper), _check, _fresh
application/
  ports.py          UserStore
  services.py       AccessControl(users, *, allowed_users, shell_default,
                    learning_default, now), OWNER_WORKSPACE, user_workspace_dir
infrastructure/
  repositories.py   UserRepository(engine) -> UserStore
presentation/
  router.py         router: APIRouter(tags=["identity"]) - the six routes, plus
                    PUBLIC_PATHS, LOCAL_HOSTS, is_local_origin, error_response, _cookie
  users_router.py   router: APIRouter(tags=["identity"], owner-only) - the
                    owner's user list: list, invite, disable, enable, grants
  dependencies.py   the six providers over core.di.provide, plus login_payload
                    (the raw login body; see Dependencies below)
  schemas.py        user_wire(user_id, is_owner, profile=None),
                    member_wire(u, defaults), members_wire(rows, allowlisted,
                    defaults), UserCreate, UserGrantsPatch, ModelDefaultPut
```

`UserRow` is registered in `core/db/registry.py` (the one deliberate core→modules
edge: Alembic needs every model in one `MetaData`). Migration `0005_users.py`
creates the table and attributes every existing row; `0008_model_choice.py` adds
the personal model default and `0009_user_grants.py` the two grant columns.

**Why `tables.py` exists at all** — every other module keeps its ORM rows in
`domain/models.py`, and identity would too, except that identity's *application*
layer needs `User`/`Profile` for its own signatures and the contracts "application
layers are framework-free" and "presentation layers hold no persistence" forbid
`sqlalchemy` **through a chain**. One file holding both would put SQLAlchemy one hop
behind `application/services.py` and `presentation/{router,schemas}.py` and break two
contracts at once. Identity is the first module whose application layer imports its
own `domain/models.py`, which is why it is the first to need the split.

`core/auth.py` (`CurrentUserMiddleware`) was built by this stream but lives in
`core/` on purpose: no module owns the request pipeline, and it takes its two
callables by injection so core stays ignorant of cookies, secrets and the ACL.
`core/workspace.py::WorkspaceResolver` is the same arrangement for the other
direction: it is bound to `AccessControl.workspace_dir_of` at composition, so core
resolves a path without knowing what a user is.

## The table

```sql
users(
  id            BIGINT PRIMARY KEY,   -- the Telegram user id
  role          TEXT NOT NULL,        -- 'owner' | 'user'
  workspace_dir TEXT NOT NULL,        -- relative to CH_HOME: '' (owner) | 'users/<id>'
  display_name  TEXT NOT NULL DEFAULT '',
  username      TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL,
  disabled_at   TEXT NULL,
  default_model  TEXT NULL,          -- 0008: this person's default for NEW lanes,
  default_effort TEXT NULL,          --       as catalog ids; NULL = the deploy default
  shell_enabled    BOOLEAN NULL,     -- 0009: Bash/Agent/Task on their lanes;
  learning_enabled BOOLEAN NULL      --       the learning loop over their turns.
                                     --       NULL = inherit the config default
)
CREATE UNIQUE INDEX users_one_owner ON users (role) WHERE role = 'owner';
```

`sessions.owner_id`, `messages.owner_id`, `files.owner_id`, `jobs.owner_id`,
`turn_costs.owner_id`, `catalog_drafts.owner_id` and `threads.owner_user_id` all
reference `users(id)` with `ON DELETE SET NULL`. **`turns.user_id` does not**: it is
the *sender* of a turn — an audit field, NOT NULL, and a group turn's sender may
have no row at all.

## Public surface

### Ports consumed
- `UserStore` — `get`, `owner`, `create`, `touch`, `set_disabled`, `list`,
  `active_ids()` (every non-disabled id, **no limit** — Wave 4b, R-5:
  `list(limit=100)` would have silently dropped user 101 from every curator
  sweep), since 0008 `default_of(user_id) -> (model, effort)` /
  `set_default(user_id, model, effort) -> bool`, and since 0009
  `set_grants(user_id, *, shell, learning) -> bool` (one UPDATE of only the
  named columns; `None` leaves a column alone; never creates a row). Satisfied by
  `UserRepository(engine)`. The only port: since Step 5 the `users` table is the
  only owner record (the `OwnerStore` mirror over `meta.owner_user_id` is gone).

### Ports provided (what other modules receive from identity)
- `AccessControl.check(user_id, chat_type, profile=None) -> "claimed" | "allow" | "deny"`,
  `is_owner(user_id) -> bool`, `user(user_id) -> User | None`,
  `workspace_dir_of(user_id) -> str`, and since Wave 4b `admitted(user_id) ->
  bool` (`check(uid, "web") == "allow"` — the allowlist and `disabled_at`,
  asked by `CurrentUserMiddleware` on EVERY request rather than at login, R-8)
  and `active_user_ids() -> list[int]` (what learning's `LearningScopes` sweeps).
  Consumers (turns' `AclPort`, telegram's `AclPort`, the web routers,
  `WorkspaceResolver`, learning's wiring) declare their own Protocol; structural
  typing matches.
- `AccessControl.default_of(user_id) -> (model, effort)` satisfies sessions'
  **`OwnerDefaults`**: the lane owner's personal default, which the model
  resolver asks for on a lane that is not shared. `set_default(user_id, model,
  effort) -> (model, effort)` is the writer (telegram's `ModelDefaultsPort` takes
  the pair). Neither validates — the offer is `cfg.models`, which this module does
  not read, so the CALLER validates with `core.model_catalog.validate`; a stored
  model that later leaves the offered list is skipped by `resolve` rather than
  run, so a stale row is harmless.
- `AccessControl.grants(user_id) -> Grants` (0009) is a person's **effective**
  shell and learning grant, and satisfies three consumer-side ports: turns'
  **`LaneGrants`** (with `grant_defaults() -> Grants`, the config defaults alone),
  scheduler's **`OwnerGrants`**, and the `learning_of` callable `main.py` hands
  learning's `LearningScopes`. One `get` per call, never cached (the agent and
  the worker must both see a flip on the next turn); `user_id <= 0` and an id
  with no row answer the defaults; a failing store **propagates** — the turn
  must not guess, and a routine or the sweep decides its own fallback. The rule
  itself is **`Grants.of(user, defaults)`**, pure, in `domain/models.py`: the
  owner's row → `(True, True)`, any other row → its column, else the default;
  `member_wire` calls the same function, so nothing re-implements it. The
  defaults are `users_shell_enabled` / `users_learning_enabled`, passed as
  `shell_default=lambda: cfg.users_shell_enabled` (and the learning twin) and
  read at call time, like `allowed_users`. `set_grants(user_id, *, shell=None,
  learning=None) -> User` is the writer: `OwnerImmutable` for the owner,
  `UnknownUser` for an id with no row, and it resets no session — the turn
  compares the lane's session stamp with the grant at its own start.
- `profile` is **whatever the channel knows** — a `Profile`, a Login Widget payload
  dict, or another module's own value object with `display_name`/`username`
  (`modules/telegram`'s `SenderProfile`). `Profile.of` normalises it. Modules are
  independent of each other, so neither type is ever imported across the boundary.

### Routes — `router: APIRouter(tags=["identity"])`, in registration order
| Method | Path | `operation_id` | Notes |
|---|---|---|---|
| POST | `/api/auth/telegram` | `identity_telegram_login` | Login Widget payload (`Depends(login_payload)`) → cookie, SameSite=Lax |
| POST | `/api/auth/webapp` | `identity_webapp_login` | `{"init_data": …}` (`Depends(login_payload)`) → cookie, SameSite=None |
| GET | `/api/auth/session` | `identity_session_link` | `?t=<token>`, local origins only, else 404 |
| POST | `/api/auth/logout` | `identity_logout` | 204, clears the cookie |
| GET | `/api/me` | `identity_get_me` | `{"user_id", "role", "display_name", "username", "default_model", "default_effort"}` |
| PUT | `/api/me/model-default` | `identity_set_model_default` | `{"model": str\|null, "effort": str\|null}` → `{"default_model", "default_effort"}`; 400 `bad_model` / `bad_effort`, 404 `not_found` |
| GET | `/api/config` | `identity_get_config` | `{"bot_username": …}` — public |

### The owner's user list — `users_router.py`, owner-only on the `APIRouter`

| Method | Path | `operation_id` | Notes |
|---|---|---|---|
| GET | `/api/users` | `identity_list_users` | the rows, owner first (the first 100 — `list_users` keeps its default `limit`), plus every `allowed_users` id without one (`source: "config"`) |
| POST | `/api/users` | `identity_invite_user` | `{"user_id": 7}` → 201; 409 `user_exists`, 400 `bad_request` |
| POST | `/api/users/{user_id}/disable` | `identity_disable_user` | sets `disabled_at`; 409 `owner_immutable`, 404 `not_found` |
| POST | `/api/users/{user_id}/enable` | `identity_enable_user` | clears it |
| PATCH | `/api/users/{user_id}` | `identity_set_user_grants` | `{"shell"?: bool, "learning"?: bool}` (strict JSON booleans) → 200 + the member; 400 `bad_request` (neither field, or a non-boolean), 404 `not_found` (no row — a `source: "config"` id included, and none is created), 409 `owner_immutable` |

Every member on the wire (list, invite, disable/enable, PATCH) carries `shell`,
`learning` — the **effective** grants, from `Grants.of` — and `shell_inherited`,
`learning_inherited` (the column is NULL, so the value is the config default
showing through). The owner is `true/true`, never inherited; a `source: "config"`
entry carries the defaults, inherited, because there is no row to hold a choice.

The gate sits on the router, the **read included**: who else can reach this agent is
the owner's business. `invite` writes the row `check` would have written on that
person's first message — `role='user'`, `workspace_dir=users/<id>`, no profile
(Telegram resolves an id to a name, never a @username to an id, so the display
fields fill in on the first `touch`) — which admits them in both channels at once
and needs no restart. Nothing is created on disk: the tree is still built lazily by
`WorkspaceResolver.ensure()`.

Disabling is the first writer `disabled_at` has ever had; the column has been read
since Wave 4a. It takes effect on the **next request**, because
`CurrentUserMiddleware` asks `admitted()` per `/api/*` call — a signed, unexpired
cookie stops working immediately rather than at its expiry. The row, the threads,
the files and the workspace are left intact, so enabling gives back exactly what
was there. Two refusals: the owner cannot be disabled (`is_owner` gates every
mutation, so it would take SQL on the host to undo), and an id with no row cannot
be either — **unless it is in `allowed_users`**, where the disable creates the row
already disabled, because a row is the only thing that outranks the config file.

`PUBLIC_PATHS` = the first three plus `/api/config`; the app shell unions every
router's set. Wire shapes and error `code` strings (`login_failed`, `not_found`,
`unauthorized`) are frozen (spec req. 36). **Wave 4a adds `display_name` and
`username` to `/api/me` only** — `user_wire` emits them solely when a profile is
passed, so both login bodies stay byte-identical to what `client.ts` parses.

### Dependencies (`presentation/dependencies.py`, built on `core.di.provide`)

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_acl` | `acl` | `AccessControl` (`check`, `is_owner`, `user`; the users router's `list_users`, `allowlisted_ids`, `invite`, `set_disabled`, `grant_defaults`, `set_grants`) |
| `get_cfg` | `cfg` | `core.config.Config` (`web_session_days`, `web_secret`, `web_public_origin`, `model`, `models`, `review_model`) |
| `get_now` | `now` | `Callable[[], int]` — epoch seconds |
| `get_login_key` | `login_key` | `bytes` — sha256(bot token) |
| `get_webapp_key` | `webapp_key` | `bytes` — HMAC("WebAppData", bot token); `required=False` |
| `get_bot_username` | `bot_username` | `str`; `required=False` |
| `login_payload` | — (reads the request, not the bundle) | `Any` — the login body, or `401 login_failed` |

That is the whole list of bundle attributes. The last two are `required=False`
because `b""` / `""` are documented answers the handlers render themselves (`401
login_failed` for a shut Mini App door), where `provide`'s default 503 would be a
new status this router never had. `cfg` is read **per request**, never captured:
`tests/test_web_app.py` flips `web_public_origin` on a built app and expects
`/api/auth/session` to start answering.

`CurrentUserMiddleware(app, *, read_session, is_owner, public_paths, redirect_on_401,
admitted=None)` takes no `deps`: `read_session(request) -> int | None`,
`is_owner(user_id) -> Awaitable[bool]` and `admitted(user_id) -> Awaitable[bool]`
are bound by the composition root (`http/app.py` binds the last to
`acl.admitted`). A cookie whose user is no longer admitted gets the same 401 as
no cookie at all: "was admitted once" stopped being enough when 4b let a
logged-in user write prompt text into a tree.

### Tools / events
None.

## Invariants

- **`users_one_owner` IS the claim arbiter.** The claim is an INSERT, never a
  check-then-insert: two first private chats racing produce one owner row and one
  `IntegrityError`, which `check` catches (the transaction is already rolled back)
  and answers `"deny"`. The claim writes one row and nothing else.
- **"Claimable" means no owner row.** `owner()` reads the `users` table and
  nothing else (Step 5). The old `meta.owner_user_id` key is materialised into
  the row once, by migration `0005_users` at upgrade time — so every volume that
  reaches Step 5 through `alembic upgrade head` has the row, and a stale `meta`
  row afterwards is inert (`scripts/import_sqlite.py::_ensure_users` does the
  same for a SQLite import). The one state the old mirror guarded against — key
  present, table empty — is reachable only by hand-truncating `users` or by an
  `alembic downgrade` past 0005 on a deploy claimed after Step 5, which nothing
  in the app does and `docs/deploy.md` forbids: an unclaimed deploy is one the
  next private DM can take.
- **The web never claims ownership.** `_admit` calls `acl.check(uid, "web")`, never
  `"private"` — only `"allow"` gets in. Otherwise the first stranger to find the
  tunnel hostname would own the agent. Covered by
  `tests/modules/identity/test_identity_router.py::test_the_web_never_claims_an_unowned_deploy`.
- **The owner's `workspace_dir` is `""`.** Their root IS `CH_HOME`, so
  `/data/agent_home`, `/data/.claude-cli` and `/data/logs` do not move: a live
  volume needs no file migration and every SDK session keeps resuming.
- **`workspace_dir_of` raises `UnknownUser`; it never falls back to the owner's
  tree.** Guessing there would silently run a stranger's turn in the owner's
  workspace, which is the exact failure Wave 4a exists to prevent.
- **A row is created on first contact, never eagerly from `allowed_users`** — so a
  typo in the allowlist creates nothing, and the table is the record of who
  actually showed up.
- **A personal model default is a preference, not a permission.**
  `PUT /api/me/model-default` is open to every admitted user — the same offered
  list as the owner, by explicit owner decision — because it only ever reaches
  that person's own row and their own lanes. It never moves a conversation that
  is already running: a live session keeps the pair it started on until `/new` or
  a reset, because that session's cached prefix is model-scoped (P1), and shared
  lanes (a group, a forum topic, a `cron:` job) never read a personal default at
  all.
- **`set_default` is an UPDATE, like `touch` and `set_disabled`.** It is handed no
  role and no `workspace_dir`, so an INSERT branch would have to invent them and a
  preference would create a user nobody admitted. "There was nobody to write" is
  an answer (`UnknownUser` → 404), unreachable through a real login because every
  door materialises the row first.
- **`default_of` is two columns, not a `User`.** The model resolver asks on every
  turn of every private lane, and `User` is a frozen snapshot the workspace
  resolver caches per process — which is also why `/api/me` reads the pair with a
  second narrow query instead of widening the row.
- **A grant needs a row, and never makes one.** `set_grants` is an UPDATE with
  no config-allowlist exception (unlike `set_disabled`): a row is an admission,
  and creating one to hang a grant on would enrol a never-seen id in
  `active_user_ids()` and let the curator seed their tree. `NULL` is "no choice
  of their own" and there is no way back to it from the wire — booleans only.
- **The owner has no grants.** Their lanes get the shell and the observer
  because they own the lane (`is_owner_lane` in turns), so `Grants.of` answers
  `(True, True)` for their row and `set_grants` refuses them.
- **`touch(profile=None)` means "no news", not "no name".** Telegram omits
  `username` for people who have none; an update without it must not blank a handle
  the Login Widget already gave us.
- **Two derived keys, never the bot token.** `login_key` = sha256(token),
  `webapp_key` = HMAC("WebAppData", token); neither proof replays as the other.
  `main.run` derives both and then `os.environ.pop`s the token — that stays in `main.py`.
- `/api/auth/session` returns 404 on any non-local origin.
- Cookies: always `httponly` + `secure`; Mini App `SameSite=None` (Telegram frames
  the page under web.telegram.org), everything else `lax`. CSRF is the Origin check.
- `LoginError` stays a `ValueError` subclass this wave — the route handlers catch
  `(LoginError, ValueError, TypeError, AttributeError)` and answer `401 login_failed`.
- **Neither login body is a Pydantic model**, and this is the one module in Wave 2b
  where that is so. The Login Widget's signature is an HMAC over *all* the fields it
  sent, so dropping `username`/`photo_url` or coercing `id` would turn a valid login
  into a 401 — and a declared model also moves the *malformed* cases (`{not json`,
  `[1]`) from the router's own `401 login_failed` to the shell's `400 bad_request`,
  which `client.ts` branches on differently.
- **Nothing writes `meta.owner_user_id`** since Step 5; the row in `users` with
  `role = 'owner'` is the record, and `users_one_owner` keeps it single.
- Every body in `domain/telegram_proof.py` and `domain/session_token.py` moved
  byte-identically from the pre-modular `web/auth.py` (Wave 2b); `main.run` reads
  `login_key` / `webapp_key` from `domain/telegram_proof.py` directly.

## Known debts (paid in the wave named)

| Debt | Paid by |
|---|---|
| ~~**The owner mirror.** `meta.owner_user_id` written next to the `users` row on every claim, `owner()` materialising from it.~~ **Paid in Step 5**: `OwnerStore`, `MetaOwnerRepository`, `AccessControl(owner_mirror=)` and `OWNER_KEY` are gone; the table is the only record (see Invariants). | done |
| ~~`acl.py` shim: `AccessControl(db, cfg)` over the `Database` facade.~~ **Deleted in Step 5** with `tests/test_acl.py`; `main.build_app()` builds `AccessControl(UserRepository(engine), allowed_users=lambda: cfg.allowed_users)`. | done |
| ~~The Mini App login learns no profile.~~ — **paid in Wave 4b** (D10): `verify_webapp_user(init_data, key, now, max_age) -> (id, user_mapping)` keeps parsing and HMAC verification in ONE function (a standalone parser is the function the next caller uses alone, and the display name becomes attacker-supplied — R-7); `verify_webapp` is the thin `[0]` wrapper, and `webapp_login` calls `_admit(..., profile=Profile.of(user))`. | done |
| **`touch` never clears a field.** A user who deletes their Telegram username keeps the stale one until they set another — the price of "an empty field means *not sent*". Nothing shows either field to anyone but its owner yet. | 4b |
| **`disabled_at` has no writer.** `check` refuses a row that carries it, so an operator can lock somebody out with one `UPDATE`; there is no owner UI for it. | 4b |
| **Per-user skills and learning — paid in Wave 4b.** Every user has their own skills library, `skill_admin` (on their private/web lanes) and Skills page regardless; with a learning grant (their own `users.learning_enabled` since 0009, else the `users_learning_enabled` default) their turns are observed, reviewed and curated in their own tree. **Per-user extensions are decided against, and the decision is final for this iteration** (4b D9): an install changes every turn's cached prefix and MCP set, runs under the OWNER's credentials (one `SecretBox`, OAuth tokens) and lands a clone in `CH_HOME/plugins` that every tree loads; per-user installs would need per-user secrets (P7), a second extensions module (P2) and N install states multiplying prefix churn (P1). The per-user customisation channel is the skills library and the wiki. `ext_manage` stays owner-gated; `installed_by` remains audit-only. | done / decided |
| ~~`web/auth.py` shim.~~ **Deleted in Step 5.** | done |
| `presentation/router.error_response` duplicates `http/app.error_response` (a module may not import a composition root; `core/` would be the place for one body). | open |
| `presentation/router.PUBLIC_PATHS` is unioned into one frozenset by `build_web_app`; no other router contributes to it yet, so the union is a shape, not a fact. | when a second router needs a pre-login path |
| ~~No `users` table~~ — **paid in Wave 4a**: the table, the ACL over it, the owner columns and the workspace resolver seam. | done |
| ~~`auth_routes(deps)`~~ — **deleted in Wave 2b**. The module exports `router: APIRouter`. | done |

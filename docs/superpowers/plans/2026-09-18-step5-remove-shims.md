# Step 5 — remove the shims, full import-linter contracts, dead code

Status: plan, 2026-09-18, advisor amendments folded in (B-1…B-4, R-1…R-8,
S-1…S-3; table at the end). Follows Wave 4b (1de31dd, f3010d1, f24001e).
Spec: `docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`,
req. 35 ("Step 5: remove all shims; full import-linter contracts; delete dead
code") and the acceptance criteria: "`src/claude_hermes/` contains `core/`,
`integrations/`, `workers/` and `modules/…`; no `web/`, `gateway/`, `cron/` packages
remain" and "`import-linter` passes with the full reference contracts; a
deliberately added `modules/turns → modules/sessions/infrastructure` import fails".

## Where we stand

Every module has moved (Waves 1–4b). What is left at the legacy paths is
**shim-only** — verified by grep: no file under `src/claude_hermes/{core,
integrations,modules,workers}` imports a legacy root; the only importers are
`main.py` (six edges, listed in D3) and **129 test files**.

The legacy surface, by kind:

| Kind | Files |
|---|---|
| `sys.modules` aliases (old name IS the new module) | `config.py`, `options.py`, `paths.py`, `gateway/voice.py`, `learning/{archive,backup,prompts}.py`, `extensions/{approvals,catalog,drafts,install,oauth,plugins,probe}.py` |
| Re-export shims | `guard.py`, `hooks.py`, `prompt.py`, `wiki.py`, `cron/{executor,scan,schedule}.py`, `extensions/{secrets,usercatalog}.py`, `gateway/{events,presenter,questions,rich,topics}.py`, `learning/{loop,nudges,observe,review,usage}.py`, `web/{auth,commands,connectors,drafts,files,plugins,routines,skills,threads,turns,voice,wiki}.py` |
| Adapter shims (old constructor over the module object) | `acl.py`, `db.py` (the `Database` facade), `filestore.py`, `runner.py` (`AgentRunner`), `sessions.py` (`SessionRouter(db, cfg)`), `tools.py` (`make_handlers`/`build_hermes_server`, `_OneScope`), `turnbus.py`, `cron/{adapters,store,ticker}.py`, `extensions/{compose,manage,store,tokens}.py`, `gateway/telegram.py` (`handle_event`, `intake_attachments`), `learning/{curator,library}.py` |
| **Live code still only at a legacy path** | `gateway/titles.py::Titler`, `gateway/attachments.py::{Spec,specs,download_all}`, `logsafe.py::RedactingFormatter`, `web/{app,deps,errors}.py` (the FastAPI assembly and the bundle) |
| Code in the modules that exists only for a shim | `routes(deps)` in eight module routers; `extensions.wiring.{connectors_routes,plugins_routes,drafts_routes,LegacyOAuthRefresher}`; `core.app_context.MessageDownloader`; `files.presentation.dependencies.{get_upload_limit, get_files' `.service` reach}`; `turns.presentation.dependencies.get_runner` as the commands fallback; `turns.presentation.dependencies.events_after`'s `deps.db` fallback; identity's owner MIRROR (`OwnerStore`, `MetaOwnerRepository`, `owner_mirror=`, `_mirror_owner_id`); `CuratorService(learning=)`; learning's `PromptCacheInvalidator.reset_all`; `MemoryBundle.lint` (the owner-only lint service nothing reads since 4b); `AppContext.db`, `WebDeps.{db, library, skills_dir, sidecar, job_store, ext_store}` |

Vulture over the modern packages at 70% confidence finds three unused names
(`core/redis.py:153-154` `exc_type`/`tb`, `turns/application/ports.py:491`
`first_message`). The real dead code is the shim-facing code above, which vulture
cannot see while the shims import it.

## Goal

After Step 5:

- `src/claude_hermes/` is `__init__.py`, `main.py`, `core/`, `integrations/`,
  `modules/`, `workers/` and `http/` (the HTTP composition root — see D2). Nothing
  else. `grep -rn "claude_hermes\.\(web\|gateway\|cron\|acl\|db\|…\)"` over `src`
  and `tests` hits nothing.
- `pyproject.toml`'s import-linter section states the full architecture and
  nothing legacy: a **layers** contract (composition roots → modules →
  integrations → core), module independence, the four layer contracts per
  module, domain purity, application framework-free, presentation without
  persistence. A deliberately added `modules/turns → modules/sessions/infrastructure`
  import fails `lint-imports` (verified by hand in Phase V, not committed).
- Every test drives a module object or the composition root. No test builds a
  `Database`, an `AgentRunner`, a `SessionRouter(db, cfg)`, a `JobStore(db)`.
- No docstring describes a shim that no longer exists. History stays in
  `docs/llm-wiki/log.md`, not in comments.
- Behaviour on the wire is unchanged: same HTTP routes, same tool schemas and
  descriptions (the prompt prefix is byte-identical — `tools.py` implemented none
  of it), same Telegram behaviour, same task payloads. **No migration, no new
  config key, no prompt-cache miss.**

## Locked decisions

### D1. Delete the legacy surface wholesale, in one commit

The 18 top-level modules (`acl config db filestore guard hooks logsafe options
paths prompt runner sessions tools turnbus wiki` — `logsafe` moves, see D2) and the
five packages (`cron extensions gateway learning web`) go in one change, with the
tests that pinned them. No deprecation period: the spec scheduled the removal, the
shims were documented as "deleted in Step 5" from the day they were written, and
nothing outside `main.py` and the tests imports them. A half-removed surface would
be worse than either state — an alias that survives keeps `sys.modules` patching
alive and the forbidden lists in `pyproject.toml` with it.

### D2. Live code at a legacy path moves into the package that owns it

- **`web/{app,deps,errors}.py` → `claude_hermes/http/{app,deps}.py`.** The spec
  says the FastAPI app "in `core/` (or `main.py`) only assembles routers,
  middleware and exception handlers". `core/` may not import a module (the first
  contract), and `build_web_app` imports twelve routers; `main.py` is the process
  root and already 985 lines. So the HTTP assembly gets the same standing the
  queue assembly has: a composition-root package beside `workers/`, importing
  downwards only. The acceptance criterion enumerates `core/ integrations/
  workers/ modules/` and forbids `web/ gateway/ cron/`; `http/` is a fourth root
  added deliberately (S-2), for the reason above, and the decision page says so.
  `web/errors.py` folds into `http/app.py` (`error_response` is its only
  surviving reader; `ApiError` — every module router aliases
  `ApplicationException` under that name itself — is deleted). `core/di.py`'s
  docstring names the bundle by its new path; the object stays duck-typed.
  No `sys.path` mutation in the repo ever puts `src/claude_hermes` itself on the
  path (`scripts/*.py` insert `src`), so the stdlib `http` is never shadowed.
- **`gateway/titles.py::Titler` → `modules/telegram/infrastructure/titler.py`.**
  It satisfies telegram's own `ThreadTitlerPort` today (`is_new_thread`, `spawn`)
  and is the one aiogram-bound title writer. **Every** `modules.sessions` import
  goes (R-3): `SdkTitleGenerator`, and the `TITLE_PROMPT`/`TITLE_MAX_WORDS`/
  `_clean` re-exports (no reader). The class takes `generator` (anything with
  `async generate(text) -> str`), `sessions` (`is_unbound`) and `titles`
  (`apply`); the three collaborators are declared as Protocols in
  `modules/telegram/application/ports.py` beside `ThreadTitlerPort`, so
  `test_telegram_ports.py::test_the_real_infrastructure_satisfies_the_ports_it_claims`
  covers the moved class. `main.run` builds its own
  `SdkTitleGenerator(cfg.title_model)` for it — which is what happens today; the
  generator is a stateless model-name holder, so instance identity buys nothing
  (R-2). `cfg` leaves the constructor. `title_for` (no reader) goes.
- **`gateway/attachments.py::{Spec, specs, download_all}` →
  `modules/telegram/infrastructure/attachments.py`** as an `AiogramDownloader`
  class satisfying `AttachmentDownloaderPort`; `core.app_context.MessageDownloader`
  (the adapter that only existed because the functions lived at a legacy path)
  is deleted and `main.build_telegram_gateway` passes the object. `specs` needs
  files' `safe_name` (the message-id-prefixed sanitised filename) and telegram
  may not import files. **The naming invariant moves to `core/naming.py`** (R-4):
  `_UNSAFE`, `safe_name` and `lane_token` together — the files docstring says the
  two derive from one regex "or old files stop resolving", so they move as one —
  and `modules/files/domain/naming.py` re-exports both, so its callers and
  `tests/modules/files/test_files_naming.py` are untouched. Not a naming callable
  injected into the downloader: a containment rule must not be a composition-root
  choice. `MAX_ATTACHMENT_BYTES` already lives in telegram's domain constants —
  the copy in `gateway/attachments.py` goes. Core is the legal back door between
  modules for **invariants** only, never services (R-8) — Phase V reviews what
  `core` gained in this step against that rule.
- **`logsafe.py` → `core/logsafe.py`.** `core/logging.py` imports it, which is
  a `core → top-level` edge no contract names today; after the move it is
  `core → core`. Same class, same regex, same tests (retargeted).

### D3. `main.py` builds the module objects it was reaching through shims

The six edges, and what replaces each:

| Today | After |
|---|---|
| `db = Database(engine)`; `AppContext.db`; `ctx.db.close()` in `shutdown` | no facade; `shutdown` calls `engine.dispose()` directly; `AppContext.db` removed |
| `ext_store = ExtensionStore(db)` (+ `.tokens`) | `SqlExtensionRepository(engine)` and `SqlOauthTokenRepository(engine)`, both already accepted by `extensions_wiring.build(store=, tokens=)`; `ExtensionsBundle.tokens` is what the connectors router reads (`ext.tokens`) — it already does |
| `job_store = JobStore(db)` | `JobRepository(engine)` |
| `from .web.auth import login_key, webapp_key` | `modules.identity.domain.telegram_proof` |
| `from .gateway.attachments import download_all, specs` + `MessageDownloader` | `AiogramDownloader(safe_name)` from telegram infrastructure (D2) |
| `from .gateway.titles import Titler` | `modules.telegram.infrastructure.titler.Titler(topics, generator=…, sessions=, titles=)` |
| `from .web.app import build_web_app` | `from .http.app import build_web_app` |
| `MemoryBundle.lint = build_wiki_lint_service(cfg, home, wiki.root)` | dropped — no reader since 4b's `wiki_lint_for`; `MemoryBundle` loses the field |

### D4. The owner mirror goes; the `users` table is the only owner record

`OwnerStore`, `MetaOwnerRepository`, `AccessControl(owner_mirror=)`,
`_mirror_owner_id` and the two mirror branches (`owner()` materialising a row,
`admitted()` trusting the key) are deleted. The claim writes one row and nothing
into `meta`. Why it is safe now and was not in 4a: migration `0005_users`
materialises the owner row **from** `meta.owner_user_id` at upgrade time, so every
volume that reaches Step 5 through `alembic upgrade head` has the row. The state
the mirror guarded against — key present, table empty — is now only reachable by
hand-truncating `users`, which the app never does. The SQLite import path is
covered the same way: `scripts/import_sqlite.py::_ensure_users` inserts the owner
row from the SQLite meta key before anything that references it. `identity/domain/
constants.py` loses `OWNER_KEY`/`_OWNER_KEY` (no reader survives). The stale `meta`
row on a live volume is inert; `docs/deploy.md` says so and gives the one-line
`DELETE` for the tidy-minded. `tests/test_acl.py::test_allowed_users_config` (sets
only the meta key) is deleted with its file — its twin inserts the owner row,
which is what a claimed deploy IS.

The one-way door (R-1): after Step 5 nothing writes `meta.owner_user_id`, so a
deploy claimed post-Step-5 has its owner ONLY as a `users` row. `alembic
downgrade` below 0005 followed by an upgrade would yield an unclaimed deploy that
the next private DM could claim. `docs/deploy.md` gets the sentence ("never
downgrade past 0005 on a claimed deploy; recovery is `INSERT INTO users (id, role,
workspace_dir, …) VALUES (<id>, 'owner', '', …)`") and the 0005 docstring says
its meta read is now the only materialiser.

### D5. Learning's `PromptCacheInvalidator` says what it means

The port declares `reset_owned(owner_id: int, *, include_unowned: bool = False)`;
the `reset_all` declaration goes. Extensions' own `PromptCacheInvalidator` keeps
`reset_all` — an install changes every lane's prefix, that port is correct.
`WebDeps.invalidate` stays bound to `runner.reset_all` for extensions only, and
its comment says so once.

### D6. The bundle carries what a provider reads, and nothing else

`http/deps.py::WebDeps` keeps exactly the attributes some module's
`provide("<name>")` names (26 today: `cfg runner acl bus threads turns turn_events
owned auto_title slash_sources sdk_commands file_store broker voice scheduler
extensions learning wiki_root telegram update_dedup login_key webapp_key
bot_username home now` and the two properties `visibility`/`usage` if a provider
still reads `usage` — check) plus `dist_dir`. Dropped: `db`, `job_store`,
`ext_store`, `library`, `skills_dir`, `sidecar` (owner-scope objects no router has
read since 4b), and the `usage` / `logs_dir` properties with them (R-5; no
provider names them either). `visibility` stays — files' `get_lane` reads it.
The skills router's mount condition becomes `deps.learning is not None` —
equivalent on a real boot, where `LearningBundle` always carries both (R-6) —
and `tests/web_helpers.py` sets `learning=FakeLearningScopes(...)`
unconditionally, so no harness loses the router silently. A test that drove
`deps.sidecar` drives the owner scope through `deps.learning.owner()` instead —
that IS the object.

### D7. Providers lose their shim branches

- `files.get_files`: returns `_file_store(request)`; the `.service` reach for
  `LocalFileStore` goes. `get_upload_limit` goes; the router passes no
  `max_bytes` and the service's own ceiling (built from `web_upload_max_mb` in
  `main`) applies — same number, one place.
- `turns.commands_router`: `sdk_commands` becomes the only source of the CLI's
  reported names (`MetaSlashCommands(ctx.meta)` is always bound by `web_deps`);
  `get_runner` stays only for learning's `reset_owned`/extensions' `reset_all`.
  `events_after`'s `deps.db` fallback goes.
- `routes(deps)` in the eight module routers and `extensions.wiring.
  {connectors_routes, plugins_routes, drafts_routes}` go; `LegacyOAuthRefresher`
  goes if its only caller was `extensions/compose.py` (verify at edit time).
- `CuratorService(learning=)` goes.
- `core.app_context`: `MessageDownloader` goes (D2); `AppContext.db` goes (D3);
  the comment on `LearningBundle` that mentions `web/deps.py` and the
  `runner.AgentRunner` head is rewritten. `LaneWorkspace`'s `resolver is None`
  branches go and `resolver` becomes required (R-7): they existed for the
  legacy `gateway.telegram` entry point, and leaving them keeps the 4a bug they
  describe (a non-owner's photo in the owner's inbox) one mis-wire away.
- `core/session_keys.py::thread_owner` goes with its `__all__` re-exports in
  `modules/sessions/domain/keys.py` (no call site in `src`; R-7), and its two
  tests are trimmed (rule 4).
- Extensions, after `LegacyOAuthRefresher` and the `extensions/compose.py` caller
  go (R-7): the `.engine` properties on `SqlOauthTokenRepository` /
  `SqlExtensionRepository` marked "deleted with it", the `home`+store positional
  compat shape in `oauth_client.py:227-232`, and `secret_key.bind` /
  `_FALLBACK_BOX` — the last one is checked, not assumed: it goes only if no
  store still calls `secret_box()` after the shim callers are gone.

### D8. Tests: three rules, one table

1. **A test of a shim is deleted** — it asserted that path A is path B, and
   path A no longer exists.
2. **A behaviour test driven through a legacy constructor is ported** to the
   module object the shim built (`SessionRouter(store)` for `SessionRouter(db,
   cfg)`, `TurnService` + ports for `AgentRunner`, `JobRepository(engine)` for
   `JobStore(db)`, `build_hermes_server(ports)` for `tools.make_handlers(...)`,
   the telegram services for `gateway.telegram.handle_event`, …). **It is
   deleted instead only when a test under `tests/modules/` already pins the same
   behaviour** — named in the table, so nothing is lost by assertion rather than
   by assumption. The ported test keeps its docstring and its assertions; only
   the setup changes.
3. **An alias-only import is rewritten** (`claude_hermes.config` →
   `claude_hermes.core.config`, `claude_hermes.paths` → `core.workspace`,
   `extensions.catalog` → `modules.extensions.infrastructure.catalog_repository`,
   …). `monkeypatch.setattr(legacy_module, …)` retargets to the module the alias
   pointed at — which is the same object, so nothing changes in what is patched.

`tests/web_helpers.py` (the legacy web harness: `Database`, `AgentRunner`,
`JobStore`, `ExtensionStore`, `acl`) is rewritten over `http.deps.WebDeps` and
real module objects on the test engine, or replaced by the harness the
`tests/modules/*/test_*_router.py` files already use — whichever leaves fewer
lines; the table names the choice per consumer.

4. **A test broken by a SOURCE deletion (D4–D7) is ported in the same step**
   (B-2). These do not import a legacy root, so the grep that built the
   inventory missed them: `grep -rlE "OwnerStore|MetaOwnerRepository|OWNER_KEY|
   get_upload_limit|webhook import .*routes|thread_owner" tests` →
   `tests/modules/identity/test_identity_{access_control,domain,users}.py` (the
   mirror tests go, the rest run on the table-only `AccessControl`),
   `tests/modules/files/test_files_router.py` (`test_the_upload_ceiling_is_its_
   own_provider` moves to `test_files_service.py`, which already exercises
   `max_bytes`), `tests/modules/telegram/test_telegram_webhook.py` (13 tests on
   the shim `routes(deps)` — the webhook door's security tests, ported onto
   `router` + the shell, NOT deleted: `test_telegram_router.py` covers ~40%),
   `tests/modules/sessions/test_sessions_policies.py` and
   `tests/test_core_session_keys.py` (`thread_owner`, R-7).

Deletion is reported **per test, not per file** (S-3): each Phase T agent emits
`deleted <file>::<test> -> <twin file>::<twin test>` and Phase V diffs that list
against `git show HEAD:<file> | grep "def test_"`.

The per-file table is in "Test inventory" below. Partition rule for the parallel
phase: **no two agents touch the same test file**, and the source changes land
first so every agent runs its own files green against the final `src`.

### D9. Full import-linter contracts

`pyproject.toml`'s `[tool.importlinter]` becomes:

```toml
[[tool.importlinter.contracts]]
name = "composition roots -> modules -> integrations -> core"
type = "layers"
layers = [
    "claude_hermes.main : claude_hermes.http : claude_hermes.workers",
    "claude_hermes.modules",
    "claude_hermes.integrations",
    "claude_hermes.core",
]
ignore_imports = ["claude_hermes.core.db.registry -> claude_hermes.modules.**"]
```

`:` and not `|`: in import-linter 2.x `a | b` are INDEPENDENT siblings (may not
import each other) and `a : b` are siblings that may. The three roots do import
each other — `main` imports `http.app` and `workers.tasks`, `workers.taskiq_app`
imports `main` lazily — and that is legitimate: they are one composition layer.

plus the unchanged `independence` contract over `claude_hermes.modules.*`, the
nine per-module `layers` contracts, "domain layers are pure" (framework list
only — the legacy names go), "application layers are framework-free" and
"presentation layers hold no persistence or transport". The three `forbidden`
contracts whose lists were the legacy roots are replaced by the layers contract
above: it forbids every edge they forbade (core → anything above it,
integrations → modules and roots, modules → roots) and also the two they did
not (`workers → main`? — `taskiq_app` imports `main` lazily and legitimately:
both are roots, same layer, allowed; `modules → workers`, now forbidden). The
`core.db.registry → modules.**` ignore stays with its comment. `lint-imports`
already runs in `make lint` and in `.github/workflows/deploy.yml`.

### D10. Dead code, and comments that describe the shim era

After the deletions, vulture at 60% over the whole tree, and every hit reviewed by
hand (a Protocol's method is "unused" to vulture and is not dead). The three
current hits are fixed. Then `grep -rn "shim\|legacy\|Step 5\|Phase C\|until Step"
src/` and each hit rewritten or deleted by one rule: **a docstring may say when
something moved (that is history and helps a reader of `git log`); it may not
describe a shim, a fallback or a "today" that no longer exists.** Mentions of
"Wave N" that only date a design stay.

### D11. Scope guard

Not in Step 5: per-user quotas, memory routes per user, the Telegram reply
re-send after an agent restart, the presenter binding on a busy lane, the reap
window (all noted as debts on the decision page). No behaviour change on the
wire is allowed in this step; a behaviour change found necessary is a separate
commit with its own note.

## Phases

### Phase S — source (one agent, first)

Everything in D1–D7, D9, D10 on `src/` and `pyproject.toml`, in this order:
`git rm` the shims; `git mv` the four live files (D2) and edit them; `main.py`
(D3); identity (D4); learning port (D5); `http/deps.py` (D6); providers (D7);
contracts (D9); vulture + comment sweep (D10); then the harness (conftest
`engine` fixture, `web_helpers.py`, `ext_helpers.py`) and the rule-4 tests under
`tests/modules` (B-2). Exit criteria (S-1): `ruff check src tests`,
`lint-imports` green, `python -c "import claude_hermes.main, claude_hermes.http.app,
claude_hermes.workers.taskiq_app"`, and **`./.venv/Scripts/python.exe -m pytest
tests/modules -q` green** — the whole module tree, so no Phase T agent inherits a
broken tree. (`tests/modules/*/test_*_shims.py` are deleted here, they are rule 1.)

### Phase T — tests (N agents in parallel, disjoint file sets)

The table's partitions, one agent each. Each agent ports/deletes/rewrites its
files per D8, runs exactly those files, and reports per file: ported / deleted
(with the modern test named) / rewritten. An agent that finds a behaviour with
no modern pin and no cheap port writes the port; it does not delete.

### Phase V — verification (me)

Full suite; `ruff check src tests scripts`; `lint-imports`; the deliberate
`modules/turns → modules/sessions/infrastructure` import fails and is reverted;
vitest unchanged (no SPA change); `grep` for legacy roots over `src tests scripts
docs` hits only history (`log.md`, plans); the acceptance greps (`print(`,
`logging.getLogger`, `aiosqlite`, `sqlite3`); live smoke: `build_app` + one web
turn + one Telegram-shaped intake through the new `AiogramDownloader` + a claim
on a fresh DB (no `meta` row written) + `/api/skills` for two users + a curator
sweep — the 4b smoke script re-pointed at the new paths.

### Phase D — docs and wiki (me)

`docs/llm-wiki`: `components/{web,gateway,cron,core-runtime,learning,
extensions}.md` evidence paths and the shim passages; `decisions/modules-and-ports.md`
("shims until Step 5" → closed); `decisions/multi-user-isolation.md` debts list;
`contracts/state-db.md` (the mirror is gone; `meta.owner_user_id` inert);
`quality/testing.md` (the harness); `operations.md`; `system.md`; `index.md`;
`log.md` entry. `README.md` (layout section), `docs/deploy.md` (six mentions;
the upgrade note). Module READMEs (each has a shim/bundle section), `workers/README.md`,
new `http/README.md`. Re-fingerprint, validate, receipt.

## Test inventory

99 files import a legacy root (`grep -rlE "claude_hermes\.(acl|config|db|…|web)\b"
tests --include=*.py`); `tests/conftest.py` is clean. Five more reach a legacy
path only through `tests/web_helpers.py` (`test_web_{isolation,routines,skills,
voice,wiki}.py`) and need no edit of their own.

### Harness first (Phase S, with the source)

- `tests/conftest.py` gains an `engine(test_database)` fixture, promoted from
  `tests/modules/identity/test_identity_users.py` — the pattern every
  `Database.open()`-only test (c2 below) switches to.
- `tests/web_helpers.py` (300 lines, used by 22 files) is ported in place: its
  five legacy imports become `core.config.Config`, the `engine` fixture,
  `SqlExtensionRepository(engine)` + `SqlOauthTokenRepository(engine)`,
  `modules.turns.domain.value_objects._attachment_wire`, `http.app.build_web_app`
  and `http.deps.{WebDeps, PluginSlashSources, extensions_view, thread_guard}`.
  `AccessControl` is built without the mirror (D4). `WebDeps(...)` loses the six
  dropped fields (D6); `FakeLearningScopes` stays and is what `learning=` gets.
- `tests/ext_helpers.py` drops its `Database`/`ExtensionStore` branches and
  takes a bare engine.

### (a) Shim-only — delete (5 files + 2 mixed)

`tests/modules/learning/test_learning_shims.py`, `tests/modules/scheduler/
test_scheduler_shims.py`, `tests/modules/sessions/test_sessions_shims.py`,
`tests/modules/telegram/test_telegram_shims.py`, `tests/test_filestore.py` (its
three behaviours live in `tests/modules/files/test_files_service.py::
test_put_upload_lands_in_inbox_with_a_safe_name` / `test_resolve_refuses_paths_
outside_agent_home`). Mixed: `tests/modules/extensions/test_extensions_catalog_
location.py` loses `test_the_legacy_name_is_the_same_module_object`; `tests/
modules/turns/test_turns_bus_and_shims.py` loses its last two tests and is
renamed `test_turns_bus.py`.

### (b) Behaviour through a legacy constructor — port or delete-with-a-named-twin

| File | Lines | Action | Modern twin (for a delete) |
|---|---|---|---|
| `test_tools.py` | 209 | move `test_memory_save_is_gone`, `test_session_search_handler_finds` to `tests/modules/turns/test_turns_tool_gates.py`; delete the rest | `test_turns_tool_gates.py`, `tests/modules/learning/test_learning_skill_admin.py` |
| `test_ask_user_tool.py` | 154 | port the option-count / cap / timeout body tests over `build_hermes_server` + `AskService`; delete the gate test | `test_turns_tool_gates.py::test_ask_user_never_exists_outside_a_private_lane`, `test_turns_ask_service.py` |
| `test_send_document.py` | 262 | port the ten refusal cases over `make_send_document`; delete gate + containment | `test_turns_tool_gates.py::test_send_document_needs_a_turn_and_somewhere_to_send`, `test_turns_workspace.py::test_send_document_is_contained_by_the_lane_owners_root` |
| `test_costs.py` | 23 | port over `TurnCostRepository(engine)` + the `cost_report` handler | — |
| `test_ext_manage.py` | 248 | port draft / probe / list / disable / remove over `ExtensionAdminService`; delete the four with twins | `tests/modules/extensions/test_extensions_admin_service.py`, `test_turns_tool_gates.py::test_ext_manage_*` |
| `test_ext_store.py`, `test_tokens.py` | 94, 27 | port over `SqlExtensionRepository(engine)` / `SqlOauthTokenRepository(engine)` | — |
| `test_compose.py`, `test_drafts.py`, `test_manage.py`, `test_oauth.py` | 226, 213, 89, 192 | port over `ComposeService`, `PgDrafts`, `ExtensionService`, `oauth_client` (the shim built exactly these) | partial: `test_extensions_ports.py`, `test_extensions_drafts_cli.py` |
| `test_cron_store.py` | 112 | delete | `tests/modules/scheduler/test_scheduler_repository.py`, `test_scheduler_claiming.py` |
| `test_cron_ticker.py` | 218 | port `test_delivery_lane_reproduces_the_topic_or_chat_key` (the only behavioural pin of which chat a routine delivers into), `test_delivery_failure_does_not_stop_other_jobs`, `test_a_run_is_emitted_as_a_cron_turn_on_the_delivery_lane`, `test_a_failed_run_is_emitted_with_its_status`, `test_the_cron_turn_and_the_delivery_lane_carry_the_job_owner`, and `…runs_its_extras_on_the_same_clock…` if untwinned; delete the rest (B-4) | `test_scheduler_claiming.py`, `test_scheduler_service.py` |
| `test_cron_executor.py` | 169 | port `test_cron_sees_an_in_scope_server` over `ComposeService`; delete the rest | `test_scheduler_executor.py` (8), `test_scheduler_claiming.py::test_execute_job_runs_records_and_releases` |
| `test_cron_tool.py` | 154 | delete | `test_scheduler_tool.py`, `test_scheduler_service.py::test_create_opens_a_topic…`, `test_scheduler_policy.py` |
| `test_runner.py` | 579 | port `test_agent_tool_is_allowed_so_refuter_is_reachable`, `test_review_fork_cannot_spawn_subagents`, `test_tool_output_is_truncated_to_config`, `test_turn_end_carries_the_last_calls_context_usage`, `test_usage_without_a_window_still_carries_the_tokens` over `TurnService`/`SdkTurnExecutor`; delete the rest | `tests/modules/turns/test_turns_service.py` (23), `test_turns_execution.py`, `test_turns_workspace.py`, `test_turns_observation.py` |
| `test_runner_attachments.py` | 154 | port: `_build_prompt`/`_compose_text` from `modules.turns.domain.value_objects`, the turn through `TurnService` | `test_turns_execution.py::test_attachments_are_re_resolved_against_the_rows_lane` (one case) |
| `test_runner_cron_context.py` | 153 | delete after confirming `test_a_fresh_delivery_injects_again` has a twin | `test_turns_service.py::test_the_cron_report_is_quoted…`, `…is_not_reinjected`, `…job_lookup_failure…` |
| `test_runner_extensions.py` | 85 | port (two option-composition tests) | — |
| `test_reset_all.py` | 66 | delete | `test_turns_service.py::test_reset_all_does_not_take_the_lane_lock`, `…reset_owned…`, `test_sessions_repositories.py::test_reset_all_empties_every_lane`, `test_sessions_services.py::test_reset_and_reset_all_go_through_the_router` |
| `test_sessions.py` | 167 | **c2 rewrite, not a delete** (B-3): keep the two `reset_owned` tests (the only real-DB pin of the `owner_id IS NULL` belt — every other pin is a fake) and the three route-window tests (`test_route_daily_reset_at_4am`, `test_sessions_are_eternal_by_default`, …; nothing under `tests/modules` tests `daily_reset_hour`); delete only the key-building tests | `tests/test_core_session_keys.py` (keys only) |
| `test_db.py` | 165 | delete | `test_sessions_repositories.py` (14), `test_turns_repository.py`, `test_files_service.py`, `test_identity_users.py` |
| `test_import_sqlite.py` | 93 | port: facade reads → the repositories | — |
| `test_turn_bus.py` | 115 | port `test_to_wire_shape`, `test_a_cron_delivery_does_not_disturb_the_live_turn` into `test_turns_bus.py`; delete the rest | `test_turns_bus.py`, `test_turns_snapshots.py` |
| `test_review.py` | 421 | port: imports + `db.upsert_session` → `SessionRepository(engine)`; assertions unchanged | partial: `test_learning_ports.py` |
| `test_gateway.py` | 217 | port `test_denied_user_is_never_titled`, `test_unmentioned_group_message_is_never_titled` (B-4), and `test_chunk_text_*` / `test_thread_lane_*` if untwinned; delete the rest | `tests/modules/telegram/test_telegram_routing.py` (14), `test_telegram_infrastructure.py` |
| `test_approve_command.py`, `test_secret_command.py`, `test_attachments_intake.py` | 182, 103, 119 | delete | `test_telegram_commands.py` (same six + first four), `test_telegram_intake.py` (271 lines, all seven) |
| `test_titles.py` | 173 | port `test_spawn_titles_and_renames_the_topic`, `test_the_topic_name_is_also_the_web_threads_title` over the moved `Titler`; delete the rest | `test_sessions_title_generator.py`, `test_sessions_services.py::test_autotitle_*` |
| `test_acl.py` | 45 | delete | `test_identity_access_control.py` (four 1:1 twins) |
| `test_web_app.py` | 338 | port the app-shell half (SPA fallback, `/healthz`, error shape, 400-not-422, OpenAPI) over `http.app`; delete the auth half | `tests/modules/identity/test_identity_router.py` (15) |

### (c) Mechanical rewrite (59 files)

- **c1, alias/re-export only (39):** the mapping the classification produced
  (`config → core.config`, `paths → core.workspace`, `options →
  integrations.claude_sdk.options`, `guard → …guards`, `prompt →
  modules.turns.domain.prompt`, `wiki → memory wiki_store + domain.constants`,
  `hooks → memory turn_hooks`, `learning.backup → core.snapshots`,
  `learning.archive → …skill_archive`, `learning.prompts → …domain.prompts`,
  `learning.usage → domain.usage + infrastructure.usage_sidecar`,
  `learning.observe → core.events + turns.application.observation`,
  `learning.{curator,loop,nudges,library} → the services`, `gateway.voice →
  integrations.openai.voice`, `gateway.{events,topics,rich,presenter} → telegram`,
  `gateway.questions → turns ask_broker + value_objects`, `cron.{scan,schedule} →
  scheduler domain`, `extensions.{catalog,drafts,install,oauth,plugins,probe,
  approvals} → the infrastructure modules`, `extensions.secrets → domain +
  pg_secrets`, `extensions.usercatalog → core.workspace.user_catalog_dir`,
  `web.auth → identity domain`, `web.threads → sessions policies + schemas`,
  `web.turns → turns sse`, `web.routines → scheduler router`). String patch
  targets move too (`test_plugin_components.py:147`).
  Files: `test_archive test_ask_broker test_ask_callback test_attachments
  test_backup test_catalog test_cron_scan test_cron_schedule test_curator
  test_curator_pass test_guard test_install test_learning_prompts
  test_learning_usage test_options test_paths_config test_plugin_components
  test_presenter test_probe test_prompt test_rich test_skill_library test_topics
  test_usercatalog test_voice test_wiki test_wiki_hooks test_web_auth
  test_web_threads test_web_turns test_web_commands test_web_connectors
  test_web_drafts test_web_plugins test_web_files`, `tests/modules/learning/
  test_learning_{ports,queue,scopes}.py`, `tests/modules/memory/test_memory_lint.py`.
  `test_attachments.py` retargets to `modules.telegram.infrastructure.attachments`
  (D2). `tests/test_logsafe.py` (if present) retargets to `core.logsafe`.
- **c2, `Database` only as an engine factory (12):** `Database.open()/.engine/
  .close()` → the `engine` fixture. `tests/modules/extensions/test_extensions_
  {drafts_cli,ports}.py`, `tests/modules/scheduler/test_scheduler_{claiming,
  repository,tool}.py`, `tests/modules/sessions/test_sessions_repositories.py`,
  `test_approvals.py`, `test_secrets.py`, `test_import_data_dir.py`,
  `test_workers_tasks.py`, `test_learning_loop.py`, `test_nudges.py`.
- **c3, `build_web_app` via `web_helpers` only (8):** the eight
  `tests/modules/*/test_*_router.py` — one import line each.

### Partitions for Phase T (disjoint; the harness is already ported in S)

| Agent | Files |
|---|---|
| T1 web | `test_web_app test_web_auth test_web_threads test_web_turns test_web_commands test_web_connectors test_web_drafts test_web_plugins test_web_files`, the eight c3 router tests (`test_files_router.py` also loses `test_the_upload_ceiling_is_its_own_provider`, moved to `test_files_service.py`), `tests/modules/turns/test_turns_bus_and_shims.py → test_turns_bus.py` (+ the two ports from `test_turn_bus.py`), `test_turn_bus.py`; runs also the five no-edit consumers |
| T2 turns tools | `test_tools test_ask_user_tool test_send_document test_costs test_ask_broker test_ask_callback test_prompt test_options test_guard`, `tests/modules/turns/test_turns_tool_gates.py` (receives two tests) |
| T3 runner & sessions | `test_runner test_runner_attachments test_runner_cron_context test_runner_extensions test_reset_all test_sessions test_db test_import_sqlite test_import_data_dir test_workers_tasks test_core_session_keys`, `tests/modules/sessions/test_sessions_{repositories,policies}.py` |
| T4 extensions | `test_ext_manage test_ext_store test_tokens test_compose test_drafts test_manage test_oauth test_catalog test_install test_plugin_components test_probe test_usercatalog test_approvals test_secrets`, `tests/modules/extensions/test_extensions_{catalog_location,drafts_cli,ports}.py` |
| T5 scheduler & telegram & identity | `test_cron_tool test_cron_store test_cron_ticker test_cron_executor test_cron_scan test_cron_schedule`, `tests/modules/scheduler/test_scheduler_{claiming,repository,tool}.py`, `test_gateway test_approve_command test_secret_command test_attachments_intake test_attachments test_titles test_presenter test_rich test_topics test_voice`, `tests/modules/telegram/test_telegram_webhook.py` (rule 4), `test_acl.py`, `tests/modules/identity/test_identity_{access_control,domain,users}.py` (rule 4, if Phase S left anything) |
| T6 learning & memory & core | `test_archive test_backup test_curator test_curator_pass test_learning_prompts test_learning_usage test_skill_library test_learning_loop test_nudges test_review test_paths_config test_wiki test_wiki_hooks test_filestore`, `tests/modules/learning/test_learning_{ports,queue,scopes}.py`, `tests/modules/memory/test_memory_lint.py`, `tests/test_logsafe.py` |

The four `test_*_shims.py` files and the harness are Phase S's (they must be gone
or green before `pytest tests/modules` can pass there).

## Advisor amendments (plan pass)

| # | Finding | Folded into |
|---|---|---|
| B-1 | `\|` in a layers contract means independent siblings; `main`, `http`, `workers` import each other | D9: `:` delimiter |
| B-2 | inventory built from "imports a legacy root" misses `tests/modules` files broken by the source deletions | D8 rule 4; Phase S owns the harness and the rule-4 identity/files tests; T1/T3/T5 partitions |
| B-3 | `test_sessions.py` holds the only real-DB pin of the `reset_owned` NULL belt and the only `daily_reset_hour` tests | table row: c2 rewrite, keep five |
| B-4 | `test_cron_ticker.py` / `test_gateway.py` have five + two untwinned tests | table rows: port them |
| R-1 | post-Step-5 the owner exists only as a `users` row; a downgrade past 0005 unclaims the deploy | D4: deploy.md sentence + 0005 docstring |
| R-2 | "the SAME `SdkTitleGenerator`" is not reachable from `run()` | D2: `run()` builds its own; stateless |
| R-3 | `Titler` must drop all three sessions imports; declare its collaborators as Protocols | D2 |
| R-4 | `safe_name` and `lane_token` share one regex; `contracts.py` holds shapes only | D2: `core/naming.py` with both, files re-exports |
| R-5 | `WebDeps.usage`/`logs_dir` properties would raise after the drop | D6 |
| R-6 | mount condition on `learning` differs from `library` only in a harness | D6: harness sets `learning=` unconditionally |
| R-7 | `LaneWorkspace` fallback, `thread_owner`, extensions' compat shapes, `secret_key.bind` | D7 |
| R-8 | core as the back door between modules: invariants only | D2, Phase V |
| S-1 | Phase S exit = `pytest tests/modules` green | Phase S |
| S-2 | say `http/` is a deliberate fourth root | D2 |
| S-3 | deletions reported per test | D8 |

## Risks named

- **A behaviour pinned only by a legacy test is lost.** D8's rule 2 requires the
  modern pin to be NAMED before a deletion; Phase V greps the deleted files'
  test names against the report.
- **`main.py` edits collide** if Phase S is split. It is not split: one agent,
  one file history.
- **The owner mirror had a reader nobody grepped** (an ops script, the smoke
  scripts, `deploy.md` §7's SQL). Grep says: `deploy.md:259,519` (prose), the
  4a/4b smoke scripts in scratchpad (not in the repo). Both docs lines are
  rewritten in Phase D.
- **`safe_name` in core changes an import path files' tests use.** They import
  `modules.files.domain.naming.safe_name`, which keeps re-exporting it; no test
  changes for that.
- **`http` as a package name shadows nothing**: absolute imports inside the
  package resolve `http` to the stdlib as before (`claude_hermes.http` is the
  only way to reach ours), and nothing in the tree does `import http`.
- **Prompt cache.** `tools.py` never held a description; `hermes_server.py` is
  untouched. Phase V diffs the tool schema JSON before/after on a fixed port set.

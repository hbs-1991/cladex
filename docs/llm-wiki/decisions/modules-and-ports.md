---
id: decision.modules-and-ports
kind: decision
status: current
summary: Ten vertical-slice modules (content since 2026-09-28) with layered packages, cross-module needs as consumer-side Protocol ports wired in one composition root, per-module Depends providers over the bundle; since Step 5 no shims and one layers contract (roots over modules over integrations over core).
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:5d0ca03a5e26157184ade88375fb2a1e7dd424f55cfa68d7ea258cefb025399f
evidence:
  - path: docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md
  - path: docs/superpowers/plans/2026-09-15-wave2-modules.md
  - path: docs/superpowers/plans/2026-09-15-wave2b-apirouter.md
  - path: pyproject.toml
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/http/deps.py
  - path: src/claude_hermes/http/app.py
  - path: src/claude_hermes/http/README.md
  - path: src/claude_hermes/core/naming.py
  - path: docs/superpowers/plans/2026-09-18-step5-remove-shims.md
  - path: src/claude_hermes/core/di.py
  - path: src/claude_hermes/core/auth.py
  - path: src/claude_hermes/modules/memory/presentation/dependencies.py
  - path: src/claude_hermes/modules/memory/presentation/schemas.py
  - path: src/claude_hermes/modules/sessions/presentation/dependencies.py
  - path: tests/modules/memory/test_memory_router.py
  - path: src/claude_hermes/modules/turns/application/ports.py
  - path: src/claude_hermes/modules/turns/README.md
  - path: src/claude_hermes/modules/sessions/application/ports.py
  - path: src/claude_hermes/modules/sessions/domain/models.py
  - path: src/claude_hermes/modules/scheduler/application/ports.py
  - path: src/claude_hermes/modules/extensions/wiring.py
  - path: src/claude_hermes/core/db/migrations/versions/0002_threads_has_turns.py
  - path: src/claude_hermes/modules/telegram/infrastructure/titler.py
  - path: src/claude_hermes/modules/telegram/infrastructure/attachments.py
relations:
  - decision.postgres-and-modular-core
  - system.overview
  - quality.invariants
  - component.core-runtime
  - component.web
  - quality.testing
---
# Modules and consumer-side ports

Wave 1 gave the process a `core/` kernel — one engine, Alembic, structlog, an
`ApplicationException` hierarchy ([[decision.postgres-and-modular-core]]). Wave 2
spends that on the rest of the tree: every file under `src/claude_hermes/` now
belongs to `core/`, `integrations/`, `workers/`, `http/` (since Step 5) or one of
nine modules — ten since the content plan (2026-09-28) — and the edges between them
are declared instead of ambient.

## The decision

**Nine vertical slices, four layers each.** `identity`, `sessions`, `turns`,
`files`, `scheduler`, `extensions`, `memory`, `learning`, `telegram`, and since
2026-09-28 `content` ([[component.content]]) — each with
`domain / application / infrastructure / presentation` and its own `README.md`
(purpose, layout, ports consumed and provided, routes, tools, invariants, known
debts). import-linter enforces the layering per module (`presentation |
infrastructure` > `application` > `domain`), keeps `application` free of
`fastapi`/`starlette`/`aiogram`/`httpx`/`sqlalchemy`/`claude_agent_sdk` and
`presentation` free of persistence and aiogram, and keeps `domain` pure. Fourteen
contracts since Step 5 (below), fifteen with `content layers`, all green, one `ignore_imports`: `core.db.registry ->
claude_hermes.modules.**`, because Alembic needs every ORM model in one metadata
object and the models live with their modules.

**Cross-module needs are consumer-side ports.** A module that needs something
from another declares a `typing.Protocol` in its own `application/ports.py`, named
for the capability and not the provider — `SessionLane`, `ThreadRegistry`,
`FilePublisher`, `WikiLintPort` — and receives an object that satisfies it at
composition. Structural typing does the matching, so the provider never learns it
is one. A module imports only stdlib, third-party, `core.*`, `integrations.*` and
its own packages: never a sibling — and since Step 5 there is no legacy path to
reach one through. `core/` is the one legal back door between modules, for
**invariants** only (`core/naming.py`), never services.

**One composition root.** `main.build_app() -> AppContext` (`core/app_context.py`)
builds every object and plugs them into each other's ports. `AppContext` is
headless by construction — no bot, no token — which is what makes it testable and
what lets a web-only deployment exist; `run()` adds the Telegram half through
`build_telegram_gateway(ctx)`. The `runner._x = y` attribute-injection era ended
here: `TurnService` takes its collaborators as keyword arguments. Where a
consumer's Protocol and a provider's method disagree about argument shape, a tiny
adapter in `core/app_context.py` bridges them — and only that; an adapter that
decides anything belongs in a module.

The content plan is the worked example of two modules meeting only here. The
scheduler consumes a `RoutineTools` port (tools lent to a routine run) that content
satisfies; content consumes a `Publisher` port (`describe`, `wake_if_idle`) that
`core/app_context.py`'s `SchedulerPublisher` satisfies over `SchedulerService`;
turns consumes a `ContentPlanPort` that content's tool object satisfies, its name,
description and schema declared once by content and carried on the port (the
`ext_manage` pattern). Neither of the three imports another. The first two ports
form a cycle, so one edge is late-bound: the adapter is built unbound and
`build_app` sets `.scheduler` once the service exists. The plan's v2 adds a
fourth module on the same terms: content consumes `ApprovalCards` (`send`,
`edit`, `ensure_topic`), which telegram's `TelegramApprovalCards` satisfies once
`build_delivery` has a Bot, and telegram consumes `ContentDecisionsPort`
(`approve_from_card`, `redraft_from_reply`, `is_card_topic`), which content's
`ContentCardDecisions` satisfies — both handed over in `main.py`, the card sender
through a late-bound holder like `deliver_document`.

`http/deps.py` is the same idea for HTTP: `web_deps(ctx)` is the web channel's view
of an `AppContext`, and `build_web_app` assembles the routers from the module
`presentation/router.py` files (turns before threads, or
`/api/threads/{id}/messages` is shadowed by `/api/threads/{id}`).

## Depends and providers

Wave 2b finished the presentation layer: the routers are `APIRouter`s and a
handler's collaborators arrive through `Depends` instead of a bundle captured
when the route list was built ([[component.web]]).

**The bundle lives on `app.state.deps`.** `build_web_app` puts the `WebDeps` it
was handed there before including the first router, and `core/di.py` is the whole
bridge: `bundle(request)` returns it, and `provide(name)` returns a dependency
callable reading that attribute. `core/` may not import `http/`, so the
bundle stays **duck-typed** — a structural contract, the same trade the ports make
everywhere else; `core/di.py` names it by its `http/deps.py` path and knows
nothing more about it.

**One typed provider per collaborator, in the module.**
`modules/<m>/presentation/dependencies.py` declares `def get_threads(request) ->
ThreadService` over `provide("threads")`, and a handler writes `threads:
ThreadService = Depends(get_threads)`. The providers are built **once at module
scope**, because FastAPI keys `dependency_overrides` by the callable object and
`provide("threads")` called twice returns two callables an override could not both
reach. A module reads only the bundle attributes its README already listed — the
old "deps attributes read" list became a provider → bundle attribute → type table,
which is now the readable form of that contract. Extensions is the one module with
a single provider for eighteen collaborators (`get_extensions -> ExtensionsDeps`),
because the *bundle attribute* its three routers read is one view object.

**A missing attribute is a programming error, not a feature switch.** `provide`
raises `ApplicationException(503, "unavailable", …)`, and that 503 is unreachable
in a correctly assembled app: mounting is the shell's decision
(`mount(module, enabled=deps.wiki_root is not None)`), so a deployment without a
wiki has no wiki routes and the `/api/*` catch-all answers the 404 the tests pin.
`required=False` is for the collaborators whose `None` is a documented answer the
handler renders itself — voice, the ask broker, the Mini App key.

**Tests swap a collaborator by overriding the provider.** That is the reason
`Depends` exists here at all: `app.dependency_overrides[get_graph] = lambda: fake`
replaces one object on a real app, where the old shape forced a test to rebuild
the whole bundle ([[quality.testing]]).

**Who is asking comes from `core/auth.py`.** `CurrentUser(user_id, is_owner)` is a
frozen dataclass; `current_user` reads what `CurrentUserMiddleware` stamped and
`require_owner` raises `Forbidden("owner only")`. Route-level
`dependencies=[Depends(require_owner)]` is solved before any parameter provider,
which is the position `owner_only(request)` held as every owner-only handler's
first line. No handler under `modules/` reads `request.state` any more; the one
place left that does is the lane guard on the bundle itself
(`http/deps.thread_guard`), which is a per-request callable by design.

**Dict wire shapes stay the contract.** Request bodies became Pydantic models in
`presentation/schemas.py` — the only file in a module where `pydantic` may be
imported — but *responses* are still the dicts `to_wire` builds, and no route
declares a `response_model`. The frozen shapes are the contract (spec req. 36) and
a model would be a second copy of them, free to drift. The models are also
deliberately lenient: `extra="ignore"`, fields `Any`/`Optional` with defaults, and
the handler keeps the check that produces its own status and `code`, because a
Pydantic 422 must never replace a status a test asserts. Malformed bodies land on
one `RequestValidationError` handler that answers **400 `bad_request`**, the code
the hand-written handlers gave and the one `client.ts` branches on. With the
routers typed, the OpenAPI document is back on at `/api/openapi.json` and
`/api/docs` — behind the session cookie, since neither path is public.

## What this cost, deliberately

**Shims at every old import path, from Wave 2 until Step 5.** `runner.py`,
`tools.py`, `turnbus.py`, `sessions.py`, `acl.py`, `filestore.py`, `wiki.py`,
`hooks.py`, `guard.py`, `prompt.py`, `cron/*`, `gateway/*`, `extensions/*`,
`learning/*` and `web/*` kept resolving. Three kinds: an *alias shim* (`sys.modules[__name__] =
<module>`) for a file that moved whole, so a test that monkeypatches an attribute
on the old name still reaches the object; a *re-export shim* for a file that
split; an *adapter shim* for a class whose constructor changed
(`SessionRouter(db, cfg)`, `TurnBus(db)`, `JobStore(db)`, `LocalFileStore(db,
home)`, `AgentRunner(cfg, home, db, router, wiki, bus=)`). Phase C emptied the
adapter shims of behaviour — old *signatures* plus a handful of `Database`-facade
ports — and `db.py` itself became a delegating facade with no SQL in it. The
alternative was rewriting every call site and every test in the same commit;
this way the suite proved the move at each step, and Step 5 (below) deleted the
whole surface in one commit once nothing but `main.py` and the tests imported it.

**`threads.has_turns`, a denormalization** (Alembic `0002`, boolean, not null,
default false, backfilled from `turn_events`). `list_threads` used to join
`turn_events` to decide whether a lane had ever spoken — the one cross-module SQL
join left after the split, and sessions may not read turns' table. `upsert_thread(
…, *, talked: bool = False)` sets the flag and never clears it; the runner's
turn-start touch and the cron delivery pass `talked=True`, while thread creation
from the web and job-topic creation do not. A denormalized column is the price of
the boundary, and it is cheap because the flag is monotonic.

**Duck-typed router `deps`.** Wave 2 kept the `routes(deps) -> list[Route]` shape
and Wave 2b replaced it with `APIRouter` + `Depends` (above), but `deps` itself is
still any object carrying the attribute names a router reads — now read per
request off `app.state.deps` rather than closed over. Every router but identity's
kept a two-line `routes(deps)` returning `list(router.routes)` in the old shape
until Step 5 deleted them with the `web/*.py` shims that were their reason.

## Step 5 (2026-09-18): shims removed

Commit `12a3460`, plan `docs/superpowers/plans/2026-09-18-step5-remove-shims.md`.
The fifteen top-level legacy modules (`acl config db filestore guard hooks logsafe
options paths prompt runner sessions tools turnbus wiki`) and the five packages
(`cron extensions gateway learning web`) went in one commit: nothing outside
`main.py` and the tests imported them, and a half-removed surface would have kept
`sys.modules` patching alive. `src/claude_hermes/` is now `main.py`, `core/`,
`integrations/`, `modules/`, `workers/` and `http/` — nothing else. No wire
change, no migration, no config key, no prompt-cache miss.

**The contracts as they stand** (`pyproject.toml`, fourteen — fifteen since
the content plan added `content layers`). One **layers**
contract, `claude_hermes.main : claude_hermes.http : claude_hermes.workers` >
`claude_hermes.modules` > `claude_hermes.integrations` > `claude_hermes.core`,
with the one `ignore_imports` (`core.db.registry -> modules.**`). The colon
matters: in import-linter 2.x `a | b` are independent siblings, `a : b` are
siblings that may import each other, and the three roots do (`main` imports
`http.app` and `workers.tasks`; `workers.taskiq_app` imports `main` lazily) —
they are one composition layer. That contract forbids every edge the three old
`forbidden` contracts forbade (core → anything above it, integrations → modules
and roots, modules → roots) plus `modules → workers`, which they did not. Beside
it: `modules are independent of each other`, the nine (now ten) per-module `layers`
contracts (`presentation | infrastructure` > `application` > `domain`), `domain
layers are pure`, `application layers are framework-free`, `presentation layers
hold no persistence or transport`. A deliberately added `modules/turns →
modules/sessions/infrastructure` import fails three of them (verified by hand,
not committed). `lint-imports` runs in `make lint` and in the deploy workflow.

**`http/` is a deliberate fourth composition root.** The spec put the FastAPI
assembly "in `core/` (or `main.py`)"; `core/` may not import a module and
`build_web_app` imports twelve routers (seventeen now), and `main.py` is already the process
root. So the HTTP assembly got the standing the queue assembly has
(`workers/taskiq_app.py`): a package beside `workers/` that imports downwards
only. `web/{app,deps}.py` moved there whole; `web/errors.py` folded into
`http/app.py` (`error_response` was its only surviving reader; `ApiError` went —
every router aliases `ApplicationException`). The acceptance criterion
enumerated `core/ integrations/ workers/ modules/`; `http/` is the addition, and
this page is where it is said. No `sys.path` mutation in the repo puts
`src/claude_hermes` itself on the path, so the stdlib `http` is never shadowed.

**`core/naming.py`: the back-door rule.** Telegram's attachment downloader
(`modules/telegram/infrastructure/attachments.py::AiogramDownloader`) applies
files' `safe_name` to a downloaded document before files ever sees it, and
telegram may not import files. `safe_name` and `lane_token` derive from one
regex (`_UNSAFE`) — the files docstring says they move together "or old files
stop resolving" — so all three moved to `core/naming.py`, and
`modules/files/domain/naming.py` re-exports them so its callers and tests are
untouched. Not a naming callable injected into the downloader: a containment
rule must not be a composition-root choice. The rule this sets: **core is the
legal back door between modules for invariants only, never services**, and what
`core` gains is reviewed against it. The other live code at a legacy path moved
the same way — `Titler` into telegram's `infrastructure/titler.py` with its three
collaborators as telegram's own Protocols (`TitleGeneratorPort`,
`LaneBindingPort`, `ThreadTitleWriterPort`; every `modules.sessions` import gone),
`logsafe.py` into `core/` (`core/logging.py` imports it, so a `core → top-level`
edge no contract named became `core → core`).

**The owner mirror went, and the door is one-way.** `OwnerStore`,
`MetaOwnerRepository`, `AccessControl(owner_mirror=)` and the two mirror branches
(`owner()` materialising a row from `meta.owner_user_id`, `admitted()` trusting
the key) are deleted; a claim writes one `users` row and nothing into `meta`.
Safe now and not in 4a because migration `0005` materialises the owner row from
the meta key at upgrade time, so every volume reaching Step 5 through `alembic
upgrade head` has the row; the state the mirror guarded (key present, table
empty) is reachable only by hand-truncating `users`. A stale meta row on a live
volume is inert. Since nothing writes the key any more, a deploy claimed after
Step 5 has its owner **only** as a `users` row: `alembic downgrade` below `0005`
followed by an upgrade would yield an unclaimed deploy the next private DM could
claim. Never downgrade a claimed deploy past `0005`; recovery is `INSERT INTO
users (id, role, workspace_dir, …) VALUES (<id>, 'owner', '', …)`
([[decision.multi-user-isolation]], [[contract.state-db]]).

**What went in the modules with the shims.** `routes(deps)` in the eight routers
and `extensions.wiring.{connectors,plugins,drafts}_routes`; `WebDeps.{db,
job_store, ext_store, library, skills_dir, sidecar}` (the skills router mounts
on `learning`); files' `get_upload_limit` (the service's own ceiling applies);
the commands router's in-process runner fallback (`sdk_commands` over the meta
key is the only source); `events_after`'s `deps.db` fallback;
`core.app_context.MessageDownloader` and `AppContext.db`; `LaneWorkspace`'s
`resolver is None` branches (`LaneWorkspace(lane_dir, resolver)` requires it —
the branch kept a non-owner's photo one mis-wire from the owner's inbox);
`core.session_keys.thread_owner`; `MemoryBundle.lint`; `CuratorService(learning=)`;
learning's `PromptCacheInvalidator.reset_all` (its port says `reset_owned`;
extensions' port keeps `reset_all`, an install really does change every prefix);
extensions' `LegacyOAuthRefresher`, `oauth_client.ensure_fresh`, `secret_key.bind`
and the repositories' `.engine` properties (`wiring.build` binds
`OAuthService(wire=oauth_client.http_wire, …)`).

**Tests.** 99 legacy-importing files were ported to the module object the shim
built (`SessionRouter(store)`, `TurnService` + ports, `JobRepository(engine)`,
`build_hermes_server`, the telegram services) or deleted with a named twin **per
test** under `tests/modules/`; `tests/web_helpers.py` runs over `http.deps.WebDeps`
and real module objects, `tests/conftest.py` gained an `engine` fixture; 1585
pass ([[quality.testing]]). Live smoke: a claim writes no meta row, `/api/skills`
and the curator sweep are per user, `AiogramDownloader` contains
`../../etc/passwd` to `42_etc_passwd`, a web turn for user 2 runs in `users/2/`.

## What stays in process memory until Wave 3

The boundary is drawn, but the transport behind three seams is still the process
itself, so this deployment is one worker by construction:

- `turns`: `AskBroker` (every open `ask_user` question) and `InProcessPublisher`
  (every SSE listener). A second replica would not see a button press raised on
  the first.
- `scheduler`: `CronTicker` is an asyncio loop, and `claim_due` is `due` inside
  one transaction — at-most-once holds through reschedule-before-run in a single
  process, not through a lease.
- `learning`: the review pass and the curator pass now go through
  `core.tasks.TaskQueue` — an `asyncio.Task` in this process inline, a worker in
  redis mode ([[decision.redis-taskiq-scaling]]). Before Wave 3a they were
  `asyncio.create_task`,
  single-flight in-process only.
- `extensions`: `PendingStates` (the OAuth flow between Connect and the callback)
  and the refresh lock.

Wave 3 replaces these with Redis and taskiq. Each is named in the owning module's
README under its own debt row, so the work is findable from the code.

## Consequences

- A new capability is a module, not a layer: its tables, its rules, its router
  and its adapters ship together, and what it needs from elsewhere is visible as
  a Protocol in one file.
- Prompt-cache and security invariants moved *verbatim* with their comments, and
  are listed as untouchables in [[quality.invariants]] — the move was allowed to
  change structure, never bytes the model reads or a check the agent must not
  pass.
- The frozen surfaces stayed frozen: route paths, wire shapes, error `code`
  strings, meta keys, session-key formats, `mcp__hermes__*` tool names and
  `cron:<id>` lanes.

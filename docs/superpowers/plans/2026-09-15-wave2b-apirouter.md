# Wave 2b — `APIRouter` + `Depends` + Pydantic schemas + OpenAPI

Status: plan, 2026-09-15. Follows Wave 2 (`2026-09-15-wave2-modules.md`, committed
as 947d904..b48a243). Spec: `docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`
(req. 5, 6, 7, 31, 36).

## Goal

Every module's `presentation/` becomes a real FastAPI presentation layer: a
module-level `router: APIRouter`, handlers that take their collaborators through
`Depends`, request bodies as Pydantic models in `presentation/schemas.py`, and an
OpenAPI document served from the app. The wire contract does not move: every
path, method, status, JSON shape and error `code` that `tests/test_web_*.py` pin
today stays byte-identical, and those tests pass **without modification** (import
and fixture names excepted).

Out of scope: Step 3 (Redis/taskiq/MinIO), Step 4 (users), Step 5 (shim removal).
`web/deps.py` and the `web/*.py` shims survive this wave.

## Locked Decisions

- **Where the bundle lives.** `build_web_app(deps)` stores the `WebDeps` on
  `app.state.deps`. `core/di.py` (new) exposes `bundle(request) -> Any` (returns
  `request.app.state.deps`) and `provide(name: str, *, required: bool = True)`
  which returns a dependency callable reading `getattr(bundle, name)`; a missing
  required attribute raises `core.exceptions.ApplicationException(503,
  "unavailable", ...)` — the shell never mounts a router whose bundle attribute is
  `None`, so this is a programming-error guard, not a feature switch.
- **Per-module providers.** `modules/<m>/presentation/dependencies.py` declares
  one typed provider per collaborator (`def get_threads(request) -> ThreadService`),
  built from `core.di.provide`. Handlers write `threads: ThreadService =
  Depends(get_threads)`. A module reads only the bundle attributes its README
  already lists; the README's "deps attributes read" section becomes
  "dependencies" and names the provider functions.
- **Current user.** `core/auth.py` gains `CurrentUser(user_id: int, is_owner:
  bool)` (frozen dataclass), `current_user(request) -> CurrentUser` (reads
  `request.state`, raises `Unauthorized` if the middleware did not run) and
  `require_owner(user = Depends(current_user)) -> CurrentUser` (raises
  `Forbidden("owner only")`). `modules/identity/domain/models.CurrentUser` becomes
  an alias of core's. Every `owner_only(request)` / `request.state.is_owner` in a
  router is replaced by these two dependencies. Error messages stay what the
  tests assert (`"owner only"` / `"forbidden"` per router — check each test).
- **Request bodies.** Every `await request.json()` becomes a Pydantic
  `BaseModel` parameter defined in that module's `presentation/schemas.py`
  (`model_config = ConfigDict(extra="ignore")`). Field leniency is preserved:
  where the old handler tolerated a missing or wrongly-typed field and answered
  with its own `code`, the model field is `Optional`/`Any` with a default and the
  handler keeps its check — a Pydantic 422 must never replace a status/code a
  test asserts. Query params become typed parameters with the same defaults and
  clamps (`limit = min(int(...), 200)` → `limit: int = Query(50, le=200)` only
  if the old code rejected `>200`; if it clamped, keep the clamp in the handler).
- **Validation errors.** The shell registers a `RequestValidationError` handler
  rendering `{"error": {"code": "bad_request", "message": <first error as
  "loc: msg">}}` with status **400**, matching what the hand-written handlers
  answered for malformed bodies. `web/errors.error_response` is reused.
- **Responses.** `to_wire` functions stay and keep returning dicts; handlers
  return `JSONResponse` or plain dicts (FastAPI serialises). No `response_model`
  this wave unless the shape is trivially a dataclass already — frozen dict
  shapes are the contract and a model would be a second copy of them. SSE keeps
  `StreamingResponse`. Uploads use `UploadFile = File(...)`.
- **Route order.** `build_web_app` mounts routers with `app.include_router` in
  the same order as today (turns before threads; the three extensions routers
  are order-free), then the `/api/{rest:path}` 404 catch-all, then the SPA
  `Mount`. Conditional mounting stays in the shell with the same conditions
  (`deps.threads is not None`, `deps.wiki_root is not None`, …). Path converters:
  `{id:path}` stays `{id:path}`; `{id:int}` becomes `id: int`.
- **Errors.** Handlers raise `core.exceptions.*` or `ApplicationException(status,
  code, message)` exactly as now; `HTTPException` is not introduced. The one
  `ApplicationException` handler and the new validation handler are the only
  handlers.
- **OpenAPI on.** `openapi_url="/api/openapi.json"`, `docs_url="/api/docs"`,
  `redoc_url=None`. Both paths sit under `CurrentUserMiddleware` (not in
  `PUBLIC_PATHS`), so a login is required. Each router carries `tags=[<module>]`;
  every operation gets a stable `operation_id` (`<module>_<snake_verb_noun>`,
  e.g. `sessions_list_threads`). `tests/test_web_app.py` asserts the document
  lists every frozen `/api/*` path.
- **Mixed mode during the fan-out.** Foundation teaches the shell to mount a
  module either way: `presentation.router` (APIRouter) if the module exports it,
  else the legacy `routes(deps)` list. After Compose only the router form remains
  and the `routes(deps)` branch is deleted.
- **Legacy `web/*.py` shims** keep `routes(deps)` but return `list(router.routes)`
  from the module (`APIRoute` has `.path` and `.methods`, which is all the shim
  tests read). `web/deps.WebDeps` field names are unchanged.
- **One `ext_manage` definition.** `modules/extensions/presentation/agent_tool.py`
  becomes the only source of the tool name, description and schema; the root
  passes them to turns through `core.app_context.ExtensionAdminTool`
  (`name`, `description`, `schema`, `handle`) and `hermes_server.py` reads them
  from the port object. The description text is a prompt-cache invariant and
  moves byte-identically; `tests/test_ext_manage_guidance.py` and
  `tests/test_tools.py` pin it.
- **Untouchables** (Wave 2 header still applies): prompt-cache and security
  invariants; `_admit` with `chat_type="web"`; Mini App cookie flags;
  `/api/auth/session` 404 on non-local origin; the threads paging predicate runs
  in Python after the SQL LIMIT; wiki/skills/files `resolve` containment checks.

## Global Constraints

- Checks: `.venv/Scripts/python.exe -m pytest <files> -q -p no:cacheprovider`
  (test PostgreSQL at 127.0.0.1:55432, `make test-db`), `.venv/Scripts/python.exe
  -m ruff check <paths>`, `.venv/Scripts/lint-imports` (16 contracts must stay
  kept; `modules.*.application` still may not import fastapi/starlette).
- `tests/test_web_*.py` pass unmodified except import/fixture names. A test that
  starts failing is a contract break, not a test to edit.
- `web/src/api/client.ts` is the frontend contract; read it before changing a
  handler's response.
- No `print()`, no `logging.getLogger`, no `HTTPException`, no Pydantic outside
  `presentation/schemas.py`.
- Every touched module README: replace the "deps attributes read" section with a
  "Dependencies" section (provider → bundle attribute → type) and list the
  router's `operation_id`s.

## Phases

### Phase A — Foundation (serial, one agent)

Owns: `core/di.py` (new), `core/auth.py` (add `CurrentUser`, `current_user`,
`require_owner`), `modules/identity/domain/models.py` (alias), `web/app.py`
(state.deps, mixed-mode mounting, validation handler, OpenAPI on), `web/errors.py`
(only if needed), `modules/memory/presentation/{router,dependencies,schemas}.py`
+ `modules/memory/README.md` (the reference conversion), `tests/test_web_app.py`
(validation-shape test, openapi test skeleton listing memory's two paths for now),
`tests/modules/memory/test_memory_router.py` (new: `app.dependency_overrides`
swaps the wiki service — the reason Depends exists).
Verification: `tests/test_web_*.py`, full suite, ruff, lint-imports.

### Phase B — Conversion streams (parallel, disjoint files)

Each stream owns its modules' `presentation/**`, README, and may add
`tests/modules/<m>/test_<m>_router.py` (unique basenames). Each reads the memory
module first as the template. Nothing outside the listed modules is touched.

- **B1 — identity + sessions.** identity: `auth_routes()` → `router`;
  `PUBLIC_PATHS` stays exported; `LOCAL_HOSTS`/`is_local_origin` unchanged;
  login/webapp bodies as models with the exact error behaviour the tests pin
  (400/401/404 codes). sessions: 7 routes, `owned_thread` becomes a dependency
  taking `CurrentUser`; `PATCH` body model with `title: str = ""` and the
  handler's own "title is required" check kept.
- **B2 — turns.** `router.py` (5), `commands_router.py` (1), `voice_router.py`
  (2), `sse.py`. Turn POST body model; attachments ids list; SSE unchanged;
  voice transcribe reads the raw body (`audio/` content type — no model).
- **B3 — files + scheduler + learning.** files: upload via `UploadFile`, download
  unchanged (`FileResponse`), `owned` via sessions-shaped `ThreadVisibility`
  provider. scheduler: 9 routes, `{id:int}` → `id: int`, create/edit body models
  with the exact `create_job` message strings preserved; `presentation/tool.py`
  untouched. learning: 8 routes, no DELETE, `If-Match`/etag header handling
  unchanged, 409 body `{error:{code,message,content,etag}}` unchanged (it is a
  custom `JSONResponse`, not the shared handler — keep it so).
- **B4 — extensions + telegram.** connectors (10), plugins (6), drafts (5):
  the `ExtensionsDeps` view stays the bundle these routers read
  (`provide("extensions")` then attributes); `REDIRECT_ON_401` path constant
  unchanged; the OAuth callback stays a redirect. telegram `webhook.py`: one
  POST, body is the raw update dict (`dict[str, Any]` model-free), mounted only
  in webhook mode as today.

### Phase C — Compose (serial, one agent)

Owns: `web/app.py` (delete the `routes(deps)` branch; include every router in
order; OpenAPI tags), `web/{auth,threads,turns,files,routines,skills,wiki,voice,
commands,connectors,plugins,drafts}.py` shims (`routes(deps)` → module
`router.routes`), `core/app_context.py` + `main.py` + `modules/turns/infrastructure/hermes_server.py`
+ `modules/extensions/presentation/agent_tool.py` (single `ext_manage`
definition through the port), `tests/web_helpers.py`, `tests/test_web_app.py`
(openapi test now lists every frozen path), `tests/test_main.py` if the
AppContext shape changes.
Verification: full suite, ruff, lint-imports, and `grep -rn "request.json()"
src/claude_hermes/modules` must be empty except telegram's webhook.

### Phase D — Advisor (read-only), Phase E — Fix, Phase F — Docs & Gate

Advisor checks: wire parity per router against `git show HEAD:<path>` and
`client.ts`; status/code parity for every error branch; `current_user`/
`require_owner` used wherever the old code checked `request.state`; no Pydantic
outside `presentation/schemas.py`; no 422 leaking (grep tests for 422; run the
suite); OpenAPI document lists every path; `ext_manage` description
byte-identical; lint-imports; full suite.
Docs & Gate: READMEs' Dependencies sections; `docs/llm-wiki/components/web.md`
(routers, dependencies, OpenAPI), `decisions/modules-and-ports.md` (Depends
section), `quality/testing.md` (`dependency_overrides` pattern), `index.md`;
`README.md` (mention `/api/docs` after login); fingerprint + validate through the
container; full gate.

## Open Questions (defaulted)

1. **Response models now or later?** Default: later (Step 5), dicts stay the
   contract.
2. **OpenAPI visibility.** Default: login required, not owner-only.
3. **`WebDeps` vs `AppContext` on `app.state`.** Default: `WebDeps` — the tests
   build it directly and its field names are the documented bundle.

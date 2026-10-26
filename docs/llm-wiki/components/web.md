---
id: component.web
kind: component
status: current
summary: The CladeX web channel — TurnBus over the channel bus and snapshot store, the FastAPI app with one APIRouter per module and OpenAPI behind login, Login-Widget and Mini App auth, REST/SSE routers (202 + turn_id, SSE seq watermark), the FileStore and the React SPA with its Calendar.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:bb1d57706cd2214a0f296f0eefd6f5a1ea83da7b20f56f1ae9497cb0cc843696
evidence:
  - path: src/claude_hermes/modules/turns/application/bus.py
  - path: src/claude_hermes/modules/turns/application/launcher.py
  - path: src/claude_hermes/modules/turns/infrastructure/snapshots.py
  - path: src/claude_hermes/modules/turns/infrastructure/publishers.py
  - path: src/claude_hermes/modules/turns/presentation/sse.py
  - path: src/claude_hermes/core/eventbus.py
  - path: src/claude_hermes/modules/files/application/service.py
  - path: src/claude_hermes/http/app.py
  - path: src/claude_hermes/modules/identity/presentation/router.py
  - path: src/claude_hermes/modules/identity/presentation/users_router.py
  - path: tests/test_web_users.py
  - path: src/claude_hermes/http/deps.py
  - path: src/claude_hermes/http/README.md
  - path: src/claude_hermes/core/auth.py
  - path: src/claude_hermes/core/di.py
  - path: src/claude_hermes/modules/sessions/presentation/dependencies.py
  - path: src/claude_hermes/modules/turns/presentation/dependencies.py
  - path: src/claude_hermes/modules/extensions/presentation/dependencies.py
  - path: src/claude_hermes/modules/telegram/presentation/webhook.py
  - path: tests/test_web_app.py
  - path: tests/test_web_isolation.py
  - path: src/claude_hermes/modules/identity/presentation/schemas.py
  - path: src/claude_hermes/modules/sessions/presentation/router.py
  - path: src/claude_hermes/modules/turns/presentation/router.py
  - path: src/claude_hermes/modules/files/presentation/router.py
  - path: src/claude_hermes/modules/scheduler/presentation/router.py
  - path: src/claude_hermes/modules/extensions/presentation/routers/connectors.py
  - path: src/claude_hermes/modules/extensions/presentation/routers/plugins.py
  - path: src/claude_hermes/modules/extensions/presentation/routers/drafts.py
  - path: src/claude_hermes/modules/extensions/presentation/routers/secrets.py
  - path: tests/test_web_secrets.py
  - path: src/claude_hermes/modules/learning/presentation/router.py
  - path: src/claude_hermes/modules/learning/presentation/dependencies.py
  - path: src/claude_hermes/modules/sessions/infrastructure/repositories.py
  - path: src/claude_hermes/modules/identity/domain/telegram_proof.py
  - path: src/claude_hermes/modules/identity/application/services.py
  - path: src/claude_hermes/modules/memory/presentation/router.py
  - path: web/src/components/cladex/SettingsDialog.tsx
  - path: web/src/components/cladex/SecretsSection.tsx
  - path: web/src/components/cladex/UsersSection.tsx
  - path: web/src/components/ui/switch.tsx
  - path: web/src/store/members.ts
  - path: web/src/store/index.ts
  - path: web/src/i18n/en.ts
  - path: web/src/components/cladex/ConnectorsTab.tsx
  - path: web/src/components/cladex/PluginCard.tsx
  - path: src/claude_hermes/modules/extensions/presentation/schemas.py
  - path: web/e2e/running.spec.ts
  - path: web/src/store/secrets.ts
  - path: web/e2e/settings.spec.ts
  - path: web/src/components/cladex/UserMenu.tsx
  - path: web/src/components/cladex/PluginsTab.tsx
  - path: web/src/components/cladex/PendingDrafts.tsx
  - path: web/src/components/cladex/DraftDialog.tsx
  - path: web/src/components/cladex/PluginInstallDialog.tsx
  - path: web/src/components/cladex/SkillsTab.tsx
  - path: web/src/pages/PluginPage.tsx
  - path: web/src/pages/SkillPage.tsx
  - path: web/src/pages/Memory.tsx
  - path: web/src/components/cladex/MemoryGraph.tsx
  - path: web/src/lib/memoryGraph.ts
  - path: web/src/lib/markdown.ts
  - path: tests/test_web_wiki.py
  - path: web/src/lib/pluginMenu.ts
  - path: web/e2e/customize.spec.ts
  - path: src/claude_hermes/modules/turns/presentation/voice_router.py
  - path: src/claude_hermes/main.py
  - path: docker-compose.yml
  - path: web/src/api/client.ts
  - path: src/claude_hermes/modules/turns/presentation/commands_router.py
  - path: web/src/components/assistant-ui/elements/composer-voice.tsx
  - path: web/src/components/assistant-ui/elements/composer-context.tsx
  - path: web/src/components/assistant-ui/elements/composer-slash-commands.tsx
  - path: web/src/components/assistant-ui/elements/day-separator.tsx
  - path: web/src/components/cladex/ContextMeter.tsx
  - path: web/src/components/cladex/SlashCommands.tsx
  - path: web/src/components/cladex/AskDialog.tsx
  - path: web/src/components/cladex/ThreadRow.tsx
  - path: web/src/runtime/context-usage.ts
  - path: web/src/runtime/adapters/dictation.ts
  - path: web/src/pages/Routines.tsx
  - path: web/src/pages/Calendar.tsx
  - path: web/src/components/cladex/calendar/CalendarBoard.tsx
  - path: web/src/components/cladex/calendar/PublisherControl.tsx
  - path: web/src/components/cladex/calendar/ContentItemDialog.tsx
  - path: web/src/lib/calendar.ts
  - path: web/src/store/content.ts
  - path: web/e2e/calendar.spec.ts
  - path: src/claude_hermes/modules/content/presentation/router.py
  - path: web/src/telegram.ts
  - path: web/src/app/AuthGate.tsx
  - path: web/src/app/Layout.tsx
  - path: web/src/components/cladex/MobileBar.tsx
  - path: web/e2e/mobile.spec.ts
  - path: web/e2e/dictation.spec.ts
  - path: web/e2e/files.spec.ts
  - path: web/src/components/cladex/FilePanel.tsx
  - path: web/src/components/cladex/FileParts.tsx
  - path: web/src/components/cladex/FileCard.tsx
  - path: web/src/components/cladex/FilePreview.tsx
  - path: web/src/components/cladex/ChatHeader.tsx
  - path: web/src/lib/files.ts
  - path: web/src/components/ui/dialog.tsx
  - path: web/src/pages/Login.tsx
  - path: docker-compose.caddy.yml
  - path: web/src/api/sse.ts
  - path: web/src/runtime/fold.ts
  - path: web/src/runtime/provider.tsx
  - path: web/src/store/events.ts
  - path: web/src/store/ui.ts
  - path: web/src/i18n/index.ts
  - path: web/src/toolkit/index.ts
  - path: web/src/app/routes.tsx
  - path: web/e2e/smoke.spec.ts
  - path: web/e2e/fake-server.ts
  - path: web/playwright.config.ts
  - path: .github/workflows/web.yml
  - path: Dockerfile
  - path: docs/design/DESIGN.md
  - path: docs/design/globals.css
  - path: docs/design/logo/cladex-icon.svg
  - path: docs/design/logo/cladex-logo.svg
  - path: docs/design/logo/cladex-logo-dark.svg
  - path: web/index.html
  - path: web/public/favicon.svg
  - path: web/public/favicon.ico
  - path: web/public/favicon-16x16.png
  - path: web/public/favicon-32x32.png
  - path: web/public/favicon-48x48.png
  - path: web/src/components/cladex/Logo.tsx
  - path: web/src/components/cladex/Logo.test.tsx
  - path: web/src/assets/cladex-icon.svg
  - path: web/src/assets/cladex-logo.svg
  - path: web/scripts/render-mark.mjs
  - path: web/package.json
  - path: src/claude_hermes/modules/turns/presentation/models_router.py
  - path: src/claude_hermes/modules/sessions/presentation/schemas.py
  - path: src/claude_hermes/modules/sessions/application/services.py
  - path: src/claude_hermes/core/model_catalog.py
  - path: web/src/components/cladex/ModelPicker.tsx
  - path: web/src/components/cladex/ModelLabel.tsx
  - path: web/src/store/models.ts
  - path: web/src/lib/modelCommand.ts
  - path: web/src/lib/models.ts
  - path: web/src/runtime/thread-id.ts
  - path: web/src/components/cladex/RoutineForm.tsx
  - path: web/e2e/model.spec.ts
relations:
  - decision.model-choice-is-lane-state
  - component.core-runtime
  - component.gateway
  - component.cron
  - component.content
  - contract.state-db
  - flow.message-turn
  - decision.web-as-a-channel
  - decision.postgres-and-modular-core
  - decision.modules-and-ports
  - decision.multi-user-isolation
---
# Web (CladeX)

A browser client for the same agent, served by the same process. One FastAPI
app on uvicorn runs as an asyncio task next to aiogram's polling (main.py);
the runner, the tools and the prompt learn nothing about HTTP. Off by default
(`web_enabled: false`): with the flag down nothing under `web/` is imported and
the process behaves exactly as it did before Phase 6.

## TurnBus — the seam between the runner and every consumer

`modules/turns/application/bus.py`: one typed
event stream per session lane. The runner (and the cron
ticker) `emit`; the bus persists the durable events into `turn_events`, mirrors
the turn in flight into a `SnapshotStore`, and publishes each event onto that
lane's channel, where the SSE endpoint and the Telegram presenter are subscribed.

Since Wave 3b the consumer is not necessarily in the same process as the emitter,
and the bus is built out of three replaceable pieces rather than one listener
list: `TurnBus(turn_events, ChannelPublisher(events_bus), snapshots)`, where
`events_bus` is `InProcessEventBus` or `RedisEventBus` (`core/eventbus.py`, see
[[component.core-runtime]]) and `snapshots` is `MemorySnapshots` or
`RedisSnapshots`. `snapshot` and `is_running` are therefore **`async`** — the
state may be in Redis — while `subscribe` stays synchronous and a new
**`async ready(lane)`** is the seam a caller that must not miss the first event
awaits.

**Two orderings are what make the stream at-least-once**, and both are written
into the docstrings that own them:

- `emit` records into the snapshot store **before** it publishes;
- `snapshot(lane)` awaits `publisher.ready(lane)` **before** it reads the store.

So after `snapshot()` returns, every event is either in what it returned or still
on its way to the subscription. Nothing is lost; de-duplicating is the SSE
stream's job, below.

`RedisSnapshots` keeps the mirror under `hermes:snap:` — `hermes:snap:<lane>` is a
list of active turn ids in start order, `hermes:snap:<lane>:<turn>` a hash
carrying the turn's latest `seq`, `…:events` the persisted events as JSON and
`…:text` / `…:thinking` the unflushed buffers. Every write is one pipeline that
also `EXPIRE`s every key it touched to `turn_snapshot_ttl_seconds` (3600): that
TTL is the only cleanup for a worker killed between a heartbeat and the reaper,
and the reaper's `clear` removes the keys sooner. `is_running` is deliberately not
a bare `EXISTS` on the lane list — a killed worker can leave an id listed whose
own keys aged out, and `read` skips that turn, so the two must agree. Nothing in
`sessions` reads it either way: "running" is the `turns` row
([[contract.state-db]]).

- `TurnEvent(session_key, turn_id, seq, ts, type, payload, id)`; `to_wire()` is
  both the storage and the wire shape, so a replayed row and a live event are
  indistinguishable to the client.
- `TRANSIENT = {"text.delta", "thinking.delta"}` — deltas reach listeners and the
  snapshot buffer, never the database, so `turn_events` grows per block, not per
  token. The runner asks the SDK for `thinking={"type": "adaptive", "display":
  "summarized"}` because Opus 4.7+ otherwise returns signature-only thinking and
  the web thread would never see the model's reasoning.
- Active-turn state is keyed **lane → turn_id → state** (modules/turns/application/bus.py), not one
  slot per lane: a cron job delivers its report into the owner's private lane
  (`ticker._emit_delivery`) while a live turn may be running there, and a single
  slot let the delivery reset the live turn's `seq`, empty its snapshot and
  report the lane as idle mid-turn.
- `snapshot(key)` returns, for every turn in flight, its persisted events plus
  one synthetic `thinking.delta` and one `text.delta` holding what is not yet
  flushed into a `thinking` / `text.block`; that is what lets a browser reloading mid-turn catch up without
  a re-run. Every event names its own `turn_id`, so two interleaved turns fold
  into two messages.
- `is_running(key)` is the bus's own notion of busy — any turn in flight on the
  lane, as the snapshot store sees it. **No route reads it any more**: since
  Wave 3b `POST /messages` asks the launcher, which asks the `turns` row, and the
  unique partial index on that table is what actually refuses the second turn.
- A listener that raises is logged and skipped, and a failed `append_event` is
  logged rather than raised — an event sink must never be able to kill a turn.

Event types and payload keys are the contract: `turn.start`, `user.message`,
`text.delta`, `text.block`, `thinking.delta`, `thinking`, `tool.call`, `tool.result`, `ask.open`,
`ask.answer`, `file`, `thread.title`, `thread.model`, `turn.end`. See [[flow.message-turn]].
Since the model switcher `turn.start` also carries `model`/`effort` (the pair the
turn ASKED for) and every `turn.end` `model`/`effort`/`fallback_from` (the pair
that ANSWERED — always present, `null` on the synthetic ends and on a turn no
model answered); `thread.model` `{model, effort}` carries a lane's EXPLICIT pair
only, emitted like `thread.title` with `turn_id=""` when a person changes it from
either channel ([[decision.model-choice-is-lane-state]]).

## The app (`http/app.py`)

Since Step 5 the assembly lives in `http/` — a composition-root package beside
`main.py` and `workers/`, not a module: `core/` may not import a module and the
app imports sixteen routers, so the package sits above the modules and imports
downwards only ([[decision.modules-and-ports]]). `web/errors.py` folded into it
(`error_response` is what both exception handlers render; `ApiError` went, every
router raises `ApplicationException`).

The app object is a **`FastAPI`** instance and, since Wave 2b, a real FastAPI
one: `FastAPI(openapi_url="/api/openapi.json", docs_url="/api/docs",
redoc_url=None)`, assembled with `app.include_router` from sixteen module-level
`APIRouter`s. Wave 1 swapped the application class over a Starlette `Route`
list and had to keep OpenAPI switched off, because a schema generated from bare
endpoint functions would have been empty and the docs page would have lied.
Wave 2 moved every handler into its module's `presentation/router.py` but kept
the `routes(deps) -> list[Route]` shape; Wave 2b converted those to `APIRouter`
+ `Depends` and turned the schema back on, and Step 5 deleted the last
`routes(deps)` wrappers ([[decision.modules-and-ports]]).

**The schema is not public.** Neither `/api/openapi.json` nor `/api/docs` is in
`PUBLIC_PATHS`, so both sit behind `CurrentUserMiddleware` and answer 401 until
a session cookie exists — a logged-in user reads the docs, an anonymous one is
told to log in. Each router carries `tags=[<module>]` and every operation a
stable `operation_id` spelled `<module>_<verb_noun>` (`sessions_list_threads`,
`extensions_confirm_plugin_install`, `turns_stream_events`), because a generated
client names its methods after them: an `operation_id` is as frozen as the path
beside it, and each module's README lists its own. `tests/test_web_app.py`
fetches the document and asserts every frozen `/api/*` path is in it.

Two exception handlers are registered, and only two. `ApplicationException`
renders as `{"error": {"code", "message"}}`, so any error in the hierarchy —
including `ApiError`, a subclass with the same `(status, code, message)`
constructor — reaches the browser in one shape and a service deeper in the app
can raise `NotFound` without importing anything web-shaped. `RequestValidationError`
renders as **400** `bad_request` with the first error as `"<loc>: <msg>"`, *not*
FastAPI's 422: every hand-written handler Wave 2b replaced answered `400
bad_request` for a malformed body and `client.ts` branches on that code, so a
schema that started answering 422 would be a wire break. That is also why the
request models are lenient — `extra="ignore"`, fields `Any`/`Optional` with
defaults, the handler keeping its own check — wherever the old handler had a
`code` of its own to answer with. `HTTPException` appears nowhere in
`src/claude_hermes/`.

`build_web_app(ctx_or_deps)` takes an `AppContext` (or a ready `WebDeps`, as the
tests pass), puts the bundle on **`app.state.deps`** before the first router is
included, and mounts, in this order: identity, users, turns (before threads —
`/api/threads/{id}/messages` must match before the generic `/api/threads/{id}`),
threads, files, routines, content, connectors, plugins, drafts, secrets, skills, wiki, voice,
commands, models, telegram's webhook, a catch-all `/api/{rest:path}` 404, **`GET
/healthz`**, and, when a build exists, the SPA at `/` (`SpaStaticFiles` falls
back to `index.html` for client-side routing).

**`GET /healthz` → `200 {"ok": true}`** (Wave 4a) is public, outside `/api/`, and
deliberately shallow: it says "this process is serving HTTP" and asks no database.
It is appended to `app.router.routes` **before** the SPA catch-all would swallow
it, sits outside both middlewares (they guard `/api/*`), and needs no cookie, so a
compose healthcheck or a proxy probe can use it — `docker-compose.prod.yml`
documents the `curl` variant next to the default asyncpg one
([[system.operations]]). Nothing in the app depends on it. **Mounting is the shell's decision**: `mount(module, *,
enabled=…)` is the whole of the policy, and a deployment without threads, a file
store, a scheduler, extensions, a skill library or a wiki on disk simply does not
include that router — its paths fall through to the catch-all and get the 404 the
tests pin. Telegram's webhook asks the module itself (`webhook_enabled(deps.cfg)`
— `False` in polling mode, the default, and a `RuntimeError` at boot for webhook
mode with no secret, rather than a public door anyone may POST to). Webhook mode
is live since Wave 4a ([[component.gateway]]). The routers are imported inside the
function, not at module scope, so `app.py` still loads with one of them stubbed
out.

**Handlers take their collaborators through `Depends`, not through a closure.**
`core/di.py` is the two-function bridge: `bundle(request)` is
`request.app.state.deps`, and `provide(name)` turns a bundle attribute into a
dependency callable. Each module's `presentation/dependencies.py` declares one
typed provider per collaborator (`get_threads -> ThreadService`,
`get_scheduler -> SchedulerService`, `get_extensions -> ExtensionsDeps`) and a
handler writes `threads: ThreadService = Depends(get_threads)`; the providers are
built **once at module scope**, because FastAPI keys `dependency_overrides` by the
callable object. A missing *required* attribute raises `ApplicationException(503,
"unavailable", …)`, which is a programming-error guard rather than a feature
switch — the shell already refused to mount that router — while `required=False`
hands the `None` through for the collaborators whose absence is a documented
answer the handler renders itself (`503 voice_disabled`, an empty `/` menu, a
shut Mini App door). See [[decision.modules-and-ports]] for why the bundle stayed
duck-typed and the responses stayed dicts.

**The shell compresses, because nothing in front of it does.** `GZipMiddleware`
is added *last*, so it is the outermost of the three and sees the SPA bundle the
static mount serves and the refusals the two middlewares below it answer with,
not only what a handler returns. It has to live here rather than in the proxy:
the Caddyfile carries no `encode` (and would be dead config if it did, since
Caddy passes an already-encoded body through), and a deployment behind a tunnel
or on a bare port has no proxy at all. Uncompressed, the SPA entry chunk is
1.4 MB against 430 KB, a Shiki grammar 785 KB against 52 KB, and a thread's
`GET …/events` history is JSON that compresses about tenfold — which is the wait
a person feels on opening a thread. `compresslevel=6`, not the default 9: a
couple of percent of ratio for several times the CPU, on a body built per
request. **`text/event-stream` is excluded** (Starlette's own
`DEFAULT_EXCLUDED_CONTENT_TYPES`) and that exclusion is load-bearing, not
incidental: a gzip stream holds each frame until its window fills, so a
compressed SSE tail would deliver a whole turn in one lump at the end instead of
token by token. `tests/test_web_app.py` pins both halves.

Two more middlewares guard every `/api/` request, and Wave 2 split them along the
question each answers. `TransportPolicyMiddleware` (`http/app.py`) is transport: a
mutation needs an `Origin` exactly equal to `web_public_origin` (403
`bad_origin`) and the per-route content type (415) — JSON everywhere except
`/api/uploads` (`multipart/form-data`) and `/api/voice/transcribe` (`audio/`).
`core.auth.CurrentUserMiddleware` is identity: every route outside the union of
`PUBLIC_PATHS` (identity's `POST /api/auth/telegram`, `POST /api/auth/webapp`,
`GET /api/auth/session`, `GET /api/config`) needs a valid cookie (401 — except
the paths in `REDIRECT_ON_401`, today the OAuth callback, which are browser
navigations and get a 302 to a page that can say so) and it stamps
`request.state.user_id` / `is_owner`. It takes `read_session`, `is_owner` and,
since Wave 4b, `admitted` as callables, so `core/` stays ignorant of cookies,
secrets and the ACL, and it is added *first* so it runs **inside** the transport
check: a forged `Origin` is refused before anyone asks whose session it carries.
**Admission is re-checked on every request** (`admitted(uid)`, bound to
`AccessControl.admitted` — the allowlist and `disabled_at`, read-only, so a
SELECT and not `check()`'s `last_seen_at` UPDATE per call): a valid cookie whose
user has since been taken off the allowlist or disabled gets the same 401 as no
cookie at all, redirect included — a distinct body would tell a removed user
they are removed. Before 4b that cookie could still read; after 4b a logged-in
user can write prompt text into a tree through `/api/skills`, so "admitted at
login" stopped being enough. Verified live: a cookie for a never-admitted user id
answered 401 (`tests/test_web_isolation.py`). `ApiError` raised anywhere in a handler
becomes the one error shape `{"error": {"code", "message"}}` (`errors.py`), by
the same handler that renders every other `ApplicationException`.

**No handler reads `request.state` any more.** `core/auth.py` turns what the
middleware stamped into two dependencies: `current_user(request) -> CurrentUser`
(a frozen `(user_id, is_owner)` dataclass; `Unauthorized` if the middleware did
not run) and `require_owner(user = Depends(current_user))`, which raises
`Forbidden("owner only")`. A handler that needs the id takes
`user: CurrentUser = Depends(current_user)`; a route that only needs the gate
puts `dependencies=[Depends(require_owner)]` in its decorator, and FastAPI solves
**route** dependencies before parameter ones — the position `owner_only(request)`
held as every owner-only handler's first line, so a non-owner is still refused
before the catalog is asked for a manifest or a path is built from a URL segment.
`modules/identity/domain/models.CurrentUser` is an alias of core's, so there is
one class.

## Auth (`modules/identity/`, `core/auth.py`)

Two proofs of a Telegram identity, one admission rule. The **Login Widget**
(`POST /api/auth/telegram`): the data-check string is every field but `hash`,
sorted by key, joined with newlines, HMAC-SHA256 under `sha256(bot_token)`. The
**Mini App** (`POST /api/auth/webapp`, body `{"init_data"}`): the same string
shape over the query-string fields of `Telegram.WebApp.initData` (`user` is
JSON, the id is read out of it), HMAC-SHA256 under
`HMAC-SHA256("WebAppData", bot_token)` — a different key by Telegram's design,
so neither proof replays as the other (`verify_webapp_user`, tested against a
widget signature; it returns the id **and** the decoded `user` object in one
function, so the Mini App login records `display_name`/`username` like the
widget does since 4b — parsing and verification are deliberately not split,
because a standalone parser is the function the next caller reaches for alone
and a display name read from unverified `initData` is attacker-supplied;
`verify_webapp` is the thin id-only wrapper). Both digests (`login_key`, `webapp_key`) are derived in `main.py`
**before** the token is popped from the environment and are the only
token-derived values the web layer ever holds; `deps.webapp_key == b""` shuts
the Mini App door outright. Either way `_admit` then requires
`acl.check(uid, "web") == "allow"`: the chat type is what lets `AccessControl`
CLAIM an unowned deploy, and passing anything but `"private"` makes that
impossible here — ownership is claimed in a Telegram DM, never from a public
hostname. A non-allowlisted user (and every login on a deploy with no owner
yet) is refused with the same `401 login_failed` as a bad signature.

The Mini App's cookie alone is `SameSite=None`: Telegram's web clients frame a
Mini App under `web.telegram.org`, where a Lax cookie never travels, while the
native apps load the page top-level and do not care. CSRF is the Origin check's
job for every session, not SameSite's. The button that opens it is registered
by `main.py` on every boot — `setChatMenuButton` with a `web_app` pointing at
`web_public_origin` — whenever that origin is `https://` and `web_menu_button`
is on, so the Mini App follows the hostname instead of a BotFather step.

On the client, `web/src/telegram.ts` reads `initData` from
`window.Telegram.WebApp` (the bridge script in `index.html`) or, before it has
landed, from the `tgWebAppData` fragment Telegram opens the page with.
`AuthGate` tries that sign-in when `GET /api/me` says anonymous and only then
redirects to `/login`; the login page itself skips the widget inside Telegram
and shows `login.failed` on a refusal, since the widget would only offer the
same account again.

`GET /api/me` returns `{"user_id", "role"}` and, since Wave 4a, the two
additive keys `"display_name"` and `"username"` (both `str`, possibly `""`).
`schemas.user_wire` adds them **only when a profile is passed**: `/api/me` always
passes one — even an empty `Profile()`, so the two keys are never conditionally
absent from that route — while the two login routes pass none, so their bodies
stay byte-identical to what `client.ts` has always parsed. The wire contract
allows additive keys, not surprises. The model switcher follows the same rule:
`/api/me` always carries `"default_model"` and `"default_effort"` (both possibly
`null`), the login bodies still do not — so the SPA types a login answer as
`LoginWire` (`user_id`, `role`) and reads the full `Me` from `GET /api/me` once
the cookie is set (`pages/Login.tsx`, `telegram.ts::loginFromMiniApp`).
`PUT /api/me/model-default` (`identity_set_model_default`, any logged-in user,
their own row) takes `{"model": str|null, "effort": str|null}`, validates it with
`core.model_catalog.validate` (`400 bad_model` / `bad_effort`) and answers the two
keys; `null` clears the default. A default applies to new sessions on the user's
own web and private lanes — never to a live session and never to a shared lane.

**Isolation is tested through the routes, not through the repositories.**
`tests/test_web_isolation.py` drives user A and user B through `make_client` and
asserts that A's threads, thread reads, events, SSE stream, uploads, message
search, routines, drafts and skills are invisible to B via every route that could
serve them, and that B can neither change the model of A's lane nor move it with
B's own personal default ([[decision.multi-user-isolation]]).

`GET /api/config` is public and returns `{"bot_username"}` — the one fact the
login page needs before anyone is logged in. It is served at runtime
(`deps.bot_username`, from `bot.get_me()`) rather than baked into the bundle:
the `webbuild` stage has no idea which bot the image will run.

Telegram never links `localhost`, so on a developer's machine the widget cannot
render. `GET /api/auth/session?t=<token>` (`modules/identity/presentation/router.py`,
`session_link`) takes a token
signed with the same secret the cookie is checked against, sets it as the
HttpOnly cookie and redirects to `/`; it exists only while `web_public_origin`
is `http://localhost[:port]` or `http://127.0.0.1[:port]` (`is_local_origin`) and
answers 404 anywhere else. It grants nothing the token holder did not have.

The session cookie `cladex_session` is `<user_id>.<exp>.<hmac-sha256>` signed
with `CH_WEB_SECRET`; httpOnly, Secure, SameSite=Lax (None from the Mini App
route, above), `web_session_days` long.
Rotating the secret logs everyone out; `main.py` refuses to start with
`web_enabled: true` and no secret rather than signing with an empty key.

## Routers

Seventeen `APIRouter`s in ten modules, each declared at module scope in
`presentation/router.py` (extensions has four under `presentation/routers/`,
identity has a second in `users_router.py`,
turns has `router`, `commands_router`, `voice_router` and `models_router`, telegram has
`webhook.py`), each tagged with its module and each route carrying its
`operation_id`. Request bodies are Pydantic models in that module's
`presentation/schemas.py` — the only file in the module where `pydantic` may be
imported — and the *responses* are still the dicts the `to_wire` functions build:
a `response_model` would be a second copy of a frozen wire shape, so that is
deliberately deferred. What each router reads off the bundle is in its module's
README, as a provider → bundle attribute → type table.

- **`modules/sessions`** — the lane list. `visible()` is the single place that decides
  who may open which lane: a `web:` lane belongs to its `owner_user_id`, a
  private Telegram lane to the user whose chat id it is, `cron:` lanes are
  hidden from everyone, group and topic lanes are the owner's alone.
  `POST /api/threads` mints `web:<user_id>:<uuid4 hex>`; `DELETE` is archival
  (sets `threads.archived_at`). `auto_title` names an untitled thread from its
  first message in the background and emits `thread.title`. Because `visible()`
  is a Python predicate over a SQL page, `GET /api/threads` loops batches until
  it has enough VISIBLE rows — filtering after the `LIMIT` returned an empty
  page and a null cursor whenever a user's only lane sat behind a page of lanes
  they cannot see.
  **A lane's model**: `GET /api/threads/{id}` also carries `model`, `effort` (the
  lane's explicit pair, both `null` when it has none) and `effective_model`,
  `effective_effort` (what its next turn will run, from `SessionService.effective`
  — the bundle's `lane_models`); the list route does not, because a per-row answer
  would be a session read and an owner-default read for every sidebar line.
  `PUT /api/threads/{id}/model` (`sessions_set_thread_model`, declared before the
  generic `{id:path}` routes) takes `{"model": str|null, "effort": str|null}`,
  applies `owned_thread` first (a lane the caller may not see — including, for a
  non-owner, every group lane — is a 404, never a 403), validates (`400 bad_model`
  / `bad_effort`), emits `thread.model`, and answers the four keys. `model: null`
  clears the choice and redirects the live session to the lane's default. An
  effort-only change is sent as the effective model plus the new effort, which
  pins the model.
- **`modules/turns`** — `POST /messages` validates, refuses `409 busy`, resolves
  the attachment ids (where the "not your lane" 400 lives), auto-titles, and then
  **`await turns.start(...)`**: it inserts a `turns` row, enqueues
  `run_turn(turn_id)` and answers `202 {accepted, thread_id, turn_id}` at once
  (Cloudflare cuts requests over 100 s). `turn_id` is the only wire change of
  Wave 3b and it is additive. There is no `asyncio.create_task(run())` any more —
  in redis mode the turn runs in a worker, and the process that took the request
  only watches the lane. The busy check reads the row (`await turns.is_busy`) and
  is a **courtesy**: `LaneBusy` from the insert is the same `409` with the same
  message, so a lane lost between the check and the insert renders a byte-identical
  body. `GET /events?after=` is the plain
  history read; `GET /stream?after=` is SSE — persisted rows after the cursor,
  then the active-turn snapshot, then the live tail, `: ping` every 15 s, each
  frame `id: <turn_events.id or empty>` / `event: turn` / `data: <JSON>`.

  The generator's order is an **invariant**: `subscribe → DB replay → await
  snapshot → drain the queue → yield the snapshot → forward what was drained →
  tail`. The listener is subscribed before the replay, so anything emitted while
  the replay runs is on the queue AND in the snapshot. Until Wave 3b the queue was
  taken aside with no `await` between the drain and the snapshot; an async
  `snapshot` makes that impossible, and in redis mode a delta can be inside the
  coalesced snapshot *and* still in flight to this process. So the stream builds a
  **seq watermark** — `{turn_id: max seq}` over what `snapshot()` returned — and
  the last two stages run every frame through one `covered()` filter: a persisted
  event is dropped by id (`<= cursor` or already replayed), a delta by
  `seq <= watermark[turn_id]`. Deltas only, because a reaper's synthetic
  `turn.end` starts a fresh seq at 1 in a foreign process. Reordering those four
  statements doubles the text in the browser, which is exactly the bug the
  ordering was written against; `tests/test_web_turns.py` and, against a real
  Redis, `tests/modules/turns/test_turns_two_processes.py` both pin it. A reconnect
  is lossless and never duplicates. `POST /cancel` is
  `await turns.cancel(key)` → `409 not_running` when nothing was active;
  `POST /api/asks/{qid}` resolves an open `ask_user` question through the same
  broker a Telegram tap uses — in redis mode a Redis hash, compare-and-deleted by
  whichever process is serving the request, with a nudge on `hermes:ctl` to wake
  the worker that is parked on it.
- **`modules/files`** — `POST /api/uploads` (multipart, capped at `web_upload_max_mb`)
  writes into `inbox/<lane>/`; `GET /api/files/{id}` resolves the row, checks
  containment under `agent_home` and checks lane ownership.
- **`modules/scheduler`** — the cron jobs of [[component.cron]] over REST: list (any
  logged-in user, the caller's own routines), create, pause, resume, run now, run
  history, patch, delete — the mutations owner-only; a non-owner creates routines
  through `cron_manage`. Create goes through `SchedulerService.create_or_raise`, the
  tool's own `create_job` path with an exception instead of a reason string, which
  also writes the `threads` row for the job's `delivery_lane` — the Routine page
  mounts its runtime on that lane as soon as the job exists, and every thread route
  404s on a lane with no row. Create and patch take an optional `model`/`effort`,
  validated here against the offered `models:` (`400 bad_model` / `bad_effort`; the
  pair travels whole), and a prompt that IS `/model` or `/effort` is `400
  bad_prompt`. `GET /api/routines/calendar?from=&to=` (any logged-in user, their
  own routines) answers future `fires`, once-per-routine `dense` markers (a period
  an hour or less) and recorded `runs` in the window; over 62 days is `400
  bad_range` ([[component.cron]]).
- **`modules/content`** — the owner's content plan ([[component.content]]):
  `/api/content*`, every route `require_owner` and scoped to the caller's rows (a
  foreign or unknown id is 404), mounted only when `deps.content is not None`
  (`content_enabled`). With it off the paths fall to the `/api/*` catch-all, and
  that 404 is the SPA's "content disabled" signal. `{id:int}` converters keep
  `/api/content/publisher` from reading as an item id. Errors are the one shell
  shape, `{"error": {"code", "message"}}`, with the service's codes (`bad_channel`,
  `bad_media`, `bad_time`, `locked`, `not_approvable`, `media_missing`,
  `empty_body`, `media_required`, …) — the story that introduced it wrote
  `{"detail": …}`, which no router here answers. The v2 routes add theme approval
  (`/{id}/approve-theme`, `/{id}/unapprove-theme`, `POST /approve-themes` over a
  range), `/{id}/retry-drafting` and `GET/PUT /api/content/drafter`.
- **`modules/extensions` (connectors)** — the Connectors tab's backend, and the only channel that can
  run an OAuth sign-in. Connectors are **per user** since Wave 5: the catalog is
  shared, while the registry row, the secrets and the OAuth token are the caller's
  own. `GET /api/connectors` (any logged-in user) merges catalog and user-catalog
  MCP manifests with the CALLER's registry rows and secrets into the shape
  `connector_to_wire` (`presentation/schemas.py`) defines — catalog entries first,
  then custom, both by label. Every mutation acts on the caller's own row, secrets
  and token and goes through [[component.extensions]]'s `ExtensionService`, the
  same code `ext_manage` runs from chat: `POST /{name}/secrets` (only names the
  manifest declares), `/connect`, `/test`, `/enable`, `/disable`, `/oauth/start`,
  and `DELETE /{name}` — revoke best effort, drop the caller's token row, forget
  every declared credential of theirs (with or without a registry row, so a
  sign-in whose probe failed cannot leave a live token on disk) and, for the owner
  alone, unlink a custom manifest. **One route stays owner-only**, `POST
  /api/connectors`: a custom connector **is** a manifest in the shared
  `user_catalog/`, written through `CatalogWriter.promote` and validated by the
  same `parse`; 409 `name_taken` if the name is already in either catalog.
  `GET /api/connectors/oauth/callback` is the odd route out: a **top-level
  navigation** back from the provider, authenticated by the SameSite=Lax cookie
  plus a single-use state bound to the user who started the flow (`pending.take(
  state, user_id)` refuses anybody else's), answering with a 302 to `/customize/connectors?connected=<name>` or
  `?error=<code>` — which is why a request with no session is redirected there too
  (`REDIRECT_ON_401`, app.py) instead of being shown a JSON 401. Secret and token
  values never appear in a response or a log line.
- **`modules/extensions` (plugins)** — the Plugins tab's backend, over `kind: plugin` catalog entries.
  `GET /api/plugins` merges manifests with the CALLER's registry rows through
  `plugin_to_wire`: `state` is `state_of` (`domain/registry.py`) with `connected`
  spelled `installed` (a plugin is not dialled, it is on disk), and two fields exist
  only here. `present` is the plugin reader's `is_present` — the clone resolves and
  holds a `.claude-plugin/plugin.json` — which is how a row whose directory vanished
  is visible *before* the next turn records `last_error`. `components` is the
  reader's `components(plugin_root)`, read live from the clone on every listing and
  never stored: a listing is a page load and the files are local, so a value read
  from disk cannot go stale (the opposite trade-off from a connector's
  `tool_count`, which would cost a network probe). It is `null` unless the plugin
  is both installed and present. A registry row of the caller's whose manifest left
  the catalog still lists, as `state: "error"` with empty `repo`/`ref`/`subdir`, so
  the only thing that can clear it is reachable. **The clone is shared, the row is
  not**: install, confirm and `DELETE` fetch and remove code every tree loads, so
  they are owner-only; `/enable` and `/disable` write the caller's own row, and
  enabling with no row of one's own **adopts** the shared clone (`404` when there
  is no tree to adopt).

  **Install is two calls, because a plugin brings its own MCP servers.** The CLI
  reads a plugin's `.mcp.json` itself — the SDK's `strict_mcp_config` docstring
  names "plugin-provided servers" among the configurations it would otherwise
  load, and `options.py` leaves that flag at its default — so those servers never
  pass `catalog.parse`, never get a registry row or a probe, and `for_scope`
  cannot drop a broken one. The only moment a person can see them is before the
  plugin is registered. So `POST /{name}/install` fetches (synchronous,
  `timeout = min(cfg.plugin_clone_timeout_seconds, 85.0)` — Cloudflare cuts at
  100 s, and the configured 120 s would kill the request with git still running)
  and answers with the rendered surface (`render_surface`), the servers it found,
  the components and a token that is the full sha256 of that surface
  (`surface_token`); `POST /{name}/install/confirm` sends the token back and only
  then is the owner's row written. A fetched-but-unconfirmed clone is inert,
  because `for_scope` reads only enabled rows. `409 stale_install` when the tree
  changed under the token, `409 not_fetched` when there is nothing on disk.
  A fetch whose `.mcp.json` carries a server that would **run a command** is
  refused outright — `400 local_server`, and the clone is removed with it: a
  remote server is a URL the owner reads on that screen, a local one is an
  arbitrary argv arriving by a route that never passed the catalog's
  `npx`/`uvx`/`python` allowlist. Real plugins do ship these (`discord` and
  `fakechat` in the official marketplace both run `bun run …`), so it is a rule
  and not a hypothetical. Bundling servers is **not** itself a refusal: a declared
  remote server, configured or not, is listed on the surface and installs — see
  `_classify` in [[component.extensions]] for the shape that used to be read as a
  command and refused three shipped plugins.

  `/enable`, `/disable` and `DELETE /{name}` are one call each. `DELETE` removes
  **every owner's** row for the plugin — checked per row for `kind: plugin`, so a
  connector's rows under the same name are never taken — and then the clone, once;
  leaving another user's row would leave them an enabled plugin pointing at a
  directory that is gone. It doubles as Cancel — with no registry row it removes
  the clone the owner read and declined — and checks `valid_name` first, because it
  skips the manifest lookup on purpose and its no-row branch hands the URL segment
  to an rmtree.

  **There is no create route on this router**, and that is the design: a plugin is
  code the SDK loads and runs, so registering one is never one request
  ([[decision.readonly-catalog-boundary]]). The path for a new one is
  draft → approve → install, and the browser's way onto that path is
  `POST /api/drafts` on the drafts router below — it queues a manifest and nothing
  more. Both gates are on the Plugins tab: the approval above the grid, and what the
  fetched clone actually contains on the install dialog.
- **`modules/extensions` (drafts)** — the browser as the approval screen, shared by both tabs
  (`mcp` drafts belong to Connectors, `plugin` drafts to Plugins) — and, since the
  Plugins tab gained "Add plugin", the place a draft can also be *written*.
  `POST /api/drafts` (owner-only, `kind: plugin` only — `400 bad_kind` otherwise)
  takes `{name, label, description, version, repo, ref, subdir, scopes}`, defaults
  `label` to the name, `version` to `"1"` and `scopes` to `[turn, cron]`, and hands
  the result to `drafts.save` — the **same** validator a chat-authored draft goes
  through, so a branch ref or an `ext::` repo is `400 bad_manifest` carrying the
  validator's own sentence to the form that can fix it. `is_repo_entry` is `409
  shadowed` here as well as in `approve`, because filing a draft that can never be
  approved only wastes the next click. It writes to `catalog_drafts`, never to the
  catalog; a re-file under the same name overwrites the queued draft, which is how
  a custom entry's pinned ref gets bumped. Approval is
  two owner-only calls with a content-bound token, exactly the guarantee chat
  gives: `POST /{name}/review` calls `approvals.begin`, which snapshots the draft
  bytes and returns `{surface, token}` — the token is `sha256` of *those* bytes —
  and `POST /{name}/approve` sends the token back so `approvals.confirm` promotes
  the **snapshot**, never whatever is on disk now. A draft rewritten between the
  two calls is `409 stale_draft` and nothing is promoted; a second approve with
  the same token is `409 stale_draft` too, because the snapshot is gone.
  `local_command` is `surface != drafts.describe(m)` — whether
  `describe_with_banner` prepended the LOCAL COMMAND banner — so the dialog draws
  its warning strip from a flag instead of parsing the text and the two cannot
  disagree. `is_repo_entry(name)` is refused `409 shadowed` (the anti-downgrade
  rule), and the post-write read-back through `catalog.get` answers `500
  unverified` **leaving the draft queued**, so a retry is free rather than
  stranding the manifest. `GET /api/drafts` also returns `rejected`
  (`{file, reason}`) — `drafts.pending` drops unparseable files silently, which is
  right for the agent and wrong for the operator, who would otherwise wait forever
  for an approval that cannot appear. `DELETE /api/drafts/{name}` is what chat
  never had: discard, unlinking the draft and any snapshot under both kinds.
  Every `{name}` here is checked against `catalog.valid_name` first: discard is the
  one handler that hands a URL segment straight to code that builds a path from it
  (`approvals.clear` → `draft_path`) and a route param is only guaranteed not to
  contain `/`, so on Windows a backslash name would walk out of `catalog-drafts/`
  into an arbitrary unlink. The catalog's own name rule refuses that and refuses
  nothing a real draft could be called.
  These are routes, not tools, so a prompt-injected turn still cannot enact its
  own draft.
- **`modules/identity` (users)** — the owner's user list, `users_router.py`, and the
  first door either channel has ever had onto admission. Before it, a person was
  admitted because their id sat in `allowed_users` in a file the process reads at
  boot, or because they claimed the deploy; and nothing could take access away at
  all — `disabled_at` had been read since Wave 4a and written by nothing.
  `GET /api/users` is the rows (owner first, then by arrival — the first 100:
  `acl.list_users()` keeps its default `limit=100`) plus every allowlisted id
  without one, marked `source: "config"`, because a list of only the rows would understate who
  can get in. `POST /api/users` writes the row `check` would have written on that
  person's first message — `role='user'`, `workspace_dir=users/<id>`, no profile,
  since Telegram resolves an id to a name and never a @username to an id — which
  admits them in both channels at once with no restart, and creates nothing on disk
  (`WorkspaceResolver.ensure()` still builds the tree lazily).
  `POST /{user_id}/disable` and `/enable` set and clear `disabled_at`; the revoke
  takes effect on the **next request**, because `CurrentUserMiddleware` asks
  `admitted()` per `/api/*` call, so a signed unexpired cookie stops working at once
  rather than at its expiry — and the row, threads, files and workspace are left
  intact, so enabling gives back exactly what was there. Two refusals: `409
  owner_immutable` (an owner who disabled themselves could not undo it from any
  channel — `is_owner` gates every mutation) and `404 not_found` for an id with no
  row, unless it is in `allowed_users`, where the disable *creates* the row already
  disabled, because a row is the only thing that outranks the config file. Owner-only
  on the `APIRouter`, the read included.
  Since migration `0009`, `PATCH /api/users/{user_id}` (`identity_set_user_grants`)
  sets per-person **grants** — `{"shell"?: bool, "learning"?: bool}`, strict JSON
  booleans (`StrictBool`, so `"true"` or `1` is `400 bad_request`), at least one
  present, only the named columns written — and answers `200` with the member;
  `404 not_found` for an id with no row (a `source: "config"` entry included, and
  none is created: a row is an admission), `409 owner_immutable` for the owner.
  Every member on the wire (list, invite, disable/enable, PATCH) carries `shell` and
  `learning` — the EFFECTIVE grants from identity's `Grants.of`, the same rule the
  turn runs on — plus `shell_inherited` / `learning_inherited` (the column is NULL,
  so the value is the `users_shell_enabled` / `users_learning_enabled` default
  showing through); the owner is `true/true` and never inherited, a config entry
  carries the defaults, inherited. The route resets nothing: the person's next turn
  compares its session's shell stamp with the grant and starts a fresh session if
  it moved ([[flow.message-turn]], [[decision.multi-user-isolation]]).
- **`modules/extensions` (secrets)** — Settings → Secrets, and the only route set whose
  **read** is owner-only too: the gate sits on the `APIRouter` rather than per route,
  because a list of what credentials a deployment holds is not a plain user's to see.
  `GET /api/secrets` answers `{name, set, updated_at, used_by[]}` per row and merges
  two sources — the owner's own rows of `extension_secrets` (the table is per user
  since Wave 5, so this is the owner's credentials; a non-owner sets theirs per
  connector on the Connectors tab) and every name the catalog declares — so a
  variable a connector wants and nobody has filled in is a row with `set: false`. It reads `SecretStore.names()`, not `load()`: the ciphertext is never
  opened, which is both why no response can carry a value and why the list still
  answers correctly under a rotated `CH_SECRETS_KEY`. Declared-but-unset **oauth**
  names are left out — those arrive by signing in, never by typing — while an oauth
  token already mirrored into the table lists like any other secret, `used_by` naming
  whose it is. `PUT /api/secrets/{name}` takes `{value}` and applies the domain's own
  rules (`check_name`, `check_value`), so nothing reachable here is a name `/secret`
  would have refused; `DELETE /api/secrets/{name}` forgets one, and a declared name
  stays listed as unset. This is the first way in for a credential **no manifest
  declares yet** — until Wave 5 the only doors were `/secret NAME=value` in a
  Telegram DM and the per-connector form, and neither could list what was stored.
- **`modules/learning`** — the Skills tab's backend over [[component.learning]]'s
  `library.py`; the router owns only the bookkeeping the two channels do
  differently (the snapshot, the patch count, the reset). **Every route is the
  requesting user's own library** since Wave 4b: `dependencies.get_scope`
  resolves `deps.learning.for_user(user.user_id)` (the `LearningScopes`
  registry) and `get_library`/`get_usage`/`get_skills_dir`/`get_logs_dir` read
  off that scope, so a read lists *their* skills, a mutation edits *their* skills,
  and a name from somebody else's library is a plain 404 — the scope is the
  authorization and no route can name another user's tree. There is no
  `require_owner` on the mutations any more (it existed because the one library
  was the owner's; that library is now one of N), and this closes the 4a leak
  where `GET /api/skills` served the owner's library to any logged-in user. The
  bundle attributes it reads are `learning`, `cfg` (`backup_keep`) and `runner`
  (`reset_owned`). The detail adds
  `content`, `etag` and `tree` (the other files with their sizes).
  `PUT /api/skills/{name}` requires the etag and answers **409 `stale_edit`
  carrying the current `content` and `etag`** when it no longer matches — the
  learning loop's review pass writes these same files after most turns, so an
  unguarded Save would silently discard what the agent had just learned, and
  handing the text back turns the conflict into a decision rather than an errand.
  The pre-edit snapshot goes under the `skill_edits` backup kind and is taken
  *inside* `library.write_skill`, through its `before_write` hook: after the etag
  and the frontmatter are known good and before the atomic replace, so a refused
  save cannot spend a slot in the keep-N store. `POST /api/skills` creates,
  `/pin`, `/unpin`, `/archive`, `/restore` do the rest. **Nothing here deletes**
  (PRD P4): archive moves the directory into `.archive/`, restore moves it back,
  and `DELETE /api/skills/{name}` is a 405 that a test pins. Pinning also blocks a
  manual archive, not just the curator's. The invalidation after create / save /
  archive / restore is **`runner.reset_owned(scope.user_id, include_unowned=
  scope.is_owner)`**, not `reset_all` — see the rule below. The SPA's buttons
  follow the API: *New skill* and the edit/archive controls (`SkillsTab.tsx`,
  `SkillPage.tsx`) show for any logged-in user, since the page is always the
  caller's own library, and the Connectors and Plugins tabs follow their routers
  the same way (see Customize below).
- **`modules/memory`** — the Memory page's backend over `agent_home/wiki`, and the only
  read of that tree the browser gets. **Owner-only, both routes**: an allowlisted
  user may chat, but the wiki is where the agent writes down who the owner is,
  what they eat and what they are working on, so it stays with the owner the way
  secrets do. **Read-only, both routes**: the wiki is the one tree whose every
  byte the model owns ([[component.core-runtime]]), and a browser that could write
  it would be a second author — there is no PUT, no POST, and no `reset_all` to
  need.
  `GET /api/wiki/graph` answers `{pages, namespaces, missing}`. The guarantee
  worth naming is that **it is built by scanning the directory, never from
  `WikiStore._NAMESPACES`**: that tuple is what `seed()` creates on a fresh
  volume, not what the wiki *is* — the agent invents folders (`content/` exists on
  the live deployment and is not in it), and a graph assembled from the constant
  would hide whole namespaces from the one screen that exists to show them. Each
  page carries `path` (extension-free, forward slashes), `ns` (the **first**
  segment, so `topics/ai/foo` hangs off `topics` and the graph stays two rings
  deep), `title`/`summary`/`tags`/`updated` from the frontmatter, the
  deduplicated `[[…]]` targets with any `|alias` and `#anchor` stripped, and
  `in_index` — whether `index.md` mentions `<path>.md`. A page the agent wrote and
  forgot to index is **shown and flagged**, not hidden: that drift is exactly what
  the screen is for, and the same reasoning keeps `missing` (link targets with no
  page behind them) in the payload as ghost nodes rather than as an error.
  Frontmatter is parsed with `yaml.safe_load` behind a tolerant splitter — a page
  whose YAML does not parse still appears, titled by its filename stem, because
  the moment the agent writes malformed frontmatter is the moment you most want to
  see the page. `index.md` and `log.md` are bookkeeping and never become nodes;
  any *other* root-level `.md` (a legacy page from before the namespaces) is kept
  under the synthetic namespace `""`, which sorts last on the ring — losing a page
  for sitting one directory too high is the failure this endpoint exists to
  prevent.
  `GET /api/wiki/pages/{path:path}` is the same object plus `content` (the whole
  file, frontmatter included) and `etag` (`sha256` of the bytes — shipped now so
  the editor this page does not have already holds the precondition a write would
  demand). Containment is proved twice: the segment check refuses `..`, an empty
  segment and a drive-qualified path before touching the disk, and an
  `is_relative_to` check on the resolved path refuses anything that escaped anyway
  — a symlink the agent planted, for instance. `index` and `log` 404 here too.
  `deps.wiki_root` is `WikiStore.root`; when it is `None` the shell does not
  include this router at all and the `/api/*` catch-all answers the 404 — the
  same shape the skill library and the scheduler use.
- **`modules/turns` (voice)** — `POST /api/voice/transcribe` and `/speak` over the same
  `OpenAIVoice` the Telegram gateway uses; `503` when no key is configured.
- **`modules/turns` (commands)** — `GET /api/commands` (any logged-in user, the CALLER's
  own list): what `/` in the composer offers. `{"commands": [{name,
  description, source}]}` — the skills in the caller's own tree
  (`get_caller_home`: the `WorkspaceResolver`'s answer for the requesting user,
  `deps.home` when none is bound), then the plugins the caller has enabled, their
  commands and skills namespaced `<plugin>:<x>`, then
  the names the CLI reported in its `init` system message — the meta key
  `sdk_slash_commands`, written by whichever worker ran the last turn and read
  through `sdk_commands`; since Step 5 the only source, the in-process
  `runner.slash_commands` fallback went with the shims; empty before the first
  turn —
  deduplicated by name with the file-backed description winning and `__`
  names dropped. The CLI executes a `/name` prompt sent through `query()` as
  the command (verified live: a project command and a skill both ran in one
  turn; `/compact` is handled by the CLI locally), so the browser sends the
  literal text unchanged — except `/model` and `/effort`, which the CLI would
  run as its own local commands and which `POST /api/threads/{id}/messages`
  refuses with `400 model_command` (the SPA never sends them: it opens the picker).
- **`modules/turns` (models)** — `GET /api/models` (`turns_list_models`, its own
  `models_router.py`, mounted before the catch-all; any logged-in user, the same
  list for everyone): `{"models": [{id, label, window, price: {in, out},
  cache_write, efforts, default_effort}], "deploy_default": {model, effort:
  null}}` — the configured `models:` joined with `core.model_catalog`'s facts, the
  deploy default always present. `window: 0` means unknown and never disables a
  model in the picker; `price` is USD per 1M tokens and `cache_write` the multiple
  of `price.in` a cold re-read costs, which is what the picker's switch estimate is
  computed from in the browser.

### The `reset_all` / `reset_owned` rule

`TurnService.reset_all()` → `SessionRepository.reset_all()` (`DELETE FROM sessions`).
Anything a Customize mutation changes — the toolset, the plugin list, the skill
index — lives in the **cached prompt prefix** ([[decision.prompt-cache-discipline]]),
which a lane resuming an old SDK session never re-reads. A mutation that skips the
reset therefore appears to work and changes nothing until every lane happens to
start a new session.

Since Wave 5 the extensions routers share one invalidator,
`http.deps.prefix_invalidator(runner, deploy_owner)`, with two arities, because
they mean two different things by "the prefix changed". **`invalidate()`** is
`reset_all` and follows a **draft approve** only — the shared catalog, which
every lane's prefix describes. **`invalidate(owner_id)`** is
**`TurnService.reset_owned(owner_id, include_unowned=owner_id == deploy owner)`**
→ `SessionRepository.reset_owned` (`DELETE FROM sessions WHERE owner_id = :o`,
plus `OR owner_id IS NULL` only for the deploy owner) — one person's lanes. It
follows every connector mutation with the caller's id — `connect`, `enable`,
`disable`, `remove`, **and the OAuth callback**, which is a connect and the most
common one, so it owes the same reset the `POST /connect` route does — and a
plugin confirm / enable / disable; a re-fetch of an already-installed plugin and a
plugin `DELETE` call it once for **every owner holding a row**, because the clone
they all load changed or went away under them. Not called for `/test` (a probe installs
nothing) or for a first fetch (no row yet, so nothing loads it). A connector one
user enables reaches only their prefix, so `reset_all` there would throw away
every other user's warm cache for nothing — the NFR-1 regression the per-user
prefix was meant to avoid.

The skills mutations — create / edit / archive / restore, since Wave 4b — make
the same call through learning's own per-request `get_invalidate`, for the same
reason: a skill one user edits changes the index in *their* cached prefix and
nobody else's. The NULL belt exists because a lane bound while the deploy owner
was still `0` wrote NULL, and those lanes are the owner's — only the caller that
*is* the owner may sweep them. Skill pin/unpin resets nothing (they change no text the model sees,
only what the curator and Archive may do).

Two ordering rules, both tested: the reset comes **after** the registry or file
write, never before — a lane that rebinds in between would resurrect the stale
session (the `ext_manage` rule, modules/turns/application/turn_service.py) — and a *refused* mutation resets
nothing. Neither reset takes a lane lock on purpose: a turn in flight writes its
session row back when it ends and so keeps its session, and the reset is for idle
lanes, which is every lane in practice when someone is clicking in Customize.

## FileStore (`modules/files/`)

The port the web layer depends on for anything crossing the turn boundary.
`FileService` (over `FileRepository(engine)` and a `BlobStore`) keeps everything
under the same keys — uploads in `inbox/<lane>/`, files the agent sends in
`outbox/<lane>/` — so the agent's own Read/Glob reach them (PRD NFR-5). Since
Wave 3a the port is **async** and has two implementations: `LocalBlobStore`, and
`S3BlobStore` over a MinIO bucket that also writes the same on-disk tree as a
mirror, so nothing above the port changed and `tar ./data` became a cache rather
than the backup ([[decision.minio-blobs]], [[system.operations]]). Files are
addressed by an opaque `file_id` (uuid4 hex) in the `files` table, never by
path; `files.path` holds an address (`local:<key>` / `s3://<bucket>/<key>`, or a
bare path for a pre-3a row) and `resolve()` runs the one containment body,
`layout.resolve_within`, which raises `PermissionError` for anything outside
`agent_home` — before any download, so a forged or stale row cannot become an
arbitrary file read. `GET /api/files/{id}` stays the only way to read one:
no presigned object URLs, because a presigned URL is the authorization and the
lane check has to happen per request.

## Frontend (`web/`)

### Visual specification and implementation status

The target visual identity is **CladeX**, defined in
`docs/design/DESIGN.md`. It uses the supplied orange-to-blue
ribbon mark, outlined wordmark, solid blue light-theme actions, and light-violet
dark-theme actions. `docs/design/globals.css` mirrors the document's token block;
the three `docs/design/logo/cladex-*.svg` files contain the mark and light/dark
wordmark variants. The Open Channel explorations and the mockups that went
with them have been deleted; the ribbon is the only identity in the repository.

The browser favicon implements the CladeX mark: `web/index.html` links the
square, transparent `web/public/favicon.svg` and an ICO fallback containing
16/32/48px frames. Matching PNG exports live beside them. The favicon retains
the standalone mark's paths and gradients, with a square viewBox and no wordmark.

`web/src/styles/globals.css` carries the same token block plus the app's own
imports, keyframes and variants, so the SPA renders the specification directly.
`components/cladex/Logo.tsx` pairs a native responsive `<img>` ribbon with a
prefiltered wordmark alpha mask using `currentColor` for both themes. The former single 384px
PNG was reduced to about 49px inside a transformed SVG in the sidebar, leaving
strong minification to the browser. The current `scripts/render-mark.mjs`
(`npm run render:mark`, Sharp) first rasterizes the canonical icon SVG at 4x,
then materializes PNGs with Lanczos3 filtering at 49/64/82px and their 2x/3x
densities, plus a 384px fallback. `srcSet` and `sizes` let the browser select
an appropriate image directly; do not put this raster back inside SVG.
The ribbon-only fix left the letters as browser-rasterized SVG paths, so it
did not address the user's stepped text edges. The renderer now also exports
transparent wordmark masks from the original paths, with Lanczos3 filtering.
`Logo` uses CSS `image-set()` at 1x/2x/3x over a `currentColor` background,
preserving theme colours without a baked background or a CSS blur. At 144px
the mask rectangle is 144x38; at 240px it is 240x64. Higher densities use exact
multiples of these rectangles, and the component rounds its CSS height to
match, avoiding another fractional resize of the prefiltered letters.
The artwork's paths and gradients remain unchanged. The component reads the
viewBoxes and placement transform from the SVG sources; the wrapper has one
accessible `CladeX` image label and its artwork is decorative. Sizes follow DESIGN.md §7: 144px for the
complete sidebar logo, 240px on login, 64px for the standalone empty-thread
mark. Component tests cover these image sizes, density candidates, inherited
letter colour and accessible names. Visual comparison
at 1x covered 144px and 240px logos on light and dark backgrounds.

The rename to CladeX is complete down to the identifiers: the session cookie is
`cladex_session`, browser state lives under `cladex.theme` and `cladex.locale`,
the app's own components sit in `web/src/components/cladex/`, and the vendored
registry files mark local edits with `// cladex:`. What keeps the old names is
what a rename cannot reach: the `claude-hermes` repository and `claude_hermes`
package, the `cl-*` CSS prefix, and the live Telegram handle `@your_bot`.

### Application structure

A Vite + React + TypeScript SPA, Tailwind v4 for tokens, shadcn/assistant-ui
elements for chrome. It is a client of the contract above and of nothing else:
every call goes through `src/api/`, and the only stateful path is
events → fold → runtime.

```
/api ──► api/client.ts ──► store/ (zustand) ──► runtime/fold.ts ──► runtime/provider.tsx ──► elements
     └─► api/sse.ts   ──┘                                            └─► toolkit/ (labels, AskCard, FileCard)
```

- **`api/client.ts`** — one `request()` with `credentials: "same-origin"`, the
  JSON content type on mutations and `ApiError(status, code, message)` unwrapped
  from the server's `{"error": {...}}`. `api/sse.ts` `subscribeEvents(id, after,
  onEvent, onState)` reconnects after 1.5 s **from the last persisted id it saw**,
  which is why a dropped stream costs no events and duplicates none.
- **`runtime/fold.ts`** — the one place turn events become messages, so replay
  and live tail cannot drift. Thread state also carries `context` — `{model,
  window, used, input, cacheRead, cacheCreation}` from the latest `turn.end`
  that had `usage` (`used = input + cache_read + cache_creation`), reset when
  `sdk_session_id` changes and left alone by a turn without one. `window` is
  the `context_window` the CLI reported for the model that ran, never a figure
  the browser looks up or assumes, so the meter shows exactly the window the
  pinned CLI gave that model ([[flow.message-turn]]). It reaches
  the composer through one React context (`runtime/context-usage.ts`, provided
  by `CladeXRuntime`): assistant-ui's external-store adapter has no `extras`
  slot, and a second store was not wanted. Ids are derived, never generated: user message
  `<turn_id>:u`, assistant `<turn_id>:a`, tool part = the backend `call_id`,
  ask card `ask:<qid>`, file part `file:<file_id>`. `text.delta` and `thinking.delta`
  accumulate in buffers materialised as trailing provisional parts (reasoning
  ahead of text, the order a response streams them); the following `thinking` /
  `text.block` clears its buffer and pushes the authoritative part, so nothing is
  doubled. `turn.end` stamps `metadata.custom.duration_ms` on the assistant message.
  `ask.open` becomes an `ask_user` tool part (a `human` toolkit entry, hence
  standalone) and `file` a standalone `send_document` part. `foldEvents` is pure:
  `foldEvents(all)` equals folding after each arrival — which means one delta
  rebuilds every message the thread has ever held. assistant-ui caches a
  message's conversion in a `WeakMap` keyed on that object, so fresh objects
  re-convert and re-render the whole thread — every markdown body, every
  highlighted block — twenty times a second while the model reasons, and a smooth
  stream of deltas reached the screen in visible jumps. `reuseMessages(prev, next)`
  hands back the previous object wherever the new fold says the same thing; it is
  cheap because everything a fold reads keeps its event's identity, so only the
  wrappers a fold allocates (the `Date`, the metadata, a call's artifact) are
  walked. `store/events.ts` applies it on every refold.
- **`store/`** — one zustand store composed of twelve slices (`store/index.ts`:
  `threads`, `events`, `routines`, `content`, `connectors`, `plugins`, `drafts`,
  `skills`, `secrets`, `members`, `models`, `ui`). `events.open(id)` is idempotent (a module-level map of
  slots), loads history, then subscribes from the cursor; a history load
  that fails releases the slot and records `stream[id] = "error"` (the
  `ConnectionBar` says so) instead of leaving the caller an unhandled rejection;
  `push` dedupes by event id and advances the cursor; `folded(id)` memoises on
  the event array's identity, so re-renders do not refold, and carries the
  previous fold's messages through `reuseMessages` so an unchanged message keeps
  its object.
  - **A slot outlives the load, and `close` marks it rather than dropping it.**
    `open` has to await the history before it can subscribe from the right
    cursor, and `close` lands inside that await on every StrictMode mount and
    whenever somebody clicks through the sidebar faster than a long thread
    loads. The old no-op reservation was deleted by that `close`, so the second
    `open` started over and then overwrote the first one's unsubscribe: an
    `EventSource` nobody could close, still pushing deltas — which carry no id
    and so dedupe against nothing, doubling the streamed text — and holding one
    of the browser's six connections per origin until the tab was reloaded. A
    handful of thread visits was enough to spend all six and stall every later
    request. The in-flight history request is shared by thread id for the same
    reason: a cancelled `open` cannot un-send it, and it is exactly what the
    `open` replacing it is about to ask for.
  - **`pushMany` is the write; `push` is one event through it.** Appending a
    thread's history one `push` at a time scanned the whole array per event and
    copied it per event — quadratic, and over a tenth of a second on a long
    lane. A batch indexes what is already there once; a single live delta keeps
    the plain scan, which costs the same walk without building an index for one
    lookup.
  - **`loading[id]` is what turns the history skeleton on.** `CladeXRuntime`
    hands it to the external store as `isLoading`, and `thread.aui.tsx`'s
    `isHistoryLoadingView` renders `ThreadHistorySkeleton` instead of the
    welcome block. The skeleton, its two translations and its place in the view
    all shipped earlier and had never rendered once: nothing in the app ever set
    `isLoading`, so a view with no messages was always "a new chat" and a lane
    with a year of history greeted the reader with "start a conversation" until
    its events landed. Only the thread's flag is reported, never the thread
    list's — `isHistoryLoadingView` stands down while `threads.isLoading` is
    true, so wiring the sidebar's fetch into it would put the welcome block back
    on the slowest path there is, a reload straight onto `/t/:id`. The sidebar
    has its own stand-in rows (`Sidebar.tsx`, before the first page only: `load()`
    runs on every remount of the layout and must not blank rows the reader is
    already looking at).
- **`components/assistant-ui/`, `components/ui/`** — registry files copied in
  from shadcn and assistant-ui. They are **owned code**, not a dependency:
  upstream fixes do not flow in, edits are kept upstream-shaped and marked with a
  `// cladex:` comment, and the eslint config exempts these paths from the
  project's own rules.
- **`toolkit/`** — `defineToolkit` maps every known tool name to a renderer:
  `ToolRow` for backend tools (a verb + target from `toolkit/labels.ts`, in the
  language the model is thinking in: `detectLang` votes Cyrillic vs Latin over
  the message's reasoning parts and falls back to the interface language), `AskCard` for `ask_user`, `FileCard` for `send_document`.
  `FileCard` shows a picture rather than filing it away — the `image` element's
  preview, zoom and name — and keeps the card that opens the file panel for
  everything else: a glyph tile for the file's kind, the name, "Document · MD"
  (`lib/files.ts` — `kindOf`, `fileKindLabel`), and a Download button whose chevron
  holds Open and, for text, Copy. A delivered file is never an assistant-ui `image` part: the
  external store keeps only data:, https: and blob: urls, and a file of ours is a
  same-origin path, so such a part was dropped on the way in and the picture
  never reached the thread.
  `StepGroup` renders a `group-chainOfThought` / `group-tool` / `group-reasoning`
  Codex-style: a quiet header ("Worked 11 min 1 s" from
  `metadata.custom.duration_ms`, or the current step while running) over a
  borderless flow of the model's reasoning — verbatim markdown, in whatever
  language it thinks in — and one muted line per run of consecutive tool calls
  (`runLabel`: "Ran a command, searched the web"), each expandable into
  `ToolRow`s. It is open while the turn runs and collapses when the answer starts.
  One tool call is read deeper than its name: `imagePrompt` recognises the
  gpt-image-2 skill's `gen.sh` behind a `Bash` call and pulls the `--prompt` out
  of the command line, so the step reads "Generating an image" and a running one
  holds an `ImageGeneration` frame — a dot grid under the shimmering prompt —
  for the tens of seconds the drawing takes. The frame never shows a picture:
  the real one arrives afterwards as a file through `send_document`, so the
  frame is dropped the moment the call ends.
  One mark says "still going" everywhere: `Working.tsx`, a `cl-signal` dot pulsing
  on `--cl-motion-loop`, used by the live run line and the tool row inside the
  group. Above them the step-group header spins at the same tempo and its label
  shimmers — and nothing else does, so the three idioms that used to run at once
  at three speeds are one (DESIGN.md §5 "Running mark").
  The header also owns the *gaps*. A group's own status settles the moment its
  last call returns, which is exactly when the model starts thinking about the
  next one; taken literally, the chain of thought collapsed and re-opened on every
  gap and read "Worked 3 steps" mid-turn. `StepGroup` therefore keeps running
  while the turn does as long as it is the tail of the message, and `groupHead`
  names the last tool call *in flight*, falling back to "Thinking" only when
  nothing is — never going on naming a call that already returned. It returns
  that call's own Lucide icon alongside the words, so the head's mark says
  *which* work is running; `icon: null` is what asks the header for the spinner,
  which therefore means "between calls" rather than decoration. Only one of the
  two is ever on screen, so the head still carries a single mark (DESIGN.md §5). A thought
  outranked by a call is deliberate: the last part of a running message counts as
  running, so a page opened mid-turn (the snapshot replays the not-yet-flushed
  reasoning *after* the pending call) used to read "Thinking" through the whole
  fetch. Being open is the user's call throughout: a group is open while it is
  the live one and closed once it is history, and the toggle works in between —
  it used to be wedged open for the whole turn, which is when there is most to
  close. The choice is stored per **message** (`ui.steps[messageId]`, together
  with the running-ness it was made under, so it lapses when the turn ends), not
  per group: a turn opens a new group after every text block it writes, and a
  choice held by one group meant the next one popped open a second later — the
  thoughts of a working agent could not be closed at all.
  Its word follows the model's language, not the interface's: `toolkit/lang.ts`
  reads the newest reasoning of the thread when this message has none of its own
  yet, so "Думаю" no longer flips to "Thinking" a second after the turn starts.
  Before any part exists there is no group to head the wait, so `Thinking.tsx`
  renders that same line — spinner plus shimmering "Thinking" — in the
  `MessagePrimitive.GroupedParts` `indicator` slot (mode `no-text`: emitted while
  the message runs and its last part is neither text nor reasoning). It renders
  nothing when a step group is already the tail, or when a standalone card
  (`ask_user`, `send_document`) is running and showing its own state, so exactly
  one mark is on screen at any moment. It replaced a bare pulsing dot, which said
  "wait" without saying what for.
- **The composer rail** (`thread.aui.tsx` `ComposerAction`, left to right):
  attachments; `ModelPicker` (below); `ContextMeter` (`components/cladex/ContextMeter.tsx` over the
  registry `composer-context.tsx`, a ring plus a one-decimal share and a
  hover/focus panel of cached / cache-write / new-input / total tokens against
  the window) — it renders nothing until a turn has reported usage, never
  zeros; `ComposerVoiceControl` (`composer-voice.tsx`, upstream's
  `ComposerVoice` — idle mic, listening with a pulsing `cl-signal` dot, 14
  sine bars and a `0:SS` timer, then a "Transcribing…" shimmer); send / cancel.
  While a dictation is active and the thread is narrower than 28rem (`@max-md`
  on the thread's `@container` — a phone, or a thread squeezed by the docked file
  panel) the model pill and the meter step aside, and the waveform itself may
  shrink (`min-w-0`, clipped bars): before that the rail's intrinsic width pushed
  the stop and send buttons out of the composer. `e2e/dictation.spec.ts` pins it
  at 320px with Chromium's fake microphone.
  The dictation adapter's `stop()` (`runtime/adapters/dictation.ts`) resolves
  only once the transcript has reached the listeners: assistant-ui's
  `stopDictation()` unsubscribes every listener as soon as `stop()` settles, and
  a `stop()` that returned before `MediaRecorder` fired lost the text every
  time. A `503` (no key) returns to the mic silently with the draft kept.
  Typing `/` at the start of the input opens `composer-slash-commands.tsx`
  (`useSlashCommands` in `components/cladex/SlashCommands.tsx`): the list is
  fetched lazily on the first `/` into `ui.commands` and cached, a failed
  fetch settles silently, arrows / Enter / Tab pick, Escape closes, choosing
  inserts `/name ` through `aui.composer.setText`, and a fully typed name lets
  Enter send. `model` and `effort` are never offered, and a text that is `/model`
  or `/effort` (`lib/modelCommand.ts`) opens the picker instead of being sent —
  guarded in `SlashCommands`' Enter handler (the draft is kept) and again in the
  runtime's `onNew` (`runtime/provider.tsx`), because Enter is not the only way to
  send; both set the `ui.modelPickerOpen` flag.
- **The model picker** (`components/cladex/ModelPicker.tsx`, left of the meter):
  a pill naming the lane's effective pair ("Opus 5.5 · medium", or "… · Auto"
  when no effort flag is sent — the effort list spells that choice "Auto (<the
  model's default effort>)") and a dropdown of the offered models with their
  price per 1M tokens, then the chosen model's effort levels. It re-reads `GET
  /api/threads/{id}` every time it opens rather than trusting the tab (the pair is
  lane state, edited from Telegram too). A model whose known `window` is smaller
  than the thread's current context (`useContextUsage`) is disabled with one
  shared hint under the list. After a switch on a thread with usage, a one-line
  note inside the panel estimates the next reply's cold re-read (`used ×
  price.in × cache_write`), kept until the next `turn.end` — inside the panel, not
  on the rail, because a second rail line would wrap at 390px. The thread id comes
  from `runtime/thread-id.ts` (`ThreadIdContext`, provided by `CladeXRuntime`).
  The `models` store slice (`store/models.ts`) fetches the catalog once per tab and
  holds each open thread's pair; `store/events.ts` patches the explicit pair from
  every `thread.model` (history replay included) and, only for a LIVE clear, re-reads
  the thread for its new effective pair. Error codes `bad_model`, `bad_effort` and
  `model_command` are translated.
- **Which model answered** (`components/cladex/ModelLabel.tsx`, in the assistant
  footer): a muted "<label> · <effort or Auto>" from `turn.end`'s `model`/`effort`,
  which `fold.ts` stores in the message's `metadata.custom` with `fallback_from`.
  It names the model that ANSWERED, so a fallback or a substitution shows where it
  happened; a turn recorded before the switcher has no such key and no label.
- **Messages** — a user message gets the same copy action as the assistant
  one (`UserActionBar`: `ActionBarPrimitive.Copy`, the check-mark swap on
  `isCopied`), revealed by CSS on hover and `focus-within` rather than
  upstream's `autohide`, which unmounts the bar and so keeps the keyboard out.
  `day-separator.tsx` (upstream `DaySeparator` plus `MessageDaySeparator`,
  which reads the previous message's `createdAt` through `useAuiState`) is
  rendered ahead of every message inside `ThreadPrimitive.Messages` and shows
  a rule labelled Today / Yesterday / the locale date (`dates.today`,
  `dates.yesterday`, `lib/dates.ts` `dayLabel`) whenever the calendar day
  changes; the flat registry `DaySeparator` stays for history views.
- **Asking in the page** (`components/cladex/AskDialog.tsx`): `ConfirmDialog`
  and `PromptDialog` over the shadcn `Dialog`. Telegram's Mini App webview
  shows no native JS dialog, so every `window.confirm` / `window.prompt` was a
  silently dead button there — renaming a thread, archiving it, deleting a
  routine, disconnecting a connector. `PromptDialog` mounts its field with the
  dialog, so a reopened prompt offers the current name rather than an
  abandoned draft.
- **Thread management** (`components/cladex/ThreadRow.tsx`): the row's `…`
  menu is in the layout at all times below `md` (a 44px target beside the
  title) and keeps the desktop's hover reveal, where it still takes the
  channel icon's place; a touch screen has no hover, so the menu — the only
  way to rename or archive — could not be reached on a phone at all. Rename
  opens `PromptDialog`, archive asks first through `ConfirmDialog`, because
  nothing in the sidebar brings an archived lane back.
- **A routine's model** (`components/cladex/RoutineForm.tsx`): model and effort
  selects beside the schedule, "Deploy default" / "Auto" meaning unset (the cron
  model, then the deploy default; no effort flag), sent with create and edit.
- **Calendar** (`pages/Calendar.tsx` — the `/calendar` layout: the "Calendar"
  heading and the Calendar/Tasks tab strip over an `<Outlet/>`; the calendar itself
  is `components/cladex/calendar/CalendarBoard.tsx`). The sidebar's "Calendar"
  item (`sidebar.calendar`, selected for `/calendar` and the legacy `/routines`)
  replaced "Scheduled". Month, week and agenda views over one range: the SPA reads
  `GET /api/routines/calendar` and, for the owner only, `GET /api/content`, and
  merges them itself (`store/content.ts`; `lib/calendar.ts` is the pure part —
  Monday-start grids, local-day bucketing, dense markers — with its own tests).
  A 404 from `GET /api/content/publisher` is learned once as "content disabled"
  and the calendar shows routines only; a 404 from the routines calendar is an
  empty one. The default view is the agenda below 768px and the month above it,
  remembered in `localStorage["cladex.calendar.view"]` (every access in
  try/catch); the agenda covers 14 days from the anchor day. Content chips carry
  the channel's icon and colour (`calendar/channels.ts`, `ChannelIcon.tsx`:
  simple-icons, LinkedIn drawn inline because the pack dropped it) and an
  attention badge for `needs_attention`; `scheduled`/`queued` times read "≈ HH:MM",
  because inline mode runs one routine at a time. Dense routines are one "every N"
  marker per day, their runs counted into it. Desktop drag-and-drop (HTML5): the
  month keeps the time of day, the week moves date and hour, an undated backlog
  item dropped on a month cell gets 09:00; fires and `queued`/`publishing`/
  `published` items do not drag. `ContentItemDialog.tsx` creates and edits (local
  date-time, media paths, previews from `/api/content/{id}/media/{n}`), approves,
  unapproves, retries (a two-choice: re-post, or "it was published" with a URL)
  and deletes, showing the server's `message` verbatim. `PublisherControl.tsx` in
  the toolbar shows the publisher routine (warning when it is paused or gone),
  picks among the owner's `manual` routines, or creates one from the "Content
  publisher" template and sets it in one go; the same component with
  `kind="drafter"` is the drafter picker (the "Content drafter" template), and
  each picker hides the routine holding the other role. The v2 dialog offers
  "Approve theme" / "Remove theme approval" by status, "Retry drafting" for a
  `planned` item with an error, shows the redraft comment and the card state, and
  makes a `drafting` item read-only; the toolbar's "Approve week's themes"
  (`ApproveThemesButton.tsx`) confirms a count first. `/calendar?item=<id>` —
  the card's "open in calendar" link — opens that item's dialog, reading it by id
  when it is outside the range, and drops the parameter on close. `api/client.ts`
  reads the error body as `error` first and falls back to an object `detail`.
- **Tasks list** (`pages/Routines.tsx`, the `/calendar/tasks` tab; its heading is an
  sr-only "Tasks" h2 because the tab strip names it): the routine form offers "On
  demand" (`manual`, `lib/schedule.ts` `PRESETS`, described by `describeSchedule`),
  and the templates include the owner-only "Content publisher" and "Content
  drafter" (both `manual`, `timeout_seconds` 1800). A task's prompt is clamped to two
  lines. It used to show whole — `line-clamp-2` sets `display: -webkit-box`
  and the `block` utility beside it won the cascade; the routine page keeps the
  full prompt.
- **Code rendering** — `markdown-text.tsx` hands every fenced block to
  `shiki-highlighter.aui.tsx`, which highlights with Shiki
  (`github-light-default` / `github-dark-default`, emitted as `light-dark()` so a
  theme switch is a repaint and not a re-tokenize) and skips tokenizing while the
  message part is still running: the same plain code sits in the same frame, so
  settling changes color and not layout. An unknown fence language degrades to
  plaintext. `FilePreview` calls the same component directly, with the language
  read off the file name, and strips the code-card chrome back off. The block's
  own surface — border, background, radius, 13/20px mono — stays on the design
  tokens; only the tokens inside are colored (DESIGN.md §2 "Syntax
  highlighting"). Shiki's grammars are separate lazily-fetched chunks, so the
  build emits ~300 of them and only the languages actually shown are downloaded.
- **Phone layout** (`app/Layout.tsx`, `components/cladex/MobileBar.tsx`): below
  `md` (768px, DESIGN.md §4) the sidebar is not docked — a 48px `MobileBar`
  (menu, the mark in a 44px target, new chat) opens the same `Sidebar` in a
  left `Sheet` drawer, `min(18rem, 100vw - 32px)` wide, at `cl-z-dialog`;
  `ui.sidebarOpen` is the flag and any route change clears it. Page chrome
  drops to 16px gutters and the chat header to 48px; the composer's
  Enter-to-send hint is hidden, message text (the assistant's prose and the
  user's bubble, and markdown H5/H6 with it) is DESIGN.md's body 16px below `sm`
  instead of the page's 14px — the same size as the composer, which is already
  `text-base`; from `sm` up it stays 14px — and the root is `h-dvh` with the bottom
  safe-area inset (`viewport-fit=cover`) for Telegram's iOS webview.
  `e2e/mobile.spec.ts` drives the drawer at 390px and asserts the page never
  scrolls sideways. Every `DialogContent` is capped at `100dvh - 2rem` and
  scrolls, and its single grid column is `minmax(0, 1fr)` — an `auto` track let
  fixed-width fields (the routine form's `w-56` selects) widen the dialog past a
  320px screen; the routine form's fields are full-width below `sm`, and
  `AskCard` options wrap.
- **The file panel** (`components/cladex/FilePanel.tsx`, `ui.openFile` +
  `ui.openFileThread`): docked from `xl` at `clamp(28rem, 42vw, 56rem)`, an
  overlay with a scrim from `md`, the whole screen below `md`, and "Expand" makes
  it the whole screen anywhere. From `md` up it is **resizable**: the left edge is
  a `role="separator"` the reader drags (pointer capture, so crossing a PDF's
  iframe does not drop the drag) or moves with the arrows (32px; Home/End the
  extremes), double-click or Enter restores the responsive default, and the width
  is kept in `localStorage["cladex.filePanelWidth"]` as a per-browser convenience.
  It is clamped to 20rem at the narrow end and, docked, to the window minus the
  sidebar and 20rem of thread — in JS while dragging and in CSS (`max-w`) when the
  window later shrinks under a saved width. The header is the document's toolbar: a
  preview/source toggle for Markdown only (code has one view), the name — and,
  when the chat delivered more than one file, a chooser over all of them
  (`FileParts.tsx` `FileMenuItems`, `uniqueFiles` by `file_id`), fed by
  `folded(openFileThread).files`, which is why `setOpenFile(f, threadId)` records
  the thread and a switch inside the panel keeps it — then Copy (Download for a
  file with no text) with the rest behind its chevron, Expand, Close. The thread
  header's "Files: N" opens the same list. A Markdown preview drops the
  frontmatter and wraps each table in its own horizontal scroller, so a wide
  table never widens the panel. `e2e/files.spec.ts` walks the chooser, the
  source toggle and the 320px layout.
- **Routes** (`app/routes.tsx`): `/login` is public; `/`, `/t/:id` (the URL-encoded
  `session_key`), `/calendar` (index: the calendar; `tasks`; `tasks/:id`, which
  renders the routine page alone, without the heading and tabs, because it is a
  full-height thread), `/memory` and `/customize` sit behind `AuthGate`; `/routines`
  and `/routines/:id` are kept as redirects to `/calendar/tasks` and
  `/calendar/tasks/:id`. All of them sit behind `AuthGate`, which resolves `GET /api/me` once and redirects anonymous visitors. Theme is a `.dark`
  class on `<html>` persisted under `localStorage["cladex.theme"]`.
- **Customize** (`pages/Customize.tsx`): `/customize` is a tab strip whose tabs are
  URL segments (`connectors`, `plugins`, `skills`, the index redirecting to
  `connectors`) — so a tab is linkable and the OAuth return has somewhere to land.
  The **Connectors** tab (`components/cladex/ConnectorsTab.tsx`) lists what
  `GET /api/connectors` returns with one action per state (Connect / Reconnect /
  Enable) and the rest in a `…` menu. Every control on a card acts on the caller's
  own row since Wave 5, so the cards are live for everyone (and so is the connector
  page); only *Add custom connector*, which writes the shared `user_catalog/`, is
  shown to the owner alone.
  Connect either opens the secrets dialog, or navigates to the URL
  `POST /{name}/oauth/start` returns — the provider then returns the browser to
  `/customize/connectors?connected=<name>` or `?error=<code>`, which the tab turns
  into a toast (translated for the codes in `lib/connectorErrors.ts`, otherwise the
  server's own message) before clearing the query. A custom connector is added from
  the same tab by URL and appears in the list immediately, in the order the list
  route would return it.
  The **Plugins** tab (`PluginsTab.tsx`, `PluginCard.tsx`, `usePluginActions.tsx`,
  `pages/PluginPage.tsx`) reuses that card grid wholesale — the square state
  control, the `…` menu, `firstSentence`, the catalog check — with a `Puzzle`
  tile instead of a brand mark. `owner` on a card (`PluginCard.tsx`) gates the
  clone, not the card: Install, Remove and *Add plugin* fetch or delete code every
  tree loads and show for the owner only, while Enable and Disable write the
  caller's own row and show for everybody — the split the router enforces.
  `lib/pluginMenu.ts` is the connectors idiom with
  three plugin-shaped rules: an uninstalled plugin has no Remove (there is no row
  and no clone to drop); `needsReinstall` (`installed && !present`) says the files
  are gone and offers the one call that fixes it, Install being idempotent
  (`clone_plugin` removes and re-fetches at the pinned ref); and a `state:
  "error"` row is offered **only** Remove, because its manifest left the catalog,
  so install would 404 and re-cloning is not the cure. The page shows *What it
  adds* from `components` — chips for commands, skills and agents, a sentence each
  for hooks and a bundled MCP server — plus `repo@ref` linking
  `<repo>/tree/<ref>/<subdir>`, the exact revision fetched rather than the branch
  tip; an orphan row carries no repo, so that field reads "—" rather than linking
  nowhere.
  **Pending review** (`PendingDrafts.tsx`, `DraftDialog.tsx`) sits above both
  grids, `kind="mcp"` on Connectors and `kind="plugin"` on Plugins, and renders
  nothing when that kind's queue is empty — so neither tab changes shape in the
  normal case. It is hidden from a plain user entirely rather than shown with dead
  buttons, and a failed `loadDrafts` leaves it hidden rather than taking the tab
  it sits on down with it. Opening a draft calls `review` once and prints the
  returned `surface` **verbatim in a `<pre>`** — never through the markdown
  renderer, because the whole point of the screen is that the human reads exactly
  the bytes the token is bound to; `local_command` draws a warning strip above it.
  Approve posts that token back, and a `409 stale_draft` says the draft changed
  and re-runs `review`, so the next approval is given for what is now there. A
  rejected draft is a muted line with its reason and a Discard.
  The **Skills** tab (`SkillsTab.tsx`, `SkillRow.tsx`, `NewSkillDialog.tsx`,
  `pages/SkillPage.tsx`) lists the library with the badges each row earns
  (Bundled / Pinned / Stale), its use count and its source; the filter (active /
  archived / all) and the search run in the browser, because the library is a few
  dozen rows and a round trip per keystroke would buy nothing. The page renders
  `SKILL.md` through `react-markdown` + `remark-gfm` directly — not the
  assistant-ui `MarkdownText`, which wants a message context — and Edit swaps it
  for a plain monospace `<textarea>` seeded with `content` (no new dependency;
  `Ctrl/Cmd+S` saves). Save carries the `etag` the text was read with; on
  `stale_edit` the conflict message offers a Reload that replaces the buffer with
  the server's copy, so the owner chooses instead of one side being lost silently.
  Archive is absent while a skill is pinned; `New skill` shows for any logged-in
  user, the library being always their own. `New skill` opens on a **choice**, not a form: *Write it with the agent*
  opens a fresh thread with a request already in the composer, unsent (the router
  `state.draft` handover `ComposerDraft` reads, the same one the routines page uses)
  naming the bundled `skill-authoring` skill — writing a SKILL.md by hand assumes
  you already know what a good description looks like, and the agent has a checklist
  for exactly that; *Write it myself* is the original name + textarea form, for text
  that already exists. Back returns to the choice, and closing the dialog resets it,
  so it never reopens mid-step.
- **Memory** (`pages/Memory.tsx`, `components/cladex/MemoryGraph.tsx`,
  `lib/memoryGraph.ts`): `/memory` draws the wiki as a brain — the CladeX mark
  (`assets/cladex-mark-64.png` as an SVG `<image>`) over a blurred signal-coloured
  disc at the centre,
  one hub per namespace on an inner ring, every page as a pill further out,
  thin lines for the hub edges and the `[[…]]` links (dashed into a ghost when the
  target does not exist), over a dotted `radial-gradient` ground painted from
  `--border` so it reads in both themes. Pure SVG and **no new npm dependency**;
  `lib/memoryGraph.ts` holds `layout()`, a pure function of the wire in two
  deterministic passes: a radial seat where each namespace's slice of the circle
  is proportional to its page count (a slice too narrow for its pills spills onto
  further rings), then a relaxation that pushes any two overlapping boxes apart
  along their smaller overlap until none overlap (the mark is a fixed obstacle) —
  equal slices per namespace used to pile `env` and `projects` into one unreadable
  heap. The same wiki still draws the same picture twice, and the layout is
  testable without a DOM (a crowded-wiki test asserts no two boxes overlap).
  Edges carry node ids, not coordinates; positions are looked up at draw time.
  **Every node is draggable**: a page moves alone, a hub carries its whole
  namespace (pages and ghosts). What the owner moved is kept per browser in
  `localStorage` (`cladex.memory.moved`, absolute positions by node id, written
  once per drag and pruned to ids the wiki still has) — a convenience, so a
  refetch or a new page leaves the arrangement alone; *Put the nodes back* drops
  it and *Fit to screen* frames every node; *+* / *−* zoom about the middle of the
  screen, and two fingers pinch (the point between them stays between them; lifting
  one finger ends the gesture rather than turning into a jumping pan). **On a phone**
  (`max-width: 639px`, read once at mount) the layout runs at type scale 1.3 —
  pills, labels and hubs 30% bigger, laid out at that size so they still do not
  overlap — and the graph opens at about one unit per pixel around the centre
  instead of fitted: sixty pills squeezed into 390px left 3px labels and, with no
  wheel on a phone, no way to zoom in. Pan and zoom are a `viewBox`, not a
  CSS transform, so strokes keep their width; pixels map to graph units through
  `clientToGraph()`, which models the default `xMidYMid meet` (one scale for both
  axes, margins on one), so a dragged node stays under the pointer. The wheel
  listener is registered by hand with `passive: false`, because
  React attaches `onWheel` passively and a passive listener cannot stop the page
  scrolling under the zoom. `setPointerCapture` runs only once the pointer has travelled past the
  5px drag slop, never on `pointerdown`: capture retargets the `pointerup` and the
  `click` after it at the svg, so a plain tap on a page would never reach that
  page's handler — jsdom has no capture, so only a browser shows it. The graph is
  `absolute inset-0` under the header and fills the page. Hovering (or dragging) a page raises its edges and neighbours and
  dims the rest; a page with `in_index === false` wears a dot badge. The
  refetch-on-visible deliberately does **not** reset the view, or every return to
  the tab would undo whatever the owner had zoomed in on.
  The selected page lives in the URL (`/memory?page=user/nutrition`), which is
  what makes a pasted link, the back button and a wikilink inside the sheet one
  gesture; the sheet renders the markdown with the shared `MD_BODY` from
  `lib/markdown.ts` (extracted from `SkillPage.tsx`, which now imports it),
  strips the frontmatter block — the header already shows summary, tags and
  `updated` — and rewrites `[[a/b]]` into `[a/b](#wiki:a/b)` so a click selects
  that node instead of navigating. Freshness is level 1: the graph is fetched on
  mount and on `visibilitychange` → visible. The wiki moves at the pace of a
  conversation, and an SSE channel for it would buy a subscription to save a
  reload.
- **Settings** (`components/cladex/SettingsDialog.tsx`): a dialog with its own
  sidebar of sections, opened from the user menu and mounted once in `Layout.tsx`
  beside the command palette (`ui.settingsOpen` is the flag). A dialog and not a
  page because settings are a detour: the open thread stays open behind it. The
  sidebar is what keeps the shape stable as sections arrive — today **Secrets** and
  **Users**. Only the open section's panel is mounted, so a section nobody looked at
  fetches nothing. Owner-only twice over: the menu hides the entry, and the dialog
  renders `null` for anyone else, which matches a router whose read is owner-only.
  `SecretsSection.tsx` lists `GET /api/secrets` as one alphabetical list — a stored
  name carries the day it was written, a name a connector declares and nobody set
  reads "not set" and offers no delete, and both name the extensions that use them.
  Adding one is a name + value form checked against the same `^[A-Z][A-Z0-9_]*$`
  the server applies; deleting asks first. The panel is mounted with the dialog, so
  every opening re-reads — a secret set from Telegram in the meantime is on screen
  without a reload. The `secrets` store slice re-reads after every write rather
  than patching the row, because `used_by` and the declared-but-unset entries come
  from the catalog, not from what was written.
  `UsersSection.tsx` is the same shape over `GET /api/users`: the owner carries a
  badge and no control (revoking it would be a door that locks from the inside),
  everyone else a shield button that revokes behind a confirmation and gives access
  back in one click, and an id that is only in `allowed_users` says so instead of
  showing a name nobody has learned. Adding takes a numeric Telegram id and nothing
  else — a field that accepted @handles would be a field that quietly failed.
  Every row but the owner's also carries two grants as **switches**
  (`components/ui/switch.tsx`, the radix `Switch`, `role="switch"`, labelled "Bash
  for <name>" / "Learning for <name>"): **Bash** (a shell, and with it `Agent` and
  `Task`) and **Learning** (reviews of their conversations and the weekly curator
  and wiki-lint pass over their tree, on the owner's account). A switch shows the
  EFFECTIVE value the server resolved (`shell` / `learning`), with a "default"
  marker while `*_inherited` says the person has no choice of their own and follows
  `config.yaml`. A `source: "config"` row shows both disabled, with "can be set
  after their first message" — only a `users` row can hold a choice, and a grant
  never creates one; the owner row has none. Turning Bash **on** first opens a
  `ConfirmDialog` saying what a shell reaches in the one shared container — every
  user's files and credentials: connector tokens, `secrets.env`, the Claude login
  — that this person's open conversations restart with a fresh session, and that
  their routines get the shell too; turning Bash off and either Learning change
  apply at once, with a toast naming the grant that moved. Each change is
  `api.users.setGrants` → `PATCH /api/users/{id}` with the one named field, and
  `setMemberGrants` then re-reads the list like every other `members` action, so
  the markers are what the server resolved rather than a local guess. One write
  at a time per row — a `useRef` guard, not `disabled`, which would throw keyboard
  focus off the switch mid-write. Under the list a `dl` legend spells the two
  hints, which each switch points at with `aria-describedby` (on the page rather
  than in a tooltip: the Mini App has no hover), and a permanent note says that in
  a group chat anyone allowed to talk to the bot runs as the owner — on the
  owner's lane, with the owner's tools, the shell included — so the switches are
  no boundary there. The row is a grid, so the switches get a line of their own
  under the name at every width.
- **Interface language** (`i18n/`): every UI string is read through `t()` /
  `useT()` from a two-locale dictionary — `en.ts` is the dictionary of record
  and `ru.ts` is typed as its shape, so a key missing in one language fails
  `tsc`; a test in `i18n.test.ts` also scans `src/` for Cyrillic outside the
  dictionary. The locale is a client preference in the `ui` slice
  (`localStorage["cladex.locale"]`, `en` by default, never sent to the server)
  and is switched from the user menu in the sidebar footer (`UserMenu.tsx`): an
  identity line, `Language` as a submenu that shows the current language, the
  theme as a three-icon segmented control (sun / moon / monitor, built from
  `DropdownMenuRadioGroup` so it keeps menu semantics, and selecting one does not
  close the menu), **`Default model ▸`** (the personal default — a radio group of
  the offered models plus "Deploy default", and one of efforts, captioned that it
  applies to new chats and after `/new`; it lives here rather than in Settings,
  which is owner-only, because a default is every user's own preference), Settings
  and log out. Dates, durations and schedule descriptions take
  the locale as an argument; tool-step labels keep following the language the model thinks
  in and use the locale only as the fallback. Not translated: thread titles,
  routine names and prompts, agent output, file names, server error messages.

Checks: `npm run lint && npm run typecheck && npm run test && npm run build`,
plus `npm run e2e` — Playwright walks that serve the build on :4173 and run
against `page.route`-faked `/api` (`web/e2e/fake-server.ts`, including a canned SSE
body), so they need no backend: one drives a whole turn (`smoke.spec.ts`), four
cover a running turn's step group (`running.spec.ts`), one the phone drawer
(`mobile.spec.ts`), three the model picker (`model.spec.ts`: naming the lane's
model, a switch with its cold re-read estimate and the answer's label; a `/model`
draft opening the picker; the pill at phone width), and `dictation.spec.ts` and
`files.spec.ts` are described with their components above. `customize.spec.ts`
walks all three Customize tabs — the Connectors states, actions and add dialog;
installing a plugin and seeing the card turn installed; reading, editing and
saving a skill and seeing the rendered Markdown change; reading a plugin draft's
surface, approving it and seeing the plugin appear as available; and adding a
plugin to the catalog from the browser, where it waits for approval.
`settings.spec.ts` opens Settings from the user menu, adds a secret, checks that
the value it typed is nowhere on the page and deletes the stored one; a second
walk switches to Users, invites an id and revokes somebody's access; a third sees
the group-chat note, no switch on the owner row and a disabled one on an id that
is only in `allowed_users`, turns a user's Learning on from the keyboard (focus
stays on the switch through the write and the re-read) and Bash through the
confirmation, and watches both "default" markers go.
`.github/workflows/web.yml` runs the same sequence on any `web/**` change.

## Deployment

`WebDeps` (`http/deps.py`) is the dependency bundle `main.py` fills in
(`web_deps(ctx)`) and `tests/web_helpers.py` fills with real module objects over
the test engine plus one fake runner; since Step 5 it carries exactly the
attributes some module's provider reads — `db`, `job_store`, `ext_store`,
`library`, `skills_dir` and `sidecar` are gone, and the skills router mounts on
`learning` (the scopes registry) alone. Nothing in it imports aiogram, and it
imports no router, so the package stays acyclic. uvicorn binds `0.0.0.0:web_port` **inside the container
only** — `docker-compose.yml` publishes no ports, and the single public surface
is the `cloudflared` sidecar dialing out to Cloudflare, which routes the
hostname at `http://agent:8080` ([[decision.web-as-a-channel]]). The built SPA
is copied out of the Dockerfile's `webbuild` Node stage into `/app/web_dist`;
`main.dist_dir()` reads `CH_WEB_DIST`, falls back to `<package>/web_dist`, and
returns None when there is no build — in which case the API serves no static
files and the rest still works.

# `turns`

## Purpose

One inbound message becomes one **turn**: the module locks the lane, resolves or
starts the SDK session, runs the Claude Agent SDK loop, publishes a typed event
stream while it runs, records what was said, and hands the reply back to whichever
channel asked for it. It also owns the `hermes` MCP server — the tool surface the
agent sees — even where the tool's *behaviour* belongs to another module.

Since Wave 3b a turn is **a row and a task**, not a coroutine in the handler that
received the message. `POST /api/threads/{id}/messages` and a Telegram message both
insert a `turns` row and enqueue `run_turn(turn_id)`; a worker claims the row and
runs the SDK loop, and the process that took the request only watches the lane. The
consequences are the whole point: restarting the agent no longer kills a turn, two
API instances cannot start two turns on one lane, and a browser that reloads
mid-turn catches up from whichever instance answers it.

Everything a turn needs from outside (`sessions`, `files`, `scheduler`,
`extensions`, `learning`, `memory`, `telegram`) arrives as a Protocol port declared
in `application/ports.py`. This module imports no other module.

## Layout

```
domain/
  models.py         TurnEventRow, TurnCost, TurnRow (ORM); ACTIVE_STATUSES.
                    Wave 4a adds `turn_costs.owner_id` (FK -> `users(id)`). **`turns.user_id`
                    gets NO FK**: it is the SENDER of a turn — an audit field, NOT NULL,
                    and a group turn's sender may have no `users` row at all. The turn's
                    workspace comes from `lane_owner`, never from that column.
  events.py         TurnEvent (wire), TRANSIENT, NO_MODEL_END_KEYS, Listener, _now
  value_objects.py  TurnContext (+ `shell_enabled`, `learning_enabled`, the
                    `has_shell` property — 0009), TurnFlags, ComposedTools,
                    CANCELLED, _compose_text, _attachment_wire
  prompt.py         TURN_RULES, WIKI_RULES, build_system_append  (byte-identical)
  policies.py       _BUILTIN_TOOLS, _FALLBACK_REPLY, _MODEL_COMMAND_REPLY,
                    SHELL_CHANGED_NOTICE, _SDK_ERROR_RESULT_PREFIX,
                    MAX_DOCUMENT_BYTES, TOOL_PREFIX, builtin_tools, denied_tools
  exceptions.py     LaneBusy, TurnNotRunning, TurnFailed, AskExpired,
                    ModelRejected (per-call facts, never state on the executor),
                    CliCallFailed (a failed call re-raised with what the CLI said)
application/
  ports.py          every Protocol + TurnSettings
  turn_service.py   TurnService — the runner, minus the SDK loop ("run it here, now")
  launcher.py       TurnLauncher — start / is_busy / cancel / wait / reap
  execution.py      TurnRunService — claim, heartbeat, run, finish (the worker side);
                    its optional `bus=` is ONLY the envelope for a failure before
                    the turn body — `TurnService` owns the real sequence
  control.py        CancelRegistry, TurnControl, MetaSlashCommands
  bus.py            TurnBus over TurnEventRepository + EventPublisher + SnapshotStore
  ask_service.py    AskService — open / present / wait / dismiss
  commands_service.py  SlashCommandsService — the `/` menu
  observation.py    observe_block (fills core.events.TurnStats)
  voice_service.py  VoiceService
infrastructure/
  repositories.py   TurnEventRepository, TurnCostRepository (AsyncEngine)
  turn_repository.py  TurnRepository — the `turns` table, one statement per method
  snapshots.py      MemorySnapshots / RedisSnapshots — the in-flight mirror
  dispatch.py       QueueTurnDispatcher, RUN_TURN_TASK
  sdk_runner.py     SdkTurnExecutor — the ONLY claude_agent_sdk importer here
  hermes_server.py  make_handlers + hermes_tools + build_hermes_server (order, gates,
                    descriptions)
  tool_handlers/    search, costs, memory, documents, ask, delegating
  ask_broker.py     AskBroker (in-memory)
  redis_ask_broker.py  RedisAskBroker — the same port across processes
  publishers.py     ChannelPublisher over core.eventbus; InProcessPublisher
presentation/
  router.py         router: APIRouter — messages / cancel / events / stream / asks
  sse.py            event_stream, _frame, HEARTBEAT_SECONDS
  commands_router.py  router: APIRouter — GET /api/commands
  models_router.py  router: APIRouter — GET /api/models (mount BEFORE the
                    `/api/{rest:path}` catch-all)
  voice_router.py   router: APIRouter — /api/voice/{transcribe,speak}
  schemas.py        MessageIn, AnswerIn, SpeakIn (the only Pydantic in the module)
  dependencies.py   the providers, TurnLauncherPort, StreamDeps,
                    resolve_attachments, events_after
```

## Public surface

### Ports consumed (`application/ports.py`)

| Port | Methods | Satisfied by |
| --- | --- | --- |
| `SessionLane` | `route`, `bind(..., *, index_mtime=None, owner_id=None, model=None, effort=None, shell_enabled=None)`, `touch(..., *, index_mtime=None, owner_id=None, model=None, effort=None, shell_enabled=None)`, `reset`, `reset_all`, `reset_owned(owner_id, *, include_unowned=False)` — `model`/`effort` record the pair that ACTUALLY ran, `shell_enabled` the `has_shell` the session was built with (0009) | `sessions` |
| `MessageLog` | `record(..., *, owner_id=None)`, `last` | `sessions` |
| `MessageSearch` | `search(query, limit, *, owner_id=None)` | `sessions` |
| `LaneModels` | `effective(session_key, owner_id) -> core.model_catalog.Choice` — which (model, effort) this lane runs next; asked ONCE per turn, before `turn.start` | `sessions` |
| `LaneGrants` | `grants(user_id)` and `grant_defaults()`, each an object with `.shell` / `.learning` — the lane owner's EFFECTIVE grants, and the config defaults alone (what an unstamped pre-0009 session ran under); asked once per turn for a non-owner lane, inside the turn's envelope | `identity` (`AccessControl`) |
| `ThreadRegistry` | `touch(key, channel, owner, now, *, talked)` — `owner` is `lane_owner(...)` | `sessions` |
| `TurnEventRepository` | `append`, `after` | own infrastructure |
| `TurnCostRepository` | `record(..., *, owner_id=None)`, `by_day(days, *, owner_id=None)` | own infrastructure |
| `EventPublisher` | `subscribe`, `ready`, `publish` | own infrastructure |
| `SnapshotStore` | `record`, `read`, `is_running`, `clear` (all async) | own infrastructure |
| `TurnStore` | `create`, `claim`, `heartbeat`, `finish`, `fail_queued`, `request_cancel`, `active`, `active_lanes`, `expired`, `mark_lost`, `prune`, `get` | own infrastructure |
| `TurnDispatcher` | `dispatch(turn_id)` | own infrastructure over `core.tasks` |
| `WorkspacePort` | `agent_home`, `root` | `core.workspace` |
| `WorkspaceResolverPort` | `for_lane(key, channel, user_id)`, `for_user(id)`, `owner_id()` | `core.workspace.WorkspaceResolver` |
| `WikiPort` | `index()`, `index_mtime()`, `root` | `memory`, **per lane** (`wiki_for`) |
| `WikiSearch` | `async search(query, *, owner_id, limit=8, depth=1) -> [{owner_id, path, ns, title, summary, tags, links, shared, snippet, via, hop}]` — the wiki INDEX, a cache over the markdown tree; identity is `(owner_id, path)`, and a row whose owner is not the lane's is the deploy owner's `shared/` page | `memory` (`PgWikiIndex`) |
| `TurnHooksProvider` | `build(session_key, index_seen, workspace=None, *, owner_id=0)` — `owner_id` is what the Stop-hook index flush stamps its rows with; `0` means "do not index" | `memory` |
| `TurnObserver` | `after_turn(session_key, stats, *, owner_id=0)` — `owner_id` is the LANE's owner, so the observer bumps that user's sidecar and counters; `0` reads as the deploy owner | `learning` |
| `SkillAdminPort` | `handle(args, *, ctx=None) -> str` — `ctx.owner_id` picks whose library; `None` is the owner's | `learning`, through `core.app_context.SkillAdminTool(scopes)` |
| `SchedulerPort` | `handle(args, *, ctx) -> str` | `scheduler` |
| `JobLookup` | `job_for_thread(chat_id, thread_id)` | `scheduler` |
| `ExtensionAdminPort` | `handle(args, *, ctx) -> (str, bool)` | `extensions` |
| `ContentPlanPort` | `name` / `description` / `schema` (the declaration `declared_spec` reads) + `async handle(args, *, owner_id, origin, thread_id) -> dict` — the MCP result dict itself, `"is_error": True` on a refusal; plain values rather than `ctx` because the same body serves routine runs | `content` (`presentation/agent_tool.py`) |
| `ExtensionComposePort` | `for_turn() -> ComposedTools` | `extensions` |
| `SlashSourcePort` | `plugin_roots() -> [(Path, str)]` | `extensions` |
| `FilePublisher` | `async publish(key, path, *, owner_id=None)`, `to_wire`, `async get`, `async resolve` | `files` |
| `DocumentDeliveryPort` | `(chat_id, thread_id, path, caption=None)` | `telegram` |
| `AskPresenterPort` | `(chat_id, thread_id, question, options, user_id, timeout, emit=None)` | `telegram` / `AskService` |
| `AskBrokerPort` | async `open` / `wait` / `options_for` / `resolve`; **sync** `deliver` / `discard` | own infrastructure |
| `VoicePort` | `transcribe`, `synthesize` | `integrations.openai.voice` |
| `AclPort` | `is_owner(user_id)` | `identity` |
| `ThreadAccess` / `ThreadTitles` | the `deps.owned` / `deps.auto_title` callables | `sessions` |
| `TurnExecutor` | `execute(...) -> (final_text, sdk_session_id, stats, cost, usage, ran_model)`, `is_cli_death(e)`; raises `ModelRejected` / `CliCallFailed` | own infrastructure |
| `SlashCommandsPort` | async `sdk_commands() -> list[str]` | `MetaSlashCommands` (meta key `sdk_slash_commands`), always bound by `http.deps.web_deps`; since Step 5 it is the only source of the CLI-reported names |
| `SlashCommandsStore` | async `sdk_commands`, `save(names)` | `MetaSlashCommands`, from the writing side |

Every one of these is `None`-able where the tool it backs is optional: the gates in
`hermes_server.make_handlers` are `port is not None`.

### Ports provided

* `TurnEventBusPort` — `emit / subscribe / ready / snapshot / is_running`. Consumed
  by `telegram` (presenter), `scheduler` (ticker deliveries) and `sessions` (titles).
  `snapshot` and `is_running` are **async** (the state may be in Redis, in another
  process); `subscribe` stays synchronous and `ready` is the seam a caller that must
  not miss the first event awaits.
* `TurnLauncher` — `start / is_busy / cancel / wait / reap`. The web routes see three
  of those through `presentation/dependencies.TurnLauncherPort`; `wait` belongs to the
  Telegram adapter (`core/app_context.QueuedTurnRunner`) and `reap` to the scheduler
  tick.
* `LaneActivity` for `sessions` — `TurnStore.active_lanes(keys)`, batched per page of
  the sidebar. The row is the one source of truth for "running"; the snapshot store's
  `is_running` is the bus API and nothing in `sessions` reads it (a dead worker's
  snapshot outlives its lease by up to the TTL).
* `TurnService` — `run_turn / reset / reset_all / reset_owned / cancel / is_busy /
  bus / slash_commands`. `reset_all()` is the prompt-cache invalidation an
  extensions mutation calls (the toolset changes for everybody);
  `reset_owned(owner_id, *, include_unowned=False)` (Wave 4b, B-4) is what a
  skill edit calls — that user's lanes only, since after 4b their skills reach
  nobody else's prefix, and evicting every other user's warm cache for one
  person's edit would be the NFR-1 regression the per-user prefix was meant to
  avoid. Neither may silently no-op.
* `AskBrokerPort` — `resolve(qid, token, user_id)` is what a Telegram callback and
  `POST /api/asks/{qid}` both land on.

### Routes

Registered **before** the `threads` routes — `/api/threads/{id}/messages` would
otherwise be shadowed by `/api/threads/{id}`.

Three `APIRouter`s, all tagged `turns`. Every route needs a session; none is
owner-only — an allowlisted user owns their own lanes, and the `/` menu is what they
may type, not what they own.

| Method | Path | `operation_id` | Notes |
| --- | --- | --- | --- |
| POST | `/api/threads/{id:path}/messages` | `turns_send_message` | 202 `{accepted, thread_id, turn_id}`; 409 `busy`; 400 `model_command` for `^\s*/(model|effort)(\s|@|$)` |
| POST | `/api/threads/{id:path}/cancel` | `turns_cancel_turn` | 204; 409 `not_running`. By lane, not by turn id |
| GET | `/api/threads/{id:path}/events` | `turns_list_events` | `{events: [...]}`; `?after=<cursor>` |
| GET | `/api/threads/{id:path}/stream` | `turns_stream_events` | SSE, frame format frozen; `?after=<cursor>` |
| POST | `/api/asks/{qid}` | `turns_answer_ask` | 204; 410 `gone`; 400 `bad_request`; 503 `unavailable` |
| GET | `/api/commands` | `turns_list_commands` | `{commands: [{name, description, source}]}` |
| GET | `/api/models` | `turns_list_models` | `{models: [{id, label, window, price: {in, out}, cache_write, efforts, default_effort}], deploy_default: {model, effort}}`; any logged-in user |
| POST | `/api/voice/transcribe` | `turns_transcribe_audio` | raw audio body; 400 `bad_audio` |
| POST | `/api/voice/speak` | `turns_speak_text` | `audio/ogg`; 503 `voice_disabled` |

### Dependencies (`presentation/dependencies.py`)

One typed provider per collaborator, built once at module scope so
`app.dependency_overrides[get_bus] = …` reaches it. This table IS the bundle contract
that the old "attribute names `routes(deps)` reads" list was.

| Provider | Bundle attribute | Type |
| --- | --- | --- |
| `get_owned` → `owned_thread` | `owned` | `ThreadAccess` — `async (request, thread_id) -> Mapping`, the 404 |
| `get_auto_title` | `auto_title` | `ThreadTitles` — `(session_key, first_message) -> None` |
| `get_turns` | `turns` | `TurnLauncherPort` — `is_busy` / `start` / `cancel` |
| `get_runner` | `runner` | `TurnService` — no route in this module reads it; the bundle carries it for learning's `reset_owned` and extensions' `reset_all`, and the name is published once |
| `get_bus` | `bus` | `TurnEventBusPort` — `subscribe` / `ready` / `snapshot`, for SSE |
| `get_events` | `turn_events` | `TurnEventRepository` — `after(key, after_id, limit)` |
| `get_files` | `file_store` | `FilePublisher` — `get` / `resolve`, both awaited |
| `get_broker` | `broker` | `AskBrokerPort \| None` (`required=False`) |
| `get_voice` | `voice` | `VoiceService` over `VoicePort \| None` (`required=False`) |
| `get_home` | `home` | `Path \| None` (`required=False`) |
| `get_slash_sources` | `slash_sources` | `SlashSourcePort \| None` (`required=False`) |
| `get_sdk_commands` | `sdk_commands` | `SlashCommandsPort \| None` (`required=False`) |
| `get_cfg` | `cfg` | `Config` — only `models`, `model` and `review_model` are read, by `GET /api/models` |

The five `required=False` providers are the ones whose `None` is a documented answer
the handler renders itself (`503 voice_disabled`, `503 unavailable "questions are not
wired"`, an empty `/` menu for either commands source) rather than a mounting
mistake. `turns` is **not** one of them: a bundle without a launcher is a
composition bug, and `503 unavailable` says so instead of raising an `AttributeError`
somewhere inside the handler.

`core.auth.current_user` replaced every `request.state.user_id`; `StreamDeps` is the
two-field stand-in the SSE route hands `event_stream`, whose bundle-shaped argument is
pinned by `tests/test_web_turns.py`.

### Request bodies (`presentation/schemas.py`)

`MessageIn` (`text`, `attachment_ids`), `AnswerIn` (`answer`), `SpeakIn` (`text`) —
every field `Any` with a default and `extra="ignore"`, because each handler keeps the
check that produces its own status and `code`. A Pydantic 422 never replaces one of
them; the shell renders a rejected body as `400 bad_request`.

### Tools (`mcp__hermes__*`, in registration order)

`session_search`, `cost_report`, `cron_manage`, `skill_admin`, `ext_manage`,
`send_document`, `ask_user`, `memory_search`, `content_plan`.

| Tool | Gate (`port is not None`) | Notes |
| --- | --- | --- |
| `memory_search` | `wiki_search` | Full-text search over the lane owner's wiki index plus the pages it links to, schema `{"query": str, "depth": int}` (`depth` clamped `0..2`). The runner passes `wiki_search=None` on a **group lane** — the index it would read is the deploy owner's — and whenever `TurnSettings.memory_search_enabled` is false. Registered after `ask_user`, and `content_plan` after it: `allowed_tools` is built from the registration order and is part of the cached prefix, so appending is the only change that leaves every existing name where the warm prefix already has it. Flipping the flag is a toolset change and costs every lane one session reset at boot. |
| `content_plan` | `content` | The deploy owner's content plan (`content_enabled`). Name, description and schema come off the port (`declared_spec`), like `ext_manage`, and a port without them raises; the content module builds the same tool into its own `hermes` server for the owner's routine runs, so both are `mcp__hermes__content_plan`. The handler (`tool_handlers/delegating.py`) passes `owner_id=ctx.owner_id, origin="chat", thread_id=ctx.session_key` and returns the port's answer untouched — `origin` is never `cron:…` here, so the publisher-only actions (`due`/`claim`/`published`/`failed`) always refuse in chat. The runner passes the port only when `ctx.is_owner_lane is True` (explicitly — not the `True` default the builtin toolset reads), the lane is **not a group lane**, and `ctx.owner_id > 0`: a property of the lane, never the sender. **Registered LAST**, after `memory_search`, and the next tool goes after it. `tool_names()` and `tool_fingerprint()` carry it, so flipping `content_enabled` — or changing its declared description or schema — resets every lane once at boot. |

The handler (`tool_handlers/memory.py`) never raises — a failing port becomes
`"memory_search is unavailable right now (<type>)."`, because the wiki is a hint
and a cold cache must not end a turn — and it prints a **foreign** row's outgoing
links only when they name a page already visible in the same result: a shared
page linking `[[user/health]]` would otherwise tell every admitted user which
pages the owner keeps. The answer is clipped at `MEMORY_SEARCH_MAX_CHARS` (6000)
on a block boundary, never mid-page.

### Events (`TurnEvent.type`)

`turn.start`, `user.message`, `thinking.delta`*, `thinking`, `text.delta`*,
`text.block`, `tool.call`, `tool.result`, `file`, `ask.open`, `ask.answer`,
`turn.end`. The two marked `*` are `TRANSIENT`: they reach listeners and the
in-flight snapshot, never the database.

`turn.start` carries `model` and `effort` — what the turn ASKED for. `turn.end`
carries `model`, `effort` and `fallback_from` — what ANSWERED, and the model it
was asked for when those differ (`null` otherwise). Both keys are on **every**
one of those events, including the synthetic pair `TurnRunService._announce`
emits for a turn that failed before its body ran and the reaper's
`LOST_END_PAYLOAD`, where they are `null` (`domain/events.NO_MODEL_END_KEYS`):
a consumer of `turn.end` never needs a special case for where the end came
from.

### The `turns` table (migration `0004_turns.py`)

```
turns(id, session_key, channel, user_id, text, attachment_ids, status,
      created_at, started_at, finished_at, lease_until, worker_id,
      cancel_requested_at, reply, error)
CREATE UNIQUE INDEX turns_one_active_per_lane ON turns (session_key)
  WHERE status IN ('queued', 'running');
CREATE INDEX turns_active_lease ON turns (lease_until) WHERE status IN ('queued','running');
CREATE INDEX turns_finished     ON turns (finished_at) WHERE finished_at IS NOT NULL;
```

`status` is `queued | running | ok | error | cancelled | lost`. Every
`TurnRepository` method is **one** statement; nothing there is check-then-write.

### A turn's lifecycle

1. **Create.** The route (or the Telegram adapter) calls `TurnLauncher.start`, which
   inserts a `queued` row with `lease_until = now + turn_queue_timeout_seconds` and
   enqueues `run_turn(turn_id)`. The insert is the lock: a second lane-mate gets the
   index's `IntegrityError` as `LaneBusy` → `409 busy`. If the dispatch fails the row
   is finished `error` and the exception is re-raised, so a lane is never held by a
   task nobody enqueued.
2. **Claim.** `TurnRunService.run(turn_id)` claims the row — `queued` only — and sets
   `status='running'`, `worker_id` and a fresh lease. `None` from `claim` means
   cancelled before pickup, reaped, or already claimed: a logged no-op, never a
   second execution.
3. **Run.** The heartbeat extends the lease every `lease/3` seconds and reports
   `cancel_requested_at` back; the SDK loop publishes events through `TurnBus`, which
   mirrors each one into the `SnapshotStore` **before** publishing it.
   A failure *before* the body is entered — a blob fetch that raised, an attachment
   id that does not resolve against this row's lane — would otherwise write a
   terminal row with no event at all, and a browser watching the lane would wait
   forever. So `TurnRunService` emits the envelope itself: `turn.start`, then
   `turn.end {"status": "error", …}` with the same keys the runner's own end
   carries, before the row is written. `turn.start` first, because every consumer
   (the snapshot store, the Telegram presenter) registers a turn on that event and
   drops a bare end. The emit is best-effort — a bus that is down must not change
   the row.
4. **Finish.** Every terminal write carries `worker_id` and runs under
   `asyncio.shield`, so neither a second cancel nor a reaper can leave the row
   half-written. `finish` returning `False` is logged (`turn_finish_ignored`) with the
   row's real status.
5. **Reap.** `TurnLauncher.reap(now)` marks every expired active row `lost`, emits a
   synthetic `turn.end {"status": "error", "error": "worker lost: lease expired", ...}`
   on the lane, clears the snapshot and prunes rows finished more than
   `turn_retention_days` ago. **A lost turn is not re-executed** — re-running an SDK
   loop that may have half-completed would double its side effects and its cost.
   "Reclaimable within the lease period" is a claim about the lane, not the turn.

Cancel is **flag first, nudge second**: `request_cancel` is one UPDATE (which also
turns a `queued` row `cancelled` outright, so it never runs), then a message on
`hermes:ctl`. The heartbeat is the fallback when the nudge is lost, so cancelling
costs at most a third of a lease even with no pub/sub at all.

## Invariants

* **One model question per turn, asked before the first event.** `TurnService`
  calls `LaneModels.effective(session_key, owner_id)` right after it resolves the
  lane owner and before `turn.start`, puts the `Choice` on `TurnContext`
  (`model`, `cli_model`, `effort`) and never asks again. A port failure logs and
  runs the deploy default — a lane that cannot be resolved still answers. Every
  model string reaching the CLI goes through `core.model_catalog.cli_id`, and
  every comparison of two model ids through `lookup`, so `claude-opus-5[1m]` and
  `claude-opus-5` are one model everywhere.
* **The label names the model that ANSWERED** (P5). `SdkTurnExecutor` returns
  `lookup(AssistantMessage.model).id` of the last MAIN-LOOP message; the CLI's own
  Haiku side call is a subagent message and `<synthetic>` is its stand-in for "no
  model answered", so neither can become that id. The pair that ran is what
  `bind`/`touch` record on the session row, not the pair that was requested.
* **At most two executions per turn, and a rejected model never resets a lane.**
  `SdkTurnExecutor` raises `ModelRejected(requested, error_text)` when a call
  failed before any `tool.call` and the CLI reported `model_not_found` or an
  `api_error` 400/404 — facts off the messages, never the exception's text, which
  can be just the result subtype. `_run_turn_body` then re-runs the turn ONCE on
  the deploy default with the SAME resume id, prefixes the reply with "<M> is
  unavailable right now — <X> answered instead." (and emits it as a `text.block`)
  and sets `fallback_from`. The transcript holds that prompt twice afterwards,
  which was measured live and accepted. A `ModelRejected` for the deploy default
  itself, or on the retry, is raised as it is: a reset cannot make a refused model
  exist. Every other failure keeps the reset-and-retry-once path, and
  `is_cli_death` walks `__cause__` so the `CliCallFailed` wrapper does not change
  which failures take it.
* **An errored `AssistantMessage` is never a `text.block`.** It is the CLI
  reporting a failure ("API Error: 429 …"), not the assistant talking, so its text
  goes into the exception the call raises — and so into `turn.end.error`, which
  would otherwise carry only the SDK's result subtype.
* **One grants question per turn, asked inside the envelope (0009).** At the
  top of `_run_turn_body` — after the `/model` short-circuit, before `route()` —
  `_with_grants` asks `LaneGrants.grants(ctx.owner_id)` for a non-owner lane and
  puts the answer on the context (`shell_enabled`, `learning_enabled`); an owner
  lane never asks (`is_owner_lane` already gives it everything), and a service
  with no port (a fork, a test) leaves both at their **False** defaults. A lookup
  that raises is not caught: the turn has already emitted `turn.start`, so it ends
  `turn.end {status: "error"}` — never on a guessed grant, because a wrong False
  would reset the lane under a notice that lies (P5). Nothing is cached per
  process: a grant flipped in Settings → Users reaches the lane's next turn in
  whichever process runs it. `TurnSettings` no longer carries
  `users_shell_enabled` / `users_learning_enabled` — the frozen boot copy was what
  made a flip need a restart; those keys are now only identity's defaults.
* **`TurnContext.has_shell` (`is_owner_lane or shell_enabled`) is the one shell
  value.** The tool policy is built from it (`sdk_runner` passes
  `ctx.shell_enabled` with `is_owner_lane`), the session row is stamped with it
  on `bind`/`touch`, and the next turn compares against it. Nothing re-derives it.
* **The lazy reset (0009): a session built with another toolset does not carry
  on.** Right after `route()`, before the cron context and the append are chosen
  from the row, `_session_shell(row, ctx)` reads the row's `shell_enabled` stamp —
  or, for a NULL stamp (a session begun before 0009, or by a process that
  predates it), the rule it was built under: `is_owner_lane or
  grant_defaults().shell`. If that differs from `ctx.has_shell` the turn calls
  `self._sessions.reset(session_key)` (never `TurnService.reset`, whose lane lock
  `run_turn` already holds), drops the row so the turn runs without `resume` on a
  fresh `build_system_append`, logs `shell_grant_changed`, and emits
  `SHELL_CHANGED_NOTICE` ("Your shell access (Bash, Agent, Task) was changed by
  the owner, so this conversation starts a fresh session.") as a `text.block`
  before the answer; the returned reply (what Telegram sends) is prefixed with
  the same line, joined with a blank line to the model-fallback notice when both
  apply. An equal NULL stamp resumes as before and is stamped by the turn's
  `touch`. The HTTP route that changes a grant resets nothing, so the reset
  cannot race a running turn's write-back. **Accepted limit:** the model is
  resolved by `_effective` before `route()`, so a lane reset for its shell starts
  its fresh session on the model the old session ran — the same as the
  resume-retry path; `bind` then records the pair that answered.
* **A stopped turn keeps its session.** The executor reports the id from the
  CLI's `init` message through `on_session_id`; if the turn is cancelled before
  its `ResultMessage` on a lane with no row yet, `_run_turn_body` binds that id
  before re-raising, so the next message resumes the interrupted conversation
  instead of starting a new one.
* **`/model` and `/effort` never reach the CLI.** The CLI runs both as its own
  local commands in non-interactive mode, which would move the session's model
  with nothing recorded on any row. `core.model_catalog.is_model_command` is the
  one guard every path uses: `POST …/messages` answers `400 model_command`, and
  `_run_turn_body` short-circuits any other path with
  `policies.MODEL_COMMAND_REPLY` and no CLI call. `/models` is an ordinary
  message.

**Prompt cache** (a break here is a cache miss on every lane, not a test failure):

1. `build_system_append` output is byte-stable for the same inputs; `TURN_RULES`
   and `WIKI_RULES` moved byte-identically.
2. Tool descriptions in `hermes_server.py` are byte-identical. The one deliberate
   exception so far: Wave 4b rewrote `send_document`'s description (relative
   paths resolve against the agent's home, anything outside it is refused), so
   every lane's cached prefix missed once after that upgrade — a cost paid
   knowingly, and the reason such a change is made once, not per wave.
   `ext_manage` is not described there at all: its name, description and schema ride in on the port as a
   `ToolSpec` from `modules/extensions/presentation/agent_tool.py`, which is where
   `tests/test_ext_manage_guidance.py` parses the paragraph it guards. A port that
   declares nothing raises — there is no local copy to fall back to. `content_plan`
   follows the same rule, declared by `modules/content/presentation/agent_tool.py`.
   The list itself is built in ONE place, `hermes_tools(handlers)`, which
   `build_hermes_server` wraps and `SdkTurnExecutor.tool_fingerprint()` reads:
   sha256 over each tool's name, description and input schema (a type object as
   its name, JSON with sorted keys), in registration order, for the owner's
   private-lane probe `tool_names()` also builds. The composition root stores it
   under meta `hermes_tools`, so a reworded description — a built-in literal like
   `session_search` or a declared one like `content_plan` — resets every lane once
   at boot instead of missing the cache under warm sessions
   (`tests/test_tool_fingerprint.py`).
3. `allowed_tools` is `builtin_tools(is_owner_lane=, shell_enabled=) +
   mcp_tool_names + composed.tool_names`, in that order; the registration order in
   `hermes_server.py` is what produces the middle term, and the first term is
   `_BUILTIN_TOOLS` itself for an owner lane and a strict subset of it for a
   non-owner one. Under `bypassPermissions` this list only AUTO-APPROVES, so what
   actually removes a tool is `disallowed_tools` (`policies.denied_tools`).
4. `_build_prompt` returns a bare `str` when there are no attachments, and a
   **fresh** async iterator on every call otherwise (the resume retry runs the
   executor a second time).
5. `reset_all()` and `reset_owned()` never no-op.
6. `ctx.flags.reset_lane` is applied **after** `sessions.bind`, or the bind
   resurrects the session the tool just invalidated.

**Security** (moved verbatim):

* `send_document` joins a relative path onto the LANE OWNER's `agent_home` (the
  turn's cwd — what the seeded skills name, `outbox/x.png`), resolves, and
  refuses anything outside that `agent_home` (Wave 4b, D7). The boundary is
  `agent_home` and not the workspace root because for the deploy owner
  `root == CH_HOME`, so a group member on the owner's lane could otherwise name
  `/data/users/7/…` by absolute path and have it sent. Resolve-then-
  `is_relative_to` is kept, so `..` and a planted symlink are judged by where
  they land; `secrets.env` stays refused; delivery is only to `ctx.chat_id` —
  never to a chat the model names.
* `ask_user` exists only on a **private** lane and only when a presenter is wired;
  registering a stub port would expose it on a group lane.
* `ext_manage` fails closed: no acl or no turn context means nobody to authorize.
* `AskBroker.wait` uses `asyncio.wait` (not `wait_for`) and pops before reading
  `fut.done()`. Both are load-bearing; the docstring says why.
* `AskBroker.resolve` peeks before popping, so a stranger's click cannot destroy a
  question its owner is still looking at.

**Owner scoping (Wave 4a, D3/D7)** — the lane's owner is on `TurnContext.owner_id`:

* **`lane_owner` is applied ONCE per turn**, in `TurnService._lane_owner`, and
  carried. Everything the turn writes is stamped with the answer
  (`threads.owner_user_id`, `sessions.owner_id`, `messages.owner_id`,
  `turn_costs.owner_id`, and through the tools `files.owner_id` and
  `jobs.owner_id`) and everything it recalls is scoped to it. Re-deriving it at a
  write site is how the row and the workspace the turn actually runs in drift
  apart.
* **`owner_id` is not `user_id`.** `user_id` is the SENDER — an audit field, which
  is why `turns.user_id` has no foreign key. In a group lane a stranger's message
  writes rows owned by the deploy owner, and their `ask_user` keyboard still binds
  to them.
* **The deploy owner arrives as a callable**, `TurnService(deploy_owner=...)`,
  bound to `WorkspaceResolver.owner_id` — the same cached number the workspace
  resolution uses, so the two cannot disagree. `None` (the five runner test modules,
  the forks) means `0`: unscoped, the pre-4a behaviour, and a NULL column rather
  than a row claimed by user zero.
* **`session_search` and `cost_report` take `ctx`** and scope their reads to
  `ctx.owner_id`. Both are offered to every lane, so unscoped they would hand an
  allowlisted user the deploy's transcript and the deploy's spend.

**The turn as a row** (each of these is a one-line change away from a double turn or
a stuck lane):

* The unique partial index is the **only** lane lock. Nothing anywhere does
  check-then-insert; `is_busy` in the route is a courtesy that makes the common case
  quiet, and `LaneBusy` renders the identical 409 body when it loses the race.
* `claim` matches `queued` only; `heartbeat` and `finish` are guarded by `worker_id`;
  `finish` returns a bool and a refusal is logged, never swallowed.
* `TurnBus.emit` records into the snapshot store **before** it publishes, and
  `TurnBus.snapshot` awaits `publisher.ready(lane)` **before** it reads the store.
  Together those two orderings are what make the SSE stream at-least-once.
* `event_stream`'s order is `subscribe → DB replay → await snapshot → drain → yield
  snapshot → pending → tail`, and both of the last two stages run through the same
  `covered()` filter: persisted events by id/cursor, deltas by the per-turn seq
  watermark. Reordering it doubles text in the browser.
* Every `bus.subscribe(...)` whose caller must not miss the first event is followed by
  `await ...ready(...)` — the SSE stream (inside `snapshot`), the Telegram dispatcher,
  and `TurnLauncher.wait`.

**The workspace a turn runs in (Wave 4a, D6)** — `TurnContext.workspace` and
`TurnContext.is_owner_lane`:

* **It is resolved once, in `TurnRunService.run`**, from the row:
  `resolver.for_lane(session_key, channel, user_id)`. That object holds the row and
  is therefore the only thing that knows the key, the channel and the sender at the
  same moment. Everything below is handed the answer; nothing re-derives it. A
  resolution that raises ends the turn with an error rather than running it
  somewhere arbitrary.
* **`is_owner_lane` is `ws.root == ws.home`**, not a second ACL lookup:
  `Workspace.for_user(home, "")` is how — and only how — the deploy owner's row is
  built, so the workspace already carries the answer and the two cannot disagree.
* **`cwd` is `ws.root / "agent_home"`** and `env` is
  `{"CLAUDE_CONFIG_DIR": ws.cli_config_dir, "TMPDIR": ws.tmp_dir}`. Without the
  first, one user's `--resume` replays another's conversation; without the second,
  every lane shares `/tmp`. For the deploy owner all three are byte-identical to
  Wave 3b, which is why a live volume needs no migration.
* **The wiki is per lane.** `TurnService` takes `wiki_for(workspace)` and builds the
  lane owner's `WikiStore` per turn, so `build_system_append` and `_index_seen` read
  THAT index; `WikiHooksProvider.build(..., workspace)` points the `raw/` guard, the
  page-touch recorder and the Stop gate at the same tree. `sessions.index_mtime` now
  means "mtime of that lane's owner's index".
* **The toolset is per lane, and `disallowed_tools` is what enforces it.**
  `policies.denied_tools(is_owner_lane=, shell_enabled=)` fills
  `ClaudeAgentOptions.disallowed_tools` with `Bash`/`Agent`/`Task` for a non-owner
  lane unless its owner holds a shell grant (`ctx.shell_enabled`, resolved per turn
  — see above) — under `permission_mode="bypassPermissions"`
  the ALLOW list is an auto-approve list, not a toolset, and a lane with `Bash`
  merely absent from it still runs `Bash`. `policies.builtin_tools` FILTERS
  `_BUILTIN_TOOLS` for the allow list (never rebuilds it — the order is cached
  prefix). The prefix is now per user rather than shared — a real NFR-1 cost,
  paid knowingly.
* **Tool presence is a property of the LANE, never of the sender** (Wave 4b,
  D6/B-5). One predicate, `core.session_keys.is_group_lane(session_key)`
  (`tg:group:*`, `tg:supergroup:*`): on a group lane `skill_admin`,
  `session_search` and `cost_report` do not exist for ANYBODY, the owner
  included (`make_handlers(skills=None, search=None, costs=None)` in
  `sdk_runner.py`) — the lane's library, recall and spend are the deploy
  owner's, and a per-sender flip (4a's `stranger` rule) changed the MCP schema
  list between two turns of one session, a cached-prefix miss the lane never
  recovers from (P1). On every other lane (`web:`, `tg:private:`, `cron:`) the
  sender is the owner structurally and all three exist, scoped to
  `ctx.owner_id`. Group lanes lost those three tools once at the 4b upgrade —
  their prefix changed too.
  `content_plan` follows the same rule and one more: it exists only on the
  DEPLOY owner's lanes (`SdkTurnExecutor._content_for` — `is_owner_lane is
  True`, not a group lane, `owner_id > 0`), so another user's private or web
  lane never has it either.
* **The shared wiki is a read root for a non-owner and a denied write root for
  them too.** `_allowed_roots` appends `ws.shared_wiki_root` — the deploy owner's
  `wiki/shared/`, the one path outside a user's own tree they may read — when
  `not ws.is_owner`, and `_build_hooks` installs a `WRITE_TOOLS` matcher with
  `readonly_guard_factory([ws.shared_wiki_root, ws.wiki_root / "shared"],
  cwd=ws.agent_home)` beside it. **Both** spellings are fenced: a model told a
  read-only shared wiki exists will try `Write("wiki/shared/x.md")`, a relative
  path that resolves into its OWN tree and would quietly succeed as a private
  page nobody else ever sees — the deny message is what teaches it the
  difference. The owner gets neither: for them the two paths are one path,
  already inside `agent_home`, and it is theirs to write.
* **`path_guard_factory` is installed for every lane, the owner included**, over
  `[ws.agent_home, ws.tmp_dir, ws.logs_dir, ws.plugins_dir, catalog_root]` (plus
  `ws.shared_wiki_root` for a non-owner). It is
  defence in depth, not a boundary: same uid, TOCTOU-racy, and `Bash` is not guarded
  at all. `send_document`'s containment root is `ws.agent_home` (see Security).
* **`observer.after_turn` runs on an owner lane always, on any other lane only
  with the lane owner's learning grant** (`ctx.is_owner_lane or
  ctx.learning_enabled` — Wave 4b D3, per user since 0009). The call carries
  `owner_id=ctx.owner_id`, so learning bumps the LANE owner's sidecar and
  counters and dispatches the review in their scope. Without the grant the
  service logs once per process (`_say_unobserved`: "a non-owner lane without a
  learning grant is not observed by the learning loop") and a non-owner lane is
  exactly what 4a made it — their library,
  `skill_admin` and the Skills page are theirs regardless; what is withheld is
  the background work, which is spend on the owner's account, and nothing in
  their reply may claim it happened.

**Other:**

* The bus keys active turns `lane -> turn_id -> state`, on purpose: a cron delivery
  and a live turn share a lane.
* The SDK seam is `sdk_runner.query`. It is patched there, on the module that calls
  it.
* Meta keys, session-key formats, `mcp__hermes__*` names and `cron:<id>` lanes are
  not renamed.

## Known debts

`main.build_app()` builds `TurnService` with its ports and `TurnEventRepository(engine)`
directly. Step 5 deleted the legacy files around this module — `tools.py`
(`make_handlers` / `build_hermes_server` under the old keyword head, `_ports`,
`_OneScope`, the `create_job` forwarders), `runner.py` (`AgentRunner(cfg, home,
db, router, wiki, bus=)`), `turnbus.py` (`TurnBus(db)` over `_DatabaseEvents`),
`web/{turns,commands,voice}.py` (`_TurnDeps`, `LegacySlashSources`, the
`routes(deps)` helpers), `guard.py`, `prompt.py`, `gateway/questions.py` — and
the two fallbacks that existed for them: `events_after`'s `deps.db` branch
(it reads `deps.turn_events` only, for `sse.event_stream`'s `StreamDeps`) and
`commands_router`'s `runner.slash_commands` (the `/` menu reads `sdk_commands`,
`slash_sources` and `home` and nothing else). No tool description changed: the
tool schema JSON on a fixed port set is byte-identical before and after.

| Where | What is still there | Who takes it |
| --- | --- | --- |
| `presentation/dependencies.TurnLauncherPort` | a consumer-side port declared in `presentation/` rather than `application/ports.py`, because it names only the three methods a route may call. Worth folding into `ports.py` if a second consumer ever wants the same three. | whenever there is a second consumer |
| `domain/policies.py` | `check_command` / `_RULES` live in `integrations.claude_sdk.guards` (Phase A put them there), not here as the brief proposed | nothing — the brief predates Phase A |
| the Telegram reply after an agent restart | Open Question 2: the **agent** sends the Telegram reply, from `TurnLauncher.wait`. A turn that outlives an agent restart still finishes in the worker and is visible in the web thread — but nobody is left waiting on that lane, so its Telegram message is not re-sent. Closing this means moving the send to the worker, which is Step 4's webhook-mode decision. | Step 4 |
| the spinner opens later | between the 202 and the worker's claim there is no `turn.start`, so the browser's spinner appears a little later than when the coroutine was local. The 202 carries `turn_id` so a later SPA can render optimistically; the SPA is not changed in this wave. | a later SPA change |
| a Telegram status draft bound to the wrong turn | the dispatcher subscribes the `Presenter` and awaits `ready` **before** `router.handle` (it must not miss `turn.start`). On a busy lane `QueuedTurnRunner` then retries `start` until the lane frees, so a second message's presenter is live while the FIRST turn is still `queued` — and `Presenter.on_turn_event` binds on any telegram `turn.start`. The cost is a duplicated status draft, never a wrong reply; the fix is an `on_started(turn_id)` callback threaded through telegram's `TurnRunnerPort`, `MessageRouter.handle` and `QueuedTurnRunner.run_turn` plus a late-bind rule in the presenter. | named, not fixed |
| the reap false positive | a worker that is alive but stalled past its lease (a long GC pause, a DB blip that starved the heartbeat) gets its lane reaped under it: the user sees "Something went wrong", and the real turn's `finish` is later refused by the `worker_id` guard and logged. The worker cancelling itself when its own clock passes `lease_until` is what keeps that window to about one heartbeat interval. | named, not fixed |
| `application/dto.py` | never created. Wave 2b gave the module `presentation/schemas.py` for the three request bodies; the RESPONSE shapes are still dicts built in the router, on purpose (plan open question 1 — a `response_model` would be a second copy of a frozen dict). | open |

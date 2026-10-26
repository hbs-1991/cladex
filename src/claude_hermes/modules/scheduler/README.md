# `scheduler`

## Purpose

Scheduled jobs: parse a human schedule, admit or refuse a prompt, decide on every
tick which jobs fire, run each one in an unattended fork, and deliver the report
to a chat topic and onto the lane's event stream.

Two channels create jobs — the `cron_manage` agent tool and the web Routines page
— and both go through `SchedulerService.create_job`, so they cannot validate a
schedule, a prompt or a timeout differently. That is the module's reason to exist.

A routine also carries its own **(model, effort)** (`jobs.model`, `jobs.effort`,
migration 0008). NULL means "inherit", exactly as `timeout_seconds` does.

Four schedule kinds: `once` (an ISO instant), `interval` (seconds), `cron` (five
fields, evaluated in UTC) and **`manual`** (value `""`) — an on-demand routine that
is `active` with `next_run_at` NULL, fires only through `run_now` or
`wake_if_idle`, and goes back to waiting after each claim instead of going `done`.
It is created from the Tasks form or the "Content publisher" template; the
`cron_manage` tool refuses it.

## Layout

```
domain/
  schedule.py    Schedule, parse_schedule, next_fire, period_seconds, is_on_demand,
                 fires_between, MANUAL, MAX_FIRES_PER_ROUTINE  (croniter)
  policy.py      scan_prompt + _RULES; job_timeout, MIN/MAX_JOB_TIMEOUT_SECONDS
  lane.py        delivery_lane(job) -> session key
  exceptions.py  ScheduleRejected (400 / "bad_request")
  models.py      Job, JobRun (ORM). Wave 4a adds `jobs.owner_id` (FK -> `users(id)`,
                 ON DELETE SET NULL, nullable, index `ix_jobs_owner`) — whose
                 routine it is, and therefore whose workspace the cron turn runs in.
                 0008 adds `jobs.model` / `jobs.effort` (nullable TEXT).
application/
  ports.py       everything the module needs from elsewhere; JobOutcome; STAY_IDLE
  service.py     SchedulerService: create/edit/tick/execute_job/deliver, wake_if_idle,
                 calendar; _MAX_CATCHUP, CALENDAR_MAX_DAYS, CALENDAR_DENSE_SECONDS
infrastructure/
  repository.py  JobRepository(engine) — the only jobs SQL, incl. claim_due
  dispatch.py    QueueJobDispatcher(queue) — enqueue("run_job", …); RUN_JOB_TASK
  executor.py    SdkJobExecutor — the unattended fork (claude_agent_sdk)
  ticker.py      CronTicker(service, tick_seconds) — the asyncio loop driver
presentation/
  router.py      router: APIRouter   (/api/routines*)
  schemas.py     to_wire(job); RoutineCreate, RoutinePatch
  dependencies.py get_scheduler
  tool.py        build_cron_tool(service, ctx) -> (handler, definition)
```

## Public surface

### Ports consumed (`application/ports.py`)

| Port | Methods | Satisfied today by |
|---|---|---|
| `JobRepository` | `create(..., next_run_at: str \| None, owner_id=None, model=None, effort=None)`, `get(job_id, owner_id=None)`, `by_thread`, `list_jobs(owner_id=None)`, `due(now_iso)`, `claim_due(now_iso, next_of=None, limit=100)`, `clear_lease(job_id)`, `reschedule`, `set_state`, `update(**fields)`, `record_run`, `runs`, `runs_between(owner_id, start_iso, end_iso)`, `wake_if_idle(job_id, now_iso) -> bool`, `delete` | this module's `infrastructure.repository.JobRepository` |
| `JobExecutor` | `async run(job) -> JobOutcome` | `SdkJobExecutor(cfg, home, *, composer=, recorder=, workspaces=, grants=, routine_tools=)` |
| `JobDispatcher` | `async dispatch(job_id: int, fire_at: str)` | `infrastructure.dispatch.QueueJobDispatcher` over `core.tasks.TaskQueue`; `None` runs the job in the ticker's own coroutine |
| `ReportDeliverer` | `async deliver(chat_id, text, thread_id=None)` | telegram's `RichSender`, through `core.app_context.SenderDeliverer` |
| `TopicProvisioner` | `async create(chat_id, name) -> int \| None`, `async close(chat_id, thread_id)` | telegram `Topics` |
| `TurnEventPublisher` | `async emit(session_key, turn_id, type, payload)` | turns' `TurnBus`, passed directly |
| `ThreadRegistry` | `async ensure(session_key, channel, owner_id, now_iso, *, talked=False)` | sessions' `ThreadService.touch`, through `core.app_context.ThreadEnsure` |
| `MessageRecorder` | `async record(session_key, role, content, ts, *, owner_id=None)` | sessions' `MessageRepository` |
| `ExtensionComposer` | `async for_scope(scope) -> ComposedExtensions` (`.mcp_servers`, `.tool_names`, `.plugins`) | extensions' `ComposeService` |
| `OwnerGrants` | `async grants(user_id) -> Any` (an object with `.shell`; the executor reads nothing else) | identity's `AccessControl` (0009) |
| `RoutineTools` | `for_owner(owner_id, *, is_deploy_owner, job_id) -> (mcp_servers: dict, tool_names: list[str])` (a coroutine answer is awaited too) | the content module's routine server (`mcp__hermes__content_plan`), when `content_enabled`; `None` otherwise |

The right-hand column is what `main.build_app()` wires — `JobRepository(engine)`
directly, since Step 5 deleted the `cron/` package (`JobStore(db)`, the
`cron/adapters.py` wrappers, the `CronTicker(cfg, home, db, …)` head and
`web/routines.py`).

`ReportDeliverer` and `TopicProvisioner` are **late-bound**: they exist only after
the aiogram Bot, and `build_app()` must stay headless. `SchedulerService` accepts
both as `None` and takes them later through `bind_delivery(deliverer=, topics=)`.
A deployment with neither degrades to flat, Telegram-less operation — it does not
fail.

### Ports provided

`SchedulerService` itself is the port other modules consume:

- turns needs only `by_thread(chat_id, thread_id) -> dict | None` (its `JobLookup`
  port — the cron→topic context bridge in `runner._with_cron_context`);
- turns' tool registry consumes `presentation.tool.build_cron_tool`;
- the content module's `Publisher` port is satisfied in `main.py` over
  `get(job_id, owner_id)` (its `describe`) and **`wake_if_idle(job_id, now)`** —
  `now` a datetime or an ISO string, normalised to UTC. One statement:
  `UPDATE jobs SET next_run_at=:now WHERE id=:id AND state='active' AND
  kind='manual' AND next_run_at IS NULL AND (lease_until IS NULL OR lease_until <
  :now) RETURNING id`, `True` only when this call set the wake. It never clears a
  lease — which is why the content tick uses it and never `run_now`.

### Dependencies

`presentation/dependencies.py`, built on `core.di.provide`. One provider, because the
router has one collaborator; a test points an app at another service with
`app.dependency_overrides[get_scheduler]`
(`tests/modules/scheduler/test_scheduler_router.py`).

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_scheduler` | `scheduler` | `SchedulerService` |
| `get_cfg` | `cfg` | the deploy `Config`, read for `models` / `model` alone |

That is the whole list. Who is asking comes from `core.auth`:
`user: CurrentUser = Depends(require_owner)` on create (it needs `user_id` for the
delivery lane) and `dependencies=[Depends(require_owner)]` on the other five
mutations, where a route-level dependency is solved first and the 403 is raised
before any other provider runs — the position `owner_only(request)` held.
**Whether this router is mounted at all is the shell's decision**
(`deps.scheduler is not None`).

### Routes (paths frozen)

`router: APIRouter(tags=["scheduler"])`.

| Method | Path | `operation_id` | Owner-only |
|---|---|---|---|
| GET | `/api/routines` | `scheduler_list_routines` | no |
| GET | `/api/routines/calendar?from=&to=` | `scheduler_routines_calendar` | no |
| POST | `/api/routines` | `scheduler_create_routine` | yes — 201, body `RoutineCreate` |
| POST | `/api/routines/{id:int}/pause` | `scheduler_pause_routine` | yes — 204 |
| POST | `/api/routines/{id:int}/resume` | `scheduler_resume_routine` | yes — 204 |
| POST | `/api/routines/{id:int}/run` | `scheduler_run_routine` | yes — 204 |
| GET | `/api/routines/{id:int}/runs` | `scheduler_list_runs` | no |
| GET | `/api/routines/{id:int}` | `scheduler_get_routine` | no |
| PATCH | `/api/routines/{id:int}` | `scheduler_edit_routine` | yes — body `RoutinePatch` |
| DELETE | `/api/routines/{id:int}` | `scheduler_delete_routine` | yes — 204 |

Reads are open to any logged-in user; every mutation is owner-only. **Since Wave
4a every route is also scoped to the asking user** (`jobs.owner_id`): the reads
were never owner-gated, so before the column existed `GET /api/routines` listed
the whole deploy's routines to anybody who could log in and `GET /api/routines/{id}`
read any of them. Somebody else's routine is a **404**, never a 403 — the same rule
the thread guard follows, and for the same reason. The mutations keep
`require_owner` in the decorator's `dependencies=` (the 403 is raised before any
other provider runs) and the handlers that also need the id declare it a second
time as a parameter; FastAPI caches a dependency by its callable within one
request, so that is one call and one 403.

`{id:int}` keeps its converter in the path so a non-numeric id never reaches a
handler — it falls through to the app's `/api/*` catch-all and its 404 — while the
parameter is a real `id: int`.

**`GET /api/routines/calendar?from=&to=`** is scoped like the list and answers
`{"fires": [{"routine_id", "name", "at"}], "dense": [{"routine_id", "name",
"every_seconds"}], "runs": [{"routine_id", "name", "at", "status"}]}` for
`from <= t < to` (`SchedulerService.calendar`). Fires are the future fires of
`active` routines from `max(from, now)`, walked forward from each routine's own
`next_run_at` (`fires_between` — an interval job fires at `claim + period`, so the
window is not its anchor), at most `MAX_FIRES_PER_ROUTINE` (500) each; a routine
whose `period_seconds` is an hour or less is listed once in `dense` instead; a
`manual` routine contributes no fires. `runs` are `job_runs` rows in the window,
joined to the asker's jobs (`runs_between`). A reversed, empty, unparseable or
longer-than-62-days window is `400 bad_range`; a naive bound is read as UTC. The
content plan is not joined here — the SPA merges this with `GET /api/content`.
`to_wire` answers `kind: "manual"` with `schedule: "manual"` and `next_run_at:
null` between wakes.

`POST` and `PATCH` also carry the routine's **`model` / `effort`**, and `to_wire`
always answers with both (`null` = inherit). They are validated in the router, not
in the service, because this is the only channel that sets one — `cron_manage`'s
input schema is prompt-cache surface and was not reopened — and a refusal names the
field: `400 bad_model`, `400 bad_effort`, and `400 bad_prompt` for a prompt matching
`^\s*/(model|effort)(\s|@|$)`, which the CLI would run as its own local command
rather than as the job. `ScheduleRejected`'s frozen `400 bad_request` would tell the
form only that something was wrong. An effort never travels without a model: a patch
that changes either half carries both, and `model: null` clears the pair.

Both request models are `Any`-typed with defaults on purpose: `SchedulerService` owns
the validation and answers `ScheduleRejected` (400 `bad_request`) in the same words
the `cron_manage` tool uses, and a Pydantic 422 in front of it would replace a message
`tests/test_web_routines.py` pins. `RoutinePatch` is dumped with `exclude_unset=True`
because `edit` branches on `"name" in body`: an absent key must stay absent.

### Tools

The tool is scoped to the turn's LANE owner (`ctx.owner_id`), not to its sender:
`list` shows only that lane's routines, `create` stamps `jobs.owner_id` with it —
which is whose workspace each unattended run then happens in (D6) — and
`pause`/`resume`/`delete`/`run_now` resolve the id against it first, so a model
that guessed `job_id=3` gets `"No such job #3."` rather than somebody else's
routine. A fork with no lane (`ctx=None`) reads unscoped, and a routine it
creates carries no owner of its own — which since Wave 4b (D11) means **the
deploy owner's**: `SchedulerService(..., deploy_owner=<async callable>)` is
bound to `WorkspaceResolver.owner_id` by `main.build_app()`, and `create_job`
with `owner_id=None` resolves it at create time. Before that the row was NULL:
it ran in the owner's tree anyway (the executor's NULL fallback), but it was
invisible on the owner's Routines page and its delivery lane belonged to
nobody. On an unclaimed deploy `deploy_owner()` is `0` and the row stays NULL —
there is nobody to give it to yet. `deploy_owner=None` keeps the 4a behaviour
(the row stays NULL).

`mcp__hermes__cron_manage` — `build_cron_tool(service, ctx)` returns
`(handler, {"name", "description", "schema"})`. The turns registry wraps it with
`claude_agent_sdk.tool` and keeps the registration order. The tool is registered
only when a scheduler is wired (`tests/test_cron_tool.py`).

`CRON_TOOL_DESCRIPTION` and `CRON_TOOL_SCHEMA` are prompt-cache surface: a changed
byte invalidates every cached prefix. That is why `manual` is not advertised in
them: `create` with `schedule="manual"` answers the tool result `"On-demand
routines are created in the Tasks tab."` (`MANUAL_REFUSAL`) and creates nothing.

### Claiming and dispatch (Wave 3a)

A tick is two steps now, and the split is what lets N workers run jobs:

1. **`JobRepository.claim_due(now_iso, next_of, limit=100)`** — ONE transaction.
   `SELECT … WHERE state='active' AND next_run_at <= :now AND (lease_until IS NULL
   OR lease_until < :now) ORDER BY next_run_at LIMIT :n FOR UPDATE SKIP LOCKED`,
   then per row `UPDATE jobs SET next_run_at = next_of(row), claimed_at = :now,
   lease_until = :now + max(timeout_seconds, 300s)`. `next_of` is the service's
   `next_fire` over the row's schedule, passed in so the repository never imports
   a schedule; returning `None` from it marks the row `done`, exactly as
   `reschedule(id, None)` does, and returning `STAY_IDLE` (the service's answer
   for a `manual` job) writes `next_run_at` NULL and leaves the row `active`. Each returned row carries an extra **`fire_at`** —
   the `next_run_at` the claim consumed — and is otherwise the pre-update snapshot.
2. **`SchedulerService.tick(now)`** reads the catch-up window off `fire_at`, records
   `missed` (and releases the lease) for a stale fire, and otherwise calls
   `JobDispatcher.dispatch(job_id, fire_at)`.
3. **`SchedulerService.execute_job(job_id, fire_at, now=None)`** is the body a
   worker runs: re-read the row (gone → log and drop), `executor.run`, `record_run`,
   `_emit_delivery`, `deliverer`, and `clear_lease` in a `finally`.

The lease guards only the window between the claim and a worker picking the task
up. A run that outlives its lease is *not* re-claimed — `next_run_at` has already
moved past it. `run_now` clears the lease as well as rescheduling, or a job claimed
a minute ago would not fire until its lease expired.

`run_now` latency is `cron_tick_seconds` (30 s) in inline mode and up to 60 s in
redis mode, where the periodic `scheduler_tick` task drives the same `tick(now)`.

**How many run at once is the queue's decision, not the ticker's.** `tick` hands
every claimed fire to the dispatcher and returns, so N routines due on the same
minute are N enqueues. In redis mode the answer is the number of worker
containers. In inline mode there is no such dial, so `main.build_app()` builds
`InlineQueue(registry, {RUN_JOB_TASK: 1})`: one `run_job` at a time, which is
what the ticker did when it awaited each job itself. Without it a busy minute
forks N Claude CLI processes inside a container sized for one.

### Which model a routine runs on

One resolver for every channel (`core.model_catalog.resolve`), applied by
`SdkJobExecutor._choice`: **`jobs.model`/`jobs.effort` → `cfg.cron_model` →
`cfg.model`**, with no effort at the last two steps. A `cron:` lane is shared
(`lane_is_shared`), so no personal default reaches it, and a job pinned to a model
that has since left `models:` falls through to the deploy pair instead of failing —
the same for an effort the model does not take, which becomes "send no effort flag".
The CLI is handed `cli_id` and nothing else, and `effort` is passed **only** when the
pair carries one.

`run` answers with a `JobOutcome` (`status`, `text`, the resolved `model`/`effort`,
and `ran` — the model that actually answered, off the main-loop `AssistantMessage`;
a subagent's message, an errored one's synthetic name and the CLI's own haiku side
call are not it). `JobOutcome.of` still accepts the bare `(status, text)` the port
answered with before models were a choice.

A stored prompt for which `is_model_command()` is true is a **failed run with no CLI
call at all**: `_consume` hands the prompt straight to `query`, and the CLI executes
`/model` and `/effort` as its own local commands in non-interactive mode. The web
form refuses such a prompt at create and edit; this catches the row that got in
before it, or through `cron_manage`.

### Events

On each delivery, onto `delivery_lane(job)` (not the isolated `cron:<id>` SDK
session): `turn.start` (`channel="cron"`, `job_id`, `run_id`, plus `model`/`effort`
— the pair the executor RESOLVED), `text.block`, `turn.end` (`usage: None` — a
delivery fills no context window — plus `model` (what actually ran), `effort` and
`fallback_from`, null unless something other than the resolved model answered).

## Invariants

1. **At-most-once per fire.** The reschedule happens *inside* `claim_due`'s
   transaction, in the same statement pair as the `FOR UPDATE SKIP LOCKED` that
   found the row. Moving it out — back into a second `reschedule()` call after the
   claim — reopens the window in which two ticker processes both see the same fire.
   `tests/modules/scheduler/test_scheduler_claiming.py` is the proof.
2. **A routine runs as the person who created it** (Wave 4a, D6). `SdkJobExecutor`
   resolves `jobs.owner_id` through the workspace resolver and runs the fork in
   that user's tree: `base_options(ws.root, ...)` for `cwd`, and
   `env={**NO_ADVISOR_ENV, **workspace_env(ws)}` for the CLI's own transcript store
   and `TMPDIR`. The env is **merged, never replaced** — the SDK lays `env` over
   `os.environ` but not over itself, so a replace would drop
   `CLAUDE_CODE_DISABLE_ADVISOR_TOOL` and re-enable the advisor on every scheduled
   run, at the advisor's rates, with nobody there to be advised. A NULL
   `owner_id` — every row created before this wave — falls back to the deploy
   owner, which is where those jobs have been running all along; resolving `0`
   would raise `UnknownUser` and break them all.
3. **Catch-up window.** A fire older than `min(period/2, 2h)` is recorded as
   `missed` and skipped — nobody wants yesterday's brief at lunchtime.
4. **One create path.** `create_job` is the only place a job row is inserted, and
   its refusal strings are user-facing contract (`"Rejected: prompt looks unsafe
   (<reason>)."`, `"Rejected: one-shot time is in the past."`, the
   `timeout_seconds` bounds sentence, `parse_schedule`'s "Unparseable schedule…").
   Tests on both channels assert them verbatim.
5. **`scan_prompt` is a security control**, not a nicety: the fork runs with
   `Bash`/`Write`/`WebFetch` and no human at the keyboard. `_RULES` moved
   byte-identically.
6. **`env={**NO_ADVISOR_ENV, **workspace_env(ws)}` on every unattended fork** — an advisor call there
   resends the whole transcript at Opus rates.
7. **`delivery_lane` must keep producing the same string `build_session_key`
   produces** for the topic the user replies in, or a reply lands in a different
   SDK session than the report.
8. **`cron:<id>` session keys and the `mcp__hermes__cron_manage` tool name are not
   renamed.**
9. **No jobs SQL outside `infrastructure/repository.py`.**
10. The delivery lane gets its `threads` row at create time, not at first run: the
   Routines page mounts on that lane immediately, and every thread route 404s on a
   lane with no row.
11. **One job at a time in inline mode.** The queue decides concurrency, and the
   inline queue is given `{RUN_JOB_TASK: 1}` so a deployment with no workers keeps
   the ticker's old behaviour. `tests/test_core_tasks.py` pins the limit itself;
   removing it turns "five routines at 09:00" into five concurrent forks.
12. **`owner_id` on a read is the ASKING user; the ticker passes none.** The two
   are different on purpose: a request path must never see another owner's row,
   and the tick, the dispatcher and the executor run a claimed fire with nobody
   asking. Claiming stays deliberately deploy-wide — one clock claims every due
   fire, whoever owns it, and `SdkJobExecutor` resolves the job owner's workspace
   afterwards.
13. **The delivery lane's owner is `lane_owner(lane, "telegram", deliver_chat_id,
   owner_id)`** — the routine's owner standing in for the deploy owner. Without it
   a routine delivered to a group would be owned by nobody and would vanish from
   its creator's sidebar.
14. **Every model string the CLI sees goes through `cli_id()`, and every choice
   through `resolve()`.** A second spelling of the rule here would be a routine
   running on a different model than the one its form shows.
15. **A `/model` or `/effort` prompt never reaches the CLI** — refused on create and
   edit, and recorded as a failed run if it is already stored. The CLI runs both as
   local commands, so it would change the `cron:<id>` session's model with nothing
   recorded anywhere.
16. **`cron_manage`'s input schema takes no model.** A tool schema change is a
   cached-prefix change (P1/P2), and `main.run()` fingerprints the tool list.
17. **A routine's shell is its owner's grant at fire time** (0009). The deploy
   owner's routine (`workspace.root == workspace.home`) always gets `Bash`/`Agent`/
   `Task` and never asks; anyone else's asks `OwnerGrants.grants(owner_id).shell`
   when it fires (`SdkJobExecutor._shell_of`), so a change in Settings → Users
   reaches the next run with no restart. It fails closed on both edges: no port
   wired → no shell (there is no `users_shell_enabled` fallback — only identity
   reads that key), and a lookup that raises → logged, no shell, and the run still
   happens and is recorded. Unlike a turn, which must not guess, a routine has no
   session whose cached prefix a wrong answer would reset. Withdrawing a grant
   does not pause a user's routines: they keep running without the shell, and
   one that needed it shows that in its own report, in its delivery topic.
18. **A `manual` routine is never `done`.** The claim answers `STAY_IDLE` for it
   and the row keeps `state='active'` with `next_run_at` NULL; only `run_now` or
   `wake_if_idle` put a fire back. `tests/test_scheduler_manual.py`.
19. **`wake_if_idle` is one compare-and-set and never touches the lease.** No
   wake while a run is live or a wake is pending, and two concurrent callers get
   exactly one `True` (the row lock re-checks the WHERE). `run_now` keeps
   clearing the lease — it is the owner's button, not a machine's.
20. **Lent routine tools merge after the composed extensions** and go only to a
   run with a resolved owner (`owner_id > 0`): `mcp_servers={**composed,
   **lent}`, names appended last, so an extension named `hermes` cannot shadow
   `mcp__hermes__content_plan`. The port decides who is the deploy owner from the
   executor's `is_owner_lane`; a port that raises is logged and the run proceeds
   without its tools. `tests/test_cron_executor.py`.

## Known debts

| Debt | Paid by |
|---|---|
| ~~The `cron/` legacy package (`executor`, `store`, `ticker`, `adapters`, `scan`, `schedule`), `web/routines.py` with its `routes(deps)`, and `tools.py`'s `create_job` / `_job_timeout` forwarders.~~ **Deleted in Step 5.** `tests/test_paths_config.py` asserts `"env=NO_ADVISOR_ENV" in inspect.getsource(modules.scheduler.infrastructure.executor)`; the router reads `deps.scheduler` through `get_scheduler` and is mounted when `deps.scheduler is not None`. | done |
| ~~`claim_due` is `due` inside one transaction. Two ticker processes still double-run.~~ | **Wave 3a** — `FOR UPDATE SKIP LOCKED` + `claimed_at`/`lease_until`, reschedule inside the claim, `_catchup_window` reads `fire_at`, and the job's own `timeout_seconds` is the lease floor. |
| `CronTicker` is an in-process asyncio loop and is still what inline mode runs. In redis mode the periodic `scheduler_tick` task calls the same `tick(now)`. | done for redis mode; the loop stays for inline |
| The lease length is `max(timeout_seconds, 300s)` from the *claim*, not from the moment the worker starts. A task that sits on the queue longer than that has a lease that expires mid-run — harmless today (the fire is not re-claimed, because `next_run_at` moved), but it stops being a deadline the moment anything wants one. | 3b, with the turns lease |
| The `run_job` task name is spelled in `infrastructure/dispatch.py` (`RUN_JOB_TASK`) and again wherever the body is registered. `core.tasks` raises `UnknownTask` at the call site if they drift, so this is a lint-level debt, not a runtime one. | open |
| `TurnEventPublisher` is satisfied by the in-process `TurnBus`; Wave 3 points it at a Redis publisher. | Wave 3 |

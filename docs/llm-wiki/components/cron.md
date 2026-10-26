---
id: component.cron
kind: component
status: current
summary: Scheduled automations — cron_manage, job store, claim-based tick with catch-up, the on-demand `manual` kind woken by `wake_if_idle`, queue dispatch, an executor in the owner's workspace under their shell grant with tools the content plan lends, the calendar read, per-job topics.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:2b0200155b4f576745f50ee98399030740b94eb93114e7259098ef14bbfcf754
evidence:
  - path: src/claude_hermes/modules/scheduler/domain/schedule.py
  - path: src/claude_hermes/modules/scheduler/domain/policy.py
  - path: src/claude_hermes/modules/scheduler/domain/lane.py
  - path: src/claude_hermes/modules/scheduler/domain/models.py
  - path: src/claude_hermes/modules/scheduler/application/ports.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/repository.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/executor.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/ticker.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/dispatch.py
  - path: src/claude_hermes/core/tasks.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/core/db/migrations/versions/0003_queue_and_extension_state.py
  - path: src/claude_hermes/modules/scheduler/presentation/tool.py
  - path: src/claude_hermes/modules/scheduler/presentation/router.py
  - path: src/claude_hermes/modules/scheduler/README.md
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/modules/scheduler/presentation/schemas.py
  - path: src/claude_hermes/core/model_catalog.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/workers/taskiq_app.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/integrations/claude_sdk/guards.py
  - path: src/claude_hermes/integrations/claude_sdk/options.py
  - path: src/claude_hermes/modules/content/presentation/routine_server.py
relations:
  - component.content
  - decision.model-choice-is-lane-state
  - component.core-runtime
  - flow.scheduled-job
  - contract.state-db
  - component.web
  - decision.modules-and-ports
  - decision.redis-taskiq-scaling
---
# Cron

Wave 2 moved this component into `src/claude_hermes/modules/scheduler/`; the
`cron/*.py` shims it left behind went in Step 5 ([[decision.modules-and-ports]]). One
`SchedulerService` now serves both creation channels — the agent tool and the
Routines page — which is the module's reason to exist.

## Creation — the `cron_manage` tool

Jobs are created by the model calling `mcp__hermes__cron_manage`
(modules/turns/infrastructure/hermes_server.py; registered only when a scheduler is wired). `create` runs
`scan_prompt` (modules/scheduler/domain/policy.py) — a regex safety filter rejecting destructive
commands, secret-exfiltration and instruction-override patterns (English and
Russian) — then `parse_schedule`, rejects past one-shots, validates an optional
`timeout_seconds` via `job_timeout` (modules/scheduler/domain/policy.py, bounds 30–1800), **creates a
per-job Telegram forum topic** and stores its thread id as `deliver_thread_id`
(topic failure degrades to flat delivery), and upserts the delivery lane's
`threads` row there and then, so the Routines page can open on it before the
first run. Other actions:
`list`, `pause`, `resume`, `run_now`, `delete` — delete closes the job's
topic first (modules/scheduler/application/service.py).

**A routine belongs to somebody** (`jobs.owner_id`, Wave 4a D6/D7). From the
tool it is the turn's **lane** owner (`ctx.owner_id`, never the sender — in a
group lane those differ); from the Routines page it is the requesting user, and
every mutation there is `require_owner`, so that is the deploy owner. A create
with no owner at all (a lane-less fork) is given the deploy owner
(`SchedulerService(deploy_owner=)`, 4b D11), and NULL only on an unclaimed
deploy. Reads are scoped to the asking user on both channels — `list` shows the
lane owner's routines, `pause`/`resume`/`delete`/`run_now` resolve the id against
that owner first and answer "No such job" for anybody else's, and the Routines
routes answer a stranger's id with 404, never 403. The owner decides whose tree
each unattended run happens in, and whose shell grant it runs under (Executor,
below).

`cron_manage` has **no model parameter**, deliberately: its input schema is part
of every lane's cached prefix, and a new field would be a prefix change for every
session (P1/P2). A routine created from chat therefore has `jobs.model/effort`
NULL and runs on `cron_model`, then `model`; its own pair is set on the Routines
page. `POST/PATCH /api/routines` accept and return `model`/`effort`, validated
with `core.model_catalog.validate` in the router (`400 bad_model`, `400
bad_effort`), and refuse a prompt that is a `/model` or `/effort` command with
`400 bad_prompt`. A PATCH carrying only an effort clears the routine's model: the
pair travels whole, so an effort is never stored where the resolver would drop it
([[decision.model-choice-is-lane-state]]).

## Schedules (`domain/schedule.py`)

`parse_schedule` (modules/scheduler/domain/schedule.py) accepts four kinds: duration (`30m`,
`every 2h`) → `interval` in seconds; 5-field cron string (validated by
`croniter`, evaluated in UTC) → `cron`; ISO timestamp → `once`; and `manual`
(case-insensitive, value `""`). `next_fire` and `period_seconds` derive fire
times; the period feeds the catch-up window. Both answer `None` for `manual`.

**`manual` is the on-demand routine** (the content plan's publisher,
[[component.content]]). It is created `active` with `next_run_at` NULL — the one
kind `create_job` accepts with no first fire — so no tick ever claims it. It fires
only when `run_now` or `wake_if_idle` (below) puts a `next_run_at` back, and after
that claim it returns to `active` with `next_run_at` NULL instead of going `done`.
`edit` switches a routine to or from it. It is created from the Tasks form ("On
demand") or the "Content publisher" template, never from chat: `cron_manage
create` refuses `manual` with "On-demand routines are created in the Tasks tab.",
and the tool's description and schema stay byte-identical, because they are
cached-prefix surface ([[decision.prompt-cache-discipline]]).

## The tick: claim, then dispatch (Wave 3a)

A tick is two steps now, and the split is what makes N processes safe
([[decision.redis-taskiq-scaling]]).

1. **`JobRepository.claim_due(now_iso, next_of, limit=100)`**
   (modules/scheduler/infrastructure/repository.py) is ONE transaction:
   `SELECT ... WHERE state='active' AND next_run_at <= :now AND (lease_until IS
   NULL OR lease_until < :now) ORDER BY next_run_at LIMIT :n FOR UPDATE SKIP
   LOCKED`, then per row `UPDATE jobs SET next_run_at = next_of(row), claimed_at
   = :now, lease_until = :now + max(timeout_seconds, 300s)`. The reschedule
   **inside** the claim is the at-most-once guarantee: a concurrent ticker skips
   the locked rows and gets a disjoint set, and one arriving after the commit sees
   a `next_run_at` already in the future. `next_of` is the service's `next_fire`
   over the row's schedule, passed in so the repository never imports a schedule;
   `None` back from it marks the row `done`, and the sentinel `STAY_IDLE`
   (`application/ports.py`, the service's answer for a `manual` job) writes
   `next_run_at` NULL and leaves the row `active`. Each returned row carries an extra
   **`fire_at`** — the `next_run_at` the claim consumed — and is otherwise the
   pre-update snapshot. Migration `0003` added the two columns
   ([[contract.state-db]]).
2. **`tick(now)`** (modules/scheduler/application/service.py) reads the catch-up
   window `min(period/2, 2h)` off `fire_at`: a stale fire is recorded `missed`,
   its lease cleared, and skipped. Everything else goes to
   `JobDispatcher.dispatch(job_id, fire_at)` and the tick moves on — nothing is
   awaited to completion here.
3. **`execute_job(job_id, fire_at, now=None)`**
   (modules/scheduler/application/service.py) is the body that actually runs:
   re-read the row (gone between claim and run → log and drop), `executor.run`,
   `record_run`, `_emit_delivery`, `deliverer` (failures prefixed `⚠️`, delivery
   errors caught per-job), and `clear_lease` in a `finally`. One-shot jobs are
   already `done` — the claim did that.

`QueueJobDispatcher` (modules/scheduler/infrastructure/dispatch.py) is one
`queue.enqueue(RUN_JOB_TASK, ...)`; a `None` dispatcher runs `execute_job` in the
ticker's own coroutine. **How many run at once is the queue's decision, not the
ticker's**: the number of worker containers in redis mode, and
`InlineQueue(registry, {RUN_JOB_TASK: 1})` inline, which is what keeps one process
from forking a CLI per routine due on the same minute.

The lease guards only the window between the claim and a worker picking the task
up. A run that outlives its lease is **not** re-claimed — `next_run_at` has moved
past it already. `run_now` clears the lease as well as rescheduling, or a job
claimed a minute ago would not fire until the lease expired.

**`wake_if_idle(job_id, now)` is the other way to fire a `manual` routine, and the
only one another module may use.** It is ONE statement — `UPDATE jobs SET
next_run_at=:now WHERE id=:id AND state='active' AND kind='manual' AND
next_run_at IS NULL AND (lease_until IS NULL OR lease_until < :now) RETURNING id`
— `True` only when this call set the wake, and it never touches the lease. The
next tick claims the wake like any fire. The content tick wakes its publisher only
through it, never through `run_now`: in redis mode `scheduler_tick` runs on
several workers at once, and a check-then-`run_now` would clear a live lease and
start a second publisher run beside the first. `now` may be a datetime or ISO text
and is normalised to UTC before the text comparison with `lease_until`.

## Who drives the clock

| | inline (`CH_REDIS_URL` unset) | redis |
|---|---|---|
| driver | `CronTicker.run_forever` every `cron_tick_seconds` (30 s), started by `main.run` | the `scheduler` container enqueues `scheduler_tick` on `* * * * *` |
| body | the same `SchedulerService.tick(now)` | the same `SchedulerService.tick(now)` |
| `run_now` latency | up to `cron_tick_seconds` | up to **60 s** — the cron granularity |

`CronTicker` is still what inline mode runs, with extras riding its clock:
`TurnLauncher.reap`, which frees the lane of a turn whose worker died, and — with
`content_enabled` — `ContentService.tick` after it ([[component.content]]), each
extra in its own `try` so a failed reap or plan cannot stop the cron clock.
`workers/tasks.py`'s `scheduler_tick` (the tick, then the same reap on the same
`now`, then the content tick when `ctx.content` is set, in its own `try` that logs)
and `run_job` are thin wrappers over `tick` and `execute_job`, so the decisions
live in the service whichever process asked. Because the content tick runs after
the scheduler's, a wake it sets is claimed on the NEXT tick.

`SchedulerService` also holds an optional `bus` (the `TurnBus`, wired in
main.py — in redis mode the worker builds its own through the same
`build_app()`). When present, `_emit_delivery` (modules/scheduler/application/service.py), called from `execute_job`, publishes the
report onto the **delivery lane** as a small turn: `turn.start` with
`{channel: "cron", job_id, run_id, model, effort}` (the pair the executor
resolved) → `text.block` → `turn.end{status, model, effort, fallback_from}` (the
catalog id of the model that answered), after
upserting that lane's `threads` row. These events describe the *delivery*, not
the run: the `cron:<job_id>` SDK session stays isolated as ever. That is what
makes a routine firing appear in the browser thread the user is reading, and
what the Routines page renders as run history ([[component.web]]). A bus failure
is caught and logged; delivery still happens.

## Executor (`infrastructure/executor.py`)

`SdkJobExecutor.run(job)` (modules/scheduler/infrastructure/executor.py) runs the prompt in session `cron:<job_id>` —
always fresh, never resumed, never spliced into a chat transcript (PRD
FR-26) — **in the job owner's workspace** (Wave 4a, D6): `_owner_id` is
`jobs.owner_id`, a NULL one being the deploy owner's (where every pre-4a routine
has always run), and the fork gets `base_options(workspace.root)` (so `cwd` is
that user's `agent_home`) and `env={**NO_ADVISOR_ENV, **workspace_env(workspace)}`
— their CLI transcript store and `TMPDIR`, merged rather than replaced so the
advisor stays off. Extensions are composed for scope `"cron"` **and that owner**
(`for_scope("cron", owner_id=...)`), so a routine carries its creator's
connectors and credentials. `CRON_APPEND` system prompt ("unattended scheduled
job, no user present") plus today's date; `max_turns`; `PreToolUse` hooks —
`bash_guard` on `Bash`, and the same `path_guard_factory` confinement a
foreground turn runs under over `PATH_TOOLS`, rooted at the owner's
`agent_home`/`tmp`/`logs`/plugin clones with `cwd=agent_home`.

**The toolset is `_TOOLS` — `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`,
`WebSearch`, `WebFetch` — plus the composed extension tools, and the shell in it
follows the turn's rule.** `is_owner_lane = workspace.root == workspace.home`
(decided from the resolved workspace, as the turn's runner decides it): the deploy
owner's routine always keeps `Bash` and `disallowed_tools` is empty, and it never
asks anybody. A non-owner's routine asks `_shell_of(owner_id)` **at fire time** —
identity's `AccessControl.grants(owner_id).shell` through the scheduler's
consumer-side `OwnerGrants` port (`application/ports.py`; wired
`SdkJobExecutor(..., grants=acl)` in main.py) — so a switch flipped in Settings →
Users reaches the next run with no restart ([[decision.multi-user-isolation]]).
Without a shell `allowed_tools` drops `Bash` and `disallowed_tools` is
`["Bash", "Agent", "Task"]` — under `bypassPermissions` only the second takes a
tool away. It **fails closed on both edges**: no port wired → no shell (there is no
`users_shell_enabled` fallback; only identity reads that key), and a lookup that
raises → logged, no shell, and the run still happens and is recorded. A turn
propagates such a failure instead, because a guessed answer there would reset a
live session under a notice that lies; a routine has no resumed session, so there
is no reset and no notice, and withdrawing a grant does not pause anybody's
routines — they keep firing without the shell. Hard
timeout `job["timeout_seconds"] or cfg.cron_timeout_seconds` (default 180s) via
`asyncio.wait_for` — the per-job column exists because a research sweep with
per-candidate `WebFetch` cannot finish in three minutes, and raising the global
value would hand the same rope to every other job. **The model** is resolved per
run with `core.model_catalog.resolve` — the job's pair, then `cfg.cron_model`,
then `cfg.model`; a pair whose model left `models:` falls through, an effort the
model does not take becomes `None` — and reaches the CLI as its `cli_id`, with
`effort` only when one is set (`CLAUDE_CODE_NO_MODEL_FALLBACK=1` rides in via
`workspace_env`). A stored prompt that `is_model_command()` matches is never sent:
the run is recorded as failed with a sentence saying why, because the CLI would
run it as its own local command. Returns a `JobOutcome(status, text, model,
effort, ran)` — `ok`/`timeout`/`error`, never raises; `JobOutcome.of()` still
accepts an executor answering the bare `(status, text)`. Prompt and output are
recorded into the `messages` corpus under the cron session key.

A scheduled run has **no hermes MCP server of the turn's**: `_TOOLS` plus composed
extensions is the whole toolset, so `send_document`, `session_search` and
`ask_user` do not exist there and `execute_job` delivers a text string only. A job
that needs to hand over a file has to stop at the text and let a chat turn do the
delivery — that is the split the content pipeline is built around
(`docs/linkedin-content-pipeline.md`).

**The one exception is lent, not built here.** `SdkJobExecutor(...,
routine_tools=)` takes the `RoutineTools` port (`application/ports.py`,
`for_owner(owner_id, *, is_deploy_owner, job_id) -> (mcp_servers, tool_names)`),
which main.py satisfies with the content module's routine server when
`content_enabled`: an in-process server named `hermes` holding exactly
`content_plan`, so the tool is `mcp__hermes__content_plan` in a routine as in chat
and one skill text works in both. `is_deploy_owner` is the executor's
`is_owner_lane`; the port answers `({}, [])` for anybody else, and the executor
never asks for `owner_id <= 0`. The servers merge AFTER the composed extension
servers (`{**composed.mcp_servers, **lent}`), so an extension named `hermes`
cannot shadow the plan, and the names are appended to `allowed_tools`. A port that
raises is logged and the run proceeds without the tools. The routine's tool calls
carry `origin="cron:<job_id>"`, which is what the plan records as `created_via` and
what lets the configured publisher — and no other routine — use its publisher-only
actions.

## Replying in a job's topic — the context bridge

A routine's delivery goes through `RichSender` (`SenderDeliverer`), never
through a turn, so the topic's chat session has not seen the report. The runner closes the gap:
`_with_cron_context` (modules/turns/application/turn_service.py) — on every turn in a thread that matches a
job's `deliver_thread_id` (`SchedulerService.by_thread`, modules/scheduler/application/service.py) — quotes the
job's latest recorded output (sessions' `MessageRepository.last("cron:<id>",
role="assistant")`, through the turn service's messages port) into the user message, but only when the lane has not run a
turn since that delivery (`sessions.updated_at <` the output's `ts`) — or when
it has no live session at all: a new lane, an expired one, or one the turn has
just reset because its owner's shell grant moved (that reset happens before the
bridge runs, so a fresh session gets the report re-injected too). Injection
is in the user message, not the system prompt, so the cached prefix is
untouched, and the injected string is what `messages` records — the
searchable history stays equal to what the model saw. A failure inside the
bridge logs and runs the turn without context, never kills it. Before this
bridge the daily brief ended "reply with a number", the user replied "3", and
the model asked what "3" meant (2026-08-01).

## Wiring

`main.build_app()` builds one `SchedulerService` over `JobRepository(engine)`,
an `SdkJobExecutor(cfg, home, composer=extensions.compose, recorder=messages,
workspaces=workspaces, grants=acl, routine_tools=...)` (content's routine tools, or
`None` with the plan off), the `TurnBus`, sessions' thread service
(`ThreadEnsure(threads.touch)`), a `QueueJobDispatcher` over the process's
`TaskQueue` and `deploy_owner=workspaces.owner_id`, and hands the same object to
the `cron_manage` tool (`CronToolPort`), the Routines router and the turn's
context bridge (`JobLookup`). Delivery arrives later: `run()` calls
`build_delivery`, which calls
`ctx.scheduler.bind_delivery(deliverer=SenderDeliverer(sender), topics=topics)`
once there is a Bot, and in inline mode then runs
`CronTicker(ctx.scheduler, cfg.cron_tick_seconds, extra=extra).run_forever()`
(`extra` is `(ctx.turns.reap,)`, plus `ctx.content.tick` with the plan on) as a background
task — `build_app()` stays headless, and a deployment with no Bot degrades to
Telegram-less operation instead of failing ([[decision.modules-and-ports]]). In
redis mode no ticker is started here: the `scheduler` container enqueues the tick
and a `worker` runs it, having bound its own `AppContext` through the same
`build_app()` and built the same delivery objects through `main.build_delivery`.
The `jobs` table schema, including the two lease columns, is in
[[contract.state-db]].

The content plan reaches the scheduler the other way too, through its
`Publisher` port: `core/app_context.py`'s `SchedulerPublisher` answers `describe`
from `SchedulerService.get(job_id, owner_id)` cut to `name, kind, state,
next_run_at, lease_until, timeout_seconds` (another owner's routine is `None`) and
passes `wake_if_idle` straight through. It is late-bound — `publisher.scheduler =
scheduler` right after the service is built — because the executor needs the
plan's routine tools and the plan needs this port: a cycle, and one edge has to
wait. The same instance is also the plan's `Drafter` port: the drafter routine is
another `manual` routine (meta `content_drafter_job_id`, never the publisher's),
woken the same way, and `claim_draft`/`submit`/`draft_failed` answer only its
`cron:<job_id>` origin. Neither module imports the other
([[decision.modules-and-ports]]).

## The calendar read

`GET /api/routines/calendar?from=&to=` (`scheduler_routines_calendar`, any
logged-in user, scoped to them like the list) is `SchedulerService.calendar(owner_id,
start, end, now)`: `{"fires": [{routine_id, name, at}], "dense": [{routine_id,
name, every_seconds}], "runs": [{routine_id, name, at, status}]}`. Fires are
expanded with `next_fire` for `active` routines from `max(from, now)` to `to`,
counted forward from each routine's stored `next_run_at` (an interval fires at
claim time plus its period, so counting from `from` would draw it at the wrong
times), capped at 1500 per routine; a routine with `period_seconds <= 3600` is listed
once in `dense` instead; `manual` contributes nothing. `runs` are `job_runs` rows
with `from <= ts < to` (`JobRepository.runs_between`, joined to the owner's jobs).
A range over 62 days, `to <= from`, or a missing/unparsable bound is `400
bad_range` (a naive bound is UTC). The SPA merges this with the content plan's own
`GET /api/content`; no endpoint joins the two modules ([[component.web]]).
`to_wire` answers `schedule: "manual"` for the new kind.

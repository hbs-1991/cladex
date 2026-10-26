---
id: flow.scheduled-job
kind: flow
status: current
summary: From "every morning at 8" to a delivered digest in the job's own topic.
verification:
  base_commit: 01094046215a669ab2ade7be691b49c547418d16
  evidence_fingerprint: sha256:83e6ddc9a0952c32f149820352ed07d77e17ac46f26bc7c5710e509cb1cc8107
evidence:
  - path: src/claude_hermes/modules/scheduler/presentation/tool.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/ticker.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/repository.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/dispatch.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/executor.py
  - path: src/claude_hermes/modules/scheduler/domain/policy.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
relations:
  - component.cron
  - decision.redis-taskiq-scaling
---
# Flow: scheduled job

1. **Create** — the model calls `cron_manage(action="create", ...)`
   (modules/turns/infrastructure/hermes_server.py). `scan_prompt` rejects destructive/exfiltration/override
   prompts; `parse_schedule` accepts a duration, 5-field cron or ISO
   timestamp; a Telegram forum topic is created for the job and its thread id
   stored as `deliver_thread_id`.
2. **Tick** — `SchedulerService.tick(now)`, driven every `cron_tick_seconds`
   (30 s) by `CronTicker` inline or once a minute by the `scheduler` container in
   redis mode. It **claims** what is due: `claim_due`
   (modules/scheduler/infrastructure/repository.py) selects `FOR UPDATE SKIP
   LOCKED` and, in the *same transaction*, moves `next_run_at` forward and stamps
   `claimed_at`/`lease_until` — that one transaction is the at-most-once guarantee
   across processes ([[decision.redis-taskiq-scaling]]). Fires older than
   `min(period/2, 2h)` are recorded `missed` and their lease released.
3. **Dispatch** — each surviving fire goes to `JobDispatcher.dispatch(job_id,
   fire_at)` (modules/scheduler/infrastructure/dispatch.py) and the tick returns.
   Inline that is an `asyncio.Task` limited to one `run_job` at a time; in redis
   mode it is a task a worker picks up. The claimed row carries `fire_at` — the
   `next_run_at` the claim consumed — because the clock has already moved.
4. **Execute** — `SchedulerService.execute_job(job_id, fire_at)` re-reads the row
   (gone → logged and dropped) and calls `run_job` (modules/scheduler/infrastructure/executor.py) in fresh session `cron:<id>`
   (never resumed, never spliced into a chat transcript), cron-scoped
   extensions (plus, for the deploy owner's routines with `content_enabled`, an
   in-process `hermes` server holding `content_plan`, lent through the
   `RoutineTools` port — [[component.content]]), `CRON_APPEND` system prompt,
   `bash_guard`, and a hard timeout of
   `job["timeout_seconds"] or cfg.cron_timeout_seconds` (default 180s).
   Returns `ok`/`timeout`/`error`, never raises. The lease is cleared in a
   `finally` whatever happened.
5. **Deliver** — the same coroutine posts the output (or `⚠️ <error>`) to the
   job's chat and topic via `RichSender` — the worker builds that sender through
   `main.build_delivery`, the same function `run()` uses, so the message is
   identical whichever process produced it; a delivery failure on one job never
   stops the others. Prompt and output are recorded into `messages`
   under the cron key. When a `TurnBus` is wired, `_emit_delivery`
   (modules/scheduler/application/service.py) also publishes the report onto the delivery lane as a small
   `channel: "cron"` turn (`turn.start` → `text.block` → `turn.end`), which is
   how it appears in an open browser thread and in the Routines run list
   ([[component.web]]); the `cron:<id>` SDK session stays isolated either
   way.
6. **Reply** — the user answers in the job's topic ("3"). That turn runs in the
   topic's own chat lane, which never saw the delivery, so
   `runner._with_cron_context` (modules/turns/application/turn_service.py) quotes the job's latest recorded
   output into the user message — once per delivery, skipped as soon as the
   lane has a turn newer than the report.
7. **Lifecycle** — `list`/`pause`/`resume`/`run_now`/`delete` from chat;
   `delete` closes the job's topic. One-shots become `done` in the claim itself.
   `run_now` reschedules to now **and clears the lease**, then waits for the next
   tick: up to `cron_tick_seconds` inline, up to 60 s in redis mode. An on-demand
   (`manual`) routine — created in the Tasks tab, never by `cron_manage` — skips
   step 2 until `run_now` or the content plan's `wake_if_idle` sets its
   `next_run_at`, and after its claim it goes back to `active` with `next_run_at`
   NULL rather than `done` ([[flow.content-publish]]).

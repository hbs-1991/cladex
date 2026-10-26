# `workers/` — what runs beside a turn

Scheduled jobs, learning reviews and curator passes do not belong on the request
path: they are slow, they spawn the CLI, and nobody is waiting for them. Wave 3a
gives them a queue. This package holds the process-side of that queue plus the
one loop that still runs in-process.

Since Wave 3b **a turn is on that queue too**. `run_turn(turn_id)` is the fifth
task: the route (or the Telegram adapter) inserts a `turns` row and enqueues the
id, and in redis mode a worker claims the row and runs the SDK loop. Somebody IS
waiting for that one — which is why it is the only task whose row carries a lease
the runner heartbeats, and why a worker that dies mid-turn is recovered by the
reaper rather than by a retry (`modules/turns/README.md`).

## The two modes

`config.queue_mode` is derived from `CH_REDIS_URL` and is the only switch.

| | `inline` (no `CH_REDIS_URL`) | `redis` (`CH_REDIS_URL` set) |
|---|---|---|
| deployment | `db + agent` | `db + redis + minio + agent + worker + scheduler` |
| queue impl | `core.tasks.InlineQueue` — `asyncio.create_task` in the agent | `TaskiqQueue` in `taskiq_app.py` → a `ListQueueBroker` on Redis |
| periodic work | `CronTicker.run_forever` and `curator_loop` inside `main.run` | taskiq schedule labels on `scheduler_tick` (`* * * * *`) and `curator_tick` (`*/15 * * * *`) |
| `run_now` latency | `cron_tick_seconds` (30 s) | up to 60 s — the cron granularity |
| where a turn runs | the agent, as an `asyncio.Task` through `InlineQueue` (unlimited: web turns on different lanes always ran concurrently) | a `worker`, `--max-async-tasks 4` per container |
| reaping lost turns | `CronTicker(..., extra=(ctx.turns.reap, …))` | the second line of `scheduler_tick` |
| the content plan's tick (`content_enabled`) | the next `CronTicker` extra, after the reap | the third line of `scheduler_tick`, in its own `try` |

Inline mode is not a degraded mode. It is what the app did before the queue
existed, and the entire test suite runs in it.

## Files

- `curator.py` — `run_once(service, lock=None)` is ONE scope's pass, shared by
  both modes; `run_all(scopes, lock_for=None)` (Wave 4b) is the sweep: it reads
  the UNSUFFIXED `curator_paused` through the owner scope first — the operator's
  switch, which has to stop all of it (a user's own `curator_paused:<id>` is what
  their own pass reads) — then runs `run_once` for every id in
  `scopes.user_ids()`, the deploy owner first and then every other user whose
  learning grant is on (their own choice in Settings → Users, else the
  `users_learning_enabled` default — asked per sweep, so a flip needs no restart),
  **sequentially**: N users is N passes and each may spawn
  an LLM fork, so running them concurrently would be N CLI processes inside a
  container sized for one. A scope that cannot be resolved is logged and skipped.
  `curator_loop(scopes, tick_seconds)` is inline mode's cadence over the sweep,
  kept inside `main.run` — one process, one loop, no locks because there is
  nobody to race.

  In redis mode the `curator_tick` task holds **two** locks: `lock:curator:sweep`
  around the whole sweep (TTL `curator_max_turns` minutes × the number of users),
  so two ticks never run two sweeps at once — a sweep of N users outlives the
  15-minute tick (advisor R-1) — and one per pass, `lock:curator` for the owner
  (the pre-4b key name, kept) and `lock:curator:<id>` for everybody else (TTL
  `curator_max_turns` minutes), which is what keeps an inline `curator_loop` and
  a worker off the same tree and still holds when a sweep outlives its outer
  lock's TTL. A killed worker's lock expires; it is never released by hand. The
  fourth lock in this family is `lock:review` (`RedisReviewSlot`, TTL
  `review_timeout_seconds`): one `learning_review` fork at a time deploy-wide,
  now that the per-user `learning_review_until` lease no longer bounds
  concurrency (advisor B-1).
- `taskiq_app.py` — **the only file in the repository that imports taskiq**, and
  it imports it lazily, inside functions. It binds `tasks.py`'s registry to a
  `taskiq-redis` broker (`broker.register_task(fn, task_name=name)`, plus the
  `schedule` label for the two periodic names) and enqueues with `.kiq(**kwargs)`.
  The `broker` and `scheduler` attributes that compose names are built on first access
  through a module `__getattr__`, so importing the module costs nothing and fails
  nowhere — which is what lets `tests/test_main.py` read the worker startup's
  source with taskiq absent. Nothing in `core/` or `modules/` imports it, so the
  app starts and the suite passes without it.
- `tasks.py` — the one `TaskRegistry` and the five task functions. Primitive
  arguments only (`job_id: int`, `fire_at: str`, `turn_id: str`,
  `session_key: str`, `kinds: list[str]`, `owner_id: int = 0`): the argument
  list has to survive being JSON on a Redis
  list, and a task taking a service object is a task that only ever worked
  inline. Services are read from a module-level `AppContext` that each process
  binds once — `main.build_app()` binds for the agent (inline mode runs these
  same bodies in-process) and `taskiq_app.worker_startup` for the worker. A body
  that runs before either raises `TasksNotBound` rather than an `AttributeError`
  on None.

| task | name | enqueued by | body |
|---|---|---|---|
| tick the clock, then reap lost turns | `scheduler_tick` | the `scheduler` process (`* * * * *`) / `CronTicker` inline | `SchedulerService.tick(now)` **and** `TurnLauncher.reap(now)`, on one `now`, then — when `ctx.content` is set — `ContentService.tick(now)` in its own `try` that logs (queue due items, wake the publisher through `wake_if_idle`; idempotent across workers) |
| run one claimed fire | `run_job` | `QueueJobDispatcher`, from `tick` | `SchedulerService.execute_job(job_id, fire_at)` |
| run one created turn | `run_turn` | `QueueTurnDispatcher`, from `TurnLauncher.start` | `TurnRunService.run(turn_id)` |
| the detached review | `learning_review(session_key, kinds, owner_id=0)` | `QueueReviewDispatcher`, from `LearningLoop.after_turn` | `(await ctx.learning.scopes.for_user(owner_id)).review.run(session_key, kinds)` — in THAT user's tree, and only if `scopes.observes(owner_id)` still says their learning grant is on (0009): a review queued before the owner withdrew it releases that scope's review gate, logs and returns without a fork (a failed lookup releases the gate and raises); a payload without `owner_id` (queued by a 4a agent) is the deploy owner's, and a 4b payload drained by a 4a worker is a `TypeError` and a lost review, which is why a multi-container upgrade drains the queue first |
| a curator sweep | `curator_tick` | the `scheduler` process (`*/15 * * * *`) / `curator_loop` inline | `curator.run_all(ctx.learning.scopes, lock_for)` inside `lock:curator:sweep`; `lock_for(uid)` builds `lock:curator` / `lock:curator:<uid>` |

The three task names a *module* owns (`run_job`, `run_turn`, `learning_review`)
are constants in that module's `infrastructure/dispatch.py` and are imported here,
never retyped: a name spelled differently on the two sides is a task that is
enqueued and never runs.

`taskiq_app.SCHEDULES` is unchanged by Wave 3b — `run_turn` is enqueued by a turn,
never by the clock. The inline concurrency limit is `{run_job: 1,
learning_review: 1}` since Wave 4b: the review's bound used to be the single
`learning_review_until` lease, and with one lease per user the queue limit (inline)
and `lock:review` (redis) are what replaced it. `run_turn` is deliberately
unlimited inline, because before the queue existed web turns on different lanes
always ran concurrently, and the `turns` table's unique partial index is what
stops two turns sharing a lane.

## Running it

```
# one worker (N is safe — claiming is idempotent)
taskiq worker claude_hermes.workers.taskiq_app:broker claude_hermes.workers.tasks \
  --max-async-tasks 4
# the periodic enqueuer (a duplicate is harmless: the claim decides)
taskiq scheduler claude_hermes.workers.taskiq_app:scheduler
```

**`--max-async-tasks 4`** is what `docker-compose.yml` runs the `worker` with, and
it is a memory dial, not a throughput one: a turn forks the Claude CLI, so four
concurrent turns is four CLI processes inside one container. A deployment that
wants more scales `worker` **replicas**, not this number — the lane lock is a
PostgreSQL index and does not care how the workers are grouped.

**The worker needs `TELEGRAM_BOT_TOKEN`.** Whichever process runs the turn is the
one that sends the `ask_user` keyboard and `send_document`, through the delivery
objects `build_delivery` binds — and `worker_startup` builds them only when the
token is present. A worker booted without it still registers `ask_user` (the gate
is "a presenter is wired", and `AskService` always is) and would park a Telegram
turn for `ask_user_timeout_seconds` on a question nobody ever saw. So the startup
logs `worker_without_telegram_token` at **warning**, and the compose `x-app`
anchor — one environment block for `agent` and `worker` — is what makes the
correct case the default.

Only the `agent` process polls Telegram. The worker builds the Telegram delivery
objects it needs through `main.build_delivery(ctx, token)` — the same function
`run()` calls, so a scheduled report cannot look different depending on which
process produced it — and then pops the same eleven names `main.run` does from
`os.environ`:
`TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `CH_SECRETS_KEY`, `CH_S3_ACCESS_KEY`,
`CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL`, `POSTGRES_PASSWORD`,
`MINIO_ROOT_PASSWORD`, `CH_WEB_SECRET` and `CH_TELEGRAM_WEBHOOK_SECRET`. The
worker serves no webhook, but it spawns the same CLI from the same container
environment, and anything that can read that last one can POST a forged update to
the agent's public webhook URL — an injection with a return address. The three
before it are on the list because
`docker-compose.yml` derives `CH_DATABASE_URL` from `POSTGRES_PASSWORD` and
`CH_S3_SECRET_KEY` from `MINIO_ROOT_PASSWORD` while `env_file: .env` injects the
originals too — popping only the derived spelling leaves the identical credential
one variable over. `CLAUDE_CODE_OAUTH_TOKEN` is deliberately not popped: the CLI
subprocess is what it is for. A worker spawns the CLI, so it inherits the rule
rather than being an exception to it; `tests/test_main.py` asserts the pops and
their position in the source of both entry points.

## Open debt

**`taskiq_app.py` was written offline and verified afterwards.** It was authored
on a machine with no network, where `taskiq` and `taskiq-redis` could not be
installed; the checklist below was run once the network came back (2026-09-17):

1. `pip install -e ".[dev]"` — taskiq 0.12.6, taskiq-redis 1.2.3, redis-py 8.1.0.
2. `pytest tests/test_workers_taskiq.py -q` — green, no longer skipped.
3. A live `taskiq worker claude_hermes.workers.taskiq_app:broker
   claude_hermes.workers.tasks` against `hermes-test-redis` with `run_job` and
   `scheduler_tick` enqueued by hand: both executed (`run_job` for a missing id
   logged "disappeared between claim and run" and dropped it, as designed).
   This smoke found one real defect: redis-py 8 defaults every connection to a
   5-second read timeout, the broker's BRPOP blocks until a message arrives, and
   taskiq-redis catches only `ConnectionError` — so an idle worker died and was
   restarted every five seconds. `_make_broker` now passes `socket_timeout=None`
   and `tests/test_workers_taskiq.py` pins it.
4. `docker compose config` and `docker compose build` pass with the env the
   compose files interpolate (`POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`,
   `CH_SECRETS_KEY`).

**Still open:** step 5 needs a real bot. After the first job a worker delivers
in production, check its `docker logs` for a Telegram API URL. `worker_startup`
calls `core.logging.configure(logging.INFO)` first and `configure` REPLACES the
root handlers, so taskiq's own INFO handler is gone before anything httpx logs;
if a token ever shows up anyway, add `--no-configure-logging` to the worker
command in `docker-compose.yml`.

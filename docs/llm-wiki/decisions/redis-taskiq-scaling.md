---
id: decision.redis-taskiq-scaling
kind: decision
status: current
summary: Background work moves onto our own queue port (inline or taskiq-redis), scheduled fires are claimed in one transaction, the turn is a row plus a queued task, and since Wave 4a's webhook mode the agent is a singleton only while it polls.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:03b5a4b5b0704d75259ccc5424438d92e5ced6a077fc1406fc16e9346684a0dc
evidence:
  - path: src/claude_hermes/core/tasks.py
  - path: src/claude_hermes/core/redis.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/workers/taskiq_app.py
  - path: src/claude_hermes/workers/curator.py
  - path: src/claude_hermes/workers/README.md
  - path: src/claude_hermes/modules/scheduler/infrastructure/dispatch.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/repository.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
  - path: src/claude_hermes/modules/learning/infrastructure/dispatch.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_gate.py
  - path: src/claude_hermes/core/db/migrations/versions/0003_queue_and_extension_state.py
  - path: src/claude_hermes/core/db/migrations/versions/0004_turns.py
  - path: src/claude_hermes/core/eventbus.py
  - path: src/claude_hermes/modules/turns/application/launcher.py
  - path: src/claude_hermes/modules/turns/application/execution.py
  - path: src/claude_hermes/modules/turns/application/control.py
  - path: src/claude_hermes/modules/turns/infrastructure/turn_repository.py
  - path: src/claude_hermes/modules/turns/infrastructure/snapshots.py
  - path: src/claude_hermes/modules/turns/infrastructure/dispatch.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/main.py
  - path: docker-compose.yml
  - path: src/claude_hermes/modules/telegram/infrastructure/transport.py
  - path: src/claude_hermes/modules/telegram/presentation/webhook.py
  - path: src/claude_hermes/modules/telegram/infrastructure/update_dedup.py
relations:
  - component.cron
  - component.learning
  - component.core-runtime
  - contract.state-db
  - decision.modules-and-ports
  - decision.minio-blobs
  - component.web
  - flow.message-turn
  - decision.multi-user-isolation
  - component.gateway
---
# Decision: Redis, a worker process, and what stays a singleton

## Context

Before Wave 3a one process did everything: it polled Telegram, served HTTP, ran
turns, ticked the cron clock, forked learning reviews and ran curator passes. Two
of those are slow and spawn the Claude CLI — a scheduled job and a curator
consolidation — and nobody is waiting on either. Three consequences were already
visible:

- **A second agent container was not a scaling knob, it was a duplication bug.**
  Two tickers reading `SELECT … WHERE next_run_at <= now` both saw the same fire
  and both ran it. The reschedule happened *after* the read, so the window was
  wide open.
- A job that forked the CLI for three minutes shared an event loop with whoever
  was typing in Telegram.
- `review_running` was a Python attribute. It told the curator "a review is in
  flight" only for as long as the process that started the review stayed alive,
  and only inside that process.

The wave had to move that work off the request path without changing what any of
it *does*, and without making a laptop deployment need three servers.

## Decision

### The queue abstraction is ours; taskiq is one backend

`core/tasks.py` owns the seam. `TaskRegistry` maps a stable name to an async
callable taking primitive keyword arguments; `TaskQueue` is a one-method port
(`async enqueue(name, **kwargs)`). Two implementations satisfy it:

| | `InlineQueue` (`core/tasks.py`) | `TaskiqQueue` (`workers/taskiq_app.py`) |
|---|---|---|
| runs the body | as an `asyncio.Task` in this process | on a `taskiq-redis` `ListQueueBroker`, in a worker |
| selected when | `CH_REDIS_URL` is unset | `CH_REDIS_URL` is set |
| periodic work | `CronTicker.run_forever` + `curator_loop` inside `main.run` | taskiq schedule labels: `scheduler_tick` `* * * * *`, `curator_tick` `*/15 * * * *` |

`config.queue_mode` is **derived** from `CH_REDIS_URL` and is never a second
switch — a `queue_mode:` key in `config.yaml` is refused by `load_config`, because
a deployment whose two switches disagree is a deployment that enqueues into a
Redis nothing reads.

Putting the port in `core` rather than importing taskiq is what keeps the broker
out of the architecture. `workers/taskiq_app.py` is the only file in the
repository that imports taskiq, it imports it inside functions, and its
module-level `broker`/`scheduler` (the attributes `docker-compose.yml` names) are
produced by a module `__getattr__`. Importing it therefore costs nothing and
fails nowhere — which is what lets the suite run, and `main.py`'s worker startup
be read as source, with taskiq not installed at all.

The four dispatcher ports are all one `queue.enqueue` call:
`JobDispatcher` → `run_job`, `ReviewDispatcher` → `learning_review`,
`TurnDispatcher` → `run_turn` (Wave 3b), and the
curator kick → `curator_tick`. Inline, that is exactly the `asyncio.create_task`
the code did before.

**Task arguments are primitives** — `job_id: int`, `fire_at: str`,
`turn_id: str`, `session_key: str`, `kinds: list[str]`. The argument list has to survive being
JSON on a Redis list; a task that takes a service is a task that only ever worked
inline. Services come from a module-level `AppContext` each process binds once
(`workers.tasks.bind`), and a body that runs before either binding raises
`TasksNotBound` rather than an `AttributeError` on `None`.

### `claim_due` — the at-most-once proof

Migration `0003` adds `jobs.claimed_at` and `jobs.lease_until`.
`JobRepository.claim_due(now_iso, next_of, limit=100)` is **one transaction**:

```sql
SELECT * FROM jobs
 WHERE state = 'active' AND next_run_at <= :now
   AND (lease_until IS NULL OR lease_until < :now)
 ORDER BY next_run_at LIMIT :n
 FOR UPDATE SKIP LOCKED
```

and then, in the same transaction, per row:
`UPDATE jobs SET next_run_at = next_of(row), claimed_at = :now,
lease_until = :now + max(timeout_seconds, 300s)`.

Three cases, and there is no fourth:

1. A second ticker running the identical statement **at the same instant** takes
   no lock on a row this one holds — `SKIP LOCKED` hands it the rest. The two sets
   are disjoint by construction.
2. A ticker arriving **after the commit** reads a `next_run_at` that has already
   moved into the future. The row does not match the `WHERE` at all.
3. A ticker arriving **between** the `SELECT` and the `UPDATE` cannot exist: they
   are the same transaction, and the row locks are held from the `SELECT`.

The reschedule is inside the claim, and that placement *is* the guarantee. Moving
it back out into a second `reschedule()` call after the claim reopens case 3.
`tests/modules/scheduler/test_scheduler_claiming.py` runs two concurrent
`claim_due` calls on one engine and asserts disjoint sets.

`next_of` is a callable the service passes (the domain's `next_fire` over the
row's schedule): the repository must not import a schedule to know when a job
fires next, and `None` back from it means the job is spent and the row goes
`state='done'`.

Each claimed row carries an extra `fire_at` — the `next_run_at` the claim
consumed — so the catch-up window is read off the fire and nothing downstream has
to know the reschedule already happened.

**The lease is not a deadline.** It guards only the window between the claim and a
worker picking the task up. A run that outlives its lease is not re-claimed,
because `next_run_at` has already moved past it; `execute_job` clears the lease in
a `finally`, and `run_now` clears it too — a job claimed a minute ago would
otherwise be invisible until the lease expired, and "run now" that waits five
minutes is not run now.

That lease-clearing is exactly why a **second module must never wake a routine
with `run_now`**: `scheduler_tick` runs on every worker at once, so a
check-then-`run_now` would clear a live lease and start a second run beside the
first. The content plan's publisher (and, since its v2, its drafter) is woken through `wake_if_idle` instead — one
`UPDATE … WHERE kind='manual' AND next_run_at IS NULL AND (lease_until IS NULL OR
lease_until < :now) RETURNING id` that never touches the lease — and the plan's own
wake bookkeeping is a compare-and-set too, so N ticks on the same minute wake the
publisher once ([[component.content]]). The v2 approval card is the same shape: a
`pending → sending` compare-and-set picks the one tick that sends it, the
«Контент» topic is created behind a `meta` reservation, and each worker's
`build_delivery` binds the card sender, since any worker's tick may win.

### Two leases in Redis, not in memory

- `lock:curator` — `SET NX PX` with a token, released by a Lua compare-and-delete
  (`core/redis.py::Lock`). N workers may tick on the same minute; one runs the
  pass. The TTL is `curator_max_turns * 60 s`, i.e. derived from the worst case
  rather than guessed, and a killed worker's lock expires instead of being
  released by hand.
- `lock:oauth:<name>` — the per-connector refresh lock, an `asyncio.Lock` inline
  and a `core.redis.Lock` in redis mode.

The learning gate is deliberately **not** a Redis key. `learning_review_until` is
a `meta` row holding the instant a claim expires
(`cfg.review_timeout_seconds`, default 900 s), read by both the curator gate and
`TurnService`. A row works in both modes, survives a dead worker and needs no
second store; a boolean would have stayed true forever the first time a worker was
OOM-killed mid-pass, silently disabling the curator for good.

### What stays a singleton, and why

| Singleton | Reason |
|---|---|
| **The polling Telegram client** (`agent`) — **in polling mode only** | `getUpdates` is a single-consumer API: a second poller on the same token gets `409 Conflict` and neither answers. Wave 4a's `telegram_mode: webhook` removes this one: Telegram POSTs instead, and N instances behind one public origin all receive updates (below). `worker` never calls `run_polling`; it builds its delivery objects through the same `main.build_delivery(ctx, token)` the agent uses, so a scheduled report cannot look different depending on which process produced it. |
| ~~**The in-process turn runner**~~ | Wave 3a's reason: a turn held `self._locks[session_key]`, streamed deltas onto an in-process `TurnBus`, and could park on `ask_user` — all three process-local. **Wave 3b removed all three reasons** (below); the lock survives only as protection for the legacy direct callers. |
| **Inline `run_job` concurrency** | In redis mode concurrency is the number of worker containers. Inline there is no such dial, so `main.build_app()` builds `InlineQueue(registry, {RUN_JOB_TASK: 1})`: one job at a time, which is what the ticker did when it awaited each job itself. Without it, five routines due at 09:00 fork five CLIs inside a container sized for one. |

The `scheduler` container is the opposite case: it is stateless, touches neither
database nor bucket, and a duplicate is harmless — the claim, not the enqueue, is
what decides that a job runs.

### Wave 3b — the turn is a row and a task

3a left one thing on the request path, and it was the expensive one. 3b moves it,
by replacing each of the three process-local pieces with something two processes
can both see:

| was | is | why that shape |
|---|---|---|
| `asyncio.Lock` per lane | a **unique partial index** on `turns(session_key) WHERE status IN ('queued','running')` | the lock has to hold across processes, and the only thing both processes already trust is PostgreSQL. `create` inserts and translates the `IntegrityError`; nothing anywhere does check-then-insert, so two API instances that both believe a lane is free still produce exactly one turn ([[contract.state-db]]) |
| an in-process `TurnBus` listener list | `core/eventbus.py` — `InProcessEventBus` or `RedisEventBus` on `hermes:turn:<lane>` — plus a `SnapshotStore` (`MemorySnapshots` / `RedisSnapshots` under `hermes:snap:`) | fan-out and the in-flight snapshot are different problems: the channel carries what is happening now, the snapshot is what a browser that reloads mid-turn needs, and it has to be readable by an instance that never saw the deltas |
| `AskBroker._pending` | a `hermes:ask:<qid>` hash plus a `…:answer` key, nudged on `hermes:ctl` | the tap and the wait are in different processes. The key, not the nudge, is the truth — a lost pub/sub message costs one 5-second poll, not an answer |
| `runner.cancel(key)` → `task.cancel()` | `cancel_requested_at` on the row, **then** a nudge | same rule: the flag is the truth and the heartbeat is the fallback, so cancelling works with pub/sub entirely down, within a third of a lease |
| "the process dies, the turn dies" | a **lease** the runner heartbeats, reaped by `scheduler_tick` | a lane whose worker was OOM-killed must become usable again without an operator |

**The two-instance argument.** After 3b the acceptance claim is literal: a turn
started via instance A streams over SSE from instance B, because the persisted
events are in PostgreSQL, the in-flight remainder is in Redis, and B's SSE
generator subscribes to the same channel the worker publishes on. Two turns on one
lane never run concurrently, because that is a database index and not an
arrangement of processes. And restarting `agent` mid-turn is survivable: the
worker finishes, the events are in `turn_events` and the reply is on the row — the
one casualty is the Telegram message nobody is left waiting to send (Open Question
2, recorded in `modules/turns/README.md`).

**What the agent still holds in memory, and why none of it is correctness.**
A second instance disagreeing about any of these would be a bug; each was checked:

- `TurnBus._active` — per-emitter `seq` counters only. A turn has exactly one
  emitter, and the SSE id/seq filters handle a foreign process starting at 1.
- `TurnService._locks` / `_tasks` — serialise direct `run_turn` callers. Nothing
  reads them to decide whether a lane is busy.
- `TurnService._index_seen` — now a cache of `sessions.index_mtime`, refreshed
  with `max(local, row)` every turn and written back.
- `CancelRegistry` — process-local **by definition**: the tasks running *here*.
  The row's `cancel_requested_at` is the truth.
- `AskBroker._pending` / `RedisAskBroker._pending` — local futures for questions
  opened by this process; the hash and the answer key are the cross-process truth,
  and `deliver` is a no-op for a qid this process never opened.
- `RedisEventBus._handlers` / `_ready` / per-channel delivery queues — this
  process's own subscriptions.

**The reap false positive, named.** If the reaper wins against a worker that is
alive but merely stalled past its lease (a long GC pause, a DB blip that starved
the heartbeat), the lane gets the synthetic `turn.end` and the user sees
"Something went wrong" — and the real turn later finishes, has its `finish`
refused by the `worker_id` guard, and is logged. "Two turns on one lane never run
concurrently" is a claim about **rows**; the residual window for SDK *processes*
is one heartbeat interval plus cancellation latency, and what keeps it that small
is the worker cancelling its own turn when its own clock passes `lease_until` —
it kills itself before the reaper has to be wrong about it.

### Wave 4a — in webhook mode the agent is not a singleton either

`telegram_mode: webhook` (`modules/telegram/infrastructure/transport.start_transport`)
registers the URL with Telegram at boot and blocks on the web server instead of
polling, so **`docker compose up --scale agent=N` becomes possible** — with one
condition that is easy to miss:

> `--scale agent=N` needs **both** `telegram_mode: webhook` **and** `CH_REDIS_URL`.

Webhook mode alone removes the 409; it does not remove the clocks. In inline mode
every instance builds its own `InlineQueue`, its own `CronTicker` and its own
curator loop, so N instances are N clocks over one database. The compose file says
both on the `agent` service, because only one of the two failures is loud.

**`update_id` dedup is the cross-instance arbiter.** Telegram's delivery is
at-least-once — it redelivers an update it did not get a 2xx for within its
timeout — and what follows an accepted update is a download, possibly a
transcription, and a turn. So the route drops a repeated `update_id` through the
`UpdateDedup` port before it dispatches anything: `SET hermes:tg:update:<id> 1 NX
EX 600` in redis mode, an in-memory LRU inline. `SET NX` returns true only to the
caller that created the key, so "did I get the key" *is* "was I the first instance
to accept this update" — one round trip that both records and answers, with no
lock to release. It **fails open**: a Redis that is down answers "not seen" and
logs, because losing every update while Redis reconnects is strictly worse than a
possible duplicate turn, and the 200 has already gone out. Inline mode's LRU is
the same semantics inside one process, which is all there is there.

The route also answers `200` *before* the update is handled, dispatching into a
background task held in a strong-reference set (`asyncio` keeps only a weak one, so
an unheld task can be collected mid-await and the update simply vanishes). That
trades "a crash between the 200 and the handler loses an update" for "a slow
document is never charged twice", and the first is the failure a person notices
while the second is the one that costs money.

**Two things stay per-instance, and neither is correctness.** The Titler's
fire-and-forget task set (`modules/telegram/infrastructure/titler.py`) is per **process** — a title is
one haiku call for one brand-new thread, and the name it produces lands on the
`threads` row and on the bus, so whichever instance made it, every consumer sees
the same one. The presenter's typing/status task is per **message**, held by the
process that is presenting that turn. Two instances cannot both be presenting one
turn, because only one of them is the one the browser or the dispatcher subscribed
through.

## Consequences

- **`run_now` latency in redis mode is up to 60 s** (the cron granularity), where
  inline it stays `cron_tick_seconds` (30 s). Open Question 1 of the plan, settled
  at 60 s and documented in `README.md` and `docs/deploy.md`.
- **A deployment is six services**: `db`, `redis`, `minio`, `agent`, `worker`,
  `scheduler` (+ `cloudflared`). `agent` and `worker` are the same image with a
  different `command` and share one YAML anchor for mounts and environment — a
  mount that drifts between them is a bug that only appears in whichever half you
  did not test. Since 3b the anchor also has to carry **`TELEGRAM_BOT_TOKEN`** to
  the worker: whichever process runs the turn is the one that sends its `ask_user`
  keyboard and its `send_document`, and a worker without the token would park a
  Telegram turn on a question nobody saw (`worker_startup` logs
  `worker_without_telegram_token` at warning).
- **The worker runs turns with `--max-async-tasks 4`.** A turn forks the Claude
  CLI, so that number is a memory dial per container; a deployment that wants more
  concurrency adds `worker` replicas. `run_turn` is deliberately **unlimited**
  inline, because web turns on different lanes always ran concurrently and the
  lane lock is now an index rather than a queue depth.
- **`redisdata` matters more than it did.** A dropped `run_turn` message is a
  lane held `queued` until `turn_queue_timeout_seconds` (300 s) expires and the
  reaper frees it — recoverable, and visible, but a user waited five minutes for
  it.
- **Inline mode is not a degraded mode.** It is what the app did before the queue
  existed, and the entire test suite runs in it.
- **Secrets are popped in both entry points.** `main.run` and the worker startup
  hook each pop `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `CH_SECRETS_KEY`,
  `CH_S3_ACCESS_KEY`, `CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL`,
  `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD` and `CH_WEB_SECRET` before anything
  can spawn the CLI. A worker spawns the CLI too, so it inherits the rule rather
  than being an exception to it ([[quality.invariants]]).
- **`workers/taskiq_app.py` was written offline and verified on 2026-09-17.**
  With `taskiq` 0.12.6 / `taskiq-redis` 1.2.3 installed, `tests/test_workers_taskiq.py`
  runs green and a live `taskiq worker` against the test Redis executed a
  hand-enqueued `run_job` and `scheduler_tick`. That smoke found the one defect
  the tests could not: redis-py 8 gives every connection a 5-second read timeout,
  the broker's BRPOP blocks until a message arrives, and taskiq-redis catches only
  `ConnectionError` — an idle worker died and restarted every five seconds.
  `_make_broker` passes `socket_timeout=None`, and the test file pins it. What is
  still unobserved is a job *delivered to Telegram* from a worker (step 5 of the
  checklist in `src/claude_hermes/workers/README.md`, which needs a real bot).

## Alternatives rejected

- **taskiq's own broker as the abstraction.** It would have put a third-party
  import in `core/` and in every module that enqueues, and — given the offline
  build — would have made the whole wave unrunnable rather than one file unproven.
- **A `queue_mode` config key.** Two switches that can disagree. Derived from
  `CH_REDIS_URL` instead, and a stray key in `config.yaml` is refused.
- **Advisory locks (`pg_advisory_lock`) instead of `SKIP LOCKED`.** Would have
  worked, but the lock would live for the length of the run rather than the length
  of the claim, holding a connection per in-flight job.
- **A boolean `review_running` row.** Simpler, and wrong in exactly the case the
  wave introduced: the process that sets it is no longer the process that clears
  it.
- **Re-executing a lost turn** (3b). Marking it `lost` and letting the user resend
  is the default, because an SDK loop that may have half-completed would double
  its side effects and its cost on a retry. `claim` matches `queued` only, so the
  code cannot quietly disagree with that answer.
- **Making `turns` a real queue per lane** (3b), so a second Telegram message
  would line up behind the first. It would need a `running`-only index and a
  per-lane claim order for one channel's habit; `QueuedTurnRunner` retrying
  `LaneBusy` until the lane frees gets the same user-visible behaviour with no
  schema for it.
- **Resolving `ready()` when the SUBSCRIBE command was sent** (3b). redis-py does
  not parse a reply there, and the snapshot read goes out on a different pooled
  connection — an event recorded and published in between would be in neither.
  The reader consumes the confirmation frame instead.

# Wave 3b — turns off the request path: `turns` table + lease, Redis event bus, cross-process cancel / ask_user

Status: plan, 2026-09-17, revised the same day after the implementation-advisor
pass (its six blockers and eleven risks are folded in below; the "Advisor
amendments" section at the end lists what changed and why). Follows Wave 3a
(24600c5..1ffc244). Spec:
`docs/superpowers/specs/2026-09-14-modular-fastapi-architecture-design.md`
req. 2, 16, 17, 18, 20, 33, 37, 38 and the two acceptance lines "a turn started via
instance A streams over SSE from instance B; two turns on the same lane never run
concurrently" and "killing a worker mid-turn leaves the lane reclaimable within the
lease period".

## Goal

After 3b a turn is a **row and a task**, not a coroutine in the HTTP or aiogram
handler that received the message:

- `POST /api/threads/{id}/messages` and a Telegram message both **insert a `turns`
  row and enqueue `run_turn(turn_id)`**. In redis mode a `worker` claims it and runs
  the SDK loop; inline mode runs the same task body in-process through
  `InlineQueue` — what the web route's `asyncio.create_task(run())` did, now on the
  same path as every other channel.
- **Exactly one active turn per lane**, enforced by PostgreSQL: a unique partial
  index on `turns(session_key) WHERE status IN ('queued','running')`. The row
  carries a **lease** the running worker heartbeats; a dead worker's lease expires
  and a reaper marks the turn `lost`, publishes a synthetic `turn.end`, and the lane
  is free again.
- **Every turn event goes through a channel bus** (`core/eventbus.py`): in-process in
  inline mode, Redis pub/sub in redis mode. API instances subscribe per lane and fan
  out to SSE clients and the Telegram presenter, whichever process ran the turn.
  The **in-flight snapshot** (persisted events of the turn plus the not-yet-flushed
  text/thinking deltas, plus the per-turn `seq` watermark) lives in Redis with a
  TTL, so a browser that reloads mid-turn catches up from any instance.
- **Cancel and `ask_user` work across processes.** Cancel = a flag on the row (the
  truth) plus a nudge on the control channel (the latency). An ask = a Redis key the
  worker opens, the API instance resolves with a compare-and-delete, plus a nudge.
- **The agent process holds no correctness-relevant turn state.** Restarting it
  mid-turn no longer kills the turn: the worker finishes, the events are in
  PostgreSQL and the reply is on the row.

Out of scope: Step 4 (users, workspace isolation, webhook mode) and Step 5 (shims).
The legacy `AgentRunner(cfg, home, db, router, wiki, bus=)` head and every `web/*.py`
shim survive. `TurnService.run_turn` keeps meaning "run this turn here, now" — that is
what the worker task calls, and what the five runner test modules drive.

## Wire contract (frozen, with one additive change)

- `POST /api/threads/{id}/messages` → **202 `{"accepted": true, "thread_id": key,
  "turn_id": <hex>}`**. `turn_id` is new (spec req. 16: "return turn id"); every other
  key, status and error `code` is byte-identical. The two tests that assert the 202
  body by equality (`tests/test_web_turns.py`, `tests/modules/turns/test_turns_router.py`)
  pop `turn_id` and assert the rest unchanged — the only edit either file gets.
- `409 busy`, `POST …/cancel` → 204 / `409 not_running`, `GET …/events`,
  `GET …/stream` (frame format, cursor semantics, the snapshot-after-replay dedup),
  `POST /api/asks/{qid}` (204 / 410 gone / 400 bad_request / 503 unavailable),
  `GET /api/commands`: unchanged.
- `turn.end` payload keys unchanged. A reaped turn ends with
  `{"status": "error", "cost_usd": null, "sdk_session_id": null,
  "error": "worker lost: lease expired", "usage": null}`.
- Telegram behaviour unchanged from the user's side: **a second message on a busy
  lane waits its turn and is answered** (today's per-lane lock; see `QueuedTurnRunner`),
  status drafts while the turn runs, one final message, voice reply after a voice
  note, "Something went wrong on my side — try again." on failure.
- One UX change to name in the docs: between the 202 and the worker's claim there is
  no `turn.start`, so the browser's spinner opens a little later than today. The 202
  carries `turn_id` so the SPA can render optimistically later; the SPA is not
  changed in this wave.

## Locked Decisions

### The `turns` table (migration `0004_turns.py`)

```
turns(
  id                  TEXT PRIMARY KEY,          -- uuid4 hex; the turn_id every event carries
  session_key         TEXT NOT NULL,
  channel             TEXT NOT NULL,             -- 'web' | 'telegram'
  user_id             BIGINT NOT NULL,
  text                TEXT NOT NULL,
  attachment_ids      JSONB NOT NULL DEFAULT '[]',
  status              TEXT NOT NULL,             -- queued | running | ok | error | cancelled | lost
  created_at          TEXT NOT NULL,
  started_at          TEXT NULL,
  finished_at         TEXT NULL,
  lease_until         TEXT NOT NULL,             -- queued: created + queue timeout; running: heartbeat
  worker_id           TEXT NULL,
  cancel_requested_at TEXT NULL,
  reply               TEXT NULL,
  error               TEXT NULL
)
CREATE UNIQUE INDEX turns_one_active_per_lane ON turns (session_key)
  WHERE status IN ('queued', 'running');
CREATE INDEX turns_active_lease ON turns (lease_until) WHERE status IN ('queued', 'running');
CREATE INDEX turns_finished ON turns (finished_at) WHERE finished_at IS NOT NULL;   -- for pruning
ALTER TABLE sessions ADD COLUMN index_mtime DOUBLE PRECISION NULL;                 -- see "index_seen"
```

ORM `TurnRow` in `modules/turns/domain/models.py` (registered through
`core/db/registry.py` by the existing import). Timestamps are ISO text like every
other table here.

`modules/turns/infrastructure/turn_repository.py` — `TurnRepository(engine)`. Every
statement below is **one** statement; nothing here is check-then-write:

| method | semantics |
| --- | --- |
| `create(turn_id, session_key, channel, user_id, text, attachment_ids, now_iso, lease_until)` | INSERT `queued`. The unique index's `IntegrityError` is translated to `domain.exceptions.LaneBusy` — **the index is the lock**. |
| `claim(turn_id, worker_id, now_iso, lease_until) -> dict \| None` | `UPDATE … SET status='running', started_at=:now, worker_id=:w, lease_until=:l WHERE id=:id AND status='queued' RETURNING *`. **`queued` only** — a `running` row with an expired lease is the reaper's, never re-executed (Open Question 1), and taskiq's list broker never redelivers a task anyway. `None` = cancelled before pickup, reaped, or already claimed. |
| `heartbeat(turn_id, worker_id, lease_until) -> dict \| None` | `UPDATE … SET lease_until=:l WHERE id=:id AND worker_id=:w AND status='running' RETURNING cancel_requested_at`. `None` = the row is no longer ours (reaped): stop. |
| `finish(turn_id, worker_id, status, *, reply=None, error=None, now_iso) -> bool` | `UPDATE … WHERE id=:id AND worker_id=:w AND status='running'`. **Guarded by `worker_id`**, so a worker whose turn was reaped cannot flip the row terminal; returns `False` and the caller logs `turn_finish_ignored` with the row's actual status — a completed turn recorded as `lost` must be visible in the logs. |
| `request_cancel(session_key, now_iso, turn_id=None) -> dict \| None` | one `UPDATE … SET cancel_requested_at=:now, status = CASE WHEN status='queued' THEN 'cancelled' ELSE status END, finished_at = CASE WHEN status='queued' THEN :now ELSE finished_at END WHERE session_key=:k AND status IN ('queued','running') [AND id=:turn_id] RETURNING id, status`. One statement, so a claim racing it is decided by the status check. |
| `active(session_key) -> dict \| None` | the `queued`/`running` row, **lease ignored**: the index defines busy and the reaper is the only thing that frees a lane, so `is_busy` never disagrees with `create`. |
| `active_lanes(session_keys: list[str]) -> set[str]` | one query for a page of the sidebar (sessions' `LaneActivity`). |
| `expired(now_iso, limit=100) -> list[dict]` | active rows with `lease_until < now`. |
| `mark_lost(turn_id, now_iso) -> bool` | `WHERE id=:id AND status IN ('queued','running')`. |
| `prune(before_iso, limit=1000) -> int` | delete terminal rows with `finished_at < :before` (the row holds a third copy of `text`/`reply`; see Config `turn_retention_days`). |
| `get(turn_id)` | for `wait` and tests. |

### Ports (`modules/turns/application/ports.py`)

- `TurnStore` — the Protocol of the repository above.
- `TurnDispatcher` — `async dispatch(turn_id: str) -> None`. Implementation
  `infrastructure/dispatch.py::QueueTurnDispatcher(queue)` enqueues
  `RUN_TURN_TASK = "run_turn"` with `turn_id=` only (primitives rule).
- `SnapshotStore` — the in-flight mirror: `async record(ev: TurnEvent)`,
  `async read(session_key) -> list[TurnEvent]`, `async is_running(session_key) -> bool`,
  `async clear(session_key, turn_id)`. `record` applies the same rules `_Active` does
  today: `turn.start` registers the turn, a persisted event is appended, a
  `text.delta`/`thinking.delta` extends the buffer, `text.block`/`thinking` clears
  its buffer, `turn.end` drops the turn — **and every `record` stores `ev.seq` as the
  turn's latest seq**, because the synthetic deltas `read` produces carry that seq
  and the SSE watermark (below) depends on it. `read` yields the persisted events of
  every active turn in start order, each followed by at most one synthetic
  `thinking.delta` and one `text.delta` at the turn's latest seq — byte-for-byte
  what `TurnBus.snapshot` returns today.
- `TurnEventBusPort` / `EventPublisher`: `is_running` and `snapshot` become
  **`async`**; `subscribe(session_key, listener) -> Unsubscribe` **stays sync**; a new
  **`async ready(session_key)`** resolves once the subscription is live (immediately
  in-process). Every subscriber that must not miss the first event awaits it: the
  SSE stream (inside `snapshot`), the Telegram dispatcher (one added line before
  `router.handle`), and `TurnLauncher.wait`. telegram's `TurnEventBusPort` gains
  `ready` too.
- `AskBrokerPort`: `open`, `wait`, `options_for`, `resolve` are **`async`**;
  **`discard` and `deliver` stay sync.** `discard` is called from `AskService.ask`'s
  `finally`, where a `CancelledError` can land — an `await` there is skipped and the
  entry leaks (the comment in `ask_service.py` paid for this with a live bug; the
  Redis version would leak a hash a later tap could "answer" into the void). So
  `discard` pops the local entry synchronously and, in the Redis broker, schedules
  the `DEL` as a fire-and-forget task held in a strong-reference set. The in-memory
  `AskBroker` is async-ified for the four (same bodies, same two load-bearing
  invariants in `wait`, same peek-before-pop in `resolve`); `tests/test_ask_broker.py`,
  `tests/test_ask_callback.py`, `tests/test_web_turns.py::test_answering_an_ask_resolves_the_broker`
  and `tests/modules/turns/test_turns_ask_service.py` add `await` at direct broker
  calls and nothing else. telegram's `AskBrokerPort.resolve` goes async with it;
  `handle_callback` awaits.
- `SlashCommandsPort.sdk_commands()` becomes **`async`** and is finally bound:
  `MetaSlashCommands(meta)` (meta key `sdk_slash_commands`, JSON list). The worker
  that ran the last turn is not the process serving `GET /api/commands`. Meta key
  added to the do-not-rename list.
- `sessions`' `LaneActivity` becomes `async active_lanes(keys) -> set[str]` and is
  satisfied by `TurnStore.active_lanes` — **the row is the one source of truth for
  "running"**; the snapshot store's `is_running` stays for the bus API but nothing in
  sessions reads it (a dead worker's snapshot outlives its lease by up to the TTL).
- `ControlBus` (in `core/eventbus.py`, see below) is what cancel and ask answers ride
  on; turns consumes it through `application/control.py`.

### Event bus (`core/eventbus.py`, spec req. 2)

```python
class EventBus(Protocol):
    async def publish(self, channel: str, payload: dict) -> None: ...
    def subscribe(self, channel: str, handler: Callable[[dict], Awaitable[None]]) -> Callable[[], None]: ...
    async def ready(self, channel: str) -> None: ...   # resolves once the subscription is live
    async def start(self) -> None: ...
    async def aclose(self) -> None: ...
```

- `InProcessEventBus` — a listener list per channel; `ready` returns immediately;
  a handler that raises is logged and never stops the others (today's
  `InProcessPublisher` rule).
- `RedisEventBus(client)` — one `PubSub` and one reader task per process. The first
  handler on a channel schedules `SUBSCRIBE`; `ready(channel)` awaits its
  confirmation; the last `unsubscribe()` schedules `UNSUBSCRIBE` (fire-and-forget: a
  message that arrives for a channel with no handler is dropped). Payloads are JSON
  text over the shared `core.redis.create_client(url)` (decode_responses=True; the
  stale "pass False for 3b's byte payloads" sentence in `core/redis.py` is fixed in
  this wave). The reader loop survives a connection loss: on error it logs,
  re-subscribes every channel that still has a handler, and continues with backoff —
  a pub/sub socket that dies must not silently freeze every SSE stream in the process.
- **`subscribe` is sync, `ready` is the seam.** `TurnBus.snapshot(lane)` awaits
  `publisher.ready(lane)` **before** reading the snapshot store, and the emitter
  writes the snapshot **before** it publishes — so after `snapshot()` returns, every
  event is either in what it returned or will arrive on the subscription
  (at-least-once). That ordering is written down in both docstrings.
- **At-most-once is the SSE stream's job, with a seq watermark.** Today
  `event_stream` drains the queue and reads the snapshot with no `await` between
  them; an async `snapshot` breaks that, and in redis mode a delta can be in the
  coalesced snapshot *and* still in flight to this process. So `event_stream`
  builds `watermark = {turn_id: max seq}` from the events `snapshot()` returned and
  drops, from both the drained `pending` list and the live tail, every event with
  `id is None and ev.turn_id in watermark and ev.seq <= watermark[ev.turn_id]`.
  Persisted events keep the existing id/cursor/`replayed` filters (a reaper's
  synthetic `turn.end` starts a fresh seq at 1 in a foreign process, so seq-dropping
  applies to deltas only). Order in the generator: subscribe → DB replay → `snap =
  await bus.snapshot(key)` → drain the queue (no await) → yield the snapshot → forward
  `pending` through both filters → tail through both filters.
- Channels: `hermes:turn:<session_key>` for turn events, `hermes:ctl` for control.

`modules/turns/infrastructure/publishers.py`: `ChannelPublisher(bus: EventBus)`
implements `EventPublisher` over `hermes:turn:<lane>` (`TurnEvent.to_wire()` out,
`TurnEvent(**wire)` in — `id` may be `None` for deltas, and `ts`/`seq` travel as
they are). `InProcessPublisher` becomes `ChannelPublisher(InProcessEventBus())` —
the name stays because `web_helpers.py`, `main.py` and the bus tests construct it.

`TurnBus(events, publisher, snapshots: SnapshotStore | None = None)`:
`snapshots` defaults to `MemorySnapshots()` so the two-argument constructor and the
legacy `TurnBus(db)` shim keep working. `emit` keeps the in-memory `_Active` for
**seq** only (the emitter owns the sequence; a turn has exactly one emitter), then
`await snapshots.record(ev)` (mirror), then `await publisher.publish(ev)` — in that
order. `snapshot`/`is_running` read the store; `ready` forwards to the publisher.

`MemorySnapshots` (`infrastructure/snapshots.py`) is today's `_Active` bookkeeping
behind the port; `RedisSnapshots(client, ttl_seconds)` mirrors it in Redis:
`hermes:snap:<lane>` — a list of active turn ids (RPUSH on `turn.start`, LREM on
`turn.end`); `hermes:snap:<lane>:<turn>` — a hash `{seq}`; `…:events` — a list of
persisted event JSON; `…:text` / `…:thinking` — the unflushed buffers (APPEND on
delta, DEL on flush). Every write is one pipeline that also `EXPIRE`s the touched
keys to `ttl_seconds` (`turn_snapshot_ttl_seconds`, default 3600): a worker killed
between heartbeat and reaper leaves keys that age out on their own, and the reaper's
`clear` removes them sooner. `read` = LRANGE over the id list, then per turn HGET +
LRANGE + GET + GET. `tests/modules/turns/test_turns_snapshots.py` is parametrized
over both stores and compares `read` output including `seq`.

### Control channel and cancel (`modules/turns/application/control.py`)

- `CancelRegistry` — process-local `turn_id -> asyncio.Task` of the turns running
  **in this process**; `register`, `unregister`, `cancel(turn_id) -> bool`.
  **Idempotent per turn**: the first cancel wins, later ones are no-ops — the
  heartbeat keeps seeing `cancel_requested_at` and would otherwise `task.cancel()`
  again 20 s later, landing on the terminal write's `await` and aborting it.
- `TurnControl(bus: EventBus, registry, asks: AskBrokerPort | None)` — subscribes to
  `hermes:ctl` at `start()` and dispatches: `{"op": "cancel", "turn_id": …}` →
  `registry.cancel`; `{"op": "ask", "qid": …, "answer": …}` → `asks.deliver(qid,
  answer)` (a no-op when the qid is not pending here). Unknown ops are logged and
  dropped. Every process starts it **after the env pops** (`main.run`,
  `worker_startup`); the agent in redis mode runs no turns and holds no asks, so its
  handler simply never matches.
- Cancel is **flag first, nudge second**: `TurnLauncher.cancel(lane, turn_id=None)`
  calls `store.request_cancel` (which is also what makes a `queued` turn `cancelled`
  without ever starting), logs the id it actually cancelled, then publishes the
  nudge. The running side honours the flag from two places: the control handler
  (immediate) and the heartbeat (every `lease/3` seconds, the fallback when the
  pub/sub message is lost). Either path ends in `registry.cancel(turn_id)`, and
  `TurnService` already turns `CancelledError` into `turn.end {status: cancelled}`.
  Cancelling by lane can hit a newer turn if the previous one finished between the
  click and the UPDATE — that race exists today (`runner.cancel(key)`) and the
  optional `turn_id` is how a later SPA version closes it.

### The launcher and the execution service (`modules/turns/application/`)

`launcher.py::TurnLauncher(store, dispatcher, bus, control_bus, *, queue_timeout_seconds, retention_days)`:

- `async start(session_key, text, *, user_id, attachments: list[Attachment], channel) -> str`
  — `store.create(...)` with `attachment_ids=[a.file_id for a in attachments]` and
  `lease_until = now + queue_timeout` (`LaneBusy` propagates; the router maps it to
  `409 busy`), then `dispatcher.dispatch(turn_id)`; if the dispatch raises the row is
  finished `error` (a direct terminal UPDATE without the `worker_id` guard — the row
  is still `queued`) and the exception re-raised. Returns the id.
- `async is_busy(session_key) -> bool` — `store.active(...) is not None`.
- `async cancel(session_key, turn_id=None) -> bool` — as above; True when there was an
  active row.
- `async wait(turn_id, session_key, *, poll_seconds=15.0) -> str | None` — subscribe
  to the lane, **`await bus.ready(lane)`**, then read the row (it may already be
  finished): returns `reply` on `ok`, `None` on `cancelled`, raises
  `domain.exceptions.TurnFailed(error)` on `error`/`lost`. Wakes on a `turn.end`
  with this `turn_id` or on the poll. **`turn.end` is emitted before `finish`
  commits** (`TurnService` emits inside `_run_turn_locked`; the execution service
  writes the row after it returns), so on `turn.end` `wait` re-reads the row at 50 ms
  intervals for up to 5 s before falling back to the 15 s poll — without this the
  Telegram reply is late by one poll about half the time. The poll is the fallback
  for a lost pub/sub message, not the primary path. Unsubscribes in a `finally`.
- `async reap(now) -> int` — for every `store.expired(now)` row: `mark_lost`, emit the
  synthetic `turn.end` on the lane through the bus (which clears the snapshot and
  tells every subscriber), log at warning with `turn_id`, `session_key`, `worker_id`.
  A `queued` row that nobody claimed within `queue_timeout` is reaped the same way —
  a queue message that vanished must not hold the lane forever. Then
  `store.prune(now - retention_days)`. **A lost turn is not re-executed**: re-running
  an SDK loop that may have half-completed would double its side effects and its
  cost; "reclaimable" means the lane is free, and the user resends. Called from
  `scheduler_tick` (both modes; see Tasks) — no second clock.
- **Named limitation (reap false positive).** If the reaper wins against a worker
  that is alive but stalled past its lease (a long GC pause, a DB blip that starved
  the heartbeat), the lane gets the synthetic `turn.end {error}`, `wait` raises and
  the user sees "Something went wrong" — and later the real turn finishes, its
  `finish` is refused by the `worker_id` guard and logged, while its messages and
  session row were written. "Two turns on the same lane never run concurrently" is a
  claim about rows; the residual window for SDK processes is one heartbeat interval
  plus cancellation latency, and the worker-side self-kill below is what keeps it
  that small.

`execution.py::TurnRunService(store, runner: TurnService, files: FilePublisher | None,
registry: CancelRegistry, *, lease_seconds, worker_id)`:

- `async run(turn_id)` — `claim` (None → log `turn_not_claimable` and return);
  resolve `attachment_ids` through `files.get` + `files.resolve` (the row's lane must
  equal each record's `session_key`, the same refusal the router applies — a forged
  row is not a reason to read another lane's file); register the current task;
  start the heartbeat task; `reply = await runner.run_turn(session_key, text,
  user_id=…, attachments=…, channel=…, turn_id=turn_id)`; `finish(ok, reply=…)`.
  `CancelledError` → `finish(cancelled)` and swallow (the task is done, there is
  nobody to re-raise to); any other exception → `finish(error, error=str(e))` and log.
  **Every terminal write runs under `asyncio.shield`** so a second cancel cannot
  abort it. `finally`: stop the heartbeat, unregister.
- The heartbeat: every `lease_seconds / 3`, `store.heartbeat(...)`; a returned
  `cancel_requested_at` cancels the main task; `None` (reaped) cancels it with a
  distinct log line. **An exception is caught, logged and retried on the next
  interval; if the local clock passes `lease_until` without a successful extension
  the worker cancels its own turn** (`turn_lease_lost`) — the worker kills itself
  before the reaper has to be wrong about it.
- `worker_id` = `f"{socket.gethostname()}:{os.getpid()}"`.

`TurnService.run_turn` gains `turn_id: str | None = None` (generated when omitted, so
the legacy callers and the runner tests are unchanged) and a `commands_store:
SlashCommandsStore | None = None` constructor keyword: after `_execute`, if the CLI
reported a list that differs from `self.slash_commands` (the in-process copy — no
meta read per turn), `await commands_store.save(names)`. The in-process
`asyncio.Lock` per lane stays too — it costs nothing and still protects the legacy
direct callers.

### `index_seen` moves to the session row (NFR-1)

`index_refresh_factory` (memory) injects the whole wiki index (`wiki_index_max_chars`,
8000 chars ≈ 2k uncached tokens) whenever `mtime > seen.get(session_key, 0.0)`. With
`_index_seen` process-local, N workers × 4 tasks each means a lane lands on a process
that has never seen it on a large fraction of turns — a direct hit on NFR-1 (≥60% of
input tokens from cache), not "what a restart already did". So the value becomes a
column: `sessions.index_mtime` (migration 0004). `SessionLane.route` returns it on
the row; `TurnService` seeds `self._index_seen[key]` from `row["index_mtime"]` before
`_execute` when the dict has no entry; `SessionLane.bind(..., index_mtime=)` and
`touch(session_key, now_iso, *, index_mtime=None)` write it back after the turn
(the hook may have bumped it mid-turn). sessions' `SessionStore.upsert/touch` gain the
keyword with a default so every existing caller is unchanged. The cached prefix is
untouched either way; this is about the user message.

### Tasks and the reaper

`workers/tasks.py` gains `run_turn(*, turn_id: str)` → `context().turn_runs.run(turn_id)`
and `scheduler_tick` gains a second line: `await context().turns.reap(now)`. Inline
mode reaches the same reap through `CronTicker(service, tick_seconds, extra=(ctx.turns.reap,))`
— a tuple of `async (now) -> None` callables the ticker awaits after `service.tick`,
each in its own `try/except` so a reap failure cannot stop the clock.
`taskiq_app.SCHEDULES` is unchanged (no new periodic name).

### Presentation

`presentation/dependencies.py`: `get_turns` (bundle attribute `turns`, the
`TurnLauncher`), `get_sdk_commands` (bundle attribute `sdk_commands`,
`required=False`). `get_runner` stays and still provides `deps.runner` — the
commands router reads `runner.slash_commands` **only when no `sdk_commands` port is
present** (the fallback the tests use; deleted in Step 5). **Audit every
`WebDeps(...)` construction site** (tests, shims) for the new `turns` field: `provide`
answers 503 for a required attribute that is `None`.

`router.py`:
- `post_message`: `turns: TurnLauncher = Depends(get_turns)`; the busy check becomes
  `await turns.is_busy(key)` (the bus check is dropped: the row is the one truth,
  and `LaneBusy` from `start` is also `409 busy` — the check is a courtesy, the index
  is the guarantee); attachments are still resolved in the handler (that is where
  the "not your lane" refusal lives) and passed as `Attachment`s; `auto_title`
  unchanged; 202 with `turn_id`. No `asyncio.create_task`, no `_bg` — `sse._bg` is
  deleted with it.
- `cancel`: `if not await turns.cancel(key): 409 not_running`.
- `answer_ask`: `await broker.options_for(qid)`, `await broker.resolve(...)`.
- `stream`/`sse.py`: the watermark ordering under "Event bus".

`sessions`: `LaneActivity.active_lanes` (async, batched); `ThreadService.is_running`
is replaced by `async running_lanes(keys) -> set[str]`; the sessions router computes
the set once per page / once per single-thread response.
`tests/modules/sessions/test_sessions_services.py` adapts its two assertions.

`web/deps.py`: `WebDeps.turns`, `WebDeps.sdk_commands` (both default `None`);
`web_deps(ctx)` passes `turns=ctx.turns`, `sdk_commands=MetaSlashCommands(ctx.meta)`.
`tests/web_helpers.py::FakeRunner` gains `async start(...)` (records the same
5-tuple `run_turn` did, emits the same four events, returns `"t-<n>"`), `async
is_busy`, `async cancel`; `make_client` passes the one fake as both `runner=` and
`turns=`, and the `ThreadService` it builds gets a fake `LaneActivity` over the
fake's `busy` set. `tests/modules/turns/test_turns_router.py`'s `FakeRunner`/`FakeBus`
get the same async shape and override `get_turns` as well as `get_runner`.

### Telegram

`core/app_context.py::QueuedTurnRunner(launcher, runner, *, busy_retry_seconds=1.0)`
satisfies telegram's `TurnRunnerPort`: `run_turn(...)` = `await launcher.start(...)`
then `await launcher.wait(turn_id, session_key)`; `reset` → `runner.reset`.
**`LaneBusy` from `start` is retried with a short sleep until the lane frees or
`queue_timeout_seconds` elapses** — today a second Telegram message on a busy lane
waits behind the per-lane lock and is answered, and double-texting is the most
common Telegram behaviour there is; refusing it with "Something went wrong" would be
a P5 break, not a wording change. After the timeout `TurnFailed` propagates.
`TurnFailed` also propagates so the dispatcher's existing `except Exception` sends
its "Something went wrong" line; `None` (cancelled) makes `if reply:` skip the final
message, which is what a cancelled turn did before. `main.build_telegram_gateway`
hands `MessageRouter` this adapter; `CommandService` keeps `ctx.runner` (it only
resets).

The dispatcher body changes by **one line**: `await bus.ready(key)` after
`bus.subscribe(key, presenter.on_turn_event)` and before `router.handle`. With
`RedisEventBus`, `subscribe` only schedules the SUBSCRIBE; the worker can claim the
row and emit `turn.start` before the confirmation, and `Presenter.on_turn_event`
sets its turn only on `turn.start` — one missed event means no status line for the
whole turn, silently. `handle_callback` awaits `broker.resolve`.

**The worker needs the bot token.** The `ask_user` keyboard and `send_document` are
sent by whichever process runs the turn, through the delivery objects
`build_delivery` binds; `worker_startup` builds them only `if cfg.telegram_token`. A
worker booted without the token still registers `ask_user` (the gate is "a
presenter is wired", and `AskService` always is) and would park a Telegram turn for
120 s on a question nobody saw. So `worker_startup` logs at **warning**
(`worker_without_telegram_token`) and `docs/deploy.md` says the worker's env is the
agent's env; the compose anchor already guarantees it.

### `ask_user` across processes (`infrastructure/redis_ask_broker.py`)

`RedisAskBroker(client, control: EventBus, *, answer_ttl_seconds=120)`:

- `async open(options, user_id, *, ttl_seconds) -> (qid, fut)`: `HSET hermes:ask:<qid>
  options=<json> user_id=<int>` + `EXPIRE ttl_seconds` (`AskService` passes the
  caller's timeout + 60; the in-memory broker accepts and ignores the keyword); the
  future is registered in a local `_pending` exactly as today.
- `async resolve(qid, token, user_id) -> bool` (runs on **another** process): one Lua
  script (WATCH/MULTI fallback like `core.redis.Lock`, for fakeredis without lupa):
  read the hash; refuse when missing, when `user_id` differs, when the token is not
  `c` or a valid index — **all refusals leave the hash untouched** (peek before pop);
  otherwise `DEL` the hash and `SET hermes:ask:<qid>:answer <value> EX answer_ttl`
  in the same step. Then `PUBLISH hermes:ctl {"op":"ask","qid":…,"answer":…}`. The
  key write is what makes a lost nudge harmless.
- `async wait(qid, fut, timeout)`: loop `asyncio.wait({fut}, timeout=min(remaining, 5.0))`;
  on each wake without a result, `GET …:answer` (the fallback for a missed nudge);
  at the deadline `DEL hermes:ask:<qid>` — `1` means nobody answered (return `None`),
  `0` means a resolver won the race: read the answer key once more and return it.
  The local entry is popped in a **synchronous** `finally` (a cancel arriving at the
  `finally` must not leak it). `fut` is never cancelled.
- `deliver(qid, answer)` (sync) — the control handler's entry: set the local future
  if pending.
- `async options_for(qid)` → `HGET`; `discard(qid)` (sync) → local pop + a
  fire-and-forget `DEL` of both keys, the task held in a strong-reference set.
- `CANCELLED` (`"\x00cancelled"`) is JSON-safe and travels as is.

The in-memory `AskBroker` gains the same sync `deliver` (what `resolve` already does)
so `TurnControl` can be wired to either.

### Composition (`main.py`)

```
inline (no CH_REDIS_URL)                   redis
--------------------------------------     --------------------------------------
events_bus = InProcessEventBus()           events_bus = RedisEventBus(redis_client)
snapshots  = MemorySnapshots()             snapshots  = RedisSnapshots(redis_client, cfg.turn_snapshot_ttl_seconds)
ask_broker = AskBroker()                   ask_broker = RedisAskBroker(redis_client, events_bus)
```

Both: `bus = TurnBus(turn_events, ChannelPublisher(events_bus), snapshots)`;
`turn_store = TurnRepository(engine)`; `registry_c = CancelRegistry()`;
`control = TurnControl(events_bus, registry_c, ask_broker)`;
`turns = TurnLauncher(turn_store, QueueTurnDispatcher(queue), bus, events_bus,
queue_timeout_seconds=cfg.turn_queue_timeout_seconds, retention_days=cfg.turn_retention_days)`;
`turn_runs = TurnRunService(turn_store, runner, files, registry_c,
lease_seconds=cfg.turn_lease_seconds, worker_id=…)`;
`runner = TurnService(..., commands_store=MetaSlashCommands(meta))`;
`threads = ThreadService(thread_repo, LaneActivityOverTurns(turn_store))` (an
adapter in `core/app_context.py`, or the repository directly if the signature
already fits).

`AppContext` gains `events_bus`, `control`, `turns`, `turn_runs`, `turn_store`.
`main.run()` and `worker_startup` call `await ctx.events_bus.start()` and
`await ctx.control.start()` **after the env pops** (nothing they do spawns, but the
invariant "the pops are the first thing after the objects hold their values" is
easier to keep monotone; `tests/test_main.py` asserts the order). `shutdown` closes
the control subscription and the bus before the redis client. `build_app` itself
dials nothing — it stays the headless thing every test calls.

Config (`core/config.py`, config.yaml-settable, `config.yaml.example` extended):
`turn_lease_seconds: float = 60.0`, `turn_queue_timeout_seconds: float = 300.0`,
`turn_snapshot_ttl_seconds: int = 3600`, `turn_retention_days: int = 30`.

`docker-compose.yml`: the worker command gains `--max-async-tasks 4` with a comment
(a turn spawns the CLI; concurrency per container is a memory dial, scale with
replicas); the `agent` comment says it no longer runs turns. `InlineQueue` limits
unchanged (`run_turn` unlimited inline: web turns on different lanes always ran
concurrently).

### Security

- Secrets are popped in both entry points before the bus and the control start and
  before anything can spawn (`tests/test_main.py`).
- The worker re-validates every attachment id against the row's lane.
- The ask resolver checks `user_id` before anything else and never touches the hash
  on a refusal.
- **`turns` is a new work-submission surface**: anything that can write the database
  can insert a row and have a worker spawn the CLI with chosen `text`, `user_id` and
  `channel`. That is the same posture as the existing `jobs` table — env scrubbing
  is inheritance hygiene, not a boundary — and the read-only catalog mount stays the
  boundary. Named here so nobody mistakes the row for an authorization.
- Nothing new is model-callable; `ask_user`'s gates and `ext_manage`'s fail-closed
  owner check are untouched.

### Untouchables

Prompt-cache invariants (`build_system_append`, tool descriptions, `allowed_tools`
order, `_build_prompt` shape); the `ask_user` registration gates (private lane +
presenter); `send_document` containment; `_admit`; the SSE frame; every status/code;
meta keys and session-key formats; `Attachment` fields.

## Global Constraints

- Checks: `.venv/Scripts/python.exe -m pytest <files> -q -p no:cacheprovider`
  (`make test-db`; `make test-stack` for the `redis_url` tests, which skip without it),
  `.venv/Scripts/python.exe -m ruff check src tests scripts`, `.venv/Scripts/lint-imports`
  (16 contracts kept; `modules.*.application` still imports no `redis`, no
  `sqlalchemy`, no framework — the Redis classes are `infrastructure/`).
- `fakeredis` (`FakeAsyncRedis`, shared `FakeServer` for "two processes") for the
  unit tests of every Redis class; the container only for `test_turns_two_processes.py`.
- Every file that is edited and every new file follows the repo's docstring habit:
  the *why* at the top, invariants named as invariants.
- No `print()`, no `logging.getLogger`, no `HTTPException`, no Pydantic outside
  `presentation/schemas.py`, no taskiq outside `workers/taskiq_app.py`.
- `web/src/api/client.ts` is the frontend contract; the 202 body is the only change
  and it is additive.

## Phases

### Phase A — Foundation (serial, one agent)

Owns: `core/eventbus.py` (new, both buses), `core/redis.py` (docstring),
`core/config.py` (four keys), `config.yaml.example`, `core/app_context.py` (fields
only), `modules/turns/domain/{models,exceptions}.py` (`TurnRow`, `TurnFailed`),
migration `0004_turns.py` (table, three indexes, `sessions.index_mtime`),
`modules/turns/infrastructure/{turn_repository,snapshots,publishers,dispatch}.py`
(`MemorySnapshots` only; `RedisSnapshots` is B2), `modules/turns/application/
{ports,bus,control,ask_service}.py` (async broker port with the sync
`discard`/`deliver`, async `is_running`/`snapshot`, `ready`, `SnapshotStore`,
`TurnStore`, `TurnDispatcher`, `CancelRegistry`/`TurnControl`, `MetaSlashCommands` +
`SlashCommandsStore`), `modules/turns/infrastructure/ask_broker.py` (async four +
sync two), `modules/telegram/application/{ports.py,services/callbacks.py}` (async
resolve, `ready` on the bus port), `modules/sessions/**` (`index_mtime` on the row,
store and router ports with defaults; `LaneActivity.active_lanes`;
`ThreadService.running_lanes`; the sessions router), `presentation/sse.py` (the
watermark ordering) and `presentation/router.py` **only** for the async
`snapshot`/`is_running`/broker awaits (the launcher wiring is B3's),
`gateway/questions.py`/`turnbus.py` shims if a name moved, and the test call-site
updates listed under Ports plus `tests/test_turn_bus.py`, `tests/test_runner.py`,
`tests/modules/turns/test_turns_bus_and_shims.py`, `tests/test_cron_ticker.py`,
`tests/test_send_document.py`, `tests/test_sessions.py` (await on
`is_running`/`snapshot`/`running_lanes` only). New tests: `tests/test_core_eventbus.py`
(in-process; fakeredis with two bus instances on one `FakeServer`; `ready`;
unsubscribe; a raising handler; reader recovery after a dropped connection),
`tests/modules/turns/test_turns_repository.py` (the index raises `LaneBusy`;
claim is `queued`-only; heartbeat/finish refuse a foreign `worker_id`; `finish`
returns False on a lost row; `request_cancel` is one statement and cancels a queued
row outright; `active` ignores the lease; `active_lanes`; `expired`; `mark_lost`;
`prune`), `tests/modules/turns/test_turns_snapshots.py` (`MemorySnapshots`: the
`snapshot` cases from `test_turn_bus.py`, two turns on one lane, seq on the synthetic
deltas — parametrized so B2 adds `RedisSnapshots` as a second parameter),
`tests/test_web_turns.py` gets one new stream test: a delta emitted after
`snapshot()` returned but carrying a seq at or below the watermark is dropped, one
above it is forwarded. `tests/test_migrations.py` addition (0004 up/down).
Verification: full suite, ruff, lint-imports.

### Phase B — parallel streams (disjoint files)

- **B1 — launcher, execution, tasks, composition.** `modules/turns/application/
  {launcher,execution,turn_service}.py` (`turn_id=`, `commands_store`, `index_mtime`
  seeding/write-back), `modules/turns/infrastructure/redis_ask_broker.py`,
  `workers/tasks.py`, `workers/taskiq_app.py` (start the bus and the control after
  the pops; the token warning; close in shutdown), `modules/scheduler/infrastructure/
  ticker.py` (`extra=`), `main.py` (composition table above, `run()` start calls
  after the pops, `shutdown`, `QueuedTurnRunner` into `build_telegram_gateway`,
  `LaneActivityOverTurns`), `core/app_context.py` (the two adapters),
  `docker-compose.yml`. Tests: `tests/modules/turns/test_turns_launcher.py` (fakes:
  start creates the row and dispatches; a dispatch failure finishes the row `error`;
  `cancel` flags then nudges, in that order, and logs the id; `wait` awaits `ready`
  before reading the row, returns the reply on `turn.end` after a bounded re-read
  when the row commits late, `None` on cancelled, raises on error, returns without
  an event on an already-finished row; `reap` marks lost, emits the synthetic end
  with the frozen payload, clears the snapshot, prunes), `tests/modules/turns/
  test_turns_execution.py` (claim → run → `ok` with reply; a cancel nudge mid-run →
  `cancelled` and a `turn.end cancelled`; a second cancel is a no-op and the terminal
  write survives it; the heartbeat seeing `cancel_requested_at` → same; a reaped row
  stops the run and the refused `finish` is logged; a heartbeat exception is retried
  and a lease that lapses locally cancels the turn; an unclaimable id is a logged
  no-op; attachments of another lane are refused), `tests/modules/turns/
  test_turns_redis_ask_broker.py` (fakeredis: resolve from a second broker instance
  wakes `wait`; stranger refused and the hash untouched; timeout → `None` and the
  hash gone; the answer-key fallback when no nudge is delivered; second resolve
  refused; `options_for` after answer is `None`; `discard` is sync and the DEL lands),
  `tests/modules/telegram/test_telegram_routing.py` addition for `QueuedTurnRunner`
  (busy lane retried until free; timeout raises; cancelled → None),
  `tests/test_workers_tasks.py` (`run_turn` registered and thin; `scheduler_tick`
  reaps), `tests/test_main.py` (start calls after the pops and before polling).
- **B2 — Redis snapshots and the two-process proof.** `modules/turns/infrastructure/
  snapshots.py` `RedisSnapshots` (Foundation left the memory store and the port; add
  it as the second parameter of `test_turns_snapshots.py`), `tests/modules/turns/
  test_turns_two_processes.py` (container `redis_url`, skips without it): two
  `TurnBus` instances over two `RedisEventBus` clients — an `event_stream` on B yields
  what A emits including the snapshot on a late subscribe with no doubled delta; a
  `TurnControl` on A cancels a task registered there when B publishes the nudge; an
  ask opened on A is resolved on B and A's `wait` returns the option. Also a
  connection-loss test for `RedisEventBus` if the fakeredis one in Foundation could
  not simulate it.
- **B3 — presentation, web bundle.** `modules/turns/presentation/{dependencies,router,
  commands_router}.py` (launcher wiring, 202 `turn_id`, `_bg` removal,
  `get_turns`/`get_sdk_commands`), `web/deps.py`, `tests/web_helpers.py`,
  `tests/test_web_turns.py` and `tests/modules/turns/test_turns_router.py` (the pops
  and the fakes only), `tests/test_web_commands.py` if the port fallback needs it,
  the `WebDeps(...)` construction-site audit, `modules/turns/README.md` (routes,
  dependencies, table, lifecycle, invariants, known debts including the un-resent
  Telegram reply after an agent restart).

### Phase C — Compose (serial, one agent)

Reconcile B1/B2/B3 in `main.py`/`app_context.py` if names drifted; run the full
suite, ruff, lint-imports; `docker compose config` and `build` with the three env
values; run the container tests against `hermes-test-redis`; `grep -rn
"create_task(run" src/claude_hermes/modules` empty; `grep -rn "\.resolve(\|\.options_for(\|\.open(" src/claude_hermes/modules/turns src/claude_hermes/modules/telegram`
shows only awaited calls (except the sync `discard`/`deliver`); every `bus.subscribe(`
call site that must not miss the first event is followed by `await …ready(`.

### Phase D — Advisor (Fable, read-only), Phase E — Fix, Phase F — Docs & Gate

Advisor checks, each against the code, not the plan: the index is the only lane
lock (no check-then-insert anywhere); `claim` is `queued`-only and every terminal
write is guarded by `worker_id`; `finish` cannot resurrect a reaped turn;
snapshot-before-publish in `TurnBus.emit` and `ready`-before-read in `snapshot`; the
SSE watermark drops a delta that is both in the snapshot and on the wire, in both
`pending` and the tail; the dispatcher and `wait` await `ready`; `wait` re-reads
after `turn.end`; `request_cancel` is one statement and precedes the nudge;
`CancelRegistry.cancel` is idempotent and the terminal write is shielded; the
heartbeat retries on error and self-cancels on a lapsed lease; the ask resolver's
refusals never touch the hash; the answer key closes the lost-nudge window; `wait`
never cancels the future and pops in a sync `finally`; `discard` is sync;
`QueuedTurnRunner` waits on a busy lane; attachments re-checked against the row's
lane on the worker; secrets popped in both entry points before the bus starts;
`index_mtime` seeded and written back; no in-memory state on the agent that a
second instance would disagree about (list what remains and why); the 202 body is
the only wire change; prompt-cache invariants untouched; lint-imports 16 kept.
Docs & Gate: `modules/turns/README.md`, `workers/README.md` (five tasks,
`--max-async-tasks`, the token requirement), wiki `components/core-runtime.md`
(eventbus, the agent's role in redis mode), `components/web.md`,
`components/gateway.md`, `contracts/state-db.md` (`turns`, `sessions.index_mtime`),
`flows/message-turn.md` (the new sequence in both modes), `decisions/redis-taskiq-scaling.md`
(the singleton table loses the turn runner; add the two-instance argument, what the
agent still holds, and the reap false-positive window), `quality/invariants.md`,
`quality/testing.md`, `quality/failure-modes.md` (lost turn, reap false positive,
worker without token), `operations.md`, `index.md`, `log.md`; `README.md` and
`docs/deploy.md` (turns run in the worker; a restart of `agent` no longer kills a
turn; the worker needs the bot token; new config keys; the later spinner);
fingerprint + validate through the container; full gate.

## Open Questions (defaulted)

1. **Re-execute a lost turn?** Default: no — mark `lost`, free the lane, the user
   resends. Re-running an SDK loop that may have half-completed doubles side effects.
   `claim` matches `queued` only, so the code cannot disagree with this answer.
2. **Who sends the Telegram reply?** Default: the agent, from `wait` — the presenter
   stays one object, voice replies stay where the OpenAI client is, and webhook mode
   (Step 4) can move it to the worker if Telegram's handler timeout demands it. A
   turn that outlives an agent restart finishes and is visible in the web thread;
   its Telegram reply is not re-sent (documented in the README's known debts).
3. **A second Telegram message on a busy lane.** Default: it waits (today's
   behaviour), by retrying `start` — not by making the table a queue, which would
   need a `running`-only index and a per-lane claim order for one channel's habit.
4. **Worker concurrency.** Default: `--max-async-tasks 4`; a deployment scales
   worker replicas, not this number.

## Advisor amendments (2026-09-17)

Folded in from the implementation-advisor pass on the first draft — kept here so
the reasoning survives the next reader:

- **B1** Telegram double-texting: `QueuedTurnRunner` retries `start` on `LaneBusy`
  (Open Question 3).
- **B2** `wait` wakes on `turn.end` before `finish` commits: bounded re-read; and
  `wait` awaits `ready` before reading the row.
- **B3** async `snapshot` broke the SSE drain/snapshot atomicity: the seq watermark
  and the fixed generator order; `RedisSnapshots` therefore persists `seq`.
- **B4** async `discard` reintroduced the cancel-at-finally leak: `discard` and
  `deliver` stay sync.
- **B5** the Telegram presenter could miss `turn.start`: the dispatcher awaits `ready`.
- **B6** `claim` stole expired `running` rows and `finish` was unguarded: `queued`-only
  claim; `worker_id` on every terminal write; `finish` returns bool and logs refusals.
- **R1/R2** heartbeat exceptions and double cancel: retry + self-cancel on a lapsed
  lease; idempotent `CancelRegistry.cancel`; shielded terminal writes.
- **R3** `_index_seen` is NFR-1-relevant with N workers: `sessions.index_mtime`.
- **R4** one truth for "running": `active_lanes` on the row, batched per page.
- **R5** `request_cancel` is one statement; optional `turn_id`; the cancelled id is
  logged.
- **R7/R8/R10/R11** named: the reap false-positive window, the worker's token
  requirement, `turns` as a work-submission surface, pruning with
  `turn_retention_days`.
- **Suggestions taken**: `active` ignores the lease; the `core/redis.py` docstring;
  compare against the in-process list before saving slash commands; the later
  spinner is documented; the `WebDeps` construction-site audit.

---
id: quality.invariants
kind: quality
status: current
summary: Confirmed system invariants and safety properties, with the code that enforces each.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:a3df67e0cfa0c09f4378109991f62aef2554a6a0ff3d9833b3c10c79066fb9b9
evidence:
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/modules/turns/infrastructure/tool_handlers/documents.py
  - path: src/claude_hermes/modules/turns/infrastructure/ask_broker.py
  - path: src/claude_hermes/modules/turns/infrastructure/redis_ask_broker.py
  - path: src/claude_hermes/modules/turns/infrastructure/turn_repository.py
  - path: src/claude_hermes/modules/turns/infrastructure/snapshots.py
  - path: src/claude_hermes/modules/turns/application/launcher.py
  - path: src/claude_hermes/modules/turns/application/execution.py
  - path: src/claude_hermes/modules/turns/application/control.py
  - path: src/claude_hermes/modules/turns/application/bus.py
  - path: src/claude_hermes/modules/turns/presentation/sse.py
  - path: src/claude_hermes/core/eventbus.py
  - path: src/claude_hermes/modules/telegram/infrastructure/dispatcher.py
  - path: src/claude_hermes/modules/turns/domain/prompt.py
  - path: src/claude_hermes/integrations/claude_sdk/guards.py
  - path: src/claude_hermes/modules/telegram/application/services/commands.py
  - path: src/claude_hermes/modules/extensions/application/services/admin_service.py
  - path: src/claude_hermes/modules/extensions/application/services/compose_service.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_secrets.py
  - path: src/claude_hermes/core/crypto.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_gate.py
  - path: src/claude_hermes/modules/files/infrastructure/layout.py
  - path: src/claude_hermes/modules/files/infrastructure/local_blob_store.py
  - path: src/claude_hermes/modules/files/infrastructure/s3_blob_store.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/repository.py
  - path: src/claude_hermes/workers/taskiq_app.py
  - path: src/claude_hermes/modules/learning/application/services/review.py
  - path: src/claude_hermes/modules/learning/application/services/loop.py
  - path: src/claude_hermes/modules/learning/application/services/curator.py
  - path: src/claude_hermes/modules/learning/application/services/aging.py
  - path: src/claude_hermes/core/snapshots.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/core/db/migrations/versions/0005_users.py
  - path: src/claude_hermes/modules/identity/application/services.py
  - path: src/claude_hermes/modules/turns/domain/policies.py
  - path: src/claude_hermes/modules/files/infrastructure/roots.py
  - path: src/claude_hermes/modules/sessions/domain/policies.py
  - path: src/claude_hermes/modules/telegram/presentation/webhook.py
  - path: src/claude_hermes/modules/telegram/infrastructure/transport.py
  - path: src/claude_hermes/modules/memory/infrastructure/turn_hooks.py
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
  - path: src/claude_hermes/main.py
  - path: docker-compose.yml
  - path: src/claude_hermes/modules/learning/application/scopes.py
  - path: src/claude_hermes/modules/learning/infrastructure/scoped_meta.py
  - path: src/claude_hermes/modules/content/domain/status.py
  - path: src/claude_hermes/modules/content/application/service.py
  - path: src/claude_hermes/modules/content/infrastructure/repository.py
  - path: src/claude_hermes/modules/content/infrastructure/media.py
  - path: src/claude_hermes/modules/content/presentation/agent_tool.py
  - path: src/claude_hermes/modules/content/presentation/routine_server.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/executor.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_slot.py
  - path: src/claude_hermes/modules/learning/infrastructure/fork_runner.py
  - path: src/claude_hermes/workers/curator.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/core/auth.py
  - path: src/claude_hermes/core/tasks.py
  - path: src/claude_hermes/modules/sessions/infrastructure/repositories.py
relations:
  - decision.prompt-cache-discipline
  - decision.readonly-catalog-boundary
  - decision.modules-and-ports
  - decision.redis-taskiq-scaling
  - decision.minio-blobs
  - contract.state-db
  - flow.message-turn
  - component.web
  - decision.multi-user-isolation
---
# Invariants

1. **Byte-stable cached prefix.** `system_append` is built once at session
   creation and reused byte-identical on every resumed turn
   (modules/turns/application/turn_service.py); nothing swaps toolsets mid-session — extension
   mutations reset the lane instead (modules/turns/infrastructure/hermes_server.py → modules/turns/application/turn_service.py), and a
   mutation made in the browser — which has no lane of its own — resets every
   lane an extension change reaches (`runner.reset_all`) or, for a skill edit
   since Wave 4b, every lane of the user whose prefix it changes
   (`runner.reset_owned`). All reset *after* the row write, never before.
   The user
   turn's own shape is held stable too: `_build_prompt` (modules/turns/infrastructure/sdk_runner.py) returns a
   bare `str` whenever nothing is attached, so adding inbound attachments moved
   no existing turn's prefix (see [[decision.prompt-cache-discipline]]).
2. **The read-only catalog mount is the only hard security boundary.**
   `./catalog:/app/catalog:ro` in docker-compose; everything the agent can
   write (drafts, user-catalog, `/data`) is a queue or soft boundary, never
   the source of an executable spec the agent couldn't forge.
3. **The registry records THAT, not WHAT.** The `extensions` table has no
   command/url/args/repo columns; the executable spec is resolved fresh from
   the reviewed manifest at compose time (modules/extensions/infrastructure/repositories/extension_repository.py,
   modules/extensions/application/services/compose_service.py).
4. **Privileged actions are gateway-intercepted, never model-callable.**
   `/secret` and `/approve` are handled before `run_turn`
   (modules/telegram/application/services/commands.py) — a prompt-injected turn cannot store a credential or
   enact its own draft. `ext_manage` can only *propose* (`draft`).
5. **Secrets never enter the model context.** Every credential is popped from
   `os.environ` at startup — in `main.run` **and** in the worker startup, because
   both spawn the CLI: `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `CH_SECRETS_KEY`,
   `CH_S3_ACCESS_KEY`, `CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL`,
   `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`, `CH_WEB_SECRET` (main.py,
   workers/taskiq_app.py). The last three are popped because compose derives the
   `CH_*` spellings from them and `env_file` injects the originals as well — popping
   only the derived name leaves the identical credential one variable over.
   `CLAUDE_CODE_OAUTH_TOKEN` is the one deliberate exception: the CLI subprocess is
   what it is for. The web channel's Login-Widget key `sha256(bot_token)` is derived
   one line above the pop (main.py, the widget and Mini App keys), so the HTTP layer
   holds two digests and never the token; the bash guard denies `.env`
   access (integrations/claude_sdk/guards.py); `send_document` refuses `secrets.env`
   (modules/turns/infrastructure/tool_handlers/documents.py). Since Wave 3a the
   stored extension secrets are **Fernet ciphertext in `extension_secrets`**
   (modules/extensions/infrastructure/repositories/pg_secrets.py over
   core/crypto.py) rather than a 0600 file on the volume the agent's own Bash can
   read: the ciphertext is in PostgreSQL, the key is `CH_SECRETS_KEY` and is gone
   from the environment, so a stolen dump is not a stolen credential and neither is
   a shell inside the agent. `SecretBox.from_env` takes the mapping so a caller can
   pass a snapshot taken *before* the pop; booting without the key is refused rather
   than falling back to a process-ephemeral one.
6. **Exactly one active turn per lane — enforced by PostgreSQL.** Since Wave 3b
   the answer is a **unique partial index**, `turns(session_key) WHERE status IN
   ('queued','running')` (modules/turns/infrastructure/turn_repository.py): `create`
   inserts and translates the `IntegrityError` into `LaneBusy`, and nothing
   anywhere does check-then-insert. The route's `is_busy` is a courtesy that makes
   the common case quiet and renders the byte-identical `409 busy` body when it
   loses the race. Two API instances that both believe a lane is free therefore
   still produce one turn. `TurnService`'s per-lane `asyncio.Lock` and `_tasks`
   survive (they still serialise direct `run_turn` callers and let `cancel()` stop
   exactly that turn), but nothing decides "busy" from them any more — the row
   does, and `active`/`active_lanes` ignore the lease on purpose, so `is_busy` can
   never disagree with `create` ([[contract.state-db]]).
7. **Learning never competes with the user.** The review pass runs after the
   reply is recorded (modules/turns/application/turn_service.py), single-flight,
   forked (`fork_session=True`) so the live transcript is never mutated. Since
   Wave 3a the single-flight answer is a **lease, not a flag**: `MetaReviewGate`
   (modules/learning/infrastructure/review_gate.py) holds `learning_review_until`
   in `meta`, claimed at the start of a pass and cleared in a `finally`, and both
   the curator gate and `TurnService` read it. It works whichever process runs the
   review and it survives a dead worker — a boolean would have stayed true forever
   the first time one was OOM-killed mid-pass, silently disabling the curator. An
   unreadable row reads as free for the same reason. A second nudge while a review
   runs is dropped, never queued. Since Wave 4b the lease is **per user**
   (`learning_review_until:<id>`) and is that user's single-flight rule only;
   the deploy-wide bound is invariant 36 below.
8. **Reversible autonomy — no deletion path.** `write_guard_factory` confines
   a background fork's writes to an explicit root list and always protects
   `.usage.json`/`.archive`; an **empty root list denies everything**, which
   is how a wiki-lint dry run is read-only by construction rather than by
   asking the model nicely (modules/learning/application/services/review.py; called with `roots=[]` on a dry
   run, modules/learning/application/services/curator.py). Aging archives, never deletes (`apply_aging`,
   modules/learning/application/services/aging.py); every real pass snapshots first — the skills tree
   before consolidation (modules/learning/application/services/aging.py) and the wiki tree before lint
   (modules/learning/application/services/curator.py) — through one `backup.snapshot(..., kind=)` parameterized
   for either tree (core/snapshots.py). Rollback itself takes a protective
   pre-rollback snapshot (core/snapshots.py). Pinned and user-created skills are
   untouchable.
9. **Cron sessions are isolated.** Jobs run as `cron:<id>` with no resume —
   never spliced into a live conversation (modules/scheduler/infrastructure/executor.py); prompts pass an
   injection scan before storage (modules/scheduler/domain/policy.py); a hard timeout bounds every run —
   the job's own `timeout_seconds` when set (30-1800, validated at creation),
   otherwise `cron_timeout_seconds` (180s). The cron→topic context bridge
   (modules/turns/application/turn_service.py) does not weaken this: it quotes the delivered report's
   *text* into a chat turn's user message — a copy of the output, never a
   resume of the cron session.
10. **At-most-once cron fires, across processes.** The reschedule happens
    *inside* `claim_due`'s transaction, in the same statement pair as the
    `SELECT … FOR UPDATE SKIP LOCKED` that found the row
    (modules/scheduler/infrastructure/repository.py). A concurrent ticker gets a
    disjoint set; one arriving after the commit sees a `next_run_at` already in the
    future; there is no window between the select and the update, because they are
    one transaction. Moving the reschedule back out into a second `reschedule()`
    call after the claim reopens exactly that window
    ([[decision.redis-taskiq-scaling]]). Fires older than `min(period/2, 2h)` are
    still recorded `missed` and skipped, read off the claim's `fire_at`
    (modules/scheduler/application/service.py). The lease
    (`claimed_at`/`lease_until`) guards only claim→pickup: a run that outlives it is
    not re-claimed, because `next_run_at` has moved past the fire.
11. **Session mapping survives crashes.** The session row is committed
    (`bind`/`touch`, modules/turns/application/turn_service.py) before the reply is returned; a
    stale resume resets the row and replays the turn once (modules/turns/application/turn_service.py).
12. **Fail-closed authorization.** `ext_manage` mutations refuse when ACL or
    context is missing — `core.app_context.ExtensionAdminTool` resolves `is_owner`
    to False unless both exist, and `ExtensionAdminService` takes the gate already
    decided (modules/extensions/application/services/admin_service.py); without an
    acl the tool is not registered at all
    (modules/turns/infrastructure/hermes_server.py); owner-only gates on extensions and
    secrets; allowlisted users may chat but not change what the agent is.
13. **Exactly one way to write memory.** All persistent agent-authored
    knowledge lives under `agent_home/wiki/`, reached with the same
    Read/Glob/Grep/Write/Edit the agent already has for code — there is no
    MCP tool for it. `memory_save` and `src/claude_hermes/memory.py` are
    gone; `hermes_server.py` registers no replacement handler.
14. **`wiki/raw/` is immutable.** `raw_guard_factory` denies every `Edit`
    under `wiki/raw/` and denies `Write` only when the target already
    exists (modules/memory/infrastructure/turn_hooks.py) — a source that could be quietly amended could
    never be re-checked against a page's claim. The same guard runs on a
    live turn (modules/turns/application/turn_service.py) and on the review fork (modules/learning/application/services/review.py).
15. **A turn cannot end with the wiki visibly out of sync.**
    `stop_gate_factory` blocks Stop when a page was written but `index.md`
    or `log.md` was not (modules/memory/infrastructure/turn_hooks.py). Honest caveat: `stop_hook_active`
    is the loop breaker — a turn that already ignored the nudge once is
    warned and allowed to finish, leaving the drift for the wiki-lint pass
    to catch.
16. **Non-text intake gates before it spends.** Voice notes and attachments
    both build a `MessageEvent` from the caption and run `_verdict` on it
    *before* any paid or IO work — transcription for voice, `download_all` for
    attachments (`intake_attachments`, modules/telegram/application/services/routing.py). An un-addressed
    message in a group therefore costs nothing, and emits no typing indicator
    either, which would otherwise announce the bot in a chat it is supposed to
    be silent in. The 20MB refusal is read from Telegram's own `file_size`, so
    it is free as well.
17. **A model-callable tool may block a lane, but never unboundedly.** `ask_user`
    parks the turn on an inline-keyboard answer while holding
    `self._locks[session_key]`. Nothing bounds a live turn's wall clock the way
    `cron_timeout_seconds` bounds a cron run, so the bound is a per-turn cap
    (`Config.ask_user_max_per_turn`, counted on `TurnFlags.asks_used`) plus a
    per-question timeout (`Config.ask_user_timeout_seconds`). The tool exists
    only in private chats, and returns a preference — never an authorization.
18. **Every turn ends with exactly one `turn.end`.** `_run_turn_locked`
    (modules/turns/application/turn_service.py) wraps the whole body, so `ok`, `error` (re-raised after the
    event) and `cancelled` (`asyncio.CancelledError`) all emit one closing
    event before leaving. A UI that opens a spinner on `turn.start` can
    therefore always close it. Wave 3b extended the rule past the body: a failure
    on the worker *before* the body is entered (a blob fetch, an attachment id
    that does not resolve against the row's lane) emits `turn.start` and then an
    `error` `turn.end` itself (modules/turns/application/execution.py), and the
    reaper emits the frozen synthetic end for a turn whose worker died — so the
    spinner closes even when nothing ever ran. The dual of this: an event sink
    must never be
    able to kill a turn — `_emit`, `TurnBus.emit`'s persistence and each
    listener call are individually caught and logged, so a broken browser
    stream cannot cost the user a reply ([[component.web]]).

## The Wave 2 untouchables

Wave 2 moved this code into modules ([[decision.modules-and-ports]]). Two lists
were declared untouchable while it moved, and stay that way: a break in the first
is a cache miss on every lane, a break in the second is a security regression.
Both are checked by name in the tests each item names.

**Prompt cache** (`modules/turns/`, [[decision.prompt-cache-discipline]]):

1. `build_system_append` output is byte-stable for the same inputs; `TURN_RULES`
   and `WIKI_RULES` moved byte-identically (`modules/turns/domain/prompt.py`).
2. Every `mcp__hermes__*` tool description is byte-identical to its pre-move text
   (`modules/turns/infrastructure/hermes_server.py`;
   `tests/test_ext_manage_guidance.py` parses one of them out of that file), and
   so are `CRON_TOOL_DESCRIPTION` / `CRON_TOOL_SCHEMA`
   (`modules/scheduler/presentation/tool.py`). One deliberate change since:
   Wave 4b rewrote `send_document`'s path sentence (relative to the agent's home,
   nothing outside it) — a one-time prefix change taken at that deploy, recorded
   in [[system.operations]], and the text is frozen again from there.
3. `_build_prompt` returns a bare `str` when there are no attachments, and a
   fresh async iterator on every call when there are
   (`modules/turns/infrastructure/sdk_runner.py`).
4. `allowed_tools` ordering is `_BUILTIN_TOOLS + mcp_tool_names +
   composed.tool_names`; the registration order in `hermes_server.py` produces
   the middle term.
5. `reset_all()` runs after every prefix-changing extension mutation, and
   `reset_owned(user)` after every skill mutation (Wave 4b) — never silently
   no-ops, never a wider reset than the prefix change reaches.
6. `ctx.flags.reset_lane` is applied **after** `sessions.bind` — a reset before
   the bind is undone by it.

**Security** (each moved verbatim with its comment):

1. `/approve` and `/secret` are intercepted before `run_turn`
   (`modules/telegram/application/services/commands.py`).
2. The web never claims an unowned deploy: `_admit` calls `acl.check(uid, "web")`,
   never `"private"` (`modules/identity/presentation/router.py`).
3. Token derivation then `os.environ.pop` in `main.run`, before
   `build_telegram_gateway(` — asserted by `tests/test_main.py` for all ten names
   of invariant 5, and asserted again on the worker startup's source, which has the
   same rule for the same reason.
4. The containment checks: `WikiGraphService.resolve`
   (`modules/memory/application/services/graph.py`), `SkillRepository.resolve` /
   `locate` (`modules/learning/infrastructure/skill_repository.py`), and the
   agent-home check behind `FileService.resolve` — now **one body**,
   `layout.resolve_within` (`modules/files/infrastructure/layout.py`), called by
   `LocalBlobStore.local_path` and by `S3BlobStore.local_path`/`open`. On the S3
   backend it runs **before** anything is fetched or written, so a forged row is
   refused rather than materialized; a second copy of it would be a second place to
   weaken it ([[decision.minio-blobs]]).
5. The catalog reader never reads drafts, and promotion happens only through
   `CatalogWriter.promote` — on all three channels (chat, browser, host script).
6. The `is_repo_entry` anti-downgrade check, in all five places that can write a
   user-catalog entry.

Wave 2b rewrote every handler again — `APIRouter`, `Depends`, Pydantic request
bodies ([[decision.modules-and-ports]]) — and both lists survived unchanged,
including the `ext_manage` description, which now has exactly one definition
(`modules/extensions/presentation/agent_tool.py`) that the root hands to turns
through `core.app_context.ExtensionAdminTool`. Two invariants the wave added to
them:

7. **A Pydantic 422 never replaces a status or a `code` a test asserts.** Request
   models are lenient by design (`extra="ignore"`, `Any`/`Optional` fields with
   defaults) wherever the old handler answered a malformed field with its own
   code, and the handler keeps that check. Malformed bodies land on the one
   `RequestValidationError` handler, which answers **400 `bad_request`** — the
   code `web/src/api/client.ts` branches on. `tests/test_web_*.py` passed the
   whole conversion unmodified; a file of theirs turning red is a wire break,
   never a test to edit.
8. **The owner gate is `core.auth.require_owner` as a route dependency**, solved
   before any parameter provider, so a non-owner is refused before a manifest is
   read or a path is built from a URL segment — the position
   `owner_only(request)` held as every owner-only handler's first line. Route
   paths, `operation_id`s and error `code` strings are frozen together (spec
   req. 36): a generated client names its methods after the `operation_id`.

## The Wave 3a additions

Wave 3a added a second process, a queue and an object store
([[decision.redis-taskiq-scaling]], [[decision.minio-blobs]]). Four rules it is
built on, beyond the updates folded into 5, 7, 10 and the security list above:

19. **Task arguments are primitives.** `job_id: int`, `fire_at: str`,
    `session_key: str`, `kinds: list[str]` (sorted, never the tracker's `set`).
    The argument list has to survive being JSON on a Redis list, and a task that
    takes a service is a task that only ever worked inline. Services come from a
    module-level `AppContext` each process binds once; a body that runs before the
    bind raises `TasksNotBound` (`workers/tasks.py`).
20. **`queue_mode` is derived, never configured.** It is `"redis"` when
    `CH_REDIS_URL` is set and `"inline"` otherwise, and `load_config` refuses a
    `queue_mode:` key in `config.yaml` (`core/config.py`). Two switches that can
    disagree are a deployment that enqueues into a Redis nothing reads.
21. **Only the agent polls Telegram.** `getUpdates` is single-consumer; a second
    poller on one token gets `409 Conflict` and neither answers. The worker builds
    its delivery objects through the same `main.build_delivery(ctx, token)` and
    never calls `run_polling` (`workers/taskiq_app.py`).
22. **`/api/files/{id}` stays ownership-checked; no presigned URLs.** A presigned
    URL *is* the authorization — it hands out the bytes to whoever holds the
    string, with no reference to who is asking or whether the lane is still theirs.
    The `files` row has no `owner_id`; visibility is borrowed from the thread, per
    request (`modules/files/presentation/router.py`, spec req. 36).

## The Wave 3b additions

Wave 3b took the turn off the request path ([[decision.redis-taskiq-scaling]],
[[flow.message-turn]]). Beyond the rewrites folded into 6 and 18 above, seven
rules, each one a one-line change away from a double turn, a stuck lane or a
doubled reply:

23. **Every terminal write on a `turns` row is guarded by `worker_id`, and runs
    under `asyncio.shield`.** `claim` matches `queued` only, so a `running` row
    whose lease lapsed is the reaper's and is never re-executed;
    `heartbeat`/`finish` match `id AND worker_id AND status='running'`, so a
    worker whose turn was already reaped cannot flip the row, and the refused
    `finish` is **logged** with the row's real status rather than swallowed — a
    completed turn recorded as `lost` has to be visible
    (modules/turns/application/execution.py,
    modules/turns/infrastructure/turn_repository.py). The one sanctioned exception
    is `fail_queued`, the launcher's dispatch-failure write: there is no worker
    yet, so it is narrowed to `status='queued'` instead and cannot touch a claimed
    turn.
24. **Cancel is flag first, nudge second, and idempotent.** `request_cancel` is
    one `UPDATE` (which also turns a `queued` row `cancelled` outright) and the
    `hermes:ctl` message follows it (modules/turns/application/launcher.py). The
    running side honours the flag from the control handler *and* from the
    heartbeat, so cancelling works with pub/sub entirely down, within a third of a
    lease. `CancelRegistry.cancel` is a no-op after the first
    (modules/turns/application/control.py): the heartbeat keeps seeing
    `cancel_requested_at`, and a second `task.cancel()` would land on the terminal
    write's `await` and abort it.
25. **Snapshot before publish, ready before read.** `TurnBus.emit` mirrors the
    event into the `SnapshotStore` **before** it publishes, and `TurnBus.snapshot`
    awaits `publisher.ready(lane)` **before** it reads the store
    (modules/turns/application/bus.py). Together those make the stream
    at-least-once: after `snapshot()` returns, an event is either in what it
    returned or still on its way to the subscription. In `RedisEventBus`, `ready`
    resolves on the server's SUBSCRIBE **confirmation frame**, not on the command
    send — the snapshot read leaves on a different pooled connection, so the
    weaker promise would be no promise (core/eventbus.py).
26. **At-most-once is the SSE stream's job.** `event_stream`'s order —
    `subscribe → DB replay → await snapshot → drain → yield snapshot → pending →
    tail` — is an invariant, and both trailing stages run through one `covered()`
    filter: persisted events by id/cursor, deltas by the per-turn **seq
    watermark** (modules/turns/presentation/sse.py). Deltas only, because a
    reaper's synthetic `turn.end` starts a fresh seq at 1 in a foreign process.
    Reordering it doubles the text in the browser.
27. **Every `subscribe` whose caller must not miss the first event is followed by
    `await ready(...)`.** Three of them: the SSE stream (inside `snapshot`), the
    Telegram dispatcher before `router.handle`
    (modules/telegram/infrastructure/dispatcher.py) and `TurnLauncher.wait` before
    it reads the row. The presenter latches onto its turn only on `turn.start`, so
    one missed event is no status line for the whole turn, silently.
28. **`discard` and `deliver` on the ask broker stay synchronous.** `discard` is
    called from `AskService.ask`'s `finally`, where a `CancelledError` can land —
    an `await` there is skipped and the entry leaks, which is a bug this codebase
    has already paid for once. The Redis broker pops the local entry synchronously
    and schedules the `DEL` as a fire-and-forget task held in a strong-reference
    set. Every refusal in `resolve` — wrong user, unknown token, missing question
    — leaves the hash untouched, and the `hermes:ask:<qid>:answer` key is what
    makes a lost nudge harmless
    (modules/turns/infrastructure/redis_ask_broker.py).
29. **A slow subscriber lags only its own lane.** `RedisEventBus` fans out on a
    per-**channel** delivery task rather than on the reader: the reader parses and
    enqueues, one consumer task per channel awaits the handlers in order (order
    within a lane is what the seq watermark depends on). Without it, one Telegram
    presenter waiting on a rate-limited Bot API call would stall every SSE stream,
    every pending `ready()` and every control nudge in the process. A queue that
    keeps growing logs every `SLOW_HANDLER_DEPTH` payloads, so a wedged handler is
    visible (core/eventbus.py).

One caveat on the list: `workers/taskiq_app.py` was written with no network and
verified afterwards — `tests/test_workers_taskiq.py` is green with taskiq installed
and a live worker executed hand-enqueued tasks. The redacting log handler in the
worker process (item 5 in `src/claude_hermes/workers/README.md`) is still
reasoning rather than observation until a production worker delivers a job.

## The Wave 4a additions

Wave 4a gave the deploy a `users` table, an `owner_id` on every user-owned row and
a workspace per user ([[decision.multi-user-isolation]]). Five rules, each one a
one-line change away from a cross-user read:

30. **The ROW's owner decides which tree a blob lives in — never the requester's,
    never a process global.** Both blob stores take a `RootLookup`
    (`async (owner_id) -> Path`, modules/files/infrastructure/roots.py) instead of
    a root frozen in `__init__`, and every `put`/`get`/`resolve` is handed the
    `owner_id` of the `files` row it is acting on. The download route re-resolves
    against whoever the row says owns the file, which is what keeps a guessed or
    forged file id from reading out of somebody else's tree;
    `layout.resolve_within` still re-checks containment against **that** root, so
    the check did not get weaker, it got per-owner
    (modules/files/application/service.py).
31. **The lane-owner rule is applied in exactly one place.**
    `core/session_keys.py::lane_owner(session_key, channel, user_id,
    deploy_owner)` is what `sessions.owner_id`, `threads.owner_user_id`, the `0005`
    backfill (the same rule spelled in SQL) and `WorkspaceResolver.for_lane` all
    go through, so a row and the turn that is running cannot disagree about whose
    workspace a lane is. It is **pure** given the deploy owner — `for_lane` reads
    no table — which is why a brand-new lane whose `sessions` row does not exist
    yet resolves exactly like an old one. The id comes out of the KEY wherever the
    key carries one: user B opening `web:A:…` does not become its owner.
32. **The `users` INSERT is the claim arbiter, not a check-then-insert.**
    `users_one_owner` is a UNIQUE partial index over `role = 'owner'`; a claim
    inserts first and the loser's `IntegrityError` is rolled back and answered
    `"deny"` (modules/identity/application/services.py,
    core/db/migrations/versions/0005_users.py). Same shape as
    `turns_one_active_per_lane`. Since Step 5 the `users` table is the **only**
    owner record: `AccessControl.owner()` reads nothing else, a claim writes no
    `meta` row, and a restored pre-0005 dump gets its row from migration `0005`,
    which materialises it from the old `meta.owner_user_id` key on upgrade — so
    a claimed deploy is never downgraded past `0005` ([[contract.state-db]]).
33. **Taking a tool away is `disallowed_tools`; `allowed_tools` only
    auto-approves.** Every turn runs under `permission_mode="bypassPermissions"`,
    where the allow list is not a toolset — the live 4a smoke watched a non-owner
    lane run `Bash` with `Bash` absent from it. `policies.denied_tools` fills
    `ClaudeAgentOptions.disallowed_tools` and is the control;
    `policies.builtin_tools` only keeps the approve list in `_BUILTIN_TOOLS`
    order, because that order is part of the cached prefix
    (modules/turns/domain/policies.py, [[decision.prompt-cache-discipline]]).
34. **The webhook's credential is the header, and it is required.** The path
    segment is what a reverse proxy writes into its access log on every delivery,
    so it cannot be the only proof; `X-Telegram-Bot-Api-Secret-Token` is compared
    with `compare_digest` alongside it and a missing header is a 401
    (modules/telegram/presentation/webhook.py). Two more rules travel with it: a
    repeated `update_id` is dropped before anything is dispatched (`SET NX EX` in
    redis mode — the point is that it is cross-instance), and the explicit
    `delete_webhook` before `run_polling` is load-bearing, because aiogram
    3.29.1's `start_polling` does not remove a registered webhook and `getUpdates`
    behind one answers 409 forever (modules/telegram/infrastructure/transport.py).

A caveat on this list, in the same spirit as the one above: **none of items 30-33
is a boundary against a hostile user.** Every turn in the container runs as the
same uid, the `PreToolUse` path guard is TOCTOU-racy against a symlink planted
between the hook and the open, and `Bash` is not guarded by it at all. They are
invariants about *accidents* and about a well-behaved model. The `:ro` catalog
mount remains the only hard boundary
([[decision.readonly-catalog-boundary]], [[decision.multi-user-isolation]]).

## The Wave 4b additions

Wave 4b ran the learning loop per user ([[component.learning]],
[[decision.multi-user-isolation]]). Five rules, each one a one-line change away
from a cache miss, a runaway bill, or a resurrected skill:

35. **Tool presence is a property of the lane, never of the sender.** The hermes
    tool list is part of the cached prefix and the prefix is byte-stable for the
    life of a session, so anything that shapes it may depend only on facts fixed
    for the lane. `core/session_keys.is_group_lane` is the one predicate: on a
    group lane `skill_admin`, `session_search` and `cost_report` are absent for
    everybody, the deploy owner included; on every other lane all three exist,
    scoped to the lane owner (modules/turns/infrastructure/sdk_runner.py,
    modules/turns/infrastructure/hermes_server.py). 4a's per-sender `stranger`
    rule flipped the MCP schema list between two turns of one group session and
    was a defect. Tested by `tests/modules/turns/test_turns_two_users.py`.
36. **One review fork at a time, deploy-wide.** The per-user lease (invariant 7)
    no longer bounds how many forks the deployment starts, so a second bound
    does: inline, `InlineQueue(registry, {learning_review: 1})` (core/tasks.py,
    main.py); redis, `RedisReviewSlot` on `lock:review`
    (modules/learning/infrastructure/review_slot.py). `ReviewService.run` claims
    the gate, then the slot; a held slot **releases the gate and returns `None`**
    (modules/learning/application/services/review.py) — a dropped review is the
    designed outcome and the counters fire again next interval. A fresh `Lock`
    per claim, because a taskiq worker runs several tasks in one process and a
    shared `Lock` object's `held` flag would be flipped by a loser's failed
    `acquire`. The curator sweep is sequential and behind `lock:curator:sweep`
    for the same reason: N passes may each spawn a CLI fork (workers/curator.py,
    workers/tasks.py).
37. **The operator's `curator_paused` stops every user's pass.** `run_all` reads
    the **unsuffixed** row through the owner scope's `ScopedMeta` before it
    walks a single user, and returns `[{"ran": false, "reason": "paused"}]`
    (workers/curator.py). `curator_paused:<id>` is a user's own switch and pauses
    one pass. One `ScopedMeta` per scope is handed to all four readers, so no
    reader can be built with a suffix the others disagree with
    (modules/learning/wiring.py, modules/learning/infrastructure/scoped_meta.py).
38. **A seed the user deleted or archived is never re-seeded.** `SeedManifest`
    records what was seeded and its sha256; `_seed_file` leaves an absent file
    absent when a record exists, refreshes a present file only while its bytes
    still equal the recorded ones, and leaves a modified file alone;
    `seed_skills` skips any bundle name under `skills/.archive/` whatever the
    manifest says (core/workspace.py, `tests/test_core_workspace.py`). Before
    4b every boot copied an archived bundled skill straight back in. `CLAUDE.md`
    is outside the manifest by design; `manifest=None` is the copy-if-absent
    path for a caller without one.
39. **Every background fork is confined like a turn, in its user's tree.**
    `run_review`, `run_consolidation` and the wiki lint take `workspace=`, run
    with that tree's `cwd` and `CLAUDE_CONFIG_DIR`, install the same
    `path_guard_factory` a foreground turn gets (`agent_home`, `tmp/`, `logs/`,
    the plugin clones — no catalog root), pass the same `cwd` to
    `write_guard_factory` so the two hooks on one matcher resolve a relative path
    identically, and carry `Bash`/`Agent`/`Task` in `disallowed_tools`
    (modules/learning/infrastructure/fork_runner.py,
    modules/memory/infrastructure/lint_fork.py, integrations/claude_sdk/guards.py).
    `workspace=None` means the deploy owner's tree.
    Admission travels with it: `CurrentUserMiddleware(admitted=)` re-asks the ACL
    on every `/api/*` request, so a cookie outliving an allowlist removal is a
    401, not a write into a tree (core/auth.py).

## The content plan (2026-09-28)

The calendar's content plan ([[component.content]], [[flow.content-publish]]) adds
a publisher that acts on the owner's behalf while nobody watches. Seven rules keep
it from posting what the owner did not approve, posting twice, or spending runs:

40. **Approval is a human act.** No `content_plan` action can move an item to
    `scheduled` or later: the agent's targets are exactly `idea`, `draft`,
    `review`, `skipped`, and items in `queued`, `publishing`, `stuck`,
    `published` refuse every agent `update` and `set_status`
    (modules/content/domain/status.py, tested row by row in
    `tests/test_content_service.py`). An agent content edit of approved content
    lands in `review` with the approval cleared. Only `POST
    /api/content/{id}/approve`, which is `require_owner`, and the owner-only
    Telegram card button (invariant 50) reach `scheduled`; only the owner's
    theme routes reach `planned`.
41. **Approval binds the content, and the claim re-checks it.** The row holds the
    body, channel, format and time that were approved, and each media entry the
    sha256 taken at approval; `ContentService.claim` re-hashes every file after
    the claiming `UPDATE` and, on any mismatch or a missing file, sends the item
    back to `review` with the approval cleared and refuses (`media_changed`). It
    is intent-binding, not a boundary: the owner-lane agent has Bash, and a file
    can change between the claim and the script reading it.
42. **Only the publisher publishes.** `due`, `claim`, `published` and `failed`
    answer only `origin == f"cron:{meta.content_publisher_job_id}"`
    (`ContentService.is_publisher`); from chat or any other routine they are
    refused, and `handle` refuses any `owner_id <= 0`
    (modules/content/presentation/agent_tool.py). The routine's `origin` comes
    from the executor's own `job_id`, never from the model
    (modules/content/presentation/routine_server.py).
43. **One claim wins, and nothing re-posts by itself.** `claim` is one `UPDATE …
    WHERE status='queued' … RETURNING` (modules/content/infrastructure/repository.py);
    an item in `publishing` is never retried by the system — after the
    publisher's timeout plus 10 minutes it becomes `stuck`, and only the owner's
    retry ("re-post" or "it was published") moves it on.
44. **The publisher is woken only through `wake_if_idle`, never `run_now`.**
    The scheduler's wake is one compare-and-set that never touches the lease
    (modules/scheduler/infrastructure/repository.py); `run_now` clears it, and in
    redis mode N workers tick at once. The content tick wakes only when an item
    is due and the publisher is neither running nor already woken, counts each
    wake with a compare-and-set on `next_wake_at` (released if the scheduler's
    CAS then says no), and stops after 3 wakes per item
    (modules/content/application/service.py). Two concurrent ticks → one wake.
45. **No surprise late posts.** An approved item more than 2 hours past its
    `publish_at` when the tick reaches it becomes `failed` (missed window)
    instead of `queued` — the same 2 hours as the scheduler's `_MAX_CATCHUP`.
46. **Media never leave the box as a draft.** A plan item names paths under the
    owner's `agent_home`, resolved with `resolve()` + `is_relative_to`, no
    dot-segment (so `.claude/` is unreachable), files only, images and videos
    only (modules/content/infrastructure/media.py, mirroring `send_document`'s
    rule rather than importing it). Nothing in the plan hosts a file: only the
    publish scripts do, at publish time. The only route that serves one is the
    owner-only preview.
47. **`content_plan` exists only where the plan's owner is the lane.** In chat:
    the deploy owner's private and web lanes (`is_owner_lane is True`, not a group
    lane, `owner_id > 0` — `SdkTurnExecutor._content_for`), registered after
    `memory_search`, so turning the plan on appends one tool to the boot
    fingerprint and resets every lane once. In routines: lent by the executor
    only for the deploy owner's (`is_deploy_owner`, `owner_id > 0`), merged
    after the composed extensions so an extension named `hermes` cannot shadow it
    (modules/scheduler/infrastructure/executor.py). Invariant 35 applies.

### Drafting and Telegram approval (v2, 2026-09-28)

The drafter writes posts while nobody watches, and a Telegram button can
schedule one. Six more rules keep the owner's final approval the only way out:

48. **Theme approval schedules nothing.** `planned` and `drafting` are not in the
    agent's targets; a drafted post waits in `review`, and an item still
    `planned`, `drafting` or unapproved `review` at its time is published by
    nobody (only `scheduled` is ever promoted). An agent edit of a `planned`
    item's title, channel, format or time returns it to `idea`
    (modules/content/domain/status.py).
49. **The approval gate is one function.** The web approve, the card approve and
    the drafter's `submit` all call `approval_refusal` (`empty_body`,
    `media_required` by format), and the tick fails a `scheduled` item with an
    empty body instead of queuing it (modules/content/domain/formats.py,
    modules/content/application/service.py).
50. **A card button approves its revision only, and only for the owner.** The
    gateway checks `acl.is_owner` before asking content; content scopes every
    lookup to the tapper as the item owner, re-hashes the media, recomputes the
    revision and schedules in ONE `UPDATE … WHERE status='review' AND
    card_state='sent' AND card_revision=:rev RETURNING`. It is a gateway-handled
    callback, never a model tool (modules/telegram/application/services/content_cards.py,
    modules/content/infrastructure/repository.py).
51. **Any content edit makes a live card stale in the same statement.** Web
    PATCH, agent update/set_status, approve, unapprove and redraft turn a
    `pending`/`sending`/`sent` card `superseded` (`update(..., supersede=True)`
    returns the previous card state); the best-effort edit of the old message is
    cosmetic, the row decides what a button can do.
52. **One drafter item per run, one card and one topic per tick.**
    `claim_draft` takes an owner advisory lock, refuses while the owner has an
    item `drafting`, and claims one row `FOR UPDATE SKIP LOCKED`; a card is sent
    only after the `pending -> sending` compare-and-set; the «Контент» topic is
    created only after a `meta_swap` reservation. Waiting for a busy drafter is
    never counted against an item.
53. **A reply to a card never becomes a turn.** `intercept_card_reply` runs
    before intake downloads anything, before the titler and before
    `router.handle`; only a reply whose `reply_to_message_id` is a stored
    `card_message_id` in that chat counts (every forum message replies to the
    topic root), and a port failure answers an error rather than falling
    through to a turn. The titler never renames the card topic
    (modules/telegram/infrastructure/dispatcher.py).

---
id: flow.message-turn
kind: flow
status: current
summary: A message from Telegram or the browser becomes a `turns` row, a queued task, a Claude Agent SDK call in whichever process claims it, a typed event stream and a reply.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:24040a1249ccbfcf22cae4cd11a884cf0397d28bcede98c95c340048991e513d
evidence:
  - path: src/claude_hermes/modules/telegram/infrastructure/dispatcher.py
  - path: src/claude_hermes/modules/telegram/application/services/routing.py
  - path: src/claude_hermes/modules/telegram/application/services/gating.py
  - path: src/claude_hermes/modules/telegram/presentation/presenter.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/application/launcher.py
  - path: src/claude_hermes/modules/turns/application/execution.py
  - path: src/claude_hermes/modules/turns/application/control.py
  - path: src/claude_hermes/modules/turns/infrastructure/turn_repository.py
  - path: src/claude_hermes/modules/turns/infrastructure/dispatch.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/turns/application/bus.py
  - path: src/claude_hermes/modules/turns/presentation/router.py
  - path: src/claude_hermes/modules/turns/presentation/sse.py
  - path: src/claude_hermes/core/eventbus.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/modules/turns/domain/prompt.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/modules/sessions/application/services.py
  - path: src/claude_hermes/modules/memory/infrastructure/turn_hooks.py
  - path: src/claude_hermes/modules/memory/infrastructure/wiki_store.py
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/modules/turns/domain/policies.py
  - path: src/claude_hermes/integrations/claude_sdk/guards.py
  - path: src/claude_hermes/integrations/claude_sdk/options.py
  - path: src/claude_hermes/core/model_catalog.py
  - path: src/claude_hermes/modules/turns/domain/exceptions.py
  - path: scripts/model_matrix.py
  - path: src/claude_hermes/modules/telegram/application/services/intake.py
  - path: src/claude_hermes/modules/turns/application/ports.py
  - path: src/claude_hermes/modules/turns/domain/value_objects.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: tests/modules/turns/test_turns_shell_stamp.py
relations:
  - component.gateway
  - component.core-runtime
  - component.web
  - contract.state-db
  - decision.redis-taskiq-scaling
  - quality.failure-modes
  - decision.multi-user-isolation
---
# Flow: message → turn → reply

Steps 1-5 are the Telegram entry; a browser message enters at step 6 (see
"Web entry" below). Everything from step 6 on is shared.

Since Wave 3b step 6 has two halves that may be in **two different processes**:
the channel writes a `turns` row and enqueues `run_turn(turn_id)` (step 6a), and
whoever claims that row runs the SDK loop (step 6b). Inline mode runs 6b in the
same process, through `InlineQueue`; redis mode runs it in a `worker`. Everything
below 6b is identical in both, which is the point of the split —
[[decision.redis-taskiq-scaling]].

1. **Update** — `on_message` (modules/telegram/infrastructure/dispatcher.py) accepts text, voice/audio, photos
   and documents. Non-text intake gates on the caption before transcribing or
   downloading; attachments ride on `MessageEvent.attachments` into `run_turn`.
   Voice: ACL-gate first, then download + transcribe (STT); attachments:
   `IntakeService.intake` (modules/telegram/application/services/intake.py) gates, refuses anything over 20MB from
   `file_size`, then downloads — through `files` (`put_upload`, owned by the
   LANE's owner) when that module is wired, otherwise straight into the lane
   owner's `inbox/<lane>/`; text: strip mention, compute `mentioned`.
2. **Gate** — `verdict` (modules/telegram/application/services/gating.py, called as `_verdict` by routing and intake): `ignore` (un-addressed group),
   `deny` (ACL; refusal only in private), or `run`.
3. **Session key** — `build_session_key` (core/session_keys.py):
   `tg:<chat_type>:<chat_id>[:<user_id>][:t<thread_id>]`; `thread_lane`
   (modules/telegram/infrastructure/aiogram_mapping.py) counts only real forum topics.
4. **Titling** — for a brand-new thread (`titler.is_new_thread`), a
   fire-and-forget haiku call names the topic — spawned *before* the turn,
   because the turn writes the session row that makes the thread not-new.
   The same name lands on the lane's `threads` row and on the bus
   (`thread.title`), so the web sidebar and the topic agree.
5. **Commands** — `/secret` and `/approve` are intercepted before the runner
   (never reach the model); `/new` resets the lane; `/model` opens the model menu
   (`/model default` the personal-default one) and `/effort` answers with a
   pointer to it — both are the CLI's own local commands, so neither may become a
   turn ([[decision.model-choice-is-lane-state]]); `/learn` is rewrapped by
   `build_learn_prompt` and continues as a turn.
6. **The row, the claim, the turn.**
   *(a) The row and the task* — the channel calls `TurnLauncher.start`
   (modules/turns/application/launcher.py), which inserts a `queued` `turns` row
   with `lease_until = now + turn_queue_timeout_seconds` and enqueues
   `run_turn(turn_id)`. The insert is the lane lock: the unique partial index
   answers a second concurrent turn with `LaneBusy`
   ([[contract.state-db]]). A dispatch that raises finishes the row `error`
   (`fail_queued`, the one terminal write with no `worker_id` — there is no worker
   yet) and re-raises, so a lane is never held by a task nobody enqueued.
   Telegram reaches this through `QueuedTurnRunner`
   (core/app_context.py), which retries a busy lane until it frees and then parks
   on `TurnLauncher.wait`.
   *(b) Claim and run* — `run_turn` (workers/tasks.py) is one line into
   `TurnRunService.run` (modules/turns/application/execution.py): claim the row
   (`queued` only, stamping `worker_id` and a fresh lease), re-resolve the
   attachment ids **against the row's own lane**, register the task in the
   `CancelRegistry`, start the heartbeat (every `lease/3`, reporting
   `cancel_requested_at` back), **resolve the workspace** and call
   `runner.run_turn(..., turn_id=turn_id, workspace=ws)`.
   *The workspace is decided here and only here* (Wave 4a): this object holds the
   row, so it is the one thing that knows the key, the channel and the sender at
   the same time, and `resolver.for_lane(row["session_key"], row["channel"],
   row["user_id"])` applies the `lane_owner` rule — no table read, so a brand-new
   lane resolves exactly like an old one and cannot disagree with
   `sessions.owner_id`. The answer travels down on `TurnContext.workspace` +
   `TurnContext.is_owner_lane`. An `UnknownUser` is deliberately **not** swallowed:
   it reaches `run`'s `except Exception` and finishes the row `error` (Telegram's
   waiter reads the row and says its error line) rather than running the turn in
   an arbitrary tree. Since Step 5 the run service is
   the only production caller of `run_turn`, so the workspace arrives resolved;
   `run_turn(workspace=None)` still means the deploy owner's tree, which is what
   a test driving `TurnService` directly gets ([[decision.multi-user-isolation]]).
   A failure resolving the attachments — before the body is even entered, a blob
   fetch that raised or an id that does not resolve against the row's lane —
   emits `turn.start` + an `error` `turn.end` itself (model keys null), so a
   browser watching the lane is not left waiting on a turn that already died; a
   workspace that fails to resolve gets no such envelope, only the `error` row.
   Every terminal write carries `worker_id` and runs under
   `asyncio.shield`.
   *(c) The turn* — `runner.run_turn` (modules/turns/application/turn_service.py) under the per-key lock, which
   also registers the running task so `runner.cancel(key)` can stop it.
   `_run_turn_locked` (modules/turns/application/turn_service.py) takes the `turn_id`,
   resolves the lane's (model, effort) ONCE through `LaneModels.effective` —
   sessions' `SessionService`, the one gatherer behind `core.model_catalog.resolve`
   ([[decision.model-choice-is-lane-state]]); a port failure logs and runs the
   deploy default — puts it on `TurnContext`, emits `turn.start`
   (`channel`, `user_id`, `model`, `effort` — the REQUESTED pair) and `user.message` onto the [[component.web]]
   `TurnBus`, upserts the lane's `threads` row, and guarantees exactly one
   `turn.end` (`ok` / `error` / `cancelled`) on every exit path, each carrying
   `model`, `effort` and `fallback_from` — the pair that ANSWERED
   (`AssistantMessage.model`, never the requested id and never the CLI's
   `<synthetic>` stand-in). An `ok`
   `turn.end` also carries `usage`: per model, `context_window`,
   `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`,
   `output_tokens` (`_usage_wire`). The SDK's `ResultMessage.usage` /
   `model_usage` are SUMS over every API call of the turn (each tool
   round-trip re-sends the whole prompt), so the input and cache counts are
   taken from the turn's last main-loop `AssistantMessage.usage` (subagent
   messages, `parent_tool_use_id` set, skipped) — that call's prompt is what the
   context window holds — while `context_window` and `output_tokens` come from
   `model_usage[model]`. `null` when the SDK reported nothing; the cron ticker
   emits `usage: None`. `context_window` is the CLI's own figure, not the API's,
   and it comes from the CLI's embedded model catalog: a model the CLI does not
   know gets 200K. CLI 2.1.218 did not know `claude-opus-5` and needed
   `claude-opus-5[1m]` for 1M; CLI 2.1.280 (the pinned version) gives every
   offered model — and `claude-opus-5` — its 1M window unsuffixed, measured live
   by `scripts/model_matrix.py`. A suffixed spelling still works: the CLI then
   keys `model_usage` as `claude-opus-5[1m]` while the API answers as
   `claude-opus-5`, so `_usage_wire` falls back to the suffixed entry and keeps
   the wire keyed by the API name. The CLI's own Haiku side call appears in
   `model_usage` on every run and is never the entry read.
   A text that is `/model` or `/effort` (`is_model_command`) never reaches the
   CLI — it would switch the session's model as the CLI's own local command —
   and is answered with a canned reply whose `turn.end` names no model.
   **The lane owner's grants** (0009) are resolved next, at the top of
   `_run_turn_body` and before `route()`: `_with_grants` asks
   `LaneGrants.grants(ctx.owner_id)` (identity's `AccessControl`, read from
   PostgreSQL every turn, so a switch flipped in Settings → Users reaches the next
   turn in whichever process runs it) and puts `shell_enabled` /
   `learning_enabled` on the context. An owner lane — private, web, every group
   lane and the owner's `cron:` lanes — never asks: `is_owner_lane` already gives
   it everything, and both fields keep their **False** defaults, as they do in a
   service built without the port. This is inside the envelope on purpose: a
   lookup that raises ends the turn `turn.start` → `turn.end {status: "error"}`,
   never on a guessed grant. From here `TurnContext.has_shell` (`is_owner_lane or
   shell_enabled`) is the one shell value the toolset, the session stamp and the
   next turn's comparison all read.
   `_run_turn_body` then routes resume-vs-fresh. **The lazy reset** comes first:
   a session row whose `shell_enabled` stamp differs from `ctx.has_shell` was
   built with another toolset and cannot carry on (P1). A NULL stamp (a session
   begun before `0009`, or by a process that predates it) is compared as the rule
   it was built under, `is_owner_lane or grant_defaults().shell`. On a difference
   the turn calls `self._sessions.reset(session_key)` — never `TurnService.reset`,
   whose lane lock `run_turn` already holds — drops the row, logs
   `shell_grant_changed`, and emits `SHELL_CHANGED_NOTICE` ("Your shell access
   (Bash, Agent, Task) was changed by the owner, so this conversation starts a
   fresh session.") as a `text.block` before the answer; the turn then runs as a
   fresh one. Nothing resets a lane from the request that changed the grant, so
   this cannot race a running turn's write-back. The model was resolved before
   `route()`, so a lane reset for its shell starts its fresh session on the model
   the old session ran (accepted — the same as the stale-resume retry in step 8).
   `tests/modules/turns/test_turns_shell_stamp.py` drives this over the real
   `SessionRepository`, so the key the reader compares is the column the writer
   stamps. A fresh session gets a frozen
   `system_append` — `build_system_append` (modules/turns/domain/prompt.py) wraps the turn
   rules, the wiki rules, the wiki index snapshot and today's date (for a
   non-owner, the index ends with a read-only section quoting the owner's
   `wiki/shared/` index, capped at a quarter of `wiki_index_max_chars`); a
   resumed one reuses the stored append byte-identical. **The wiki is the lane
   owner's**: `TurnService` holds no single `WikiStore` since Wave 4a, it calls
   `wiki_for(ctx.workspace)` per turn (a path object and an idempotent `seed()`,
   so per turn is free), and
   `_index_seen` is seeded from `sessions.index_mtime` of that same tree.
   Without it, user B's byte-stable cached prefix would carry the owner's index. If the thread is a
   cron job's delivery topic and the lane has not run since the last
   delivery, `_with_cron_context` (modules/turns/application/turn_service.py) quotes that report into
   the user message first (see [[component.cron]]).
7. **SDK call** — `_execute` (modules/turns/application/turn_service.py): hermes MCP server + extensions
   composed for the LANE owner (`for_turn(owner_id=ctx.owner_id)` — their
   connectors) + `base_options(ws.root, …)` (preset `claude_code`,
   `bypassPermissions`, `cwd = ws.root / "agent_home"` — the **lane owner's**,
   and for the deploy owner `root is CH_HOME` so this is byte-identical to
   Wave 3b), `model=ctx.cli_model` (the resolved model's `cli_id`, the only
   spelling the CLI ever sees) and `effort` only when one was chosen — `None`
   sends no flag, so the CLI keeps the model's own default —
   `env=workspace_env(ws)` (`CLAUDE_CONFIG_DIR`, `TMPDIR` — per user, or
   one lane's `--resume` replays another's conversation and every lane shares
   `/tmp` — and `CLAUDE_CODE_NO_MODEL_FALLBACK=1`, so the CLI cannot answer with
   a model it swapped in on its own), `resume` id, and the hook events wired per turn
   (the Bash and path guards seeded by the executor, the rest from memory's
   `turn_hooks.py`, sharing one `WikiTurnState`): **PreToolUse** — the existing
   Bash guard, `path_guard_factory` over
   `Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep` confining the turn to
   `[ws.agent_home, ws.tmp_dir, ws.logs_dir, ws.plugins_dir]` plus the catalog
   root (installed for every lane, the owner's included) — and, for a non-owner,
   the owner's `wiki/shared/` as one more READ root, fenced by
   `readonly_guard_factory` on `Write|Edit|MultiEdit|NotebookEdit` over both that
   path and the lane's own `wiki/shared/` — plus `raw_guard_factory` on `Write|Edit` (denies any edit
   under `wiki/raw/`, and any write to a raw path that already exists).
   **PostToolUse** — `page_touch_factory` on `Write|Edit` records which
   wiki pages/`index.md`/`log.md` this turn touched; `webfetch_hint_factory`
   on `WebFetch` points once per turn at the wiki-ingest skill.
   **Stop** — `stop_gate_factory` blocks a turn that wrote a page without
   also updating `index.md` and `log.md` (`stop_hook_active` breaks the
   loop: it warns instead of blocking again); after it, when a wiki index is
   wired, `index_flush_factory` re-indexes the pages the turn touched into
   `wiki_pages` under the lane owner's id (bounded at 5 s, failures logged, never
   the turn's). **UserPromptSubmit** —
   `index_refresh_factory` re-injects the current index as
   `additionalContext`, but only when `WikiStore.index_mtime()` (for a non-owner,
   the later of their index and the shared one) moved past
   what `TurnService._index_seen[session_key]` already recorded. The prompt
   comes from `_build_prompt` (modules/turns/infrastructure/sdk_runner.py), called *inside* `_execute` so the
   retry in step 8 gets a fresh one: a bare `str` when nothing is attached
   (every pre-existing turn is byte-identical), otherwise a stream-json `user`
   message carrying base64 image blocks plus a text block naming each file by
   path. `include_partial_messages=True` is set, so `query()` also yields token
   deltas. **The toolset is per lane**: a non-owner lane's
   `ClaudeAgentOptions.disallowed_tools` carries `Bash`/`Agent`/`Task` unless its
   owner holds a shell grant — `builtin_tools`/`denied_tools` read
   `ctx.is_owner_lane` and `ctx.shell_enabled`, never a setting (under
   `bypassPermissions` the *allow* list only
   auto-approves — taking a tool away is `disallowed_tools`), and on a **group**
   lane `make_handlers(skills=None, search=None, costs=None, wiki_search=None)`
   (modules/turns/infrastructure/hermes_server.py) leaves
   `skill_admin`, `session_search`, `cost_report` and `memory_search` off the
   hermes server for everybody, the owner included (Wave 4b;
   `core/session_keys.is_group_lane`) — on a private, web or cron lane all four
   exist (`memory_search` while `memory_search_enabled`), scoped to the lane owner.
   With `content_enabled`, `content_plan` registers last, after `memory_search`,
   on the DEPLOY owner's private and web lanes only (`ctx.is_owner_lane is True`,
   not a group lane, `owner_id > 0`), called with `origin="chat"`
   ([[component.content]]). Before any of this, a Telegram message that REPLIES
   to a live content approval card never becomes a turn at all: the gateway
   intercepts it as a redraft request ([[component.gateway]]).
   Decided once per turn from facts fixed for the lane's session, never from the
   sender, so a session's cached prefix stays byte-stable — a shell grant that
   moves is what the lazy reset in step 6 is for — and the cost is that the
   prefix is now per user rather than shared ([[decision.multi-user-isolation]]).
   Each message becomes an event: `text.delta`, `text.block`,
   `thinking`, `tool.call` (which also feeds `observe_block`), `tool.result`;
   `ResultMessage` supplies
   `sdk_session_id` and cost, and the executor records a non-zero cost into
   `turn_costs` right there, owned by the LANE's owner. Both consumers are listeners on the bus — the
   Telegram `Presenter` (which ignores turns from another channel) and, for a
   browser, the SSE stream.
   **Both halves of a tool's pair are capped at `web_tool_output_max_chars`**:
   the result's output, and each string inside the call's input. The arguments
   are the bigger half — a `Write` carries the whole file, an `Edit` two copies
   of a hunk — and they went in uncapped until nothing was pruning them: no
   sweep ever deletes a `turn_events` row, so every one of those strings is
   stored for the life of the lane and shipped again on every open of the
   thread. The clip is per string and not over the serialized whole, because the
   browser renders the arguments as a named structure and the Telegram presenter
   reads keys out of them — the keys and the nesting survive, and only a value
   that is too long loses its tail (`sdk_runner._clip_input`).
8. **Failure path** — two, and they never combine into a third execution.
   **A rejected model**: the executor raises `ModelRejected` (turns
   `domain/exceptions.py`) when a call failed before any `tool.call` and the
   CLI's own signals match what the live matrix recorded — an `AssistantMessage`
   with `error == "model_not_found"`, or a `ResultError` with `terminal_reason ==
   "api_error"` and status 400/404 — never from the exception's text. On a model
   that is not the deploy default the turn runs once more on the deploy default
   with the SAME resume id (nothing about the session is wrong, so it is not
   reset; the transcript then holds the prompt twice, measured and accepted),
   emits a `text.block` notice naming both models and sets `turn.end.fallback_from`.
   A rejection of the deploy default, or on that retry, fails the turn with no
   reset. **A stale resume** (`is_cli_death`): reset the row and replay the turn
   once from scratch, as before. An errored `AssistantMessage` ("API Error: 429 …")
   is never emitted as a `text.block`; its text rides `CliCallFailed` into
   `turn.end.error`, because the SDK's own exception text can be just the result
   subtype.
9. **Record** — bind/touch session row with the pair that ANSWERED
   (`sessions.model/effort`, what the resolver later reads as "what this live
   session runs") and `shell_enabled=ctx.has_shell`, the stamp the next turn's
   lazy reset compares; then the reply — prefixed with the shell notice and the
   model-fallback notice when either applies, joined by a blank line, the same
   lines the `text.block`s carried — and the user message are recorded
   (`messages.record`, user + assistant)
   into the `messages` corpus — the user row is `_compose_text`, the same string
   the model's text block carries, so a file the agent read stays findable by name — then
   `observer.after_turn(..., owner_id=ctx.owner_id)`, which runs only when
   `ctx.is_owner_lane or ctx.learning_enabled` (otherwise a once-per-process log
   line says the lane is unobserved). If an extension
   mutation set `reset_lane`, the row is reset *after* the write.
10. **Reply (Telegram)** — the agent, not the worker, sends it:
    `TurnLauncher.wait` has been subscribed to the lane since step 6a, wakes on
    the `turn.end` that carries this `turn_id`, re-reads the row (the event is
    emitted inside the turn body, before the execution service commits the
    terminal row, so `wait` re-reads at 50 ms intervals for up to 5 s before
    falling back to its 15 s poll) and returns `reply` on `ok`, `None` on
    `cancelled`, or raises `TurnFailed(error)` on `error`/`lost`. Then
    `Presenter.finalize` → `RichSender.send`:
    `sendRichMessage(markdown)` → `(html)` → plain chunked `sendMessage`.
    Voice-in + key configured + `voice_reply_enabled` → also a synthesized voice
    reply (best-effort).
    Empty output → fallback text; exceptions → "Something went wrong".

## Web entry (CladeX)

1. `POST /api/threads/{id}/messages` — cookie + `Origin` + JSON checked by the
   middleware, lane ownership by `Depends(owned_thread)` (the lane guard on the
   bundle, `404` if the lane is not this user's), `400 model_command` for a
   `/model` or `/effort` text, `409 busy` if the `turns` row
   says the lane is active. Attachment ids are resolved through the
   `FileStore` into the same `Attachment` objects Telegram builds, carrying the
   `file_id` so the `user.message` event can point the browser back at the file.
2. `await turns.start(...)` — step 6a — and the request answers
   `202 {accepted, thread_id, turn_id}` at once (Cloudflare cuts a request at
   100 s). The route no longer runs the turn: there is no
   `asyncio.create_task(run())` anywhere in the module. An untitled thread is
   still titled in the background from this first message.
3. Steps 6b-9 above run wherever the task landed, with `channel="web"`.
4. **Reply** — the browser is already on `GET /stream?after=<id>`: persisted
   events after the cursor, then the snapshot of every turn in flight, then the
   live tail with `: ping` heartbeats. What the snapshot already covered is
   dropped — persisted events by id, deltas by the per-turn **seq watermark**,
   which is what keeps a delta from arriving twice when it is both inside the
   coalesced snapshot and still in flight from the process that emitted it
   ([[component.web]]). There is no "final message" step; the reply is the
   `text.block` events and the closing `turn.end`. A reload replays from the
   last `id` it saw and loses nothing — from **any** instance, because the
   persisted events are in PostgreSQL and the in-flight snapshot is in Redis.
5. **The spinner opens a little later than it used to.** Between the 202 and the
   worker's claim there is no `turn.start`. The 202 carries `turn_id` so a later
   SPA can render optimistically; the SPA is unchanged in this wave.

## Cancel, `ask_user` and a dead worker

Three things had to work across processes for the split to be honest, and each is
a row or a key first and a message second — the message is only latency:

- **Cancel** — `POST …/cancel` (or `runner.cancel`) is one `request_cancel`
  UPDATE on the row, which also turns a still-`queued` turn `cancelled` outright,
  and *then* a `{"op": "cancel", "turn_id"}` nudge on `hermes:ctl`. The process
  running the turn honours it from two places: the control handler (immediate) and
  the heartbeat, which sees `cancel_requested_at` within a third of a lease even
  if the nudge was lost. Either path ends in `CancelRegistry.cancel`, which is
  idempotent, and `TurnService` already turns `CancelledError` into
  `turn.end {status: cancelled}`.
- **`ask_user`** — the worker opens a `hermes:ask:<qid>` hash and parks; the API
  instance serving the tap compare-and-deletes it, writes
  `hermes:ask:<qid>:answer`, and nudges. The answer key is what makes a lost nudge
  harmless: the waiter re-reads it on every 5-second wake.
- **A worker that dies mid-turn** — nothing re-runs the turn. `scheduler_tick`
  reaps every active row whose lease lapsed: `mark_lost`, a synthetic
  `turn.end {"status": "error", "error": "worker lost: lease expired", …}` on the
  lane so every subscriber and the snapshot store hear about it, and the lane is
  free. Re-running an SDK loop that may have half-completed would double its side
  effects and its cost ([[quality.failure-modes]]).

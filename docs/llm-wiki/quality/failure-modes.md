---
id: quality.failure-modes
kind: quality
status: current
summary: Confirmed reusable failure modes and how the system (or operator) recovers.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:f834c7e61dd8c4e4bc6fe71b2e766092b93dc0371e54d6360a00f041aa3f6e20
evidence:
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/modules/turns/application/turn_service.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/turns/domain/prompt.py
  - path: src/claude_hermes/modules/extensions/application/services/compose_service.py
  - path: src/claude_hermes/modules/telegram/infrastructure/topics.py
  - path: src/claude_hermes/modules/telegram/infrastructure/rich_sender.py
  - path: src/claude_hermes/modules/memory/infrastructure/turn_hooks.py
  - path: src/claude_hermes/modules/memory/application/services/lint.py
  - path: src/claude_hermes/modules/learning/application/services/curator.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
  - path: src/claude_hermes/modules/turns/application/launcher.py
  - path: src/claude_hermes/modules/turns/application/execution.py
  - path: src/claude_hermes/modules/turns/infrastructure/turn_repository.py
  - path: src/claude_hermes/core/eventbus.py
  - path: src/claude_hermes/workers/taskiq_app.py
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/core/session_keys.py
  - path: src/claude_hermes/modules/identity/application/services.py
  - path: src/claude_hermes/modules/sessions/domain/policies.py
  - path: src/claude_hermes/modules/telegram/presentation/webhook.py
  - path: src/claude_hermes/modules/telegram/infrastructure/update_dedup.py
  - path: skills/gpt-image-2/scripts/gen.sh
  - path: src/claude_hermes/modules/learning/application/scopes.py
  - path: src/claude_hermes/modules/learning/application/services/review.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_slot.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/workers/curator.py
  - path: src/claude_hermes/core/auth.py
  - path: web/src/store/events.ts
  - path: src/claude_hermes/modules/turns/domain/exceptions.py
  - path: scripts/model_matrix.py
  - path: src/claude_hermes/modules/content/application/service.py
relations:
  - decision.model-choice-is-lane-state
  - quality.invariants
  - component.web
  - decision.redis-taskiq-scaling
  - flow.message-turn
  - contract.state-db
  - decision.multi-user-isolation
---
# Failure Modes

- **Seeded skills/agents never updated in live deployments — fixed in Wave 4b,
  with one caveat.** Until 4b `agent_home/` was seeded copy-if-absent
  (`_copy_missing` — deliberately never overwrote user state), so repo changes
  to `skills/`, `agents/` or `settings.json` did **not** reach an existing
  `/data` volume, and after 4a that was true once per user. The persona never
  had this failure mode: it is delivered as `agent_home/.claude/persona.md`,
  rewritten on every boot from `bundle/persona.md` and imported by the
  once-seeded CLAUDE.md stub (the 16.07 plugin-dev refusal came from the old
  seed-once persona; commits a986c15, 2204c96, a7d8d2a were hand-recoveries from
  it). Since 4b `SeedManifest` (`agent_home/.claude/.seeded.json`) refreshes any
  seeded file whose bytes still equal what was recorded, leaves a user-modified
  one alone (logged once per tree at INFO), and leaves a deleted one deleted
  (core/workspace.py). A volume that predates 4b has no manifest: its first 4b
  boot records the files that still match the bundle, refreshes a pristine copy
  of an *older* bundle at once (`_PREVIOUSLY_SHIPPED_SEEDS` — the advisor's code
  pass caught that without it the four skill files 4b rewrites would never
  refresh on any live volume), and logs once, with the path, a file that matches
  no shipped version. Recovery for a
  file the user patched and now wants back: delete it *and* its key from the
  manifest (or the whole manifest — an absent or unparseable one reads as
  empty) so the seeder copies it afresh. `CLAUDE.md` stays seed-once by design.
- **An archived bundled skill came back on every restart — fixed in Wave 4b.**
  The curator's aging pass or `skill_admin archive` moved a bundled skill into
  `skills/.archive/<name>`, the next boot found the plain name absent and copied
  the bundle in again, and a week later the curator archived it again — forever,
  with a fresh `.archive/` suffix each round. `seed_skills` now skips any bundle
  name that exists under `.archive/`, whatever the manifest says
  (core/workspace.py, `tests/test_core_workspace.py`). A tree that already holds
  several `.archive/<name>-<n>` copies of one bundled skill is the fingerprint of
  the old behaviour; they are harmless and can be left.
- **N concurrent review forks on the owner's account — bounded.** With a review
  lease per user (Wave 4b) nothing in the lease stops N users' nudges from
  starting N CLI forks at once in a container sized for one. What you would see
  is not that: a nudge that finds `lock:review` held (redis) or the inline
  queue's `learning_review: 1` slot busy is **dropped** with an INFO line
  ("review slot held by another pass"), its gate released, and the counters
  fire again next interval (modules/learning/application/services/review.py,
  modules/learning/infrastructure/review_slot.py, core/tasks.py). A slot that
  stays held after the fork ended is a worker killed mid-pass; it frees itself
  at `review_timeout_seconds`. The curator sweep is sequential behind
  `lock:curator:sweep` for the same reason (workers/tasks.py).
- **A stale cookie of a de-allowlisted user.** The session cookie is signed for
  `web_session_days`; removing a user from `allowed_users` (or setting
  `disabled_at`) does not revoke it. Before 4b such a user could keep reading
  the API until it expired. Since 4b `CurrentUserMiddleware` asks
  `AccessControl.admitted(uid)` on **every** `/api/*` request and answers the
  plain 401 (`login required`) — the same body and redirect as no cookie at all,
  so the removed user learns nothing from the difference (core/auth.py,
  modules/identity/application/services.py). Verified live with a cookie for a
  never-admitted id. A 401 on a valid-looking cookie is therefore the allowlist,
  not the secret.
- **A 4b agent's `learning_review` on a 4a worker.** The payload carries
  `owner_id`; a 4a task body has no such parameter, so the worker raises
  `TypeError` and the review is lost (the counters fire again next interval).
  Only a multi-container deploy rolled one container at a time can produce it;
  drain the queue first ([[system.operations]]). The reverse — a 4a payload on a
  4b worker — runs the deploy owner's review, which is what it meant.
- **A model the API refuses.** Not a stale transcript and never answered with a
  reset: the executor raises `ModelRejected` (turns `domain/exceptions.py`) for a
  failure before any `tool.call` whose `AssistantMessage.error` is
  `model_not_found` or whose `ResultError` has `terminal_reason == "api_error"`
  and status 400/404 — the signature `scripts/model_matrix.py` recorded live on CLI
  2.1.280 — and the turn runs once more on the deploy default with the same resume
  id, the reply naming both models. The deploy default itself refused, or a
  second refusal, fails the turn with the CLI's sentence in `turn.end.error`. The
  usual cause is a CLI too old for the model (2.1.218 answered "does not support
  this model" for Opus 5.5 and Fable 5.1), which is why a model is offered only
  after the matrix passes ([[decision.model-choice-is-lane-state]]).
- **Stale SDK transcript on resume.** A `ProcessError` — or, on
  claude-agent-sdk>=0.2.126, the CLI's own error result re-raised as a
  generic exception with a matching prefix, same signal either way
  (`_is_cli_death`, modules/turns/infrastructure/sdk_runner.py) — when resuming means the CLI
  transcript is gone; the runner drops the session row and replays the turn
  once from scratch (modules/turns/application/turn_service.py). One-off symptom: the reply loses
  prior in-session context.
- **A turn that ends itself waiting for background work.** The harness has no
  background-task notification and no wake-up: a turn ends when the model stops
  emitting, and only a new inbound message starts another. An agent that
  backgrounds a job and closes with "I'll report when it's done" has abandoned
  that job — nothing collects the result. Observed 15.08.2026 across three
  consecutive turns of a ten-image batch; the model then read its own turn
  boundaries as the user approving each tool call and asked for a "permission
  mode" change, while every call site was already
  `permission_mode="bypassPermissions"`. `TURN_RULES` (modules/turns/domain/prompt.py, in the
  system append) states the turn model and forbids both behaviours. Note the
  reach: the append is frozen per session, so lanes that predate the change
  keep the old one until `/new`.
- **A batch of generated images silently collapses onto one file.** `gen.sh`
  attributed its output by diffing a directory codex shares across sessions, so
  two concurrent runs each saw both images as new, both took "the largest", and
  returned the same PNG under different `--out` names — exit 0, valid file,
  wrong subject (a slide captioned "Istanbul" carrying a photo of Italian food,
  15.08.2026). Runs are now serialized on `$CODEX_HOME/.gen.lock` (exit 8 on
  lock-wait timeout). Detection when in doubt: equal byte sizes across a batch,
  confirmed with `md5sum`.
- **Broken extension at compose time.** A registry row whose manifest is
  missing/invalid or whose secrets are absent is dropped from the turn (not
  an exception); the reason is written to `extensions.last_error`
  (modules/extensions/application/services/compose_service.py). The turn proceeds without that server.
- **Missed cron fires after downtime.** Fires older than `min(period/2, 2h)`
  are recorded `missed` and skipped — a digest simply doesn't arrive rather
  than replaying a backlog (modules/scheduler/application/service.py).
- **A content item that does not go out says why, on the item.** The content
  tick never retries a publication and never posts late: more than 2 hours late →
  `failed` (missed window); no publisher, a deleted or paused one, or one that is
  not `manual` → the item stays `queued` with an `error` naming the cause and
  nothing is woken; woken three times and never claimed → `failed` ("never picked
  this item up" — look at the routine's own run); claimed and never reported
  within the routine's timeout plus 10 minutes → `stuck`; a media file changed
  since approval → back to `review` at claim time. Each of these is a badge on the
  calendar, and only the owner's retry (re-post, or "it was published" with the
  URL) moves a `failed`/`stuck`/`publishing` item on
  (modules/content/application/service.py, `docs/content-calendar.md`). The v2
  drafting stage says why on the item the same way: no drafter or a paused one,
  three unclaimed wakes ("never claimed this item" — Retry drafting), three
  failed or timed-out drafts (`failed` with the last reason), a card Telegram
  refused three times (the reason, card `failed`), a `planned`/`drafting`/`review`
  item whose time passed (needs attention, the card edited to "⏰"). A worker
  that dies between Telegram accepting a card and recording it sends it twice;
  only the recorded one takes a reply. A content
  tick that raises is logged and never stops the cron clock (`CronTicker` extras
  and `scheduler_tick` each wrap it in its own `try`).
- **Topic API failures degrade, never break.** `TopicManager` swallows all
  errors: job topics fall back to flat delivery; titling silently keeps the
  default topic name (modules/telegram/infrastructure/topics.py, modules/telegram/infrastructure/topics.py).
- **Rich-message delivery degrades.** `sendRichMessage(markdown)` → HTML →
  plain chunked `sendMessage`; the last rung is the pre-rich behavior, so
  formatting bugs cannot lose a reply (modules/telegram/infrastructure/rich_sender.py).
- **Voice failures never eat the answer.** STT failure → explicit "couldn't
  transcribe" reply; TTS failure after a delivered text answer is only
  logged (modules/telegram/application/services/routing.py).
- **Attachment failures are visible, never silent.** A download that raises is
  logged and answered with "I couldn't download that file — try sending it
  again" (`intake_attachments` → modules/telegram/application/services/routing.py); a file over 20MB is
  refused up front, naming the file and its size, because Telegram's `getFile`
  would reject it anyway. Both return before the turn runs, so the user gets a
  reason instead of silence. Note the shape: `download_all` raises on the first
  failure and the *caller* decides what the user sees, because only it knows
  the chat.
- **Curator misfire is recoverable.** Every real pass snapshots the skills
  tree first; `skill_admin` exposes snapshots/rollback; rollback itself
  takes a protective pre-rollback snapshot (modules/learning/application/services/skill_admin.py).
- **A question's Telegram I/O degrades without losing the answer.** The
  `ask_user` closure wraps `sendMessage` + the broker wait in one `finally:
  ask_broker.discard(qid)` — synchronous and unconditional, so it also fires
  on `asyncio.CancelledError` (a `BaseException`, not caught by `except
  Exception`), which is what stops a teardown mid-`sendMessage` from leaking
  the pending entry forever; `discard` is a pop-with-default, a harmless no-op
  once `wait()` has already popped it itself. If clearing the keyboard after
  an answer fails (`editMessageReplyMarkup`), that is logged and swallowed —
  the answer is already in hand, and a stale button left in the chat must
  never turn a delivered answer into an error (`main.py`, the `ask_user`
  closure).
- **A rushed turn can leave the wiki out of sync.** `stop_gate_factory`
  blocks a Stop once when a page was written without `index.md`/`log.md`;
  if the model still finishes without fixing it (`stop_hook_active` true —
  the loop breaker), the drift is only logged, not corrected in-turn
  (modules/memory/infrastructure/turn_hooks.py). Recovery is the curator's wiki-lint pass
  (`WikiLintService.lint`, modules/memory/application/services/lint.py), which runs on its own interval and
  is the backstop for an index or log that fell behind.
- **A worker dies mid-turn: the lane is reclaimable, the turn is not re-run.**
  Since Wave 3b the running worker heartbeats a lease on the `turns` row every
  `turn_lease_seconds / 3`. When it stops, `scheduler_tick` reaps the row on the
  next minute: `mark_lost`, then a synthetic
  `turn.end {"status": "error", "cost_usd": null, "sdk_session_id": null,
  "error": "worker lost: lease expired", "usage": null}` on the lane, which tells
  every subscriber and clears the in-flight snapshot; the lane is then free
  (modules/turns/application/launcher.py). The turn is deliberately **not**
  re-executed — `claim` matches `queued` only, because re-running an SDK loop that
  may have half-completed doubles its side effects and its cost. The user resends.
  A `queued` row nobody ever claimed is reaped the same way after
  `turn_queue_timeout_seconds`, so a task message that vanished off the Redis list
  cannot hold a lane forever.
- **The reaper can be wrong about a worker that is merely stalled.** A long GC
  pause or a DB blip that starves the heartbeat past the lease gets the lane
  reaped under a turn that is still running: the user sees "Something went wrong",
  and later the real turn finishes, has its `finish` refused by the `worker_id`
  guard and logged, while its messages and session row were written anyway. The
  mitigation is the worker killing its own turn once its own clock passes
  `lease_until` without a successful extension (`turn_lease_lost`,
  modules/turns/application/execution.py), which bounds the window to about one
  heartbeat interval plus cancellation latency. Detection: a `turn_finish_ignored`
  log line, or a `lost` row whose lane kept writing messages afterwards.
- **`lost` and a dispatch failure are two different rows, on purpose.** A turn
  whose `dispatcher.dispatch` raised — the queue is down, Redis refused the
  enqueue — is finished `status='error'` with `error = "dispatch failed: <cause>"`
  by `fail_queued`. Only the reaper writes `status='lost'` with
  `"worker lost: lease expired"`. A `lost` row in production therefore means a
  lease really lapsed, and a queue outage is not disguised as a killed worker.
- **A worker without `TELEGRAM_BOT_TOKEN` parks a Telegram turn on an invisible
  question.** Whichever process runs the turn sends the `ask_user` keyboard, and
  `worker_startup` builds the delivery objects only when the token is present —
  but `ask_user` is still registered (the gate is "a presenter is wired", and
  `AskService` always is), so the turn waits out `ask_user_timeout_seconds` on a
  keyboard nobody saw. The startup logs `worker_without_telegram_token` at
  **warning** (workers/taskiq_app.py); the compose `x-app` anchor, one environment
  block for `agent` and `worker`, is what makes the correct case the default.
- **A browser that ran out of connections looked like a slow server.** Reported
  on 2026-09-19 as "threads load very slowly on refresh or when I switch to an
  existing thread". Nothing on the server was slow. `store/events.ts`'s `open`
  reserved its slot with a no-op and `close` deleted it, so a `close` landing
  inside the history `await` — which is every React StrictMode mount, and every
  click through the sidebar that beats a long thread's load — let the next `open`
  start over and then overwrite the first one's unsubscribe. The orphaned
  `EventSource` could never be closed again. Each one holds one of the browser's
  six connections per origin for the life of the tab, so after a handful of
  thread visits every later request simply queued; each one also kept pushing
  deltas, which carry no id and so dedupe against nothing, doubling the streamed
  text. What you would see: the app growing steadily more sluggish the longer the
  session runs, and normal again after a reload. The slot now outlives the load
  and `close` marks it instead of dropping it ([[component.web]]).
- **A slow subscriber used to stall every lane in the process.** `RedisEventBus`
  fans out on a per-channel delivery task, not on the single reader, precisely
  because the Telegram presenter does a network call per status change: a
  rate-limited Bot API would otherwise hold up every SSE frame, every pending
  `ready()` and every cancel nudge in that process. A channel whose queue keeps
  growing logs every 100 pending payloads (core/eventbus.py). The cost that
  remains is named: when the **last** subscriber leaves a channel, a handler that
  is mid-await is cancelled with it.
- **Restarting the container kills the turn in flight, and the half it leaves
  behind is the dangerous half.** *(Wave 3b narrows this to inline mode and to the
  `worker` container. In redis mode, restarting `agent` no longer kills a turn —
  the worker finishes it, the events are in `turn_events` and the reply is on the
  `turns` row. The one casualty is the Telegram message: nobody is left waiting on
  that lane, so it is not re-sent. Everything below still describes what happens
  when the process that is actually running the turn goes away.)*
  Observed on 2026-09-11: a redeploy stopped the
  stack four minutes into a turn that had already edited a skill's `gen.sh` and
  was running its refuter. What survived is `data/` — the edits are on disk,
  because a rebuild never touches it. What did not: the `turn.end` event (so the
  thread shows work that never concluded), the `sessions` rebind (the runner
  writes `sdk_session_id` when the turn ends, so **the next message resumes the
  session from before the killed turn** and the model has no memory of what it
  just did), and the `messages` rows (so the exchange is missing from search).
  Nothing is *stuck* — inline, the lane lock and the snapshot are process memory
  and die with it; in redis mode they are a row and a set of Redis keys, and the
  reaper is what frees them within a lease. Either way this reads as a clean
  restart until someone asks the agent to finish the work it has already done. Find casualties with
  `SELECT turn_id FROM turn_events GROUP BY turn_id HAVING
  count(*) FILTER (WHERE type = 'turn.end') = 0`
  (a bare `thread.title` row matches harmlessly; one carrying `turn.start` is
  real). Mitigation is procedural, not code: deploy when no turn is running, and
  tell the thread what happened rather than letting it re-derive.
  See [[system.operations]].

- **A user resolves to the wrong workspace.** What you would see: their turn
  answers out of somebody else's memory — the wiki index in the system prompt is
  not theirs, `Read` of a file they just uploaded is *denied by the path guard*
  (the file went to one tree and the guard admits another), and `--resume` replays
  a conversation they never had. The cause is always the same shape: an
  `owner_id`/`lane_owner` disagreement. Check it in one query —
  `SELECT session_key, owner_id FROM sessions WHERE session_key = '…'` against
  what `lane_owner` says about that key (`web:<uid>:…` → the id in the key,
  `tg:private:<chat>` → the chat id, group and cron → the deploy owner) — and then
  `SELECT id, role, workspace_dir FROM users`, where the owner's `workspace_dir`
  must be `''`. Two specific origins are known: a `users` row created with the
  wrong `workspace_dir` (the owner's must be empty, or their whole tree appears to
  move and every SDK session stops resuming), and a lane written before migration
  `0005` whose key the backfill could not parse — those fall through to the deploy
  owner by design, loudly in the migration's `ELSE :owner` branch rather than as a
  cast error. A non-owner lane's `sdk_session_id` was deliberately nulled by
  `0005`, so a first turn after the upgrade starting fresh is **not** this failure
  ([[decision.multi-user-isolation]], [[contract.state-db]]).
- **A duplicate update in webhook mode.** Telegram redelivers an `update_id` it
  did not get a 2xx for in time, and an accepted update starts a download,
  possibly a transcription, and a turn — so the visible symptom is *two identical
  replies and two charges for one message*. Three things stop it and each can
  fail separately: the route answers `200` before it handles anything, the
  `update_id` dedup key (`hermes:tg:update:<id>`, `SET NX EX 600`) is checked
  first, and in redis mode that key is shared by every instance. `RedisUpdateDedup`
  **fails open** — a Redis that is down answers "not seen" and logs
  `update dedup unavailable`, because losing every update while Redis reconnects
  is the strictly worse failure and the 200 has already gone out. So a burst of
  duplicates with that warning in the log is Redis, not the route. In inline mode
  the LRU is per process, which is all there is there; with `--scale agent=N` and
  no `CH_REDIS_URL` it is *not* enough, and that combination has a second problem
  anyway (N cron clocks — [[decision.redis-taskiq-scaling]]).
- **A row with a NULL owner is invisible.** `owner_id` is nullable so an old row
  survives the migration and deleting a user does not delete their history, but
  every scoped read compares against a real id, so a NULL-owner row is returned to
  nobody: a thread that is not in anyone's sidebar, a file whose id 404s, a
  routine nobody lists. Nothing in the code writes NULL — if you find one it came
  from a hand-written INSERT, a fixture, or a code path that created a job with no
  lane and no requesting user. The one deliberate escape hatch is the visibility
  belt in `modules/sessions/domain/policies.visible`, which still shows the deploy
  owner a non-`web:`, non-`cron:` lane whose `owner_user_id` is unset, precisely so
  a group thread the backfill missed cannot vanish with no way to get it back. The
  fix is an `UPDATE … SET owner_id = …` derived the same way the `0005` backfill
  derives it, never a blanket assignment to the owner.

---
id: component.gateway
kind: component
status: current
summary: Telegram gateway — polling or webhook transport, routing, command interception (incl. /model), content approval cards (cnt buttons, reply-to-redraft), topics as session lanes, status drafts, rich replies, voice, attachments, and the queued-turn adapter.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:29a4ba21dd89d422377440811a75678bf30b51cf601b760b24064791304c2b72
evidence:
  - path: src/claude_hermes/modules/telegram/domain/models.py
  - path: src/claude_hermes/modules/telegram/domain/messages.py
  - path: src/claude_hermes/modules/telegram/domain/formatting.py
  - path: src/claude_hermes/modules/telegram/domain/constants.py
  - path: src/claude_hermes/modules/telegram/application/ports.py
  - path: src/claude_hermes/modules/telegram/application/services/gating.py
  - path: src/claude_hermes/modules/telegram/application/services/routing.py
  - path: src/claude_hermes/modules/telegram/application/services/commands.py
  - path: src/claude_hermes/modules/telegram/application/services/intake.py
  - path: src/claude_hermes/modules/telegram/application/services/callbacks.py
  - path: src/claude_hermes/modules/telegram/infrastructure/dispatcher.py
  - path: src/claude_hermes/modules/telegram/infrastructure/gateway.py
  - path: src/claude_hermes/modules/telegram/infrastructure/rich_sender.py
  - path: src/claude_hermes/modules/telegram/infrastructure/topics.py
  - path: src/claude_hermes/modules/telegram/infrastructure/bot_api.py
  - path: src/claude_hermes/modules/telegram/infrastructure/documents.py
  - path: src/claude_hermes/modules/telegram/infrastructure/questions.py
  - path: src/claude_hermes/modules/telegram/infrastructure/media.py
  - path: src/claude_hermes/modules/telegram/infrastructure/aiogram_mapping.py
  - path: src/claude_hermes/modules/telegram/presentation/presenter.py
  - path: src/claude_hermes/modules/telegram/presentation/formatting.py
  - path: src/claude_hermes/modules/telegram/presentation/webhook.py
  - path: src/claude_hermes/modules/telegram/infrastructure/transport.py
  - path: src/claude_hermes/modules/telegram/infrastructure/update_dedup.py
  - path: src/claude_hermes/modules/telegram/domain/dedup.py
  - path: src/claude_hermes/modules/telegram/domain/constants.py
  - path: src/claude_hermes/modules/telegram/README.md
  - path: src/claude_hermes/modules/telegram/infrastructure/attachments.py
  - path: src/claude_hermes/modules/telegram/infrastructure/titler.py
  - path: src/claude_hermes/modules/telegram/application/ports.py
  - path: src/claude_hermes/core/naming.py
  - path: src/claude_hermes/modules/sessions/infrastructure/title_generator.py
  - path: src/claude_hermes/integrations/openai/voice.py
  - path: src/claude_hermes/modules/turns/infrastructure/ask_broker.py
  - path: src/claude_hermes/modules/turns/infrastructure/redis_ask_broker.py
  - path: src/claude_hermes/modules/turns/application/launcher.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/modules/telegram/application/services/model_menu.py
  - path: src/claude_hermes/modules/telegram/infrastructure/model_menu.py
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/modules/telegram/domain/cards.py
  - path: src/claude_hermes/modules/telegram/application/services/content_cards.py
  - path: src/claude_hermes/modules/telegram/infrastructure/cards.py
relations:
  - decision.model-choice-is-lane-state
  - flow.message-turn
  - decision.threads-as-session-lanes
  - component.core-runtime
  - component.web
  - decision.modules-and-ports
  - decision.redis-taskiq-scaling
  - system.operations
  - decision.multi-user-isolation
---
# Telegram Gateway

## Structure

Wave 2 moved this component into `src/claude_hermes/modules/telegram/`
(`domain / application / infrastructure / presentation`; see its `README.md` for
the port list). Paths below are relative to that module root unless they start
with `core/`, `integrations/` or another module — those are the pieces that
deliberately live elsewhere. The `gateway/*.py` shims Wave 2 left behind went in
Step 5, and the two live files still among them (`titles.py`, `attachments.py`)
moved into `infrastructure/` ([[decision.modules-and-ports]]).

- `domain/models.py` (was `gateway/events.py`) — `MessageEvent` (modules/telegram/domain/models.py): the normalized, transport-agnostic
  update (`chat_type`, `chat_id`, `user_id`, `text`, `mentioned`, `thread_id`,
  `message_id`, `attachments`, and since Wave 4a `profile`) consumed by routing
  logic. **`SenderProfile`** (`display_name` already joined and trimmed,
  `username`) is built by `SenderProfile.of(from_user)` and carried so the ACL can
  keep the sender's `users` row current without the gate reaching back into
  aiogram's `Message`. It is telegram's own value object, never identity's
  `Profile` — modules are independent, and identity normalises by attribute name,
  so the two types never meet. `None` means "this update said nothing new about
  them", which is not "cleared": the stored names are left alone.
- `application/services/{gating,routing,intake,callbacks,commands}.py` (was `gateway/telegram.py`) — routing core: `_verdict` (modules/telegram/application/services/routing.py) →
  ignore/deny/run; `Intake`/`intake_attachments` (modules/telegram/application/services/routing.py);
  `handle_callback` (modules/telegram/application/services/callbacks.py) turns one inline-button press into popup
  text — pulled out of the aiogram handler for the same reason
  `intake_attachments` is, since `create_dispatcher` is only exercised by the
  Docker smoke run; `handle_event` (modules/telegram/application/services/routing.py); `chunk_text`
  (modules/telegram/domain/formatting.py-char paragraph chunking); `thread_lane`
  (modules/telegram/infrastructure/aiogram_mapping.py); `create_dispatcher` (modules/telegram/infrastructure/dispatcher.py) with `on_message`
  plus a thin `@dp.callback_query()` handler that answers every branch
  (success included, or the button spins forever), sends `mdl:` taps to the model
  menu first (`is_model_callback`), then `cnt:` taps to the content plan
  (`is_content_callback`, see "Content approval cards" below), and resolves pending
  questions through the `AskBroker` passed in as `broker`.
- `infrastructure/attachments.py` + `infrastructure/media.py` — `Attachment` (core/contracts.py) / `Spec` (modules/telegram/infrastructure/attachments.py), `safe_name`
  (core/naming.py since Step 5 — message-id-prefixed, `[A-Za-z0-9._-]` only, so no
  separator and no traversal survives: the live smoke handed `../../etc/passwd`
  to `42_etc_passwd`; `modules/files/domain/naming.py` re-exports it with
  `lane_token`, the two derive from one regex, and telegram may not import files —
  core is the legal back door between modules for invariants only, never
  services), `lane_dir` (modules/files/infrastructure/layout.py —
  `CH_HOME/agent_home/inbox/<lane>/`, inside the agent's cwd so its own
  `Read`/`Glob` reach the file), `specs` (modules/telegram/infrastructure/attachments.py — largest
  `PhotoSize`, or the document with its own name and MIME) — the frozen
  `Attachment` also carries an optional `file_id`, `None` for Telegram and set by
  the web turns router so `user.message` can hand the browser a `/api/files/<id>`
  URL for what it just uploaded — `download_all`, and since Step 5 the
  `AiogramDownloader` class wrapping `specs` + `download_all` as telegram's own
  `AttachmentDownloaderPort`, the one stateless object `IntakeService` holds
  (`main.build_telegram_gateway` builds it; the `core.app_context.
  MessageDownloader` adapter that only existed because the functions lived at a
  legacy path went with the shims). The raw aiogram fetch lives in
  modules/telegram/infrastructure/media.py. 20MB ceiling (`MAX_ATTACHMENT_BYTES`,
  domain/constants.py), the Bot API's own `getFile` limit. No ACL lives here.
- `presentation/presenter.py` — `Presenter` (modules/telegram/presentation/presenter.py): typing action every 4s,
  one-line status drafts pushed **only when the status changes**
  (`status_for`, modules/telegram/presentation/formatting.py), single final message via `finalize`. It is a
  **`TurnBus` listener**, not a progress callback: `on_turn_event`
  (modules/telegram/presentation/presenter.py) subscribes to the lane and maps `tool.call` → the status
  line, `text.delta` → the "thinking" line, `turn.end` → stop. It latches onto
  a turn only when `turn.start` carries `channel == "telegram"`, so a turn the
  user started in the browser on the same lane does not post drafts into
  Telegram ([[component.web]]). Known gap since Wave 3b: because the dispatcher
  subscribes (and awaits `ready`) *before* the turn exists, and `QueuedTurnRunner`
  may then wait out a busy lane, a second message's presenter can latch onto the
  **first** turn's `turn.start`. The cost is a duplicated status draft, never a
  wrong reply; closing it needs the turn id handed back to the presenter to bind
  on (`modules/turns/README.md`, known debts).
- `infrastructure/rich_sender.py` — the only caller of rich-message Bot API: `RichSender.send`
  degrades `sendRichMessage(markdown)` → `(html)` → plain `sendMessage` +
  `chunk_text`; `send_draft` pushes ephemeral `sendRichMessageDraft`.
- `infrastructure/topics.py` — `Topics`/`TopicManager` (modules/telegram/infrastructure/topics.py): fail-safe
  create/rename/close of forum topics; names truncated to 128 chars.
- `infrastructure/titler.py` — `Titler` (modules/telegram/infrastructure/titler.py
  since Step 5; `Titler(topics, generator=, sessions=, titles=)`, the three
  collaborators declared as `TitleGeneratorPort`, `LaneBindingPort` and
  `ThreadTitleWriterPort` in `application/ports.py`, so the module imports
  nothing from sessions — `main.run` binds sessions' `ThreadTitleService`,
  `SessionRepository.is_unbound` and its own `SdkTitleGenerator(cfg.title_model)`):
  auto-names a new thread from its
  first message via a one-shot haiku call with a **hand-built**
  `ClaudeAgentOptions` (`tools=[]`, `allowed_tools=[]`, `setting_sources=[]`,
  bare-string system prompt, `NO_ADVISOR_ENV`) — deliberately not
  `base_options`; `_clean` (modules/sessions/infrastructure/title_generator.py) rejects answers-instead-of-titles.
  With a `session_key` (the gateway passes the lane key) the title is also
  written to the `threads` row (`set_thread_title`, never over a manual one)
  and announced on the bus as `thread.title`, so the web sidebar shows the
  same name as the Telegram topic.
- `integrations/openai/voice.py` — `OpenAIVoice` (integrations/openai/voice.py): STT
  (`gpt-4o-mini-transcribe`) and TTS (`gpt-4o-mini-tts`, opus output straight
  to `sendVoice`, no ffmpeg).
- `modules/turns/infrastructure/ask_broker.py` — `AskBroker`: the registry of pending inline-keyboard questions
  and the wait that resolves them. Uses `asyncio.wait` (never `wait_for`, which
  cancels the future the broker still holds) and pops the entry *before* reading
  the result, so a tap on the deadline can never leave the user's popup and the
  model's answer disagreeing. Since Wave 3b `open`, `wait`, `options_for` and
  `resolve` are **async** (`handle_callback` awaits `resolve`) while `deliver` and
  `discard` stay **synchronous** — `discard` is called from a `finally` where a
  `CancelledError` can land, and an `await` there is skipped, which is how the
  entry leaked the first time. `redis_ask_broker.py::RedisAskBroker` is the same
  port across processes: the question is a `hermes:ask:<qid>` hash the worker
  opens, the resolver compare-and-deletes it and writes `hermes:ask:<qid>:answer`
  in the same step (that key is what makes a lost nudge harmless), and every
  refusal — wrong user, unknown token, missing question — leaves the hash
  untouched, so a stranger's tap cannot destroy a question its owner is still
  looking at.

## Update flow

`on_message` (modules/telegram/infrastructure/dispatcher.py) accepts text, voice/audio, photos and documents.
Every non-text intake is ACL-gated on the caption **before** any paid work —
transcription for voice, download for attachments (`intake_attachments`) — so an
un-addressed note or photo in a group costs nothing and does not even leak a
typing indicator, which would announce the bot in a chat it is supposed to be
silent in. Attachments land under the **lane owner's** `agent_home/inbox/<lane>/` with a
sanitized, message-id-prefixed filename — since Wave 4a through
`WorkspacePort.lane_dir(session_key, channel, user_id)` over the workspace
resolver, so a non-owner's photo lands in their own `inbox/` where their own turn
can read it, and the `files` row it becomes carries `lane_owner(...)` as its
`owner_id` rather than the sender's id (a photo in a group chat belongs to the
deploy owner's tree, because the lane does)
([[decision.multi-user-isolation]]) — since Wave 3a through
`FileService.put_upload` (telegram's `LaneFilesPort`) rather than by writing the
lane directory itself, so a chat attachment becomes the same object a browser
upload does: a `files` row, an `/api/files/{id}` URL, and with `blob_backend: s3`
an object in the bucket whose local mirror is that same path
([[decision.minio-blobs]]). There is no bare-`home` fallback since Step 5:
`LaneWorkspace(lane_dir, resolver)` requires the resolver, so every attachment is
registered as a row in its lane owner's tree.
Images additionally reach the turn as
base64 image content blocks (`_build_prompt`, modules/turns/infrastructure/sdk_runner.py) while documents are
passed by path for the agent to open. A caption-less attachment runs with
placeholder text. Files over 20MB (refused from `file_size`, before any
download) and failed downloads get a user-visible error rather than silence. A
media group arrives as one update per item, so each becomes its own turn. With
no `OPENAI_API_KEY` a voice note gets a "voice isn't set up" reply. Text
messages strip the mention, compute `mentioned`, build the `MessageEvent` and
the session key, spawn titling for a new thread (before the turn — the turn
writes the session row that makes the thread no longer new), then run
`handle_event` inside a `Presenter` that has subscribed to the lane's bus
(modules/telegram/presentation/presenter.py). `handle_event` takes no `progress` argument — the
parameter is gone from the whole codebase; the runner emits events and the
presenter reads them.

**Since Wave 3b the dispatcher awaits `bus.ready(key)`** after subscribing the
presenter and before `router.handle`. With `RedisEventBus`, `subscribe` only
schedules the SUBSCRIBE; the worker can claim the row and emit `turn.start` before
the server confirms it, and `Presenter.on_turn_event` latches onto its turn
**only** on `turn.start` — one missed event means no status line for the whole
turn, silently. The one line is the whole change to the dispatcher body.

**The turn itself no longer runs here.** `core/app_context.py::QueuedTurnRunner`
satisfies telegram's `TurnRunnerPort` by calling `TurnLauncher.start` and then
`TurnLauncher.wait(turn_id, lane)`; `reset` still goes to the `TurnService`.
Nothing changes from the user's side, and that is the constraint the adapter is
built around: **a second message on a busy lane waits its turn and is answered**.
`LaneBusy` from `start` is retried with a short sleep until the lane frees or
`turn_queue_timeout_seconds` elapses, because double-texting is the most common
Telegram behaviour there is and refusing it with "Something went wrong" would be a
behaviour break, not a wording change. After the timeout `TurnFailed` propagates
into the dispatcher's existing `except Exception`, which is what already sends
that line; `None` back (a cancelled turn) makes `if reply:` skip the final message,
exactly as before. Who sends the reply is deliberately still the **agent**, from
`wait` — the presenter stays one object and the voice reply stays where the OpenAI
client is. The cost is named in `modules/turns/README.md`: a turn that outlives an
agent restart finishes in the worker and is visible in the web thread, but its
Telegram message is not re-sent.

**The worker needs the bot token**, for the same reason: the `ask_user` keyboard
and `send_document` are sent by whichever process *runs* the turn, through the
delivery objects `main.build_delivery(ctx, token)` binds. A worker booted without
it still registers `ask_user` and would park a Telegram turn on a question nobody
saw, so `worker_startup` logs `worker_without_telegram_token` at warning and the
compose `x-app` anchor gives `worker` the agent's environment
([[system.operations]]).

`thread_lane` (modules/telegram/infrastructure/aiogram_mapping.py) counts a `message_thread_id` only for real
forum topics (private chats: any; supergroups: requires `is_topic_message` /
`chat.is_forum`) so plain reply chains never fork a session.

## Transport: polling or webhook

`main.run()` ends in `modules/telegram/infrastructure/transport.start_transport(
cfg, bot=…, gateway=…, web_task=…)` — one function because there are two endings
now and the choice between them is worth testing with a fake bot.

**Polling** (the default, and the only sane local-dev mode) calls
`bot.delete_webhook(drop_pending_updates=False)` and then `gateway.run_polling()`.
The `delete_webhook` is **load-bearing, not documentation**: verified against
aiogram 3.29.1, `start_polling` does *not* remove a webhook that is still
registered, and `getUpdates` behind one answers `409` forever — a deployment that
switched back from webhook mode would simply stop hearing anything.

**Webhook** (`telegram_mode: webhook`, live since Wave 4a) requires
`web_enabled` and a public `web_public_origin` — the route that receives updates
is one of the web app's routes — and refuses to start with the reason when either
is missing. It calls `bot.set_webhook(url=<origin>/telegram/webhook/<secret>,
secret_token=<secret>, allowed_updates=dispatcher.resolve_used_update_types(),
drop_pending_updates=False)` and then awaits the web server task: the web server
*is* this process's main loop. `allowed_updates` is read off the dispatcher rather
than hardcoded, because aiogram's `start_polling` defaults to the same call and a
hardcoded list would make the two transports diverge — silently, in exactly one of
them — the day a handler for a new update type is added. `set_webhook` with the
same URL and secret from N instances is idempotent; the hazard is a rolling deploy
whose instances disagree about `web_public_origin` (`docs/deploy.md`). The
registered URL never reaches a log line, because the path segment is a credential.

**Two credentials, both required** (`presentation/webhook.py`). The path segment
is the only thing Telegram offers that survives a reverse proxy stripping headers
— and it is also what a proxy writes into its access log on every delivery, so on
its own it is a secret stored in plaintext by a component nobody thinks of as a
secret store. `set_webhook(secret_token=…)` makes Telegram send the same value in
the `X-Telegram-Bot-Api-Secret-Token` header, and that one reaches no log. Both
are compared with `compare_digest` over UTF-8 bytes, and a missing or wrong header
is a `401`. A body that is not JSON at all is a `400`; a body that is JSON but
that aiogram refuses to validate is **not** — `Update.model_validate` raises
inside the background task, after the `200` has gone out, so the log line is the
whole report. The route is not under `CurrentUserMiddleware` (that guards `/api/*`
only), so there is no public-paths set to extend.

**Delivery is at-least-once, so acceptance is idempotent.** The route drops a
repeated `update_id` through the `UpdateDedup` port (`SET NX EX 600` in redis
mode, an in-memory LRU inline) and hands the update to a background task held in a
strong-reference set, answering `200` at once. That trades "a crash loses an
update" for "a slow document is never two turns and two charges" — the handler
downloads files and transcribes voice inline, and Telegram's retry timeout is
shorter than that ([[decision.redis-taskiq-scaling]]).

## Gateway-intercepted commands

Handled entirely in the host process — the text never reaches the model:

- **`/secret NAME=value`** (modules/telegram/application/services/routing.py → `_handle_secret`): carrier
  message deleted unconditionally, storage requires `acl.is_owner`. A secret
  that reached `run_turn` would land in the LLM context, the SDK transcript
  and the `messages` corpus — deletion cannot un-index it. Since Wave 3a the
  value is Fernet-sealed into `extension_secrets` rather than appended to a 0600
  file on the volume the agent's own Bash can read ([[component.extensions]]);
  the port is async and the composition root translates extensions'
  `SecretNameError` into this module's.
- **`/approve <name> [token]`** (modules/telegram/application/services/routing.py → `_handle_approve`):
  owner-only two-step approval of drafted catalog entries with a
  content-bound token; repo entries cannot be shadowed. Interception means a
  prompt-injected turn cannot enact its own draft — and also that the model
  never sees the exchange, so a verified confirm resets the thread's session
  lane (`runner.reset`, as `/new`) and says so; otherwise the drafting lane
  keeps believing the draft is queued and uninstallable. Failed, stale or
  unverified confirms do not reset.
- **`/new`** (modules/telegram/application/services/routing.py): `runner.reset(key)` for the current lane only.
- **`/model`, `/model default`, `/effort`** (routing.py → `ModelMenuPort.open`):
  the model switcher's Telegram door ([[decision.model-choice-is-lane-state]]).
  Intercepted after `_verdict` like `/new` — a denied sender gets `_REFUSAL`, an
  ignored chat stays silent — and never handed to the turn, because the CLI runs
  `/model` and `/effort` as its own local commands and would switch the session's
  model with nothing recorded. `/effort` answers with a pointer to `/model`: effort
  is part of the pair, and a second keyboard could set an effort against a model
  the lane no longer runs. `/models` is a different word and still reaches the
  model. With no menu wired `/model` says switching is not configured rather than
  falling through.

  The logic is `ModelMenuService` (modules/telegram/application/services/model_menu.py),
  testable without aiogram; `TelegramModelMenu`
  (modules/telegram/infrastructure/model_menu.py) builds the `MessageView` —
  resolving the thread with `thread_lane(cb.message)`, which the application
  layer may not import — sends the keyboard with `sendMessage` and redraws it
  with `editMessageText` after a tap (the first line names the pair the lane
  runs, so redrawing only the tick would leave that line false). `main.py` builds
  ONE menu and hands it to both `MessageRouter` and `create_dispatcher`.

  `/model` shows the lane's effective pair (`LaneModelsPort.effective`, sessions'
  `SessionService` behind `RowedLaneModels`) and one button per offered model with
  its price, the effort levels, `Auto (<default>)` and Clear; `/model default`
  shows the sender's personal default (`ModelDefaultsPort`, identity's
  `AccessControl`) plus "Also use it in this chat". A lane with no `threads` row
  answers "Send a message here first, then use /model." — `RowedLaneModels`
  (core/app_context.py) raises `NotFound` for it, because `SessionService.effective`
  deliberately never raises (the turn asks it before the row exists).

  Buttons carry exactly `mdl:t:m:<id>`, `mdl:t:e:<effort|auto>`, `mdl:t:x:-` (this
  lane), `mdl:d:m:<id>`, `mdl:d:e:<effort|auto>`, `mdl:d:x:-` (my default) and
  `mdl:a:x:-` (copy my default onto this lane) — all well inside the 64-byte
  `callback_data` limit (`domain/constants.py`). No server-side registry: the lane
  is rebuilt from the message and the tapper, and every write is validated by
  `core.model_catalog.validate`. A tap is refused (the callback still answered)
  when the tapper is not admitted, when a group or forum-topic lane
  (`is_group_lane`) is changed by anyone but the deploy owner, when the menu is
  older than `MODEL_MENU_TTL_SECONDS` (600) or Telegram returned no accessible
  message, and when the choice left `models:` since the menu was drawn. A
  private chat's topics are that user's own. `/model default` is open to every
  admitted user — a default is nobody else's business. The dispatcher routes
  `mdl:` data to the menu **before** its `broker is None` branch, so `/model`
  works on a deployment with no ask broker and `q:` ask callbacks behave exactly
  as before.

`/learn <text>` is transformed, not intercepted: the remainder is wrapped by
`build_learn_prompt` and runs as a normal turn.

## Content approval cards

The content plan's drafted posts reach the owner as cards
([[component.content]], [[flow.content-publish]]); the gateway owns their three
Telegram edges, all through `create_dispatcher(..., content_decisions=)` —
content's `ContentCardDecisions`, telegram's `ContentDecisionsPort`
(`approve_from_card`, `redraft_from_reply`, `is_card_topic`). `None` (the plan
off) is a gateway that behaves exactly as before.

- **Sending** — `infrastructure/cards.py::TelegramApprovalCards(bot, api, topics)`
  is content's `ApprovalCards` port, bound by `main.build_delivery` into the
  plan's late-bound holder in the agent and in every worker. The media go first
  through aiogram's `Bot` (`sendMediaGroup` in groups of up to 10, `sendPhoto`,
  `sendVideo`; a media refusal falls back to `sendDocument` per file), then the
  text message (title, channel/format, "≈ local time", the HTML-escaped body cut to
  fit 4096) carrying [Одобрить] [Переделать] and, for an `https://` origin only,
  the [Открыть в календаре] URL button — a media group cannot carry a keyboard, so
  the text message's id is the card's id. "message thread not found" /
  `topic_deleted` / `topic_closed` answer `{"topic_gone": true}`; any other
  refusal `{"error": reason}` with a bot-token pattern redacted; nothing raises
  across the port. `edit` is `editMessageText` without markup; `ensure_topic`
  is `TopicManager.create`.
- **Buttons** — `cnt:a:<id>:<rev12>` / `cnt:r:<id>:<rev12>`
  (`domain/cards.py`, parsed strictly: positive id, 12 lowercase hex), about 30
  bytes. The callback handler routes them after `mdl:` and before the ask broker,
  checks `acl.is_owner` first (the `/approve` pattern), and only then asks
  content; every bad case is the Russian stale answer, a failing port the failure
  answer, and the popup is cut to 200 characters. `cnt:r` changes nothing: it
  tells the owner to reply to the card with what to change.
- **Replies** — `MessageEvent.reply_to_message_id` (filled by
  `aiogram_mapping.reply_target` for text, attachments and voice) feeds
  `application/services/content_cards.intercept_card_reply`, which `on_message`
  calls BEFORE intake downloads anything, before the titler and before
  `router.handle`. Only the owner's reply to a stored `(card_chat_id,
  card_message_id)` counts — in a forum topic every message replies to the topic
  root, so "is a reply" alone means nothing — and the answer is sent while the
  message stops there. A stranger's reply goes on to the normal gate; a failing
  port fails closed (the failure answer, no turn). The comment is the text or
  caption without the bot mention; a voice note arrives as "" and content asks for
  text.
- **The «Контент» topic is never titled** — the titling gate also asks
  `skip_title` (`is_card_topic`); a failed lookup skips the title. Every other
  message in that topic is an ordinary turn on its lane.

## Presentation

Drafts carry a one-line status ("🔎 Searching the web…"), never the reply
body; the final formatted message is what commits to the chat
(see [[decision.threads-as-session-lanes]]). `cfg.rich_enabled` is a kill
switch back to plain `sendMessage`.

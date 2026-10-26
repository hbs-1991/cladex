# `telegram`

## Purpose

The Telegram channel. It turns one Bot API update into either silence, a refusal,
an intercepted command, or a turn — and turns the turn's progress and answer back
into messages, drafts, topic names, keyboards and voice notes.

It owns no tables. Its domain is the decision about one update, which is why every
collaborator (who may talk, what runs the turn, where a lane's inbox is, who owns
the catalog) arrives as a port.

## Layout

```
domain/
  models.py        MessageEvent, Intake, SenderProfile, MessageView, MenuButton, ModelMenu
  messages.py      every literal the bot can say (verbatim; _APPROVE_UNVERIFIED is security copy)
  constants.py     MAX_ATTACHMENT_BYTES, TOPIC_NAME_LIMIT, CALLBACK_PREFIX, CANCEL_TOKEN,
                   WEBHOOK_PATH, SECRET_HEADER, MODEL_CALLBACK_PREFIX + the mdl: shapes,
                   MODEL_MENU_TTL_SECONDS, CONTENT_CALLBACK_PREFIX + the cnt: shapes,
                   MESSAGE_TEXT_LIMIT, MEDIA_GROUP_LIMIT
  formatting.py    chunk_text, render_html, card_text (a content card's HTML text)
  cards.py         card_callback / parse_card_callback, CardTap — the cnt: wire shape
  dedup.py         MemoryUpdateDedup — the in-process LRU behind the UpdateDedup port
  exceptions.py    SecretNameError (an ApplicationException)
application/
  ports.py                     the Protocols below
  services/gating.py           verdict, should_title
  services/routing.py          MessageRouter.handle — the intercept-before-run_turn decision
  services/commands.py         CommandService.handle_secret / handle_approve
  services/intake.py           IntakeService.intake — gate, size-check, typing, download
  services/callbacks.py        handle_callback
  services/model_menu.py       ModelMenuService.open / .on_tap, is_model_callback
  services/content_cards.py    is_content_callback, handle_content_callback,
                               intercept_card_reply, skip_title
infrastructure/
  bot_api.py         BotApiClient(token, http)          raw Bot API 10.x
  rich_sender.py     RichSender(call, enabled=True)     markdown → html → plain chunks
  topics.py          TopicManager(call)                 create / rename / close, fail-safe
  documents.py       TelegramDocuments(bot)             send_document, send_voice (multipart)
  cards.py           TelegramApprovalCards(bot, api, topics)  content approval cards
  questions.py       TelegramQuestions(call)            present, dismiss (the ask keyboard)
  model_menu.py      TelegramModelMenu(call, service)   the /model keyboard; message_view
  menu.py            ensure_menu_button(call, origin)   idempotent, https-only
  media.py           media_filename, download_media
  aiogram_mapping.py thread_lane, reply_target
  dispatcher.py      create_dispatcher(...)             aiogram wiring only
  gateway.py         TelegramGateway(bot, dp)           run_polling, handle_update
  transport.py       start_transport(cfg, bot=, gateway=, web_task=)  the boot branch
  update_dedup.py    RedisUpdateDedup(client)           SET NX EX; re-exports MemoryUpdateDedup
presentation/
  formatting.py  status_for, _STATUS (+ re-exports of chunk_text / render_html)
  presenter.py   Presenter — the live status line for one turn
  webhook.py     router: APIRouter — POST /telegram/webhook/{secret}, off by default
  dependencies.py get_config, get_gateway, get_update_dedup — the route's providers
```

## Public surface

### Ports consumed (`application/ports.py`)

| Port | Methods | Satisfied at composition by |
|---|---|---|
| `AclPort` | `check(user_id, chat_type, profile=None) -> str`, `is_owner(user_id) -> bool` | identity `AccessControl` |
| `TurnRunnerPort` | `run_turn(key, text, *, user_id, attachments, channel) -> str`, `reset(key)` | turns `TurnService` |
| `TurnEventBusPort` | `subscribe(key, listener) -> unsubscribe` | turns `TurnBus` |
| `SecretStorePort` | `async put(name, value)` — raises `domain.exceptions.SecretNameError` | extensions `PgSecrets` (async since Wave 3a: the value is sealed and written to `extension_secrets`) |
| `ApprovalPort` | `async begin(name) -> (manifest, surface, token) \| None`, `async confirm(name, token) -> (manifest, raw) \| None`, `async clear(kind, name)` | extensions `ApprovalService` — satisfied directly since Wave 3a dropped its `home` argument |
| `CatalogPort` | `is_repo_entry(name) -> bool`, `get(name) -> manifest \| None` | extensions catalog reader |
| `CatalogWriterPort` | `promote(manifest, raw)` | extensions `FileCatalogWriter` |
| `LearnPromptPort` | `build(text) -> str` | learning `build_learn_prompt` |
| `ThreadTitlerPort` | `is_new_thread(key, thread_id) -> bool`, `spawn(chat_id, thread_id, text, session_key=None)` | this module's own `infrastructure/titler.py::Titler(topics, *, generator, sessions, titles)` (moved from `gateway/titles.py` in Step 5). Its three collaborators are ports here too: `TitleGeneratorPort` (`async generate(text) -> str` — `main.run` builds a `SdkTitleGenerator(cfg.title_model)` for it), `LaneBindingPort` (`async is_unbound(session_key)` — sessions' router) and `ThreadTitleWriterPort` (`async apply(session_key, title)` — sessions' `ThreadTitleService`). |
| `VoicePort` | `transcribe(audio, filename) -> str`, `synthesize(text) -> bytes` | `integrations.openai.voice.OpenAIVoice` |
| `AskBrokerPort` | `resolve(qid, token, user_id) -> bool` | turns `AskBroker` |
| `LaneModelsPort` | `async effective(session_key, owner_id) -> Choice`, `async set_model(session_key, model, effort)` | sessions `SessionService`. `effective` is the shared gatherer behind `core.model_catalog.resolve`; `set_model` validates, clears the session's recorded pair on `model=None` and emits `thread.model`. A lane with no `threads` row: `set_model` raises `core.exceptions.NotFound`, and `effective` may raise the same or answer `None` — the menu turns both into "send a message here first". |
| `ModelDefaultsPort` | `async default_of(user_id) -> (model, effort)`, `async set_default(user_id, model, effort)` | identity's users service. `(None, None)` means "never set" and is shown as such; it is never resolved against the deploy default here. |
| `ModelMenuPort` | `async open(event, scope="t") -> str \| None` | this module's own `infrastructure/model_menu.TelegramModelMenu` — a port, not an import, because `application` and `infrastructure` are independent siblings. `None` means "the keyboard is already in the chat". |
| `LaneFilesPort` | `async put_upload(session_key, filename, data, media_type=None, *, max_bytes=None, owner_id=None)`, `async resolve(record) -> Path` | files `FileService`, satisfied as it stands. Since Wave 3a `main` passes it as `IntakeService(..., files=)`, so a chat attachment gets a `files` row and (with `blob_backend: s3`) a copy in the bucket — the same path a web upload takes. |
| `WorkspacePort` | `async lane_dir(session_key, channel="telegram", user_id=0) -> Path`, `async lane_owner(...) -> int` | `core.app_context.LaneWorkspace(lane_dir, resolver)` over files' `lane_dir` and the workspace resolver — the resolver is required since Step 5 (the `resolver is None` branch was the 4a mis-wire that put a non-owner's photo in the owner's inbox). `lane_dir` is the destination `IntakeService` uses only when no `files=` was wired (a harness; `main` always wires it); `lane_owner` is asked on both paths, because the `files` row needs it too. |
| `AttachmentDownloaderPort` | `specs(message) -> list`, `download_all(bot, items, dest) -> list[Attachment]` | this module's own `infrastructure/attachments.py::AiogramDownloader` (moved from `gateway/attachments.py` in Step 5); the message-id-prefixed sanitised filename comes from `core.naming.safe_name`, the containment rule files and telegram share |
| `BotApiPort` / `BotApiCall` | `call(method, payload) -> dict` | this module's own `BotApiClient` |
| `SenderPort` | `send(chat_id, thread_id, text)`, `send_draft(chat_id, thread_id, status, draft_id=1)` | this module's own `RichSender` |
| `ContentDecisionsPort` | `async approve_from_card(item_id, revision, user_id) -> str` (the popup text, whatever happened), `async redraft_from_reply(chat_id, reply_to_message_id, user_id, comment) -> str \| None` (`None` = not a reply to a live card in that chat), `async is_card_topic(chat_id, thread_id) -> bool` | the content module's decisions object, passed as `create_dispatcher(..., content_decisions=)`. Optional: without it a `cnt:` button answers stale and every message behaves as before. |

### Ports provided to other modules

- `TelegramDocuments.send_document` / `.send_voice` → turns' `DocumentDeliveryPort` / `DeliverVoicePort`.
- `TelegramQuestions.present` / `.dismiss` → turns' `AskPresenterPort` (the broker orchestration stays in turns).
- `TopicManager` → scheduler's `TopicProvisioner` and sessions' topic-rename target.
- `RichSender.send` → scheduler's `ReportDeliverer` (`deliver(chat_id, text, thread_id)` wraps it).
- `TelegramApprovalCards(bot, api, topics)` → content's `ApprovalCards` port:
  `send(card) -> {"message_id", "thread_id"} | {"topic_gone": True} | {"error": reason}`,
  `edit(chat_id, message_id, text)`, `ensure_topic(chat_id, name) -> int | None`. The
  media go first (`sendMediaGroup` for 2-10 photos, more in groups of ten, `sendPhoto`
  for one, `sendVideo` for a video; a file Telegram refuses AS media — dimensions,
  aspect ratio, the 10 MB photo limit — is re-sent with `sendDocument`), then the text
  message carrying `[Одобрить] [Переделать] / [Открыть в календаре]`: a media group
  cannot hold an inline keyboard, so the text message's id is the card's id. The body
  is escaped HTML cut to 4096 with a note pointing to the calendar; the calendar button
  is https-only, like `ensure_menu_button`. Every send goes through aiogram's `Bot`
  (Telegram's description is how "message thread not found" is told apart from a bad
  file — the raw client's `raise_for_status` drops it); the edit goes through the raw
  client and is best-effort. A reason stored on the item never carries the token.

### Routes

`presentation/webhook.router: APIRouter(tags=["telegram"])` — one route:

| Method | Path | `operation_id` | Body |
|---|---|---|---|
| POST | `/telegram/webhook/{secret}` | `telegram_receive_update` | the raw Bot API update (`await request.json()`, no model) |

The body stays the raw update dict on purpose: it is Telegram's schema, not ours, and a
model of it here would be a second copy of the Bot API that silently drops the fields it
has not heard of yet. A body that is not JSON at all is `400 invalid`; a body that is
JSON but is not an `Update` is `200` and fails in the background task (there is no way
to answer 400 from there, and a retry would fail identically). Everything else is `200`,
because Telegram retries a non-2xx forever.

**Mounting is the shell's decision**, and `webhook_enabled(cfg) -> bool` is what it
asks: `False` in polling mode, `True` in webhook mode, and a `RuntimeError` at boot when
webhook mode is on without a secret. Until the shell asks it, the route is inert
anyway — `accepted_secret(cfg)` is `""` in polling mode and no request can match an
empty secret. `tests/modules/telegram/test_telegram_webhook.py` mounts `router`
through the shell and drives the door's security cases against it; the old
`routes(deps)` shape went in Step 5.

### Transport (webhook mode is a feature since Wave 4a)

`infrastructure/transport.start_transport(cfg, *, bot, gateway, web_task)` is the whole
of the boot branch `main.run()` ends in, extracted so a test can drive it with a fake
bot:

- **webhook**: refuses with a `SystemExit` naming the key unless the secret,
  `web_enabled` and `web_public_origin` are all set (the route is one of the web app's,
  so without the server there is nothing to receive on), then
  `set_webhook(url=<origin>/telegram/webhook/<secret>, secret_token=<secret>,
  allowed_updates=dispatcher.resolve_used_update_types(), drop_pending_updates=False)`
  and `await web_task` — the web server is the process's main loop. `allowed_updates` is
  read off the dispatcher and never hardcoded: aiogram's `start_polling` defaults to the
  same call, so a literal list would make the two transports diverge the day a handler
  for a new update type is added, in one of them only.
- **polling**: `delete_webhook(drop_pending_updates=False)` and then `run_polling()`.
  The delete is load-bearing — verified against aiogram 3.29.1, `start_polling` does not
  remove a registered webhook and `getUpdates` behind one answers `409` forever.

`WEBHOOK_PATH` lives in `domain/constants.py` because presentation mounts it and
infrastructure builds the URL from it, and those two may not import each other.

### Dependencies

`presentation/dependencies.py`, built on `core.di.provide`:

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_config` | `cfg` | the `Config` — `telegram_mode` and `telegram_webhook_secret` only |
| `get_gateway` | `telegram` | `UpdateSink` (a `TelegramGateway`; only `handle_update(payload)` is called) |
| `get_update_dedup` | `update_dedup` (optional) | `UpdateDedup` — `async seen(update_id) -> bool`; `None` falls back to a process-wide `MemoryUpdateDedup` |

`UpdateSink` and `UpdateDedup` are `Protocol`s rather than imports of
`infrastructure.gateway.TelegramGateway` and `infrastructure.update_dedup`: presentation
and infrastructure are independent siblings under the `telegram layers` contract.
`main.build_app` wires `RedisUpdateDedup` in redis mode and `MemoryUpdateDedup` inline.

### Tools / events

None. The module publishes no MCP tool and emits no bus event; it only
*subscribes* (`TurnEventBusPort.subscribe`) for the duration of one turn.

## Invariants

- **`/secret` and `/approve` never reach `run_turn`.** A secret that reaches a turn
  lands in the LLM context, the SDK transcript and `messages_fts`, and deleting the
  chat message does not get it back out. `/approve` makes something runnable in the
  container, so only a literal owner message may enact it — never a prompt-injected
  turn. Both comments live on the branches in `services/routing.py`.
- **The sender's profile is news, never a decision.** `MessageEvent.profile` is a
  `SenderProfile` built once in the dispatcher from `message.from_user`; `verdict`
  passes it to `acl.check` so identity can keep the sender's `users` row current.
  It is **this module's own** value object, not identity's `Profile` — modules are
  independent of each other, and identity normalises by attribute name. Nothing in
  `gating.py` or `routing.py` reads it, so a missing profile can never change who
  gets in.
- **The `/secret` deletion runs even for a denied sender** (they may have pasted a
  real key), but an *ignored* chat still gets no answer.
- **`/approve` post-write verification.** After `promote`, `catalog.get` must see
  the entry or the reply is `_APPROVE_UNVERIFIED` — the draft stays queued and the
  lane is **not** reset. The `is_repo_entry` shadow refusal comes first.
- **Gate before spend.** `IntakeService` gates, then size-checks off `file_size`,
  then shows typing, then downloads. A photo or voice note in a group we are not
  addressed in must cost zero bytes and must not leak the bot's presence through a
  typing indicator.
- **An attachment lands in the LANE OWNER's tree, not in one bound `home`**
  (Wave 4a, advisor blocker B5). `IntakeService` asks `WorkspacePort` with the key,
  the channel and the SENDER, and the binding applies `lane_owner`: a private chat
  is its own user, a group belongs to the deploy owner. So a non-owner's photo
  lands in *their* `agent_home/inbox/`, which is the only tree their own turn's
  read guard admits — with one shared `home` it landed where they could not read
  it. The same owner is passed to `put_upload`, so the `files` row and the bytes
  cannot end up in different people's trees. `0` means "nobody knows" and is
  written as NULL, never as user zero.
- **Every bad callback is the same answer.** A stale button, a second tap and a
  stranger's tap all resolve to `_CB_STALE`; one branch cannot drift from three.
  `cb.answer` fires on every branch or the button spins forever. `/model`'s own
  unparseable cases fold the same way, into `_MODEL_STALE`.
- **`/model` and `/effort` never reach the CLI.** The SDK CLI runs both as its own
  local commands in non-interactive mode ("Set model to x"), which would switch a
  session's model with nothing recorded in any table and no price shown to anybody.
  `routing.py` intercepts them next to `/new`: `/model` opens the keyboard,
  `/model default` the personal one, `/effort` answers with a pointer to `/model`
  (one picker, not two — an effort is half a pair). The match is on the exact
  command, so `/models` still reaches the model. With no menu wired the command is
  *still* intercepted, with `_MODEL_OFF`: falling through is the one thing AC #15
  forbids.
- **A content card is approved by the gateway, never by the model.** `cnt:a:<id>:<rev12>`
  is routed before the ask broker (like `mdl:`), the owner is checked first
  (`acl.is_owner`, the `/approve` pattern), and only then does the content plan get
  `approve_from_card` — which re-hashes the media and refuses a revision that moved.
  Every bad case (unparseable, a stranger, no plan wired) is `_CONTENT_STALE`; a port
  that fails is `_CONTENT_FAILED`; the popup is never empty. `cnt:r:` changes nothing —
  it answers how to redo: reply to the card.
- **A reply to a live card never becomes a turn.** `intercept_card_reply` runs in
  `on_message` before a voice note is transcribed or an attachment downloaded, before
  the titler and before the router; a string answer is sent and the message stops.
  Only a reply whose `reply_to_message_id` the content side matches to a stored
  `(card_chat_id, card_message_id)` counts — in a forum topic EVERY message replies to
  the topic's root, so "is a reply" alone is never a redraft, and matching on ids is
  what keeps a flat card (no topics) working. The owner is checked first; a stranger's
  reply proceeds to the gate untouched. A failing port fails closed (`_CONTENT_FAILED`):
  during an outage the gateway cannot tell which replies are card replies. v1 reads the
  text or caption only.
- **The cards' topic is never renamed.** The titling gate also asks `skip_title`, which
  is `is_card_topic`; a failed lookup skips the title (cosmetic) rather than risk it.
  Every other message in that topic is an ordinary turn on its lane.
- **`mdl:` is routed before the `broker is None` early return.** A deployment with
  no ask broker wired would otherwise answer every model button with "that question
  is no longer active". `is_model_callback` is in the application layer so the
  dispatcher's one decision is testable.
- **The model menu's lane comes from the MESSAGE, not from the tapper's last chat.**
  `infrastructure/model_menu.message_view` resolves the thread id with `thread_lane`
  and hands the application layer a plain `MessageView`; the lane is
  `build_session_key` over that view and the tapper's id. Group lanes are keyed per
  sender, so a stranger's tap can only ever reach their own lane — and the owner
  gate below closes even that.
- **A room's model is the deploy owner's to change.** `lane_is_shared`
  (`is_group_lane` or `cron:`) decides: in a group or forum topic only the owner may
  tap `mdl:t:`/`mdl:a:`, and everyone else gets `_MODEL_NOT_OWNER`. A private chat's
  topics are that chat's user's own, so a non-owner runs theirs. A personal default
  (`mdl:d:`) is nobody else's business and works in any chat.
- **A menu older than `MODEL_MENU_TTL_SECONDS` (10 min) is expired**, as is one
  whose message Telegram no longer returns (`None`, or an `InaccessibleMessage`
  with `date == 0`). Its header names a pair that may have moved since, so acting
  on its buttons would switch a lane against a promise the chat no longer makes.
- **An effort tap pins the model it applies to.** There is no "effort without a
  model": leaving the lane unpinned would let a later default change move it
  mid-conversation. `Auto` clears the effort and keeps the model.
- **A changed personal default never moves a live session.** `mdl:d:` writes only
  the person; moving this chat too is the explicit second button, `mdl:a:x:-`.
- **Every write goes through `core.model_catalog.validate`**, the same guard the
  HTTP route uses, so a keyboard that outlived a `models:` change answers
  `_MODEL_GONE` instead of writing a model nothing offers.
- **`handle_as_tasks=True`** is what lets a callback resolve while the asking turn
  holds the lane lock. Flipping that aiogram default self-deadlocks the design —
  the comment sits on `on_callback` in `dispatcher.py`.
- **`thread_lane` requires a real forum topic** outside private chats; Telegram
  stamps `message_thread_id` on plain reply chains too, and treating one as a lane
  forks a fresh session on every reply.
- **`should_title` repeats the gate** and refuses `/`-prefixed text: an ignored or
  denied message must not cost a title call or rename someone else's topic.
- **The rich send chain never raises.** markdown → escaped HTML → plain chunks, and
  the last rung swallows failures: raising there lets a half-delivered reply be
  followed by an error while the turn is already recorded as answered.
- **The token is a constructor argument.** `BotApiClient(token, http)` — never a
  module global, never read from `os.environ` inside the module, so `main.run()`
  can keep popping `TELEGRAM_BOT_TOKEN` before anything can spawn the CLI.
- **`ensure_menu_button` is idempotent and https-only**, and a failure is logged,
  never raised: a stale Mini App button must not stop the bot booting.
- **A webhook needs both secrets.** The path segment AND the
  `X-Telegram-Bot-Api-Secret-Token` header, each `compare_digest`-ed; a missing header
  is `401`. The path is what every reverse proxy writes into its access log, so on its
  own it is a credential stored in plaintext by a component nobody audits as a secret
  store. The header reaches no log.
- **An accepted `update_id` runs once.** Telegram's delivery is at-least-once and an
  accepted update starts a download, possibly an STT call and then a turn — so a retry
  that ran again was a second charge and a second reply. The route drops a repeat
  through `UpdateDedup` (Redis `SET NX EX`, so the drop holds across instances) and
  answers `200` before dispatching, in a background task held in a strong-reference
  set. The trade is deliberate: a crash between the `200` and the handler loses one
  update rather than duplicating one.
- **The webhook path is not under `CurrentUserMiddleware`.** That middleware guards
  `/api/*` only, so nothing has to be added to any public-paths set; the route
  authenticates by the two secrets and never by a cookie.
- **Layer note:** `infrastructure` and `presentation` are independent siblings, so
  `dispatcher.create_dispatcher` takes `presenter_factory` instead of importing
  `Presenter`, and `chunk_text` / `render_html` live in `domain/formatting.py`
  (re-exported from `presentation/formatting.py`).

## Known debts

### The composition, and what Step 5 removed

`main.build_telegram_gateway(ctx)` builds `CommandService` (over
`core.app_context.TranslatingSecrets`, `ctx.extensions.approvals` passed straight
through — the service takes no `home` — and `ctx.extensions.catalog`),
`MessageRouter` and `IntakeService(..., files=ctx.files,
downloader=AiogramDownloader())`, then calls
`infrastructure/dispatcher.create_dispatcher` with `presenter_factory=Presenter`.
`main.py` builds `BotApiClient`, `TelegramDocuments`, `TelegramQuestions`,
`ensure_menu_button`, the `Titler` and `TelegramGateway` directly.

The content cards are wired in two places: `build_delivery` binds
`TelegramApprovalCards(bot, api, topics)` into the content plan's late-bound cards
holder (it runs in the agent AND in each worker — the tick that sends cards runs on
the workers), and `create_dispatcher(..., content_decisions=)` gets the content
plan's decisions object for the buttons, the reply interception and the titler skip.
A crash between a card's media and its text message, or between a successful send and
the outbox write, shows the owner a duplicate; only the complete, recorded card's
buttons approve anything.

The model menu is wired the same way: `ModelMenuService(cfg, acl=…, lanes=<sessions'
SessionService>, defaults=<identity's users service>)` inside
`TelegramModelMenu(bot_api.call, service)`, passed to `MessageRouter(...,
model_menu=)` **and** to `create_dispatcher(..., model_menu=)` — the router opens
it, the dispatcher routes its buttons, and both are the same object.

Step 5 deleted `gateway/telegram.py` (`handle_event`, `intake_attachments` and
the six adapters that satisfied the ports from a bare `home: Path`),
`gateway/attachments.py` (now `infrastructure/attachments.py`), `gateway/titles.py`
(now `infrastructure/titler.py`), `gateway/voice.py` and the
`gateway/{events,presenter,questions,rich,topics}.py` re-exports. With them went
the second copy of `MAX_ATTACHMENT_BYTES`: `domain/constants.py` is the only one.

- `tests/test_main.py::test_the_bot_token_is_dropped_before_any_subprocess_can_inherit_it`
  inspects the source of `main.run` for `os.environ.pop("TELEGRAM_BOT_TOKEN"`
  before `build_telegram_gateway(`, with the same assertion for `OPENAI_API_KEY`.
  Never delete either.
- `SecretNameError` ideally lives in `core/` so the provider raises it directly and
  `TranslatingSecrets` disappears. Open.

### Wave 3 / 4

- **Webhook mode is live (Wave 4a), and it is the only way to run more than one
  agent.** `main.run` registers the URL and blocks on the web server; polling stays the
  default and the local-dev mode. `--scale agent=N` needs `telegram_mode: webhook` AND
  `CH_REDIS_URL` — inline mode starts a `CronTicker` and a curator loop per instance.
- **What is still per instance, and why none of it is correctness.** The bus
  subscription, the `AskBroker` and the in-flight snapshots moved to Redis in Wave 3b,
  so a button press and a turn's events cross processes. What did not: the titler's
  `asyncio.Task` set (a title is written once, by whichever instance heard the message;
  the row is the state), the presenter's typing task (a cosmetic indicator for one
  message), and the webhook's in-memory dedup fallback (only reached when no
  `update_dedup` was wired, which in redis mode never happens). Each is per instance
  and per message, and losing one loses nothing durable.
- **A crash between the `200` and the background dispatch loses that update.** The
  alternative — holding the request open through intake and STT — is the duplicate
  delivery that costs money, so this is the chosen side of the trade, not an oversight.
  A user-visible retry is the recovery: sending the message again.
- `RichSender` is the only Bot API 10.x caller and has no retry/backoff on 429;
  every failure degrades one rung down the chain instead.

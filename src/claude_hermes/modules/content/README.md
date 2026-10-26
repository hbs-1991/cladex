# `content`

## Purpose

The content plan: what content goes to which channel and when, who may change it,
how an approved theme becomes a drafted post, and how an approved post reaches the
publisher routine at its time. One row (`content_items`) is **one publication on
one channel**; variants of one idea share a `group_key`, so one row is one provider
call and one result.

Four actors reach an item and each has its own rules (`domain/status.py`):

- **the owner** (the `/api/content*` routes, and the buttons of a Telegram card)
  creates, edits, **approves a theme**, **approves content**, unapproves, retries
  and deletes;
- **the agent** (the `content_plan` tool — in chat on the owner's lanes, and in the
  deploy owner's routine runs) edits the plan but can never approve it;
- **the drafter routine** (a run of the configured on-demand drafter) is the only
  actor that moves `planned -> drafting -> review | planned`;
- **the publisher routine** (a run of ANOTHER on-demand routine) is the only actor
  that moves `queued -> publishing -> published | failed`.

```
idea --(owner: approve theme)--> planned --(drafter: claim_draft)--> drafting
  --(drafter: submit)--> review --(owner: approve, web or Telegram)--> scheduled
  --(tick)--> queued --(publisher: claim)--> publishing --> published | failed
drafting --(draft_failed | timeout)--> planned   (the third time: failed)
```

Nothing is published without the owner's final approval of the generated content:
theme approval never schedules a post.

The **tick** (`ContentService.tick(now)`) joins them: it wakes the drafter
`content_draft_lead_hours` before an approved theme's time, sends the drafted
post to the owner as a Telegram **approval card**, queues approved items whose time
has come and wakes the publisher — both routines only through the scheduler's
`wake_if_idle`; it never calls `run_now` and never issues an LLM call itself.

Owner-only in v1: every route is `require_owner`, the tool exists only on the
deploy owner's non-group lanes and in the deploy owner's routines, and a card goes
to the plan owner's private chat with the bot.

## Layout

```
domain/
  models.py      ContentItem (ORM, table `content_items`, migrations 0010 + 0011)
  status.py      STATUSES; CREATE_STATUSES; AGENT_FROM / AGENT_TO / AGENT_LOCKED;
                 APPROVED_FIELDS / THEME_FIELDS; WEB_LOCKED / WEB_SET_STATUSES /
                 APPROVABLE / RETRYABLE; agent_refusal(current, target);
                 needs_attention(status, error, *, publish_at, approved, card_state,
                 now_iso); item_needs_attention(row, now_iso)
  formats.py     MEDIA_RULES; approval_refusal(body, format, media) -> (code, msg) |
                 None; media_kind; card_media_kind
  cards.py       CARD_* states, CARD_LIVE, PREVIOUS_CARD, MAX_CARD_ATTEMPTS,
                 SENDING_STALE, TOPIC_NAME, the card texts; card_revision(body, media)
  time.py        zone, iso_utc, parse_instant (naive -> the deploy zone), local_iso
  media.py       MEDIA_EXTENSIONS, MAX_MEDIA, media_problem, split_paths, stored_media
  exceptions.py  ContentRejected (400, the field's own code), ContentConflict (409)
application/
  ports.py       ContentStore, MediaFiles, Publisher, Drafter, ApprovalCards, Settings
  service.py     ContentService; PUBLISHER_KEY, DRAFTER_KEY, TOPIC_KEY, LATE_WINDOW,
                 WAKE_BACKOFF_MINUTES, MAX_WAKES, STUCK_GRACE, MAX_DRAFT_ATTEMPTS,
                 MAX_REDRAFTS, COMMENT_MAX_CHARS, MAX_RANGE, DEFAULT_CHANNELS
infrastructure/
  repository.py  ContentRepository(engine) — the only content_items SQL (+ meta_swap)
  media.py       WorkspaceMedia(workspaces) — MediaFiles inside the owner's agent_home
presentation/
  router.py      router: APIRouter   (/api/content*)
  schemas.py     to_wire(item, now_iso=None); ContentBody, RetryBody, PublisherBody,
                 ThemesBody
  dependencies.py get_content
  agent_tool.py  TOOL_NAME / TOOL_DESCRIPTION / TOOL_SCHEMA; ContentPlanTool(service)
  routine_server.py ContentRoutineTools(tool) — the scheduler's RoutineTools port
  decisions.py   ContentCardDecisions(service) — telegram's ContentDecisions port
wiring.py        build(engine, *, workspaces, publisher, meta, timezone, channels,
                 publisher_timeout_seconds, drafter, cards, draft_lead_hours,
                 calendar_origin) -> ContentBundle(service, tool, routine_tools,
                 decisions, cards); ApprovalCardsSlot
```

## Public surface

### Composition (`wiring.py`)

`main.build_app` calls `content.wiring.build(...)` **only when `content_enabled`**;
with the flag off every object is `None` and the tool, the router, the tick and the
card decisions do not exist.

| `build` keyword | What main passes |
|---|---|
| `publisher` | the `SchedulerPublisher` adapter (late-bound to the scheduler) |
| `drafter` | **the same `SchedulerPublisher` instance** — the job id on every call tells the two routines apart; omitted, the publisher's is used |
| `cards` | a holder whose `.target` is bound in `build_delivery` to telegram's `TelegramApprovalCards` (in the agent AND in every worker, since the tick runs there); omitted, `build` makes an `ApprovalCardsSlot()` and exposes it as `bundle.cards` |
| `draft_lead_hours` | `cfg.content_draft_lead_hours` (default 24) |
| `calendar_origin` | `cfg.web_public_origin` — the card's "open in calendar" link is `<origin>/calendar?item=<id>`; empty, the card has no link |

| Bundle attribute | Type | Goes to |
|---|---|---|
| `service` | `ContentService` | the HTTP bundle's `content`; the tick (`await service.tick(now)` after `turns.reap`, inline `CronTicker` and `workers.tasks.scheduler_tick`) |
| `tool` | `ContentPlanTool` | turns' `ContentPlanPort` (`SdkTurnExecutor(content=...)`) |
| `routine_tools` | `ContentRoutineTools` | the scheduler's `RoutineTools` port (`SdkJobExecutor(routine_tools=...)`) |
| `decisions` | `ContentCardDecisions` | telegram's `ContentDecisions` port (the dispatcher's `content_decisions`) |
| `cards` | the `cards` holder | `build_delivery` sets `.target` |

`ContentService(store, media, publisher, settings, *, timezone="UTC",
channels=DEFAULT_CHANNELS, publisher_timeout_seconds=180.0, drafter=None,
cards=None, draft_lead_hours=24.0, calendar_origin="", clock=None)` can also be
built by hand; the tool object is then `ContentPlanTool(service)`, the routine port
`ContentRoutineTools(tool)` and the decisions `ContentCardDecisions(service)`. The
service carries no tool attribute: the tool's declaration is presentation, and
application may not import it. `clock` is the "now" of calls handed none (the card
decisions); tests pin it.

**The card chat** is the item owner's id: a user id IS the Telegram user id, and a
private chat's id is the user's, so nothing else has to be wired for it.

### Ports consumed (`application/ports.py`)

| Port | Methods | Satisfied today by |
|---|---|---|
| `ContentStore` | rows (`create`, `get`, `list_items`, `backlog`, `update(..., expect=, supersede=)`, `delete`, `claim`), the publisher's tick (`fail_late`, `fail_empty`, `promote_due`, `wake_owners`, `note_queued`, `postpone_wakes`, `take_wakes`, `release_wakes`, `fail_unclaimed`, `publishing_owners`, `mark_stuck`), themes and the drafter (`approve_themes`, `claim_draft`, `return_draft`, `drafting_owners`, `sweep_drafting`, `draft_owners`, `note_planned`, `clear_planned_notes`, `postpone_draft_wakes`, `take_draft_wake`, `release_draft_wake`, `exhaust_draft_wakes`, `redraft`), the card outbox (`card_by_message`, `owns_items`, `recover_cards`, `claim_card`, `card_sent`, `card_unsent`, `approve_card`, `supersede_missed`) and `meta_swap` | `infrastructure.repository.ContentRepository(engine)` |
| `MediaFiles` | `async normalize(owner_id, path) -> str` (raises `ValueError`), `async sha256(owner_id, path) -> str \| None`, `async locate(owner_id, path) -> Path \| None` | `infrastructure.media.WorkspaceMedia(workspaces)` over `core.workspace.WorkspaceResolver` |
| `Publisher` / `Drafter` | `describe(job_id, owner_id) -> {"name","kind","state","next_run_at","lease_until","timeout_seconds"} \| None`, `wake_if_idle(job_id, now_iso) -> bool` (sync or async) | ONE `core.app_context.SchedulerPublisher` over `SchedulerService`, wired in `main.py` |
| `ApprovalCards` | `async send(card) -> {"message_id", "thread_id"} \| {"topic_gone": True} \| {"error"}`, `async edit(chat_id, message_id, text)`, `async ensure_topic(chat_id, name) -> int \| None`; never raises | telegram's `TelegramApprovalCards(bot, api, topics)`, late-bound in `build_delivery` |
| `Settings` | `async get(key)`, `async set(key, value)` | `core.meta.MetaStore` — keys `content_publisher_job_id`, `content_drafter_job_id`, `content_card_topic` |

The card dict is `{"item_id", "chat_id", "thread_id" | None, "title", "channel",
"format", "body", "publish_at_local" ("01.10 10:00" in the deploy zone), "media":
[absolute paths], "media_kind": "images" | "video" | "none", "revision",
"calendar_url"}`.

### Ports provided

- **`ContentPlanTool`** — turns' `ContentPlanPort`: `name`, `description`, `schema`
  (read by `hermes_server.declared_spec`) and
  `async handle(args, *, owner_id, origin, thread_id) -> {"content": [...]}` plus
  `"is_error": True` on a refusal. `origin` is `"chat"` or `"cron:<job_id>"` and
  becomes `created_via`.
- **`ContentRoutineTools`** — the scheduler's `RoutineTools`:
  `for_owner(owner_id, *, is_deploy_owner, job_id) -> (mcp_servers, tool_names)`,
  an in-process `create_sdk_mcp_server(name="hermes")` holding the one tool, so it
  is `mcp__hermes__content_plan` in a routine exactly as in chat. `({}, [])` for
  `owner_id <= 0` or anybody but the deploy owner.
- **`ContentCardDecisions(service)`** — telegram's `ContentDecisions`:
  - `async approve_from_card(item_id, revision, user_id) -> str` — the popup text:
    `✅ Одобрено — выйдет ≈ 01.10 10:00`, or the stale answer for a wrong revision,
    a second tap, a non-owner, an item edited or a file changed meanwhile;
  - `async redraft_from_reply(chat_id, reply_to_message_id, user_id, comment) ->
    str | None` — `None` unless the message replies to a LIVE card of `user_id` in
    that chat (the message is then ordinary); otherwise the answer (`🔁 …`, the
    redraft limit pointing to the calendar, "write what to change", time passed);
  - `async is_card_topic(chat_id, thread_id) -> bool` — the stored topic, in a
    plan owner's chat; the titler skips it.
  Nothing raises across it.

### Dependencies

`presentation/dependencies.py`, on `core.di.provide`.

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_content` | `content` | `ContentService` |

The shell mounts the router only when `deps.content is not None`; with the flag
off `/api/content*` falls through to the `/api/*` catch-all (404), which the SPA
reads as "content disabled".

### Routes

`router: APIRouter(tags=["content"])`. **Every route is `require_owner`** (403
`forbidden` for anybody else) and scoped to the asking user: a foreign or unknown
id is 404. Errors are the shell's `{"error": {"code", "message"}}`; the message is
shown verbatim.

| Method | Path | `operation_id` | Answer |
|---|---|---|---|
| GET | `/api/content?from=&to=` | `content_list` | `{"items", "backlog"}`; 400 `bad_range` (missing, reversed, over 62 days) |
| GET | `/api/content/publisher` | `content_get_publisher` | `{"job_id", "name", "state"}` (name/state null when the routine is gone) |
| PUT | `/api/content/publisher` | `content_set_publisher` | same; 400 `bad_publisher` unless the owner's `manual` routine, and not the drafter |
| GET | `/api/content/drafter` | `content_get_drafter` | `{"job_id", "name", "state"}` |
| PUT | `/api/content/drafter` | `content_set_drafter` | same; 400 `bad_drafter` unless the owner's `manual` routine, and not the publisher |
| POST | `/api/content/approve-themes` | `content_approve_themes` | body `{"from", "to"}` -> `{"approved": int, "items": [...]}`: every future `idea` with `from <= publish_at < to` -> `planned`; 400 `bad_range` |
| POST | `/api/content` | `content_create` | 201 item; 400 `bad_channel`, `bad_media`, `bad_time`, `bad_status` |
| GET | `/api/content/{id:int}` | `content_get` | item |
| PATCH | `/api/content/{id:int}` | `content_edit` | item; 409 `locked` for `drafting/queued/publishing/published`; 400 `no_publish_at` (scheduled or planned), `media_missing` |
| DELETE | `/api/content/{id:int}` | `content_delete` | 204; 409 `published_item` |
| POST | `/api/content/{id:int}/approve` | `content_approve` | item in `scheduled`; 400 `not_approvable`, `no_publish_at`, `empty_body`, `media_required`, `media_missing` |
| POST | `/api/content/{id:int}/unapprove` | `content_unapprove` | item in `review`; 400 `not_approved` |
| POST | `/api/content/{id:int}/approve-theme` | `content_approve_theme` | item in `planned`; 400 `not_an_idea`, `no_publish_at` (none, or already passed) |
| POST | `/api/content/{id:int}/unapprove-theme` | `content_unapprove_theme` | item in `idea`; 400 `not_planned` |
| POST | `/api/content/{id:int}/retry-drafting` | `content_retry_drafting` | a `planned` item with `error`: drafter wakes and attempts reset, theme approval kept; 400 `not_retryable` |
| POST | `/api/content/{id:int}/retry` | `content_retry` | `{"mode": "repost"}` -> `queued` (only an item that was approved), `{"mode": "published", "url"}` -> `published`; 400 `not_retryable`, `bad_mode`, `bad_url` |
| GET | `/api/content/{id:int}/media/{n:int}` | `content_media` | the file (`FileResponse`), re-guarded on every request; 404 if missing |

`to_wire` is `ContentItemWire` exactly — v1's keys plus `theme_approved_at`,
`draft_attempts`, `redrafts`, `draft_comment`, `card_state`. `needs_attention` is
computed against the request's clock: `failed`/`stuck`; `queued` or `planned` with
an `error`; `card_state == "failed"` on an unapproved `review` item; `planned`,
`drafting` or unapproved `review`
whose `publish_at` has passed. Times are ISO-8601 UTC; the browser renders them
locally. A naive `from`/`to`/`publish_at` is read in the deploy's `timezone`.

A PATCH of a `scheduled` item keeps it `scheduled` (the owner is the approver, so
the edit is re-approved in the same act) and re-hashes its media when they changed;
a PATCH of a `planned` item keeps it `planned` the same way. A `status` in the
PATCH (`idea`, `draft`, `review`, `skipped`) leaves either and withdraws the
approval. **Any** PATCH makes a live card stale (below).

### The approval gate (`domain/formats.py`)

Every content approval (web, Telegram) and the drafter's `submit` refuse:

- `empty_body` — a blank body (after strip);
- `media_required` — media the format cannot publish: `carousel` 2-10 images;
  `reel`, `short`, `video` exactly one video; `photo` exactly one image; any other
  format takes media or none. The message names what the format needs and has.

The tick's `fail_empty` also fails a `scheduled` item with a blank body at its time
("Nothing to publish"), and `promote_due` never queues one.

### Tools

`mcp__hermes__content_plan` — declared in `presentation/agent_tool.py`, schema
`{"action", "id", "channel", "format", "title", "body", "media", "publish_at",
"status", "group_key", "source_path", "url", "error", "from", "to"}` (`media` is
newline-separated paths). Actions: `list`, `get`, `create`, `update`, `set_status`;
the publisher-only `due`, `claim`, `published` (needs `url`), `failed` (needs
`error`), which answer a refusal unless `origin == f"cron:{publisher_job_id}"`; and
the drafter-only `claim_draft` (no arguments: claims the ONE earliest due `planned`
item and answers its fields, the owner's `draft_comment` and the attempt's media
folder `content/drafts/<id>/<n>/`, or "Nothing is due for drafting."), `submit`
(`id`, `body`, `media`, optional `title` -> `review`, media hashed, a card queued)
and `draft_failed` (`id`, `error`), which answer a refusal unless
`origin == f"cron:{drafter_job_id}"`. The procedure is the bundled `content-draft`
skill.

The SDK marks every schema key required, so the model sends `""`/`0` for what it
leaves alone: in `update` (and `submit`) an empty field is **unchanged**, and
`publish_at="none"` is the one way to clear a field (back to the backlog). Answers
print times in the configured `timezone` with the offset (`2026-10-01T10:00+05:00`).

`TOOL_DESCRIPTION` is prompt-cache surface: the boot fingerprint covers every
hermes tool's name, description and schema, so a changed byte resets every lane
once on the next boot. It names no channel list — channels are config.

### The tick

`ContentService.tick(now)`, every step one conditional statement:

1. `scheduled` items more than `LATE_WINDOW` (2 h) late -> `failed` ("missed its
   publish window"). Never queued: no surprise late posts.
2. `scheduled` items whose time has come with a blank body -> `failed` ("Nothing to
   publish").
3. `scheduled` items whose time has come -> `queued` (`attempts=0`, wake due now).
4. Per owner with a due wake, the publisher (`meta['content_publisher_job_id']`):
   - none / gone / not `manual` / paused -> the queued items get an explanatory
     `error` (`needs_attention`), nothing is woken, nothing is counted;
   - busy (live lease or pending wake) -> the due wakes move forward by the item's
     current backoff step **without** counting: the backoff measures idle time;
   - idle -> items that used every wake and waited out the last step -> `failed`
     ("never picked up"); then the compare-and-set `take_wakes` counts one wake on
     each due item and, only if it counted any, `wake_if_idle` is called once. A
     `False` answer releases the count.
   Wakes: when due, then after 5 and 15 idle minutes (`WAKE_BACKOFF_MINUTES`), and
   after a final 60 the item fails — three wakes per item at most.
5. `publishing` items claimed more than the publisher's `timeout_seconds` (or
   `cron_timeout_seconds`) plus `STUCK_GRACE` (10 min) ago -> `stuck`.

Then three steps, each guarded (a failure is logged and retried next tick):

6. **Missed posts.** A `planned`, `drafting` or unapproved `review` item whose
   `publish_at` passed is published by nobody (only `scheduled` is ever queued);
   `needs_attention` shows it. Its live card goes `superseded`, and a `sent` one is
   edited to "⏰ Время публикации прошло".
7. **The drafter** (`meta['content_drafter_job_id']`). First, per owner with a
   `drafting` item: one claimed more than the drafter's timeout plus `STUCK_GRACE`
   ago -> `planned`, `draft_attempts + 1` (the third -> `failed`). A render the
   timed-out run left behind is not killed; the next attempt writes to its own
   folder. Then the wakes: a `planned` item is due when
   `publish_at - content_draft_lead_hours <= now < publish_at`. Per owner with a
   due item, a budget shaped like the publisher's, for the ONE item the next run will claim (the
   earliest due item with wakes left): missing / paused -> an explanatory `error`
   on the due items, nothing counted; busy (a live lease, a pending wake, or an
   item of this owner still `drafting`) -> postponed, nothing counted — a drafter
   busy with another item is not ignoring this one; idle -> `take_draft_wake`
   counts one wake on that item by compare-and-set and `wake_if_idle` is called
   once. After 3 wakes that got no claim (5, 15, 60 idle minutes) the item stays
   `planned` with an `error` (needs attention) and is not woken for again until
   the owner's "Retry drafting".
8. **One approval card.** With no bound `ApprovalCards` (a process with no Bot)
   nothing happens. Otherwise: `sending` cards claimed more than 5 minutes ago go
   back to `pending` (a worker died mid-send; if it died AFTER Telegram accepted,
   the owner sees the card twice and only the row's `card_message_id` is live);
   then ONE `pending` card of a `review` item goes `sending` by compare-and-set and
   is sent into the "Контент" topic of the owner's private chat:
   - the topic id is `meta['content_card_topic']`. Creating it is reserved first
     (`creating:<now>` by `meta_swap`, stale after 5 minutes), so concurrent ticks
     never create two topics — a tick that finds the reservation puts its card
     back to `pending` without counting; `ensure_topic` answering `None` (Threaded
     Mode off) releases it and the card goes flat into the private chat;
   - `{"topic_gone": true}` -> the topic is re-created once and the card re-sent;
     gone again -> the card is `failed`;
   - `{"message_id"}` -> `sent` with the chat and message ids, unless the content
     moved meanwhile (then the card just sent is edited to "✏️ Изменено");
   - `{"error"}` -> back to `pending` with `card_attempts + 1`; the third -> the
     card `failed`, the reason in the item's `error`, needs attention.

### The approval card

A card is sent only for an item the drafter `submit`ted before its `publish_at`
(`card_state = 'pending'`). Its revision is the first 12 hex chars of a sha256 over
the body and each media file's path and sha256 (`domain.cards.card_revision`); the
buttons carry `cnt:a:<id>:<rev>` / `cnt:r:<id>:<rev>`.

- **Approve** (`approve_card`): owner-only, the item still `review`, the card
  `sent` with that revision, `publish_at` still ahead, the approval gate passing,
  every file hashed FRESH and the recomputed revision equal to the button's — then
  ONE `UPDATE … WHERE status='review' AND card_state='sent' AND card_revision=:rev`
  schedules it with those hashes. The card becomes "✅ Одобрено — выйдет ≈ …".
- **Redo** (a reply to the card's text message): the comment (≤ 2000 chars) is
  stored as `draft_comment`, `redrafts + 1` (at most 3), the item goes back to
  `planned` and the card `superseded` in one statement; the card becomes "🔁
  Переделываю" and the drafter is woken.
- **Staleness.** Every content write — a web PATCH, an agent `update` or
  `set_status`, an approve, an unapprove, a redraft — turns a `pending`, `sending`
  or `sent` card `superseded` in the same statement (`update(..., supersede=True)`
  returns `previous_card_state`), and a `sent` one is edited best-effort to "✏️
  Изменено — одобри в календаре" (a web approve edits it to "✅ Одобрено"). A lost
  edit is cosmetic: the row decides what a button can do. The same write turns a
  `failed` card `none`: the owner (or the agent) has acted on the item itself, so
  the card that never arrived stops asking for attention.

## Invariants

1. **Approval is a human act.** No tool action reaches `scheduled` or `planned`;
   only the owner's routes and the Telegram approve button do. Content approval
   stores `approved_at/by` and a sha256 per media file; any agent content edit of
   approved content (a `scheduled` item, or a `failed` one awaiting a retry) lands
   in `review` with all three cleared. An agent edit of a `planned` item's theme
   (title, channel, format, time) lands in `idea` with `theme_approved_at` cleared.
2. **Theme approval schedules nothing.** A drafted post waits in `review` for the
   content approval; one still `planned`, `drafting` or `review` at its time is
   never published.
3. **The button approves its revision only.** Media hashed fresh, revision
   recomputed, one conditional write; an edited item, a second tap or a stale card
   match nothing.
4. **`claim` re-checks every hash.** A file changed or missing since approval sends
   the item to `review` with approval cleared and `error` naming the file, and the
   claim is refused. Intent-binding, not a boundary: the owner-lane agent has Bash,
   and a file can change between `claim` and the script reading it.
5. **Only the publisher publishes, only the drafter drafts.**
   `due/claim/published/failed` answer only `origin == f"cron:{publisher_job_id}"`,
   `claim_draft/submit/draft_failed` only `origin == f"cron:{drafter_job_id}"`, and
   the two may not be the same routine. `handle` refuses `owner_id <= 0`.
6. **One item per drafter run.** `claim_draft` refuses while the owner has an item
   `drafting` (an advisory lock per owner serialises claimers) and takes one row
   `FOR UPDATE SKIP LOCKED`; it never takes an item whose time has passed.
7. **No automatic re-post.** Nothing moves a `publishing` item except the
   publisher's `published`/`failed`, the tick's `stuck`, and the owner's retry; a
   never-approved item cannot be re-posted.
8. **One wake at a time, one card at a time.** The tick wakes only through
   `wake_if_idle` (a compare-and-set in the scheduler), after its own
   compare-and-set on the items; a card is sent only after the `pending -> sending`
   compare-and-set, and a topic is created only after the `meta_swap` reservation.
   Two concurrent ticks cannot both count a wake, send a wake, send a card or
   create a topic.
9. **Every state change is `UPDATE … WHERE … RETURNING`.** No read-then-write: the
   routes, the tool, the card decisions and N ticks share rows without a lock.
10. **Media stay home.** Paths are relative to the owner's `agent_home`; resolved
    with `resolve()` + `is_relative_to(agent_home.resolve())` (mirrored from
    `send_document`), no dot segment (`.claude/` unreachable), media extensions
    only, re-checked on every media request. Nothing here hosts a file.
11. **Timestamps are `iso_utc`**, one writer, because the tick compares them as
    strings.
12. **No `content_items` SQL outside `infrastructure/repository.py`**; the
    repository's one other statement is `meta_swap` on `meta`.
13. **The module imports no other module.** The scheduler is the `Publisher` and
    `Drafter` ports here and consumes `RoutineTools` there; turns consumes
    `ContentPlanPort`; telegram is the `ApprovalCards` port here and consumes
    `ContentDecisions` there. They meet only in `main.py`.

## Known debts

| Debt | Paid by |
|---|---|
| The plan is the deploy owner's only; `owner_id` is on every row so a per-user plan needs no migration, but the tool's gate and the routes' `require_owner` would both have to open. | out of scope (v1) |
| Inline mode runs one routine at a time, so a long routine delays the publisher: the plan promises "≈ HH:MM", not minute precision. | documented (`docs/content-calendar.md`) |
| A worker that dies after Telegram accepted a card but before `sent` is recorded makes the card go out twice (the orphan's buttons carry the same revision; only the recorded message takes a reply). | documented |
| A render left running by a timed-out drafter run is not killed. | documented (`docs/content-calendar.md`) |

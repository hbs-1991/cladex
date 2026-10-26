---
id: component.content
kind: component
status: current
summary: The content plan behind the Calendar — content_items, theme approval, the drafter routine (one post per run), the Telegram approval card that approves exactly its revision, content approval bound to media hashes, the content_plan tool, and the tick that wakes both routines.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:1b4ca9cbd8d9061823b8827880ad28a6270e0baa4ae9e636045ae80fa9c5f21f
evidence:
  - path: src/claude_hermes/modules/content/README.md
  - path: src/claude_hermes/modules/content/wiring.py
  - path: src/claude_hermes/modules/content/domain/models.py
  - path: src/claude_hermes/modules/content/domain/status.py
  - path: src/claude_hermes/modules/content/domain/formats.py
  - path: src/claude_hermes/modules/content/domain/cards.py
  - path: src/claude_hermes/modules/content/domain/time.py
  - path: src/claude_hermes/modules/content/domain/media.py
  - path: src/claude_hermes/modules/content/application/ports.py
  - path: src/claude_hermes/modules/content/application/service.py
  - path: src/claude_hermes/modules/content/infrastructure/repository.py
  - path: src/claude_hermes/modules/content/infrastructure/media.py
  - path: src/claude_hermes/modules/content/presentation/router.py
  - path: src/claude_hermes/modules/content/presentation/agent_tool.py
  - path: src/claude_hermes/modules/content/presentation/routine_server.py
  - path: src/claude_hermes/modules/content/presentation/decisions.py
  - path: src/claude_hermes/core/db/migrations/versions/0010_content_items.py
  - path: src/claude_hermes/core/db/migrations/versions/0011_content_drafting.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/main.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/http/app.py
  - path: src/claude_hermes/modules/turns/infrastructure/sdk_runner.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/executor.py
  - path: src/claude_hermes/modules/telegram/infrastructure/cards.py
  - path: skills/content-publish/SKILL.md
  - path: skills/content-draft/SKILL.md
  - path: docs/content-calendar.md
  - path: pyproject.toml
relations:
  - component.cron
  - component.web
  - component.gateway
  - flow.content-publish
  - contract.state-db
  - decision.modules-and-ports
  - decision.prompt-cache-discipline
  - decision.multi-user-isolation
---
# Content plan

`src/claude_hermes/modules/content/` — the tenth module, added 2026-09-28, with
its v2 (theme approval, the drafter, Telegram approval cards) the same day. What
goes out on which channel and when, who may change it, how an approved theme
becomes a drafted post, and how an approved post reaches the publisher routine at
its time. The module's `README.md` is the developer's reference (routes, codes,
the tick step by step, thirteen invariants); the operator's page is
`docs/content-calendar.md`; the calendar that draws it is in [[component.web]] and
the card's Telegram half is in [[component.gateway]].

## The row and its life

`content_items` (migrations `0010` + `0011`, [[contract.state-db]]) is **one
publication on one channel**: variants of one idea are separate rows sharing a
`group_key`, so a row is one provider call and one result. The body in the row is
exactly what gets published; `source_path` and `thread_id` are links back to where
it came from, never a source of content. Media are paths relative to the owner's
`agent_home`, stored as `[{"path", "sha256"}]`.

    idea --(owner: approve theme)--> planned --(drafter: claim_draft)--> drafting
      --(drafter: submit; card queued)--> review --(owner: approve, web or card)-->
      scheduled --(tick)--> queued --(publisher: claim)--> publishing
      --> published | failed           (\--(tick: timeout + 10 min)--> stuck)
    drafting --(draft_failed | timeout)--> planned   (the third time: failed)
    idea -> draft -> review            the hand-written way, no card
    and skipped, for anything dropped before it went out

`domain/status.py` holds the actors' rules so the router, the tool and the tick
share them physically:

- **The owner** (`/api/content*`, every route `require_owner`, a foreign id 404,
  and a card's buttons) approves a theme (`idea → planned`, needing a future
  `publish_at`; one at a time or every idea in a week), withdraws it, approves
  content (`idea/draft/review/failed → scheduled`), unapproves, edits, retries and
  deletes. An owner edit of a `scheduled` or `planned` item keeps it there: the
  editor is the approver.
- **The agent** (`content_plan`) may move an item into `idea, draft, review,
  skipped` only — never `planned` or `drafting`. A content edit of approved
  content lands in `review` with the approval cleared; an edit of a `planned`
  item's title, channel, format or time lands in `idea` with the theme approval
  cleared; `drafting, queued, publishing, stuck, published` refuse every agent
  edit.
- **The drafter routine** alone moves `planned → drafting → review | planned`,
  through the tool's drafter-only actions.
- **The publisher routine** alone moves `queued → publishing → published |
  failed`, through the tool's publisher-only actions.

**The approval gate** (`domain/formats.py`, `approval_refusal`) is one function
for the web approve, the card approve and the drafter's `submit`: `empty_body`
for a blank body, `media_required` when the media do not fit the format — a
`carousel` 2-10 images, `reel`/`short`/`video` exactly one video and nothing else,
`photo` exactly one image, any other format media or none. The tick fails a
`scheduled` item with a blank body at its time ("Nothing to publish") instead of
queuing it.

## The tool — one name in two places

`presentation/agent_tool.py` declares `content_plan` (name, description, schema)
and renders its answers; `ContentPlanTool(service)` is turns' `ContentPlanPort`,
read by `hermes_server.declared_spec` the `ext_manage` way, so the tool is spelled
once. Actions: `list`, `get`, `create`, `update`, `set_status`; the
publisher-only `due`, `claim`, `published` (needs `url`), `failed` (needs
`error`), which answer a refusal unless `origin == f"cron:{publisher_job_id}"`;
and the drafter-only `claim_draft` (no arguments: the ONE earliest due `planned`
item, its owner's `draft_comment` and the attempt's media folder
`content/drafts/<id>/<n>/`), `submit` (`id`, `body`, `media`, optional `title` →
`review`, media hashed, a card queued unless the time has passed) and
`draft_failed` (`id`, `error`), which answer a refusal unless
`origin == f"cron:{drafter_job_id}"`. The schema gained no key for v2. Times in:
ISO-8601, a naive time read in `cfg.timezone`; times out: in that zone with the
offset. Because the SDK marks every schema key required, an empty field in
`update` means "unchanged" and `publish_at="none"` moves an item to the backlog.

- **In chat** `SdkTurnExecutor(content=)` registers it as the last hermes tool,
  after `memory_search`, on the deploy owner's private and web lanes only
  (`_content_for`: `is_owner_lane is True`, not a group lane, `owner_id > 0`), with
  `origin="chat"` and the lane as `thread_id`. `tool_names()` and
  `tool_fingerprint()` include it whenever the port is wired, so flipping
  `content_enabled` — or changing its description, as v2 did — moves the boot
  fingerprint: one reset of every lane, once ([[decision.prompt-cache-discipline]]).
- **In a routine** `presentation/routine_server.py` (`ContentRoutineTools`) is
  the scheduler's `RoutineTools` port: an in-process `create_sdk_mcp_server(name=
  "hermes")` holding the same object, so the name is `mcp__hermes__content_plan`
  there too and one skill text works in both. Only for the deploy owner's routines,
  with `origin="cron:<job_id>"` taken from the executor's argument
  ([[component.cron]]). The procedures are the bundled skills `content-draft`
  (one item per run, the owner's own skills by format, never publish or approve)
  and `content-publish`.

`TOOL_DESCRIPTION` is cached-prefix surface and names no channel list, because
channels are config (`content_channels`) and a config edit must not rewrite the
prompt.

## The tick and the wakes

`ContentService.tick(now)` rides the cron clock — inline as a `CronTicker` extra
after `turns.reap`, in redis mode at the end of `scheduler_tick` on every worker,
in its own `try` — and issues no LLM call. Each step is one conditional
statement, so N workers ticking at once are safe:

1. `scheduled` more than `LATE_WINDOW` (2 h) late → `failed`, missed window; a
   due one with a blank body → `failed`, nothing to publish.
2. `scheduled` and due → `queued` with a wake due now.
3. Per owner with a due wake: no publisher, a deleted one, one that is not
   `manual`, or a paused one → the queued items get an explanatory `error`, nothing
   is woken. A busy publisher (a live lease, or `next_run_at` already set) moves
   the wakes forward without counting. An idle one: items that used their three
   wakes and waited out the last step → `failed` ("never picked this item up");
   then `take_wakes` counts one wake per due item by compare-and-set and, only if
   it counted any, calls `Publisher.wake_if_idle` once — a `False` releases the
   count. Wakes happen when due, then after 5 and 15 idle minutes, and the item
   fails after a final 60 (`WAKE_BACKOFF_MINUTES = (5, 15, 60)`).
4. `publishing` claimed longer ago than the publisher's `timeout_seconds` (else
   `cron_timeout_seconds`) plus 10 minutes → `stuck`.

Then three guarded steps:

5. **Missed posts** — a `planned`, `drafting` or unapproved `review` item whose
   time passed is published by nobody; `needs_attention` shows it and a sent card
   is edited to "⏰ Время публикации прошло".
6. **The drafter** (`meta['content_drafter_job_id']`) — a `drafting` item held
   past the drafter's timeout plus 10 minutes returns to `planned` with one
   attempt counted (the third → `failed`); the render it left is not killed, and
   the next attempt writes to its own folder. A `planned` item is due when
   `publish_at - content_draft_lead_hours <= now < publish_at`. The budget mirrors
   the publisher's but counts only the ONE item the next run will claim: busy (a
   lease, a pending wake, or an item of this owner still `drafting`) → postponed
   without counting; idle → `take_draft_wake` counts it and `wake_if_idle` is
   called once. After 3 unclaimed wakes the item stays `planned` with an `error`
   until the owner's "Retry drafting".
7. **One approval card** — see below.

Both routines are referenced by id in `meta` (`PUT /api/content/publisher`,
`PUT /api/content/drafter` accept only the owner's `manual` routine, and not the
one holding the other role). Both ports are ONE instance of `core/app_context.py`'s
`SchedulerPublisher`: `describe` = `SchedulerService.get(job_id, owner_id)` cut to
six fields, `wake_if_idle` = the scheduler's compare-and-set — never `run_now`,
which clears a live lease. `claim_draft` holds an advisory lock per owner, refuses
while the owner has an item `drafting` (`drafting_busy`), refuses a past
`publish_at`, and claims one row `FOR UPDATE SKIP LOCKED`.

## The approval card

A card exists only for an item the drafter `submit`ted before its time
(`card_state = 'pending'`); its revision is the first 12 hex chars of a sha256
over the body and each media path and sha256 (`domain/cards.py`). The tick sends
at most ONE per run: stale `sending` rows (over 5 minutes) go back to `pending`;
then a `pending → sending` compare-and-set, `ApprovalCards.send` into the
«Контент» forum topic of the owner's private chat (the chat id is the owner's
user id), and `sent` with the chat and message ids — or back to `pending` with
`card_attempts + 1`, the third failure making the card `failed` with the reason in
`error`. The topic id lives in `meta['content_card_topic']`, created behind a
`creating:<iso>` reservation (`ContentRepository.meta_swap`) so concurrent ticks
never create two; `{"topic_gone": true}` re-creates it once; no topic (Threaded
Mode off) sends the card flat into the private chat. A process with no bound
sender (no Bot) leaves the card pending without counting.

`presentation/decisions.py::ContentCardDecisions` is telegram's side:

- **Approve** — owner only, the item still `review`, the card `sent` with the
  button's revision, the time still ahead, the gate passing, every file hashed
  FRESH and the recomputed revision equal to the button's — then ONE `UPDATE …
  WHERE status='review' AND card_state='sent' AND card_revision=:rev RETURNING`
  schedules it with those hashes, and the card reads "✅ Одобрено — выйдет ≈ …".
  Anything else is the stale answer and changes nothing.
- **Redraft** — a reply to the card's text message: the comment (≤ 2000 chars)
  becomes `draft_comment`, `redrafts + 1` (at most 3), the item returns to
  `planned` and the card `superseded` in one statement, the card reads "🔁
  Переделываю", and the drafter is woken.
- **Staleness** — every content write (web PATCH, agent update or set_status,
  approve, unapprove, redraft) supersedes a `pending`/`sending`/`sent` card in the
  same statement, and a sent one is edited best-effort to "✏️ Изменено — одобри в
  календаре". The row decides what a button can do; a lost edit is cosmetic. The
  same write settles a `failed` card to `none`, and a failed card counts toward
  `needs_attention` only while its item is an unapproved `review` — so a post
  approved in the calendar after Telegram refused its card carries no badge.
- **`is_card_topic`** tells the titler to leave the «Контент» topic's name alone.

## Composition

`content_enabled` (default false) decides everything. `main.build_app` builds the
plan **before** the scheduler with `content.wiring.build(engine, workspaces=,
publisher=p, drafter=p, meta=, timezone=cfg.timezone,
channels=cfg.content_channels, publisher_timeout_seconds=cfg.cron_timeout_seconds,
draft_lead_hours=cfg.content_draft_lead_hours,
calendar_origin=cfg.web_public_origin)` over one unbound `SchedulerPublisher` `p`
→ `ContentBundle(service, tool, routine_tools, decisions, cards)`, hands
`routine_tools` to `SdkJobExecutor`, then binds `p.scheduler` once the
`SchedulerService` exists — the executor needs the plan and the plan needs the
scheduler, so one edge is late-bound. `tool` goes to `SdkTurnExecutor(content=)`,
`service` to `AppContext.content` and from there to `WebDeps.content` (the shell
mounts the router only when it is set) and to both clocks; `cards` (an
`ApprovalCardsSlot`) to `AppContext.content_cards`, where `build_delivery` binds
telegram's `TelegramApprovalCards(bot, api, topics)` in the agent and in every
worker; `decisions` to `AppContext.content_decisions` and from there to
`create_dispatcher(content_decisions=)`. The card's calendar link is
`<web_public_origin>/calendar?item=<id>`. With the flag off all of these are
`None`: no routes (404, which the SPA reads as "content disabled"), no tool,
nothing on the tick, no card handling in the gateway. `timezone` is validated
with `zoneinfo` at config load; an unknown zone stops the boot (the `tzdata`
dependency supplies the database where the OS has none).

`wiring.py` sits outside the four layers, like learning's and extensions':
presentation and infrastructure are independent siblings under the `content
layers` import-linter contract, and the bundle needs both. The module imports no
other module; telegram and content meet only through ports satisfied in `main.py`
([[decision.modules-and-ports]]).

## What approval is

Nothing is published without the owner's approval of the generated content:
theme approval schedules nothing. Content approval binds the owner's intent to
the content — body, channel, format and time in the row, and a sha256 per media
file taken at approval (for a card, re-taken at the tap) and re-checked right
after `claim` (a mismatch sends the item back to `review`, approval cleared,
`error` naming the file). It is defence in depth, not a boundary: the owner-lane
agent has Bash and could run a publish script itself, and a file can change
between the claim and the script reading it. Media are resolved with
`resolve()` + `is_relative_to(agent_home)`, no dot segment, files with an image or
video extension only; nothing in the module hosts a file — the publish scripts do,
at publish time ([[flow.content-publish]]).

Owner-only in v1: `owner_id` is on every row, so a per-user plan later needs no
migration, only the tool gate and `require_owner` opened.

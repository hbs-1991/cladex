---
id: flow.content-publish
kind: flow
status: current
summary: From an idea to a published post — theme approval, the drafter woken a lead time before (one post per run), the Telegram card (approve its revision or reply to redraft), the tick queueing and waking the publisher routine, claim, publish, report, retry.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:d116a0101e9953842e593996fdbbf71bdaea351fad5efadbbb366c1998d03ba8
evidence:
  - path: src/claude_hermes/modules/content/application/service.py
  - path: src/claude_hermes/modules/content/infrastructure/repository.py
  - path: src/claude_hermes/modules/content/presentation/agent_tool.py
  - path: src/claude_hermes/modules/content/presentation/routine_server.py
  - path: src/claude_hermes/modules/content/presentation/router.py
  - path: src/claude_hermes/modules/content/presentation/decisions.py
  - path: src/claude_hermes/modules/telegram/application/services/content_cards.py
  - path: src/claude_hermes/modules/telegram/infrastructure/cards.py
  - path: src/claude_hermes/modules/telegram/infrastructure/dispatcher.py
  - path: src/claude_hermes/modules/scheduler/application/service.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/repository.py
  - path: src/claude_hermes/modules/scheduler/infrastructure/executor.py
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/main.py
  - path: skills/content-draft/SKILL.md
  - path: skills/content-publish/SKILL.md
relations:
  - component.content
  - component.cron
  - component.gateway
  - flow.scheduled-job
---
# Flow: content publish

1. **Plan** — the agent (chat on the owner's private or web lane, or one of the
   owner's routines) calls `content_plan(action="create", …)`, or the owner uses
   the calendar's item dialog (`POST /api/content`). The item starts `idea`,
   `draft` or `review`; `created_via` is `web`, `chat` or `cron:<job_id>`, and a
   chat-created item links its lane in `thread_id`. Titles alone are enough for an
   idea: the drafter writes the rest ([[component.content]]).
2. **Approve the theme** — the owner, in the calendar: `POST
   /api/content/{id}/approve-theme` (or `POST /api/content/approve-themes` for
   every idea in the visible week) moves an `idea` with a future `publish_at` to
   `planned` and stamps `theme_approved_at`. Nothing is scheduled. Withdrawing it,
   or an agent edit of the title, channel, format or time, returns it to `idea`.
3. **Wake the drafter** — once `publish_at - content_draft_lead_hours` has passed
   (24 h by default), the tick reads the drafter (`meta['content_drafter_job_id']`)
   through the same `SchedulerPublisher` as the publisher. Unset or paused → an
   `error` on the due items, nothing woken. Busy — a run in progress, a pending
   wake, or another item of the owner still `drafting` → wait, nothing counted.
   Idle → one compare-and-set counts a wake on the item the next run will claim,
   then `wake_if_idle` ([[component.cron]]). Three unclaimed wakes (5, 15, 60
   idle minutes) leave it `planned` with an error until "Retry drafting".
4. **Draft** — the drafter run (a `manual` routine, `origin="cron:<job_id>"`, the
   bundled `content-draft` skill) calls `claim_draft`: one `UPDATE … WHERE id =
   (SELECT … ORDER BY publish_at LIMIT 1 FOR UPDATE SKIP LOCKED)` under a
   per-owner advisory lock, refused while the owner already has an item
   `drafting`. It follows the owner's own skill for the format when present,
   writes the media under `content/drafts/<id>/<attempt>/`, and calls `submit`
   (`drafting → review`, the approval gate applied, media hashed, card
   `pending`, `draft_comment` cleared) or `draft_failed` (`→ planned`, one attempt
   counted; the third → `failed`). A run that holds the item past the drafter's
   timeout plus 10 minutes counts the same as a failure. One item per run; the
   next due item waits for the next wake.
5. **Card** — the next tick (on whichever worker wins the `pending → sending`
   compare-and-set) sends ONE card through the `ApprovalCards` port bound in
   `build_delivery`: into the «Контент» topic of the owner's private chat
   (created once behind a `meta` reservation; flat if topics are unavailable;
   re-created once if deleted), the media first, then the text message with
   [Одобрить] [Переделать] [Открыть в календаре]. A refused send is retried up
   to 3 times, then the card is `failed` with the reason on the item
   ([[component.gateway]]).
6. **Approve the content** — either the card's Одобрить (the gateway checks the
   owner, content re-hashes the media, recomputes the revision and schedules in
   one `UPDATE … WHERE status='review' AND card_state='sent' AND
   card_revision=:rev`; the card reads "✅ Одобрено — выйдет ≈ …") or the calendar
   (`POST /api/content/{id}/approve`, which also supersedes the card). Both refuse
   `empty_body` and `media_required`. A stale card, a second tap, a stranger or an
   item edited meanwhile gets the stale answer and changes nothing.
   **Or redraft** — the owner replies to the card with a comment: the gateway
   intercepts it before intake, titling and routing (it never becomes a turn); the
   item returns to `planned` with the comment and `redrafts + 1` (at most 3), the
   card reads "🔁 Переделываю", and the drafter is woken — back to step 4, whose
   claim shows the comment.
7. **Tick** — every cron tick, after the scheduler's own claim and the turn reap
   (`CronTicker` extras inline, the end of `scheduler_tick` in redis mode),
   `ContentService.tick(now)`: an approved item more than 2 hours late →
   `failed` (missed window); a due one with a blank body → `failed` ("Nothing to
   publish"); a due one → `queued` with a wake due now. An item still `planned`,
   `drafting` or unapproved `review` at its time is published by nobody: it needs
   attention and its sent card is edited to "⏰ Время публикации прошло".
8. **Wake the publisher** — for the owner's queued items the tick reads the
   publisher (`meta['content_publisher_job_id']`) through
   `SchedulerPublisher.describe`. No usable publisher (unset, deleted, not
   `manual`, paused) → the items get an `error` saying so and nothing happens.
   Busy (running, or already woken) → the wake waits without counting. Idle → one
   compare-and-set counts a wake on the due items, then
   `SchedulerService.wake_if_idle` sets the routine's `next_run_at = now` in one
   guarded `UPDATE` ([[component.cron]]). Two ticks at once produce one wake.
9. **Run** — the next scheduler tick claims the `manual` routine like any fire
   (it stays `active` with `next_run_at` NULL after the claim) and dispatches it.
   `SdkJobExecutor` runs it in the owner's workspace with `_TOOLS`, the extensions
   composed for scope `cron`, and — lent by the content module for the deploy
   owner's routines — an in-process `hermes` server holding `content_plan`, called
   with `origin="cron:<job_id>"`. The prompt loads `skills/content-publish`.
10. **Due, claim, publish, report** — the skill: `content_plan(action="due")`, then
   per item `claim` (`queued → publishing` in one `UPDATE … WHERE status='queued'
   RETURNING`; a second claimer gets nothing). The service re-hashes the media: a
   changed or missing file sends the item to `review` with the approval cleared
   and the claim is refused — nothing is published. Otherwise the agent publishes
   exactly the claimed body and files with the channel's tool (Instagram reels
   and YouTube through the owner's scripts under `agent_home/tools/`, Instagram
   photos and carousels through the Instagram connector, anything else `failed`
   with "no publisher for <channel>/<format>"), then reports `published` with the
   post's URL or `failed` with the error. One attempt per claim; the run's final
   text, delivered like any routine report, lists every outcome. These four
   actions are refused from chat and from every routine but the configured
   publisher.
11. **Leftovers** — an item still `queued` after three wakes and a final idle
   hour → `failed` ("never picked this item up"). An item left in `publishing`
   past the routine's timeout plus 10 minutes → `stuck`. Nothing re-posts by
   itself.
12. **Retry** — for `failed`, `stuck` and `publishing` items the owner chooses in
   the dialog: `{"mode": "repost"}` → `queued` with `attempts=0` and the error
   cleared (step 8 again; refused for an item that was never approved), or
   `{"mode": "published", "url": …}` → `published`. For a `planned` item with an
   error, `POST /api/content/{id}/retry-drafting` resets the drafter's wakes and
   attempts and keeps the theme approval. The calendar badges every item that
   needs attention.

The precision bound is the cron tick (the wake is claimed one tick after it is
set) and, in inline mode, the one-routine-at-a-time queue: a long routine running
at publish time delays the publisher, which is why the calendar shows "≈ HH:MM".

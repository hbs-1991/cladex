# Content calendar

The sidebar's **Calendar** page puts two things on one timeline: the owner's
**content plan** (what goes out on which channel, and when) and every routine's
fires and runs. The plan is kept by the agent — in chat and in unattended
routines, through one tool, `content_plan` — and by the owner in the web UI.

The v2 flow has three steps:

1. The owner approves an item's **theme**.
2. A **drafter routine** writes the post (text, images, video) a day before its
   time and sends it to the owner in Telegram as an **approval card**.
3. The owner approves the generated content, on the card or in the calendar.
   The **publisher routine** then publishes it at its time. The server wakes
   each routine only when there is something for it to do.

Nothing is published without that final approval.

This page is the operator's: how to turn it on, how drafting and publishing are
wired, and what the guarantees are. The code lives in
`src/claude_hermes/modules/content/` (`README.md` there is the developer's page).
The two routines' procedures are the bundled skills
`skills/content-draft/SKILL.md` and `skills/content-publish/SKILL.md`.

## What you get

| URL | What |
|---|---|
| `/calendar` | The calendar: month, week and agenda views (agenda is the default below 768 px wide, month above; the choice is remembered per browser). Weeks start on Monday; times are the device's local time. Content items are coloured and iconed by channel; routine fires and recorded runs sit beside them; a routine that fires hourly or more often is one "every N" marker per day instead of dozens of chips. Filters hide channels or routines. Undated items wait in the backlog panel. |
| `/calendar/tasks` | The routines list that used to be `/routines` ("Tasks"). |
| `/calendar/tasks/:id` | One routine over its delivery thread. |
| `/routines`, `/routines/:id` | Redirect to the two above, so old links and bookmarks keep working. |

Everyone who can log in sees their own routines on the calendar. The content plan
is the **deploy owner's alone** in this version: other users never call its
routes and never get the tool.

On a desktop an item can be dragged to another day (the month view keeps its time
of day) or another hour (the week view). Items that are `drafting`, `queued`,
`publishing` or `published`, and routine fires, cannot be dragged.

`/calendar?item=<id>` opens that item's dialog, even when the item falls outside
the visible range. This is the link behind the card's "Открыть в календаре"
button. The parameter is removed when the dialog closes.

## Turning it on

1. In `config.yaml` (in `CH_HOME`):

   ```yaml
   timezone: Asia/Tashkent      # an IANA name; a misspelt zone stops the boot
   content_enabled: true
   # content_channels: [linkedin, telegram, x, threads, instagram, youtube]
   # content_draft_lead_hours: 24   # how long before publish_at a post is drafted
   # web_public_origin: https://cladex.example.com   # the card's calendar link
   ```

   `timezone` is how the plan reads a time the agent gives without an offset
   ("2026-10-01 10:00") and how it writes times back to the agent. It does **not**
   move routines: cron expressions are still evaluated in UTC.

2. Restart the agent — and, in redis mode, the worker and the scheduler too: each
   process reads its own copy of the file. Turning the plan on adds one tool
   (`mcp__hermes__content_plan`) to the toolset, so every conversation starts a
   fresh session once, at that boot. That is expected: under prompt-cache
   discipline the toolset fingerprint covers every hermes tool's name,
   description and schema. The next boot with the same setting resets nothing. A
   deploy that changes a tool's wording, such as the v2 `content_plan`
   description, also resets every conversation once.

With `content_enabled: false` (the default) the routes answer 404 — the calendar
then shows routines only — the tool exists nowhere and the tick skips the plan.

## Setting up the publisher

1. **Create the routine.** Calendar → Tasks → the "Content publisher" template
   (owner only). It is an **On demand** routine (`manual` schedule: it never
   fires on its own clock, only when woken) with a 1800-second timeout and the
   prompt "Load the content-publish skill and publish every due item in the
   content plan. You are unattended: ask nothing." The publisher control in the
   calendar toolbar offers the same template.
2. **Choose it.** In the calendar toolbar's publisher control, pick that routine
   (creating it from there chooses it too). Only an On-demand routine of yours is
   accepted: one on its own clock would fire past the wakes. The choice is stored by
   routine **id** (`meta.content_publisher_job_id`), so renaming the routine does
   not break it; deleting it does, and the calendar says so.
3. **Give the routine its channels.** A routine run gets extensions composed for
   scope `cron`. The Instagram connector must be enabled by the owner **and** its
   catalog manifest must list `scopes: [turn, cron]`. A connector drafted in chat
   without a `scopes` line defaults to `[turn]` and is invisible to every routine;
   fix the manifest and approve it again. The two publish scripts are the owner's
   own files under `agent_home/tools/` (`tools/igpub/igpub.py` for Instagram
   reels, `tools/ytc/ytc.py` for YouTube) and are not part of this repository;
   the skill tells the agent to read each one's `--help` rather than guess flags.
4. **The skill arrives by itself.** `skills/content-publish` is a bundled skill,
   and every boot seeds bundled skills into each workspace (`core/workspace.py`,
   `seed_skills`). A new skill directory is copied in on the next boot; a later
   change to the bundled file refreshes the copy too, unless the copy in the
   tree was edited there, in which case it is left alone and logged. It is also
   seeded into other users' trees, where the tool does not exist — harmless, it
   is just a file there.

Channels without a publisher (LinkedIn, Telegram, X, Threads today) can be
planned and approved; when their time comes the publisher reports them `failed`
with "no publisher for <channel>/<format>". Adding one is adding a script or a
connector and a row to the skill's table.

## Setting up the drafter

1. **Create the routine.** Use the "Content drafter" template, from the drafter
   control in the calendar toolbar or from Tasks. It is an **On demand**
   (`manual`) routine with a 1800-second timeout and the prompt "Load the
   content-draft skill, claim one due item from the content plan and draft it.
   You are unattended: ask nothing."
2. **Choose it** in the toolbar's drafter control. The choice is stored by id in
   `meta.content_drafter_job_id`, just like the publisher's. Only one of your
   On-demand routines is accepted, and it must not be the publisher: one routine
   cannot hold both roles (`bad_drafter` / `bad_publisher`). Each control's
   picker hides the routine that holds the other role.
3. **The skill arrives by itself.** `skills/content-draft` is bundled and seeded
   on boot, like `content-publish`. It picks the owner's own skills by format
   when they exist in `agent_home/.claude/skills/`:

   | Format | Skill |
   |---|---|
   | carousels | `social-carousel` |
   | reels and shorts | `vibecode-trap-reel` / `hyperframes-local` |
   | YouTube | `youtube-channel` |
   | images | `gpt-image-2` |
   | LinkedIn text | `linkedin-post`'s voice |

   When a skill is missing, the drafter writes the post directly. The skill is
   the owner-sanctioned drafting stage, so `linkedin-post`'s "an unattended run
   only proposes" rule does not stop it.
4. **Give it its tools.** Images need the owner's Codex login (`gpt-image-2`).
   Reels need HyperFrames, which runs from `agent_home/tools/chrome` and
   `agent_home/tools/bin` on the data volume and is **not** in the image. A
   render takes 10-15 minutes, which is why a run drafts one item and the
   template's timeout is 1800 seconds. When a tool is missing, the drafter
   reports `draft_failed` with that reason, and the item shows it after its
   third failure.

Each attempt writes its media under `content/drafts/<item_id>/<n>/`, where `n`
is the attempt number: failures plus redrafts plus one. A render left running
by a timed-out run is **not killed**. It can only write into its own attempt's
folder, never into a newer one, and its files are simply never submitted.

The drafter's own report goes to its routine topic, like any routine's. The
post itself reaches the owner as a card.

## The «Контент» topic and approval cards

When the drafter `submit`s a post before its publish time, the next tick sends
an **approval card** to the owner's private chat with the bot, in a forum topic
named «Контент». The card has two parts:

- the media first: a media group of 2-10 photos, one photo, or one video;
- then a text message with the title, channel/format, local publish time
  ("≈ 01.10 10:00") and body. This message carries the buttons [Одобрить]
  [Переделать] [Открыть в календаре].

A media group cannot carry buttons, so they sit on the text message. A photo or
video that Telegram refuses as media is sent as a document instead.

- **Threaded Mode must be on** for the bot (@BotFather, see the README's
  Threads section). The topic is created on the first card, and its id is
  stored in `meta.content_card_topic`. If the topic cannot be created, the card
  goes flat into the private chat and still works. If the owner deletes the
  topic, the next card re-creates it once.
- **Одобрить** is handled by the gateway, never by the model, and only for the
  deploy owner. It re-hashes the media, checks that the post is still exactly
  the version on the card, and schedules it in one write. The card then reads
  "✅ Одобрено — выйдет ≈ …". A second tap, an old card, or an item edited
  since the card was sent gets "Карточка устарела — открой пост в календаре."
- **Переделать** explains how to ask for a redraft: **reply to the card** with
  a comment. The reply never becomes a conversation turn. The item goes back
  to `planned` with the comment stored, the card reads "🔁 Переделываю", and
  the drafter is woken. The comment is the first instruction of the next
  attempt. After 3 redrafts the reply is refused with a pointer to the
  calendar.
- **Открыть в календаре** is `<web_public_origin>/calendar?item=<id>`. The
  button appears only for an `https://` origin, because Telegram refuses other
  URL buttons.
- Any other message in the «Контент» topic is an ordinary conversation on that
  topic's lane. The topic is never auto-renamed.
- **An edit makes a card stale.** Any change to the item (web, agent,
  unapprove, redraft) turns its card `superseded`, and a sent card is edited to
  "✏️ Изменено — одобри в календаре". If the publish time passes before
  approval, the card is edited to "⏰ Время публикации прошло".
- A card that fails to send is retried up to 3 times. After that it is `failed`
  with Telegram's reason on the item, and the item needs attention. If a worker
  dies between Telegram accepting a card and recording it, the owner may see
  the card twice. Only the recorded one takes a reply, and both carry the same
  revision.
- Cards are sent only for posts the drafter submitted. A post drafted in chat
  and put in `review` is approved in the calendar.

## An item's life

    idea --(owner: approve theme)--> planned --(drafter claims)--> drafting
      --(drafter submits; card sent)--> review --(owner approves, web or card)-->
      scheduled -> queued -> publishing -> published   (or failed / stuck)
    drafting --(draft_failed or timeout)--> planned   (the third time: failed)
    idea -> draft -> review   is still the hand-written way (no card)
                                                 (and skipped, any time before)

* **The agent** creates and edits items and may move them among `idea`, `draft`,
  `review` and `skipped`. It can never approve a theme or content, and never
  sets `planned` or `drafting`. A change to the body, media, channel, format or
  time of an approved (`scheduled`) item sends it back to `review`. A change to
  the title, channel, format or time of a `planned` item sends it back to
  `idea` and withdraws the theme approval. Items that are `drafting`, `queued`,
  `publishing`, `stuck` or `published` refuse every agent edit.
* **The owner** approves themes (`idea` → `planned`, one at a time or "Approve
  week's themes"), withdraws them, approves content (`review` → `scheduled`),
  unapproves, edits, reschedules, retries and deletes in the calendar. A theme
  needs a publish time that is still ahead. Content approval checks the
  **approval gate**:
  - the body is not empty (`empty_body`);
  - the media fit the format (`media_required`): a carousel 2-10 images, a
    reel, short or video exactly one video file, a photo exactly one image.
    Other formats may have media or none.

  Every media file must also exist inside the owner's `agent_home`. Files must
  be images or videos (`png jpg jpeg webp gif mp4 mov webm`), never under a
  dot-directory such as `.claude/`, and each file's sha256 is recorded. Editing
  a `scheduled` item keeps it `scheduled` (the owner's edit is the owner's
  intent) and re-hashes changed media. A `drafting` item is read-only. A
  published item cannot be deleted; mark it skipped instead.
* **The drafter routine** takes the ONE earliest due `planned` item per run
  (`claim_draft`). It writes the post and either `submit`s it (`review`, media
  hashed, a card queued) or reports `draft_failed`. These three actions work
  only inside the configured drafter's own run. When the item has a
  `group_key`, the claim also lists the idea's other items with their status and
  media, so a video already made for one channel is copied for the next one
  rather than rendered twice.
* **The tick** runs on the cron clock: every `cron_tick_seconds` inline, and
  every minute on every worker in redis mode. It does four things, and it never
  calls a model itself:
  - wakes the drafter for `planned` items inside their drafting window;
  - sends at most one pending approval card;
  - moves each approved item whose time has come to `queued`;
  - wakes the publisher routine.
* **The publisher routine** takes the due items with `content_plan(action="due")`,
  claims each one (`queued` → `publishing`, one statement, so two runs cannot both
  take it), publishes it, and reports `published` with the post's URL or `failed`
  with the error. The claim re-checks each media file against the hash taken at
  approval; a changed or missing file sends the item back to `review` with the
  approval cleared and an error naming the file, and nothing is published.

`due`, `claim`, `published` and `failed` answer only a run of the configured
publisher routine. From a chat or from any other routine they are refused, so a
prompt-injected turn cannot claim an item.

## Timing and the guarantees

* **The late window is 2 hours.** An approved item that is more than 2 hours
  past its time when the tick reaches it (the server was down, the publisher was
  paused) becomes `failed` with a missed-window error instead of being posted
  late. The scheduler's own catch-up limit is the same 2 hours.
* **No empty runs.** The publisher is woken only when an item is due, never while
  it is running or already has a wake pending, and through the scheduler's single
  compare-and-set (`wake_if_idle`), never `run_now` — so two workers ticking at
  once wake it once. An item is woken for at most 3 times: when due, then after 5
  and after 15 idle minutes; after a final 60 it becomes `failed` ("the publisher
  routine was woken 3 times and never picked this item up"). While the publisher
  is busy the item's clock waits instead of counting down.
* **No automatic re-post.** An item in `publishing` is never retried by the
  system. If the publisher reported nothing within its timeout plus 10 minutes
  the item becomes `stuck`. For `failed`, `stuck` and `publishing` items the owner
  chooses in the item dialog: **re-post** (back to `queued`, a fresh wake count)
  or **it was published** with the post's URL. The calendar badges every item
  that needs attention (`failed`, `stuck`, or `queued` with an error).
* **A missing or paused publisher** leaves due items `queued` with an error that
  says so, and wakes nothing. Resume or choose the routine, and the next tick
  wakes it.
* **When drafting starts.** A `planned` item is due for drafting once
  `publish_at - content_draft_lead_hours <= now < publish_at`. The drafter
  handles **one item per run**. `claim_draft` refuses while an item of the
  owner is still `drafting`, so due items queue up and are drafted in later
  runs.
* **Waiting is not failing.** Time the drafter spends busy never counts against
  an item: a run in progress, a wake already pending, or another item being
  drafted. A wake that gets no claim does count. The item is woken again after
  5, 15 and 60 idle minutes. After 3 unclaimed wakes it stays `planned` with an
  error (needs attention) and is not woken again until the owner presses
  **Retry drafting** in the item dialog. Retry resets the counters and keeps
  the theme approval. A missing or paused drafter puts an error on the due
  items and wakes nothing.
* **Draft failures.** Each `draft_failed`, and each run that holds an item past
  the drafter's timeout plus 10 minutes, counts one attempt and returns the
  item to `planned`. The third attempt makes it `failed`, and it needs
  attention. A never-approved item cannot be re-posted from `failed`.
* **Missed posts.** An item still `planned`, `drafting` or in unapproved
  `review` when its time passes is published by nobody. It shows "needs
  attention", and a sent card is edited to "⏰ Время публикации прошло".
  Reschedule it and approve again.
* **Nothing empty goes out.** A `scheduled` item with an empty body that
  reaches its time becomes `failed` ("Nothing to publish") and is never
  queued.
* **The real precision bound.** A wake is claimed on the next cron tick after it
  was set, so a post goes out a tick or two after its time — the calendar shows
  the time of `scheduled` and `queued` items as "≈ HH:MM". In inline mode the
  process runs **one routine at a time**, so a long routine that is running when
  a post is due delays the publisher until it finishes (and past 2 hours, the
  item fails rather than posts late). Keep long routines away from publish times,
  or run a worker (redis mode).

## What approval is and is not

Approval binds the owner's intent to the content: the body, channel, format and
time in the row, and a sha256 of every media file, re-checked when the publisher
claims the item. It is defence in depth and **not a security boundary**: the
owner-lane agent has Bash and could run a publish script directly, and a file can
still change between the claim and the script reading it. What approval does
guarantee is that the publisher routine — the only thing that publishes through
the plan — publishes nothing the owner did not approve.

Approving a **theme** approves nothing publishable: it only lets the drafter
write the post. Approving on a **card** checks the files too. The media are
hashed fresh at the tap, and the approval goes through only if the result still
matches the revision printed on the button. A file the drafter (or anyone)
changed after the card was sent makes the card stale rather than approving
content the owner never saw.

The body in the row is exactly what is published. `source_path` (a draft file
the agent worked from) and the chat thread an item was created in are links,
never a source of content.

## Media stay on the box

The plan stores local paths under the owner's `agent_home` (for example
`content/img/launch.png`). The calendar previews them through
`GET /api/content/{id}/media/{n}`, which is owner-only and path-guarded like
everything else under `/api/`. Nothing in the plan hosts a file: only the publish
scripts do, at publish time. `igpub.py` puts a local video on uguu.se for about 3
hours — anyone with that link can download it meanwhile, which is acceptable for
a file that is being published that minute and is why drafts are never hosted.

## The HTTP surface

Every `/api/content*` route is the owner's (`403` for anyone else); an id that is
not the owner's, or does not exist, is `404`. Errors are
`{"error": {"code", "message"}}`, the one shape every route answers with; the
item dialog shows the message verbatim.

| Route | What |
|---|---|
| `GET /api/content?from=&to=` | Dated items in the range plus the backlog. Over 62 days → `400 bad_range`. |
| `GET/PATCH/DELETE /api/content/{id}`, `POST /api/content` | Read, edit, delete, create (`bad_channel`, `bad_media`, `bad_time`, `bad_status`; `409 locked` while queued/publishing/published; `409 published_item` on delete). |
| `POST /api/content/{id}/approve`, `/unapprove`, `/retry` | The owner's transitions (`not_approvable`, `no_publish_at`, `empty_body`, `media_required`, `media_missing`, `not_approved`; retry takes `{"mode": "repost"}` or `{"mode": "published", "url": …}`). |
| `POST /api/content/{id}/approve-theme`, `/unapprove-theme` | `idea` ↔ `planned` (`not_an_idea`, `no_publish_at` — none or already passed; `not_planned`). |
| `POST /api/content/approve-themes` | `{"from", "to"}` → `{"approved", "items"}`: every future `idea` in the range becomes `planned` (`bad_range` over 62 days). |
| `POST /api/content/{id}/retry-drafting` | A `planned` item with an error: wakes and attempts reset, theme approval kept (`not_retryable`). |
| `GET /api/content/{id}/media/{n}` | A media file, for the preview. |
| `GET/PUT /api/content/publisher` | The publisher routine (`400 bad_publisher` unless it is one of your On-demand routines, and not the drafter). |
| `GET/PUT /api/content/drafter` | The drafter routine (`400 bad_drafter` unless it is one of your On-demand routines, and not the publisher). |
| `GET /api/routines/calendar?from=&to=` | The scheduler's half, for every logged-in user: future fires, dense markers and recorded runs of your own routines. |

## Troubleshooting

| You see | Why | Do |
|---|---|---|
| The calendar shows routines only | `content_enabled` is off (the content routes answer 404) or you are not the owner | Turn it on and restart |
| Items stay `queued` with "No publisher routine is set" | No publisher chosen | Choose one in the toolbar |
| "…is paused" / "…no longer exists" on queued items | The publisher routine was paused or deleted | Resume it, or choose another |
| `failed`: "never picked this item up" | The routine ran three times and did not claim it (wrong prompt, the skill missing, the run failing early) | Open the routine's thread in Tasks, fix it, then retry → re-post |
| `failed`: "no publisher for …" | The skill has no row for that channel/format, or the script/connector is missing from the routine's toolset | Add the script or put the connector in scope `cron`, then retry |
| `stuck` | The publisher claimed it and reported nothing in time | Check the channel: retry → "it was published" with the URL, or re-post |
| The agent says `due`/`claim` are refused | It is not the publisher routine's run (a chat, or another routine) | Working as intended |
| `planned` with "No drafter routine is set" / "…is paused" | No drafter chosen, or it was paused or deleted | Choose or resume it; the next tick wakes it |
| `planned` with "woken 3 times …" | The drafter ran and never claimed the item (wrong prompt, the skill missing, the run failing early) | Fix the routine (its topic shows the run), then **Retry drafting** |
| `failed` after drafting | Three draft attempts failed or timed out; the last `draft_failed` reason is the error | Fix the tool it names (Codex login, HyperFrames under `tools/`, a connector missing scope `cron`), then **Retry drafting**: the item goes back to `planned` with its theme approval kept |
| Cards arrive flat in the private chat | Threaded Mode is off, so the «Контент» topic could not be created | Turn Threaded Mode on in @BotFather |
| Card `failed` with Telegram's reason | Three sends were refused (file too large, bad aspect ratio) | Fix the media in the calendar and approve there |
| No "Открыть в календаре" button | `web_public_origin` is empty or not `https://` | Set a public https origin |
| Tapping Одобрить says the card is stale | The item changed since the card was sent, or it was already approved | Open it in the calendar |

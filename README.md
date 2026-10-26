# CladeX

[![ci](https://github.com/hbs-1991/cladex/actions/workflows/ci.yml/badge.svg)](https://github.com/hbs-1991/cladex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A self-hosted, self-improving personal agent on the Claude Agent SDK. Talk to it
from Telegram or the web UI; it keeps a persistent wiki memory, learns new skills
from what it does, runs scheduled jobs, and connects to MCP servers and Claude
Code plugins from a reviewed catalog — each user in an isolated workspace.

The Python package is named `claude_hermes`, the project's original name.

    git clone https://github.com/hbs-1991/cladex.git && cd cladex

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Security
issues go through [SECURITY.md](SECURITY.md), not public issues.

## Run (Docker)
1. `cp .env.example .env` and set:
   - `TELEGRAM_BOT_TOKEN` — from @BotFather
   - `CLAUDE_CODE_OAUTH_TOKEN` — `claude setup-token`; it bills against your
     Claude subscription instead of API usage
   - `POSTGRES_PASSWORD` — any long random string; compose builds the database
     URL from it
   - `MINIO_ROOT_PASSWORD` — any long random string; compose builds
     `CH_S3_ACCESS_KEY`/`CH_S3_SECRET_KEY` from it and `MINIO_ROOT_USER`
     (which defaults to `hermes` and is a username, not a credential)
   - `CH_SECRETS_KEY` — `python -c "from cryptography.fernet import Fernet;
     print(Fernet.generate_key().decode())"`. It encrypts the extension
     credentials in the database. **Back it up with the `.env` and do not lose
     it:** the ciphertext is in PostgreSQL and the key is not, which is the
     point — a lost key is every connector's credentials gone, and a database
     restore will not bring them back.
2. `mkdir -p data && cp config.yaml.example data/config.yaml`
3. `docker compose up -d`

That brings up **seven containers**: `db`, `redis`, `minio`, `agent` (HTTP,
Telegram — polling by default, or webhook — and *starting* turns), `worker` (the turns themselves, plus
scheduled jobs, learning reviews and curator passes — N of them are safe),
`scheduler` (a stateless enqueuer of two periodic tasks) and `cloudflared`. Only
`agent` talks to Telegram, and `worker` needs the same `.env` it does: whichever
process runs a turn is the one that sends that turn's buttons and files.

**Webhook mode.** `telegram_mode: webhook` in `data/config.yaml` registers a URL
with Telegram at boot and the process blocks on the web server instead of
long-polling, so `--scale agent=N` becomes possible (only one process may
long-poll a bot token; any number may receive webhooks). It needs the web channel
on, a public `web_public_origin`, `CH_TELEGRAM_WEBHOOK_SECRET` in `.env` — and
`CH_REDIS_URL`, or each instance runs its own cron clock. Polling stays the
default and is what a laptop should use. Setup and the checks are in
`docs/deploy.md`, "5b. Optional: webhook mode".

**Multi-user.** The first person to DM the bot owns the deploy; anyone the owner
admits — from **Settings → Users** in the browser, or by sitting in `allowed_users`
in the config — gets their own `users` row, their own threads,
uploads and search, and **their own workspace** — wiki, `CLAUDE.md`, seeded
skills, `inbox/`/`outbox/`, and their own CLI transcript store — under
`data/users/<id>/`. The owner's tree stays exactly where it is (`data/`), so
nothing moves on an existing volume. Their turn's Read/Write/Edit/Glob/Grep are
confined to that tree; they get **no** `Bash`, `Agent` or `Task` unless the
owner grants them a shell. Each user has **their own skills library**: the
bundled skills are seeded into their tree, `skill_admin` on their private and
web lanes administers it, and **Customize → Skills** shows each person their own
library and lets them edit it — nobody sees anyone else's. Whether their
conversations also *teach* the agent is a second grant, **Learning**: on, their
turns are observed, the review fork runs over their own sessions in their own
tree, and the weekly curator sweeps their library and wiki too. Both are set
**per person** in **Settings → Users** (a **Bash** and a **Learning** switch on
each row, no restart — the person's next turn picks the change up, and a
conversation whose shell moved starts a fresh session and says so). Someone you
have not switched follows the deploy defaults, `users_shell_enabled` and
`users_learning_enabled` in `config.yaml`, both **false**: a shell reaches every
user's files and credentials in the one container, and every learning fork runs
on the owner's Claude account for somebody else's traffic, which nothing meters
per user yet. A grant needs a `users` row, so an id that is only in
`allowed_users` can be switched after their first message. The switches are no
boundary in a **group chat**: there anyone allowed to talk to the bot runs on
the owner's lane, with the owner's tools, the shell included. **Connectors are
per person** — each user enables their own from the shared catalog and signs in
with their own account and keys — while what *exists* stays the owner's,
deploy-wide: adding a catalog entry, and installing or removing a plugin clone,
which is code every turn in the container can load. In a Telegram group nobody,
the owner included, gets `skill_admin`, `session_search` or `cost_report`: the
group's library, recall and spend are the deploy owner's, and a tool that
appeared or vanished depending on who spoke last would break the prompt cache
mid-session. One container is one uid, so this is isolation against accident
and a well-behaved model, not a boundary between people who distrust each other
— grant somebody a shell only if you would trust them with everyone else's
files.

**One wiki namespace is shared.** `data/agent_home/wiki/shared/` — inside the
owner's wiki — is the one place every admitted user reads. Only the owner writes
it (their turns, and their curator's lint pass); everyone else gets it as a
read-only tree, as a short read-only section of their prompt's wiki index, and in
their `memory_search` results, and a `Write` there is denied with a message
saying why. Use it for what the household has in common — who's who, the flat,
the car, the recurring plans — so the second person's agent knows them without
either of you writing them twice: put the page under
`data/agent_home/wiki/shared/` and list it in `shared/index.md`, which is the
file everyone's prompt actually sees. Private facts stay in `user/`,
`projects/`, `topics/`, `env/`, which nobody else can read.

**A turn is a row and a task.** A message from either channel inserts a row in
the `turns` table and enqueues it; a worker claims the row and runs the Claude
Agent SDK loop. Three things follow, and they are the point of it:
**restarting `agent` mid-turn no longer kills the turn** (the worker finishes,
the events and the reply are in the database — only the Telegram message is not
re-sent), two agent instances can never start two turns on one lane (a unique
partial index in PostgreSQL is the lock), and a browser that reloads mid-turn
catches up from whichever instance answers it. The one cost the UI shows: between
sending and the worker's claim there is no `turn.start`, so the spinner opens a
little later than it used to.

**On a laptop you can run one process instead.** Drop `worker` and `scheduler`
and leave `CH_REDIS_URL` unset: the queue becomes an in-process one and
everything — turns included — runs inside the agent, which is also how the whole
test suite runs.
`CH_REDIS_URL` is the only switch — there is no `queue_mode` setting, and a
`queue_mode:` key in `config.yaml` is refused. With it set, `cron_manage
run_now` fires on the next **60-second** cron tick rather than after
`cron_tick_seconds` (30 s); that is the one user-visible cost of the split.
Four `config.yaml` knobs shape the turn table, and the defaults are fine unless
you have a reason: `turn_lease_seconds` (60 — the dead-man's switch on a running
turn), `turn_queue_timeout_seconds` (300 — how long a queued turn may hold a
lane), `turn_snapshot_ttl_seconds` (3600) and `turn_retention_days` (30).

Sessions, threads, turns, scheduled jobs, costs, the extension registry, the
encrypted extension secrets and the catalog drafts live in PostgreSQL — a `db` service
compose starts, with its data in the `pgdata` volume. Uploads and files the
agent produces live in a MinIO bucket when `blob_backend: s3` (the default is
`local`, the on-disk tree under `./data`), and either way the agent's own
`Read`/`Glob` still find them at `inbox/<lane>/` and `outbox/<lane>/`. A usable
backup is therefore three things — the dump, the bucket and the volume:

```bash
docker compose exec db pg_dump -U hermes hermes > hermes.sql
# + mc mirror of the bucket, + the ./data archive; docs/deploy.md has the lines
```

A snapshot of `./data` alone no longer carries the agent's history. Skills and
the wiki are still in `./data`. Memory is a wiki at
`data/agent_home/wiki/` — the model owns every byte; code only seeds the tree
and reads it. Only `wiki/index.md`, a snapshot taken at session start, reaches
the prompt; the rest is read on demand with `Read`/`Grep`. Pages live under
`user/ projects/ topics/ env/`; `raw/` holds fetched sources verbatim and is
immutable — the model can add to it but never edit or overwrite what's there.
Past the index the agent finds pages with `memory_search`: a full-text search
over its own wiki (plus the shared namespace) that also returns the pages a hit
links to and the pages that link back, so a note outside the 8000-character
index is still reachable by what it says. It reads a PostgreSQL table the agent
re-reads from the files after every turn, every review pass, every lint and every
restart — the markdown stays the only source of truth, and dropping that table
costs nothing but a restart. Turn it off with
`memory_search_enabled: false` in `config.yaml` if you would rather not pay for
the tool's schema on every call; flipping it either way removes or adds a tool,
so every conversation starts a fresh session at the next restart.
Skills are standard `SKILL.md` directories under `data/agent_home/.claude/skills/`,
readable and editable from the web UI's **Customize → Skills**.

The container runs as an unprivileged user (uid 1000). On a Linux host make
the state directory writable for it: `sudo chown -R 1000:1000 ./data`
(not needed on Docker Desktop for Windows/macOS).

## Threads
Enable **Threaded Mode** for the bot in @BotFather (Bot Settings → Mini App). Each
topic in the private chat is an isolated agent session that lives as long as the
topic does; `/new` clears the current topic's context. New topics are auto-named
from their first message. Each scheduled job opens its own topic and it is closed
when the job is deleted.

## Voice
Set `OPENAI_API_KEY` in `.env` and voice notes turn on. Send the bot a voice
message: it transcribes it (OpenAI speech-to-text), runs the turn as usual, and
replies with **both** the text answer and a synthesized voice message. With no
key set, a voice note just gets a short "voice isn't set up" reply — nothing else
changes. Models and the TTS voice are configurable in `config.yaml` (`stt_model`,
`tts_model`, `tts_voice`); set `voice_reply_enabled: false` to transcribe on the
way in but answer with text only. The key is dropped from the container
environment at startup, so the agent's own Bash can never read it.

## Photos and documents
Send the bot a photo or a file and it reaches the turn: images are shown to the
model directly, documents are handed over by path so the agent can open them with
its own tools. Add a caption to say what you want done with it; without one the
turn still runs. Files are saved under `CH_HOME/agent_home/inbox/<lane>/` with a
sanitized name and are never pruned, so they stay available to later turns and
are included in a backup of the data directory.

Limits are the Bot API's: anything over 20MB is refused up front with a message
saying so, and a failed download says so too rather than going quiet. In a group
where the bot is not addressed, an attachment costs nothing — it is not
downloaded, and the bot does not even start typing. An album arrives as one
update per item, so each file becomes its own turn.

## Questions with buttons
When a wrong guess would waste real work, the agent can ask you to pick from a
short list instead of guessing. The question arrives as a message with buttons
plus **✖ Cancel**; tapping one answers it and the buttons disappear. A typed
reply will not reach the agent until the question finishes, so tap or cancel —
and if you ignore it, the agent proceeds on its own judgement after a timeout
(`ask_user_timeout_seconds`, 120s by default) and tells you what it assumed.
Questions only appear in private chats.

## Choosing the model
Each chat can run its own Claude model and reasoning effort. In Telegram, `/model`
shows what this chat runs and a keyboard of the offered models (with their price
per million tokens), the effort levels and **Clear**; `/model default` sets your
own default for new chats, with a button to use it in the current one as well. In
the web UI the same choice is a pill in the composer, your default is under the
user menu, and a routine's form carries a model of its own. Every answer shows
which model and effort actually produced it.

Switching keeps the conversation: the next reply re-reads the chat's history into
the new model's cache once (the picker says roughly what that costs), and nothing
is lost. A changed default applies to new chats and after `/new` — a running chat
keeps its model until you switch it. In a group or forum topic only the owner may
change the model. The offer is `models:` in `config.yaml` (`model` is the default
and always offered); a model is added there only after `scripts/model_matrix.py`
has proved it on the pinned CLI — see `docs/deploy.md`, "Offering a model".

## Web UI (CladeX)
The same agent in a browser: every Telegram lane and every web thread in one
sidebar, turns streamed step by step (text, thinking, tool calls and their
results) under day rules, files in and out, dictation, a context-window meter
on the composer, `/` for the CLI's slash commands and the bundled skills and
plugins, the **Calendar** (your routines' fires and runs and, for the owner, the
content plan — below; the routines themselves are its **Tasks** tab), the
**Memory** page, and
**Customize**, in three tabs. The user menu in the sidebar footer holds the language, the theme
and **Settings**, which is where the owner manages the secrets extensions run with and who
else may talk to the agent.

**Memory** draws everything the agent has written down about you as a graph: a
glowing centre, one node per section of its wiki, and every page hanging off the
section it lives in, with a thin line wherever one page links to another. Click a
page to read it; follow its links straight from the text. It is read-only on
purpose — the wiki is the agent's own notebook and it owns every byte of it — and
it is the owner's alone, since it is where the agent records who you are. A page
the agent wrote but forgot to add to its index is drawn with a small marker
rather than hidden, and so is a link that points at a page that does not exist.

**Connectors** is a curated list of remote MCP servers (Notion, GitHub, Figma,
Slack, Linear, Asana, Atlassian, HubSpot, Intercom, Canva, Amplitude, Hostinger)
connected in one click — OAuth signs in through the provider's own
page, a token-based one asks for the token, and "Add custom connector" takes any
MCP server by URL. **Plugins** is the same for Claude Code plugins: install one
from the catalog, see what it actually adds (its commands, skills and agents,
read from the clone, and whether it brings hooks or its own MCP server), and the
`repo@ref` link goes to the exact revision that was fetched; "Add plugin" takes a
link to a plugin in a repository and queues it for your own approval. **Skills** is the
agent's procedural memory made visible — every skill with its source, how often
it has been used and when it last changed; open one to read its `SKILL.md`, edit
it in place, pin one so the curator leaves it alone, or archive one. A new skill
comes either from a chat that opens with the request written for you — the agent
follows its own authoring checklist and saves the result — or from the plain form,
when the text already exists. Nothing here deletes a skill, and an edit carries the version it was read
at, so a save that would overwrite something the agent learned while you were
typing says so instead of winning silently. Skills is the one tab that is
everyone's: each signed-in user sees and edits **their own** library, and a
skill in somebody else's does not exist for them (a 404, not a 403). On
Connectors and Plugins everyone enables, disables and signs in to their own;
**Add custom connector** and a plugin's **Install** and **Remove** are the
owner's, because those change what exists for the whole deployment. Connecting or installing
from the browser goes through the same code chat does, so the catalog stays the
only source of what actually runs.
The
interface is in English by default; the user menu at the bottom of the sidebar
switches it to Russian, sets the theme and signs out. Telegram
keeps working unchanged — the two channels share one session per lane, so a
conversation started on the phone continues in the browser.

The same page is also a **Telegram Mini App**: with the web UI public, the bot's
menu button opens it inside Telegram, where it signs the visitor in from the
`initData` Telegram hands the page — no Login Widget, no second account. The
button is registered on every boot from `web_public_origin` (`setChatMenuButton`;
`web_menu_button: false` leaves it alone).

It is **off by default**. To turn it on:

1. `web_enabled: true` in `data/config.yaml` (the other `web_*` knobs — port,
   `web_public_origin`, upload cap, cookie lifetime — are in
   `config.yaml.example`).
2. In `.env`, add:
   - `CH_WEB_SECRET` — any long random string; it signs the `cladex_session`
     cookie. Rotating it logs everyone out. The process refuses to start with
     `web_enabled: true` and no secret.
   - `CH_TELEGRAM_WEBHOOK_SECRET` — optional, and only with
     `telegram_mode: webhook`: the shared secret Telegram proves itself with, in
     the URL path and in every delivery's `X-Telegram-Bot-Api-Secret-Token`
     header (`openssl rand -hex 32`).
   - `TUNNEL_TOKEN` — from the Cloudflare Tunnel you create for the hostname you
     want. The `cloudflared` sidecar in `docker-compose.yml` dials out with it;
     route the tunnel's hostname to `http://agent:8080`. **No port is published
     on the host** — all connectivity stays outbound. Without a Cloudflare
     account, `docker-compose.caddy.yml` puts Caddy on 80/443 with a Let's
     Encrypt certificate for `CLADEX_HOST` instead (docs/deploy.md, 5a).
3. In @BotFather, `/setdomain` → your hostname. Without it Telegram's Login
   Widget refuses to render, and the widget is the only way in: there is no
   password, you sign in as your Telegram account.
4. Open the URL and log in. Ownership is **not** claimable here: the login
   screen lets in the owner and `allowed_users` only, so claim ownership first
   by DMing the bot (below) — on an unclaimed deploy every web login is
   rejected, whoever finds the hostname.

Set `web_enabled: false` again and the HTTP server is never constructed; nothing
else about the process changes.

The API behind that page is a FastAPI app with one `APIRouter` per module —
threads, turns, files, routines, content, skills, memory, connectors, plugins,
drafts, auth — each handler taking its collaborators through `Depends` and its request
body as a typed model. Because the routes are typed, the app serves its own
**OpenAPI document**: browse it at `/api/docs` (the raw schema is at
`/api/openapi.json`). Both sit behind the same session cookie as everything
else, so log in first — an anonymous request gets a 401 rather than a map of the
API. Route paths, wire shapes, error `code` strings and operation ids are frozen
contracts; `web/src/api/client.ts` is the frontend's side of them.

### Developing the SPA
The frontend lives in `web/` (Vite + React + TypeScript + Tailwind v4); the
Dockerfile's `webbuild` stage runs `npm ci && npm run build` and copies `dist`
into the image, so there is no second thing to deploy.

    cd web
    npm ci
    npm run dev                        # http://localhost:5173, /api proxied to :8080

The bot username the Login Widget needs comes from `GET /api/config` at
runtime, so there is nothing to configure in the frontend and the image is not
built per bot.

Run the backend with `web_enabled: true` and `web_public_origin:
http://localhost:5173` in `data/config.yaml` — the origin check rejects
mutations whose `Origin` is not exactly that value, so the dev server needs its
own entry while you work locally.

The gate before every commit is `npm run lint && npm run typecheck && npm run
test && npm run build`; `npm run e2e` adds the Playwright smoke (it builds,
serves `dist` on :4173 and drives one turn against a faked `/api`, so it needs
no backend). CI runs the same sequence — `.github/workflows/web.yml`.

After editing the CladeX SVG artwork in `web/src/assets`, run
`npm run render:mark` from `web/` to regenerate the ribbon PNGs and wordmark
alpha masks at 1x/2x/3x density. Commit the generated images with the SVG source.
The masks smooth the letters at sidebar/login sizes and follow the active
theme through CSS `currentColor`.

The Telegram Login Widget only renders on the domain registered with
`/setdomain`, so `localhost` cannot log in: develop against a thread URL with a
session cookie already set, or point the dev proxy at a backend whose bot has
your tunnel hostname registered.

## Content pipeline
Three bundled skills turn research and shipped work into LinkedIn posts with
generated images: `content-radar` (a daily sweep producing a three-topic news
brief), `linkedin-post` (brief, shipped feature, or voiced idea → EN + RU draft plus
one image), and `gpt-image-2` (generation through the container's Codex login).

Two stages, split along what a scheduled run can do. Cron composes no hermes MCP
server, so it has neither `send_document` nor `ask_user` — both scheduled jobs
therefore **propose** (claim, angle, visual archetype, into `content/briefs/`) and
stop; you approve an angle in chat, where the **draft** stage can ask questions and
attach the picture. Replying in the job's topic works with a bare number: the
first turn after a delivery gets that report quoted into it, since the ticker
posts outside any chat session. Setup, the `gh` authentication step, and the two
cron jobs to create:
[docs/linkedin-content-pipeline.md](docs/linkedin-content-pipeline.md).

Scheduled jobs may carry their own time limit — `cron_manage` with
`timeout_seconds` (30–1800) overrides `cron_timeout_seconds` for that job alone, so
a research sweep can run for ten minutes without loosening the default for
everything else.

## Calendar and content plan

The sidebar's **Calendar** (`/calendar`) shows every routine's upcoming fires and
recorded runs in month, week and agenda views (agenda on a phone), in the
device's local time; routines that fire more than hourly are one "every N"
marker per day. The routines list moved to its **Tasks** tab
(`/calendar/tasks`); the old `/routines` links redirect. A routine can now be
**On demand** — it never fires by itself, only when run by hand or woken.

With `content_enabled: true` (and `timezone:` set to your IANA zone) the owner
also gets a **content plan** on the same timeline: one item per post per channel
(LinkedIn, Telegram, X, Threads, Instagram, YouTube), with its body, media files
from the agent's home, and a time. The agent keeps the plan with the
`content_plan` tool — in your private and web chats and in your routines — but
cannot approve anything.

You can approve just the **themes** ("Approve theme" on an idea, or "Approve
week's themes" in the toolbar). Then, `content_draft_lead_hours` (24 by default)
before each post's time, the server wakes a **drafter routine**. This is an
on-demand routine made from the "Content drafter" template, and it runs the
bundled `content-draft` skill. It writes one post per run, text plus images or a
video, using your own skills where they exist. It sends the draft to you in
Telegram as a card in a "Контент" topic, with **Одобрить**, **Переделать** and
**Открыть в календаре** buttons. Reply to a card with a comment to have it
redone (at most 3 times). A post is scheduled only when you approve the
generated content, either on the card or in the calendar. Approval checks that
the post has text and the media its format needs, and binds the text and a hash
of every media file.

At the item's time the server wakes a **publisher routine** (made from the
"Content publisher" template and the bundled `content-publish` skill). It claims
each due item, publishes it with your Instagram/YouTube scripts or connector,
and records the post's URL or the error. Nothing is re-posted automatically and
nothing goes out more than two hours late. A failed or stuck item waits for you
to choose "re-post" or "it was published". Setup, Threaded Mode for the cards,
the guarantees and the troubleshooting table are in
[docs/content-calendar.md](docs/content-calendar.md).

## Develop
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
make test-stack                    # PostgreSQL 55432 + Redis 56379 + MinIO 59000
make gate                          # ruff + import-linter + the whole suite

`make gate` is what CI runs before a deploy: `make lint`
(`ruff check src tests` and `lint-imports`, the architecture contracts) followed
by `make test` (`pytest -q -p no:cacheprovider`). The suite talks to a real
PostgreSQL: each pytest process creates its own `hermes_test_*` database on that
server, migrates it with Alembic and drops it at the end. Point it elsewhere with
`CH_TEST_DATABASE_URL`.

`make test-db` (PostgreSQL alone) is all the suite strictly needs — it runs in
inline queue mode and on the local blob store. The handful of tests that want a
real Redis or MinIO ask for a fixture and **skip** with a `make test-stack` hint
when it is not up, so a laptop with only the database still gets a green run.
Override the endpoints with `CH_TEST_REDIS_URL` and `CH_TEST_S3_ENDPOINT`.

### Module layout

`src/claude_hermes/` is four layers, and import-linter enforces the edges:

```
core/          config, workspace, session keys, naming, meta, contracts, events,
               snapshots, auth, db (engine, Alembic, registry), tasks (the
               queue port), redis, crypto, logging, logsafe, exceptions,
               app_context — imports no module
integrations/  claude_sdk (options, guards), openai (voice), s3 (MinIO)
               — one adapter per external SDK
modules/<m>/   identity sessions turns files scheduler extensions memory
               learning telegram content — each domain/ application/ infrastructure/
               presentation/ plus its own README.md
http/          the FastAPI assembly (app.py) and the WebDeps bundle every
               module router reads (deps.py), plus its own README.md
workers/       the task registry and the four task bodies, the curator pass and
               its inline loop, and taskiq_app.py — the one file that imports
               taskiq (lazily), plus its own README.md
main.py        build_app() -> AppContext — the process root
```

`main.py`, `http/` and `workers/` are the composition layer: three roots that may
import each other and everything below; `modules/` may import `integrations/`
and `core/`; `integrations/` only `core/`; `core/` imports no module. That is one
import-linter `layers` contract (`main : http : workers` over `modules` over
`integrations` over `core`), and fourteen more pin the inside: the ten modules
are independent of each other, each one's `domain/ application/ infrastructure/
presentation/` are layered, domain layers import no framework, application
layers are framework-free and presentation layers hold no persistence or
transport. There is no other importable path: the pre-modular modules and
packages (`runner`, `tools`, `db`, `cron/`, `gateway/`, `extensions/`,
`learning/`, `web/`, …) were deleted in Step 5 of the migration.

A module never imports another module. What it needs from one it declares as a
`typing.Protocol` in its own `application/ports.py`, and `build_app()` hands it
something that satisfies it — start from that module's `README.md`, which lists
the ports it consumes and provides, the routes and tools it owns, the bundle
attributes its providers read, its invariants and its remaining debts.

Tests for a module live in `tests/modules/<module>/`, with file names prefixed
`test_<module>_` — `tests/` has no `__init__.py`, so two files with the same
basename collide.

The design notes are `docs/llm-wiki/` (start at `index.md`); the architecture
decision behind this layout is `docs/llm-wiki/decisions/modules-and-ports.md`.

## Extensions

The agent can install MCP servers and Claude Code plugins for itself, from a curated catalog in
`catalog/`. Ask it in chat: "what extensions do you have?", "install the fetch server".

Only catalog entries are installable. The registry (the `extensions` table in PostgreSQL) records *that* an
extension is installed; *what it runs* comes from the reviewed manifest in `catalog/`, so no chat
message can make the agent execute an arbitrary command. Adding an entry means opening a PR
against `catalog/`.

**Settings → Users** is where the owner admits somebody and takes access back. Adding takes
a Telegram user id — a bot can turn an id into a name but never a @username into an id — and
writes the row the agent would have written on that person's first message, so they are let in
on both channels at once with no restart. Revoking sets `disabled_at`: it bites on the next
request they make, including a browser tab already open, and it is reversible — their threads,
files and workspace stay where they are. The owner cannot revoke their own access, because
every administrative action is gated on being the owner.

Secrets go in with `/secret NAME=value` — the command is handled by the gateway, so the key never
reaches the model, is never written to the message history, and the message carrying it is
deleted. With the web UI on there is a second door, **the user menu → Settings → Secrets**: it
lists every stored variable and every one a connector declares but nobody has filled in yet, and
sets or forgets one. Either way a key is Fernet-sealed in the database, never in `.env` and never
readable back — the list shows names and when they changed, and nothing else. Both are the
owner's alone.

Installing, enabling, disabling or removing an extension restarts the current thread's context:
the tool list is part of the prompt cache, and it cannot change mid-session.

With the web UI on, the same catalog is a page: **Customize → Connectors** lists every MCP entry
with its state, connects one with a token or an OAuth sign-in (a sign-in can only happen in a
browser — chat will tell you so), and disconnects it again, forgetting its credentials. A custom
connector added there is a manifest in `data/user-catalog/`, written by your own logged-in request
and validated exactly like a catalog entry. **Customize → Plugins** does the install, enable,
disable and remove for `catalog/plugins/` entries. Three ship: `marketing`, `finance` and `design`
from Anthropic's knowledge-work plugins, pinned to a commit. They carry no commands, agents or
hooks — nothing that runs on a turn — so installing one adds a handful of skills to what the agent
knows. Each does bundle a list of optional remote MCP servers (Slack, Figma, Notion and so on);
this system hands them no credentials, so they reach the agent signed out. Most of those vendors
are in the connectors catalog too, which is where to add them properly: connected there they get a
sign-in and tools the agent can actually call.

That last part is why **Install asks twice**. A plugin's own `.mcp.json` is read by Claude Code,
not by this catalog, so the servers it brings get no entry on the Connectors tab and nothing here
ever reviewed them. Pressing Install fetches the plugin at its pinned revision and shows you what
came back — its skills, commands and agents, whether it runs hooks, and every server it carries
with the URL it would talk to — and installs nothing. The button under that screen carries a token
computed from those exact bytes, so what you read is what gets registered. Until you press it the
files sit on disk unused; Cancel deletes them. One kind of plugin never reaches that screen at all:
if its `.mcp.json` would **run a command** inside the container — `npx …`, `bun run …`, which real
plugins in the official marketplace do — the fetch is refused and the files are removed, because
that is an argv nobody reviewed arriving by a route built for URLs.

**Add plugin** is a form, but it writes nothing into the catalog. Paste the link to a plugin in its
repository — `…/tree/<commit>/<directory>`, which is the page you were already looking at — and the
fields fill in from it, ref included, because a 40-character commit sha typed by hand is how a pin
ends up wrong. A branch is refused: it moves after you have read the code. Submitting queues the
manifest for review in the section above the grid, and approving it is a separate act with the text
in front of you; only then does the entry exist, and installing it is the two-step above. Two gates
for what looks like one job, because a plugin is code that loads inside the agent.

### Adding a catalog entry

`catalog/` is mounted into the container **read-only**. That is the security boundary, not a
deployment detail: the agent runs as the owner of `/data` and has Bash, so any manifest directory
it could write to would be a manifest directory it could forge — and a manifest is an executable
command (`npx` + `args`) or code to clone (`repo`@`ref`).

So the work is split. The agent may **propose**; only you may **approve**.

Send the agent a URL — a git repo, a vendor's docs page. It fetches it, works out the manifest, and
queues it with `ext_manage(action="draft")`. The draft goes to `data/catalog-drafts/` and is inert:
`catalog.load_all()` never reads it, so nothing can install from it. Then, on the host:

```bash
python scripts/approve_draft.py                 # what is waiting
python scripts/approve_draft.py <name>          # shows command/args, asks y/N, copies into catalog/
```

Or, with the web UI on, without leaving the browser: a queued draft appears as **Pending review**
at the top of the tab it belongs to — an MCP draft on Connectors, a plugin draft on Plugins.
Open it and you get the same screen the script prints, character for character, in a monospace
block. Approve promotes exactly the bytes you just read: the button carries a token computed from
them, so if the agent rewrites the draft between your reading it and your clicking, the approval is
refused and you read it again. Discard throws it away. It is a page you have to be signed in as the
owner to see, and it is not a tool — the agent cannot reach it, so it can never approve its own
draft. Everything the paragraph below says about *what to read* applies unchanged; the browser only
saves you the ssh.

Read the `command` and `args` (or `repo` and `ref`) before you type `y`. The agent extracted them
from a page someone else wrote — a README that says `args: ["-y", "@evil/pkg"]` is a prompt
injection, and the manifest is where it would land. The validator refuses a shell string, a command
outside `npx`/`uvx`/`python`, a non-https URL, an undeclared `${VAR}` and an unpinned plugin ref, so
what reaches you is already narrow. What it cannot judge is whether the package is trustworthy.
That is the one thing a human is there for.

No restart is needed: `catalog/` is a live bind mount and the catalog is re-read on every turn.
(A rebuild is only for changes to `src/`.)

Writing an entry by hand is the same thing without the drafting step: add the YAML to `catalog/mcp/`
or `catalog/plugins/`, then run `pytest tests/test_catalog.py` — it parses the real catalog.

## License

[MIT](LICENSE)

# LinkedIn content pipeline

Three ways in, one way out. The skills live in `skills/`; the two scheduled jobs
are created by the agent itself, from chat, using the messages at the bottom of
this page.

    SCHEDULED (text only, propose stage)      IN CHAT (draft stage)

    content-radar   daily 10:00   ─┐
      3 news topics                │
                                   ├─> you pick one ─> linkedin-post
    linkedin-post   Mon 09:00     ─┤                     EN + RU draft
      1-3 shipped-work candidates  │                     + one image
                                   │                     + send_document
    a voiced idea   any time      ─┘

## Why the brief and the post are separate

`cron/executor.py` composes only extension MCP servers — the hermes server is not
built for a scheduled run, so `send_document` does not exist there, and
`cron/ticker.py` delivers a text string and nothing else. A scheduled job therefore
cannot hand over a PNG.

`ask_user` is missing for the same reason, so a scheduled run cannot ask which angle
to take either.

That is why both jobs stop at `linkedin-post`'s **propose** stage: claim, evidence,
angle, visual archetype — no post prose. The user approves an angle in chat, and the
draft plus its image are produced in a turn that can both ask and attach. A
scheduled run that handed back a finished post would have decided the angle alone
and announced an image it could not send.

Both jobs write into `content/briefs/` with the same fields (`content-radar` to
`<date>.md`, the weekly job to `<date>-shipped.md`) so `linkedin-post` reads either
without knowing which produced it.

The reply ("3") lands in the job's own topic — a chat lane that never saw the
delivered brief, because the ticker posts it directly rather than through a turn.
The runner bridges that: the first turn after a delivery gets the job's latest
report quoted into the user message (`_with_cron_context` in `runner.py`), so a
bare number is enough. On builds without the bridge the reply must quote the
brief, or the agent asks what the number means.

## One-time setup

**1. `gh` authentication.** The image ships the GitHub CLI with
`GH_CONFIG_DIR=/data/.gh`, so the login lands on the volume and survives
`up --build`. Authenticate from the host — never by typing a credential into the
chat:

    printf '%s' 'ghp_xxxxxxxxxxxx' > token.txt
    docker compose exec -T agent gh auth login --with-token < token.txt
    rm token.txt
    docker compose exec -T agent gh auth status

A fine-grained read-only PAT is enough: `repo` read for the private repositories,
nothing else. Note that the agent has Bash, so anything `gh` can do, the agent can
do — scope the credential accordingly.

**2. Rebuild for `gh`.** The CLI is a new layer in the Dockerfile:

    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

**3. Codex login for images.** `gpt-image-2` drives image generation through the
Codex CLI's ChatGPT session (`CODEX_HOME=/data/.codex`). If `gen.sh` starts exiting
5 or 7, that login is what expired:

    docker compose exec -it agent codex login

**4. Verify the skills seeded.** Seeding is copy-if-missing at startup, so the four
directories appear after the first restart following this change:

    docker compose exec -T agent ls /data/agent_home/.claude/skills

Expect `linkedin-post`, `content-radar` and `gpt-image-2` among them.

## A note on the vendored `gpt-image-2`

`skills/gpt-image-2/` is a vendored copy of the third-party skill (MIT), carrying
two container-specific fixes the upstream version does not have: it honours
`CODEX_HOME` instead of hard-coding `$HOME/.codex` (without which `gen.sh` exits 6
in this image), and it reads the `generated_images/` directory that codex-cli 0.130+
writes to. It previously existed only under `data/`, which is gitignored — a rebuild
from a fresh clone would have shipped no image generation at all.

Because seeding never overwrites, editing `skills/gpt-image-2/` does not change a
container that already has the skill. Patch `/data/agent_home/.claude/skills/` for a
live change, and keep the two in sync deliberately.

### Pushing skill edits to a live deployment

Seed-once is by design — the live agent edits its own skills through the learning
loop, and a blind overwrite would erase those self-corrections. So a repo-side
skill improvement reaches a running container only by an explicit sync. From the
repo root, per skill directory you actually changed:

    scp -r skills/gpt-image-2/references skills/gpt-image-2/SKILL.md \
      deploy@<host>:~/claude-hermes/data/agent_home/.claude/skills/gpt-image-2/
    scp skills/linkedin-post/references/visuals.md \
      deploy@<host>:~/claude-hermes/data/agent_home/.claude/skills/linkedin-post/references/

Before overwriting, diff the live copy against `git show` of the last seeded
version — a difference the repo does not explain is a learning-loop edit worth
folding back into the repo, not clobbering. No restart needed: skills are read
from disk at use time.

## Creating the two scheduled jobs

Both are created by asking the agent in chat, so each one gets its own Telegram
topic and its reports never interrupt a conversation. Send these as-is.

**Times are UTC.** The ticker evaluates cron expressions against
`datetime.now(timezone.utc)`, and this host runs at UTC+5 — so 10:00 local is
`0 5 * * *`. Shift both expressions if the timezone changes.

### Job 1 — the morning brief (10:00 local, daily)

> Создай cron job с именем `content-radar`, расписание `0 5 * * *`,
> `timeout_seconds` = 600, и вот этим промптом:
>
> Load the content-radar skill and run a full sweep of the last 24 hours. Follow its
> procedure exactly: first read the newest file in content/briefs/ and the
> covered-topics list in wiki/content/linkedin.md so you do not repeat a topic; sweep
> every lane in its references/sources.md; WebFetch the primary source for each
> candidate before shortlisting it; then cut to three topics that differ in kind.
> Write content/briefs/<today>.md, then reply with the Telegram-sized version: three
> numbered blocks of about 60 words, each ending with its angle and its link, and a
> closing line telling me to reply with a number to get the post. A quiet day gets
> two topics and a sentence saying so — do not pad to three. You are unattended: ask
> nothing, generate no image, text only.

`timeout_seconds` is not optional here. The default is 180s (`cron_timeout_seconds`),
and a six-lane sweep with per-candidate `WebFetch` does not finish inside it — the
job would die mid-search and deliver a `⚠️` line instead of a brief.

### Job 2 — the shipped-work proposal (Monday 09:00 local, weekly)

> Создай cron job с именем `shipped-radar`, расписание `0 4 * * 1`,
> `timeout_seconds` = 600, и вот этим промптом:
>
> Load the linkedin-post skill and run its propose stage in shipped-work mode for
> the last 7 days. Run bash .claude/skills/linkedin-post/scripts/ship-log.sh
> --days 7, then follow references/shipped.md: rank what landed (merged pull
> requests first, then clusters of fix commits that solved one problem, then a
> single feat with a non-obvious idea), read the pull request body or commit message
> for the real reasoning behind the strongest one, and apply its confidentiality
> rules — never name a private repository, an internal ticket id, or an unreleased
> product surface. Stop at the proposal: write content/briefs/<today>-shipped.md
> with the claim, its public evidence, the angle and the visual archetype, and reply
> with the same content phone-sized plus one line on how to act on it. One to three
> candidates, strongest first; a maintenance-only week gets one line saying so
> rather than a padded list. Do not draft the post itself and do not generate an
> image — you are unattended, ask nothing.

09:00 rather than 10:00 on purpose: the ticker runs due jobs **sequentially** in one
tick (`for job in await self._jobs.due(...)`), so two jobs sharing a fire time make
the second wait for the first — up to its full 600s. An hour apart also puts your own
week in front of the news, which is the better reading order.

Then, in chat: "делаем 1" / "давай по первому" — `linkedin-post` picks the proposal up
from `content/briefs/<date>-shipped.md` and runs the draft stage: EN + RU, the image,
delivery, and the log entry.

### The third way in — a voiced idea

No job needed. Send a voice note or a line of text with the idea; `linkedin-post`
detects idea mode, brainstorms three distinct angles, asks which one with
`ask_user`, and runs the full pipeline including the image.

## Checking on the jobs

    /cron            # if the gateway intercepts it; otherwise just ask
    "покажи cron jobs"        -> cron_manage(action="list"), shows the per-job limit
    "запусти content-radar"   -> cron_manage(action="run_now")
    "запусти shipped-radar"   -> same; the weekly schedule is not disturbed —
                                 the ticker recomputes the next Monday from the
                                 cron expression after the early run

`run_now` queues the job for the next 30-second tick; it does not run it inline.

## Where the artifacts live

| Path | What |
|---|---|
| `agent_home/content/briefs/<date>.md` | daily news brief, with links |
| `agent_home/content/briefs/<date>-shipped.md` | weekly shipped-work proposal |
| `agent_home/content/posts/<date>-<slug>.md` | the drafts as delivered |
| `agent_home/content/img/<slug>-01.png` | generated images |
| `agent_home/wiki/content/linkedin.md` | voice corrections + covered topics |

All of it sits under `CH_HOME`, so `tar ./data` remains a complete backup.

## Failure modes worth recognizing

| Symptom | Cause |
|---|---|
| `⚠️ Job 'content-radar' hit the 180s time limit` | job created without `timeout_seconds=600` |
| `ship-log.sh` exits 4 | `gh` never authenticated, or the credential was revoked |
| `gen.sh` exits 5 or 7 | Codex login expired, or image generation is out of quota |
| `gen.sh` exits 6 | a `gen.sh` that ignores `CODEX_HOME` — the unvendored upstream copy |
| Brief repeats yesterday's topic | `wiki/content/linkedin.md` missing, so step 1 had nothing to read |
| Job fires at the wrong hour | cron expression written in local time instead of UTC |
| Reply "3" gets "уточните, что имеется в виду" | build predates the cron→topic context bridge (`_with_cron_context` in `runner.py`) — rebuild the container |

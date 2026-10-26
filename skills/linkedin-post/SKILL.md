---
name: linkedin-post
description: Write a LinkedIn post with generated images.
---

# LinkedIn Post

Turns one of three inputs — a topic off a brief, a feature the user actually
shipped, or a raw idea they voiced — into a publish-ready LinkedIn post plus its
image, drafted in English and Russian. It has two stopping points: **propose**
(the claim, the angle, the visual — no prose) and **draft** (the full thing). The
voice rules live in `references/voice.md`, the image system in
`references/visuals.md`, and the GitHub mining in `references/shipped.md`.

**Which stopping point:** an unattended run always stops at propose — a scheduled
job has nobody to approve the angle and no way to deliver the picture, so drafting
there spends the user's decision for him. A chat turn drafts, unless asked only for
ideas.

## When to Use

- The user picks a topic from a brief ("делаем пост по теме 2").
- The user asks for a post about their own work, a shipped feature, or a release.
- The user dumps a raw idea (often a voice note) and wants a post out of it.
- Any explicit "сделай пост в LinkedIn" / "write me a LinkedIn post".
- The weekly scheduled job asks what the last seven days of commits are worth
  posting about — propose only, see `references/shipped.md`.
- NOT for the daily AI-news sweep — that is `content-radar`, which produces the
  other kind of brief this skill consumes.

## Prerequisites

- The `gpt-image-2` skill at `.claude/skills/gpt-image-2/` with `codex` logged in.
  Read its `SKILL.md` before the image step; this skill only decides *what* to
  draw, that one knows *how*.
- `send_document` (`mcp__hermes__send_document`) — a chat-scope tool. **Scheduled
  runs do not have it**: cron composes no hermes MCP server, so a cron job can
  produce text but cannot deliver a PNG. See Pitfalls.
- For the shipped-work mode: `gh` authenticated in the container
  (`gh auth status` exits 0).
- `wiki/content/linkedin.md` — the voice corrections and the list of already
  covered topics. Create it from `references/voice.md`'s closing template if it
  does not exist yet.

## How to Run

The whole pipeline, condensed (cwd is always `agent_home`):

    # 1. raw material — shipped-work mode only
    bash .claude/skills/linkedin-post/scripts/ship-log.sh --days 7

    # 2. the image, once the draft exists
    bash .claude/skills/gpt-image-2/scripts/gen.sh \
      --prompt "<prompt built from references/visuals.md>" \
      --out content/img/<slug>-01.png

    # 3. delivery
    mcp__hermes__send_document(path="content/img/<slug>-01.png")

## Quick Reference

| Need | Where |
|---|---|
| How the post must read | `references/voice.md` |
| What to draw, and the prompt recipe | `references/visuals.md` |
| What the user shipped on GitHub | `bash .claude/skills/linkedin-post/scripts/ship-log.sh --days 7` |
| Generate the image | `bash .claude/skills/gpt-image-2/scripts/gen.sh --prompt … --out …` |
| Deliver it | `mcp__hermes__send_document(path=…, caption=…)` |
| Topics already covered, voice corrections | `wiki/content/linkedin.md` |
| Today's news brief | `content/briefs/<YYYY-MM-DD>.md` |
| The weekly shipped-work proposal | `content/briefs/<YYYY-MM-DD>-shipped.md` |
| Where drafts and images go | `content/posts/`, `content/img/` |

## Procedure

Steps 1–3 are the **propose** stage and run in every mode. An unattended run stops
after step 3b and delivers the proposal; a chat turn continues into step 4.

1. **Name the mode and gather the raw material.** Brief mode: read the dated file
   under `content/briefs/`. Shipped mode: run `ship-log.sh` and pick ONE landed
   thing — read `references/shipped.md` for how to choose. Idea mode: take the
   user's words as the seed and go to step 2. Done when you can state the post's
   claim in one sentence without hedging.

2. **In idea mode only, brainstorm before drafting.** Produce three distinct
   angles on the seed — not three rewordings — each with its own hook and its own
   "so what". Offer them with `ask_user` and wait for the pick. Done when one
   angle is chosen; never draft all three. (Unattended: skip `ask_user` — it does
   not exist in cron scope — and carry all three angles into the proposal.)

3. **Check it is not a repeat.** Read the covered-topics list in
   `wiki/content/linkedin.md`. If the claim overlaps a post from the last 60
   days, say so and offer the closest unused angle instead of writing a
   near-duplicate. Done when the claim is either new or explicitly approved as a
   follow-up.

3b. **Propose, and stop here if unattended.** Write the proposal to
   `content/briefs/<YYYY-MM-DD>-shipped.md` in the shape given in
   `references/shipped.md`, and reply with it: the claim, the evidence, the angle,
   the visual archetype, and one line on how to act on it. That file lives in the
   same directory `content-radar` writes to and uses the same fields on purpose —
   step 1's brief mode reads either kind without knowing which job produced it.
   Done when the proposal names a claim the user can approve or reject in one
   word. **A scheduled run ends here**: it has no `ask_user` to confirm the angle
   and no `send_document` to deliver the image, so a draft it produced would be an
   unreviewed post with a missing picture.

4. **Draft the English version.** Read `references/voice.md` first, every time —
   the voice is corrected over time and the file is the current version, your
   memory of it is not. Done when the draft passes that file's self-check list
   (hook survives the fold, one idea, real specifics, ≤1300 chars, ≤3 hashtags).

5. **Write the Russian version as a rewrite, not a translation.** Same claim,
   same structure, native phrasing. Done when the RU hook works as a hook in
   Russian rather than reading as a translated one.

6. **Design exactly one image.** Pick an archetype from `references/visuals.md`,
   then build the prompt with the recipe there. Fold the aspect ratio into the
   prompt text — `gen.sh` has no size flag. Done when you have a single concrete
   prompt string, not a description of a plan.

7. **Generate it.** Run `gen.sh` with `--out` under
   `content/img/`. Done when the command exits 0 AND the path it
   printed exists and is non-empty. On a non-zero exit, read the exit code
   against `gpt-image-2`'s Pitfalls and name the failing layer — do not reword
   the prompt and retry blindly.

8. **Deliver both texts and the file.** Post the EN and RU drafts as the reply
   (they are meant to be copy-pasted, so no commentary between the lines), then
   send the PNG with `send_document`. Done when `send_document` replies
   `Sent … to the chat` — a file that only exists on disk was not delivered.

9. **Log it.** Write the final draft to `content/posts/<YYYY-MM-DD>-<slug>.md`
   and append one line to the covered-topics list in `wiki/content/linkedin.md`.
   Done when a future run of step 3 would find this topic.

## Pitfalls

- **A cron run has neither `send_document` nor `ask_user`.** `cron/executor.py`
  composes only extension MCP servers, so a scheduled run cannot deliver a file and
  cannot ask which angle to take. That is why it stops at step 3b. If a scheduled
  run ever produces a finished draft, it has skipped the approval the whole split
  exists for — and it will claim an image it never sent.
- **Drafting on a proposal request.** "Предложи идею" / a Monday job asking what is
  worth posting wants the propose stage, not 1300 characters of prose. Handing back
  a finished post is not over-delivery; it decides the angle for the user.
- **Markdown does not render on LinkedIn.** `**bold**` publishes as literal
  asterisks. Never format the draft with Markdown syntax.
- **`gh search` returns HTML-escaped titles.** A PR title comes back with `&amp;`
  in it. Unescape before quoting it in a post.
- **Translating instead of rewriting.** A literal RU translation of an EN hook
  lands flat. If the RU version has the same word order as the EN one, it is a
  translation.
- **Inventing numbers.** No metric goes into a post unless it came from
  `ship-log.sh`, a source in the brief, or the user. "10x faster" that nobody
  measured is the fastest way to make the whole post untrustworthy.
- **Announcing instead of teaching.** "Excited to share" is not a hook. What did
  you learn that the reader did not know before opening the post?
- **Stacking images.** One image per post by default. A carousel is a separate
  ask, not a default upgrade.

## Verification

A **proposal** is done when: `content/briefs/<date>-shipped.md` exists with the
claim, its evidence, the angle and the visual archetype; the claim does not
duplicate `wiki/content/linkedin.md`; and the reply says in one line how to turn it
into a post. No prose from the post itself appears anywhere.

A **draft** is done when: both language versions are in the chat as
copy-paste-ready text, `send_document` has confirmed the PNG reached the chat,
the draft is saved under `content/posts/`, and the topic is listed in
`wiki/content/linkedin.md`. If the image step failed, say which exit code it
failed with and that no picture was delivered — a text-only post is a valid
outcome, a silently missing image is not.

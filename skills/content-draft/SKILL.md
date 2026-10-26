---
name: content-draft
description: Draft one due item of the content plan, unattended.
---

# Content Draft

The procedure of the **drafter routine**: the on-demand routine the owner created
from the "Content drafter" task template and chose in the calendar. The owner
approves a THEME (the item becomes `planned`); about a day before its publish
time the server wakes this routine, and each run drafts **one** item — the text
and its media — and submits it through `mcp__hermes__content_plan`. The server
then sends the draft to the owner in Telegram as a card with «Одобрить» and
«Переделать». Nothing is published until the owner approves that card or the
item in the calendar.

This skill is the owner-sanctioned drafting stage for plan items. `linkedin-post`'s
rule that an unattended run stops at propose does not apply here: the owner
approved the theme and will approve the finished draft, so this run writes the
full post. Use the owner's skills for HOW to write and render; this skill decides
WHAT to produce and where it goes.

`claim_draft`, `submit` and `draft_failed` work **only inside the drafter
routine's own run**. From a chat, or from any other routine, the tool refuses
them. If you are reading this outside that run, stop.

You are unattended. There is nobody to ask, `ask_user` does not exist here, and a
question in your final text reaches the owner only after the run is over. Decide
from what the plan and the owner's skills tell you.

## When to Use

- The drafter routine's run ("Load the content-draft skill, claim one due item
  from the content plan and draft it").
- NOT in chat. A post drafted in chat is written with `content_plan` `create` or
  `update` and left in `review`; the owner approves it in the calendar.
- NOT for publishing: that is `content-publish`, in the publisher routine.

## Procedure

1. `content_plan(action="claim_draft")`.
   - "Nothing is due for drafting." ends the run: say so in one line and stop.
   - A refusal ("already drafting", "only inside the drafter routine") ends the
     run too: report it in one line and stop. Never claim twice in one run.
   - Otherwise the answer is the item, now yours (`drafting`): id, channel,
     format, publish time, title, the current body and media if any, the media
     folder for this attempt, and — after a «Переделать» — the line
     "owner's comment on the last draft:". That comment is the owner's
     instruction for this attempt: follow it first.
   - When the item has a `group_key`, the answer ends with "Other items of this
     idea": the same idea on other channels (an IG reel and a YT short), each with
     its status and media.
1a. Render an idea once. When a sibling is `review`, `scheduled`, `queued`,
   `publishing` or `published` and has the media your format needs (a video for
   `reel`/`short`/`video`), do not render again: copy its files into your own
   folder, keeping their names and sub-folders, and write only this channel's
   caption. Render anew only when the owner's comment on this item asks for a
   change to the video, and then say in the final text that the sibling still
   has the old one. A sibling back in `planned` was sent back: never reuse its
   files.
2. Pick how to make it, by channel and format. When the owner's skill is in
   `.claude/skills/`, read its `SKILL.md` and follow it for the writing and the
   rendering; when it is not there, produce the post directly.

   | Channel / format | Owner's skill |
   |---|---|
   | `carousel` (any channel) | `social-carousel` |
   | `reel`, `short` | `vibecode-trap-reel`, rendered with `hyperframes-local` |
   | youtube / `video` | `youtube-channel` |
   | `photo`, or an image for any post | `gpt-image-2` |
   | linkedin text | `linkedin-post`'s voice (`references/voice.md`) — the full draft, not a proposal |

3. Write every media file under the folder the claim named
   (`content/drafts/<item_id>/<attempt>/`, relative to your home — the working
   directory of this run). Never write into another attempt's folder and never
   reuse a file from one: a render left over from a timed-out run may still be
   writing there.
4. Meet the format's media rule, or the submit is refused:
   - `carousel`: 2-10 images, one per slide, in slide order (at most 10 slides);
   - `reel`, `short`, `video`: exactly one video file (mp4, mov or webm) and no
     other file — no separate cover image;
   - `photo`: exactly one image (png, jpg, jpeg, webp or gif);
   - any other format: media optional.
   The body is exactly what gets published — the caption or post text, in the
   language and voice the channel uses — and it must not be empty.
5. `content_plan(action="submit", id=<id>, body=<the post>, media=<paths, one
   per line>)`, adding `title=<title>` only when you improved it. A refusal names
   what is missing: fix that and submit again, or go to step 6.
6. When the post cannot be made — a tool is missing (no Codex login for images,
   HyperFrames not installed under `tools/`), a render failed, the media rule
   cannot be met — call `content_plan(action="draft_failed", id=<id>,
   error=<one line: what failed and why>)`. The item goes back to the queue and
   is retried in a later run; after the third failure the owner sees it in the
   calendar.
7. Never publish, never approve, never change the item's status or publish time,
   never claim a second item. One item per run: a render takes minutes and the
   run's time limit is shared by everything in it.
8. Final text: one line — `#<id> <channel>/<format> — submitted (<n> media files)`
   or `— draft_failed: <reason>`, or `nothing due`.

## Verification

- The claimed item ends the run in `review` (submitted) or back in `planned`
  (`draft_failed`): `content_plan(action="get", id=<id>)` shows it. An item left
  in `drafting` is timed out by the server and counted as a failed attempt.
- Every media path you submitted exists under this attempt's folder and passes
  the format's rule.
- Nothing was published and nothing was approved in this run.

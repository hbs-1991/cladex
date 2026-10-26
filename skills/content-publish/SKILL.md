---
name: content-publish
description: Publish the content plan's due items, unattended.
---

# Content Publish

The procedure of the **publisher routine**: the on-demand routine the owner
created from the "Content publisher" task template and chose in the calendar. The
server wakes it only when an approved item's time has come; each run takes every
due item, publishes it on its channel, and records what happened in the content
plan through `mcp__hermes__content_plan`.

`due`, `claim`, `published` and `failed` work **only inside the publisher
routine's own run**. From a chat, or from any other routine, the tool refuses
them — that is the point: an approved item is published by this routine and by
nothing else. If you are reading this outside that run, stop: the owner publishes
by approving items in the calendar, not by asking you.

You are unattended. There is nobody to ask, `ask_user` does not exist here, and a
question in your final text reaches the owner only after the run is over. Decide
from what the plan and the scripts tell you.

## When to Use

- The publisher routine's run ("Load the content-publish skill and publish every
  due item in the content plan").
- NOT in chat. To add, edit or reschedule plan items use `content_plan` with
  `list`, `get`, `create`, `update` or `set_status`; approval is the owner's.
- NOT for writing posts: `linkedin-post` and `content-radar` write drafts; this
  skill only publishes what the owner already approved.

## Procedure

1. `content_plan(action="due")`. "Nothing is due." ends the run: say so in one
   line and stop.
2. For each item, in the order listed, one at a time:
   1. `content_plan(action="claim", id=<id>)`. The answer is the item exactly as
      approved — channel, format, body, media paths — and the item is now yours
      (`publishing`). If the claim is **refused** (already taken, or a media file
      changed since approval), do not publish that item; note the refusal and go
      to the next one.
   2. Pick the publisher from the table below by channel and format. No row for
      it → `content_plan(action="failed", id=<id>, error="no publisher for
      <channel>/<format>")` and go to the next item.
   3. Before the first use of a script in this run, run it with `--help` and use
      the flags it documents. Never guess a flag: the scripts are the owner's and
      change without this skill knowing.
   4. Publish **exactly** the claimed content: the body verbatim as the caption
      or description, the title as the title where the channel has one, the
      claimed media files and no others. Media paths are relative to your home
      (the working directory of this run).
   5. One attempt. On success, find the post's public URL (the script's output,
      or the connector's answer) and call `content_plan(action="published",
      id=<id>, url=<https url>)`. On failure call `content_plan(action="failed",
      id=<id>, error=<the exact error, one or two lines>)`. Do not retry in the
      same run: a failure may have half-posted, and whether to post again is the
      owner's decision (they choose "re-post" or "it was published" in the
      calendar).
3. Never publish an item you did not claim in this run, never publish an item a
   second time, and never call `published` or `failed` for an item you did not
   claim.
4. Final text: one line per item — `#<id> <channel>/<format> — published <url>`,
   `— failed: <error>`, or `— not claimed: <reason>`. The owner reads this as the
   routine's report.

### Channel and format → publisher

| Channel / format | How |
|---|---|
| instagram / `reel` | `python tools/igpub/igpub.py` with the item's video (an `.mp4`, `.mov` or `.webm` from its media) and the body as the caption. It takes a local path and hosts the file itself, temporarily, at publish time. |
| instagram / `photo`, `carousel` | The Instagram connector: `publish_media` (one image) or `publish_carousel` (several, in the item's media order), with the body as the caption. The connector takes only public `image_url`s, so host each file first the way `igpub.py` hosts a video (uguu.se, about 3 hours): use `igpub.py`'s own hosting option if its `--help` lists one, otherwise read how `igpub.py` uploads and do the same. Host only the claimed item's files, only now. Video through the connector fails ("Media ID is not available") — a video is a `reel`. |
| youtube / any | `python tools/ytc/ytc.py` with the item's video from its media, the item's title as the video title and the body as the description. There is no custom cover. |
| anything else | `failed` with `no publisher for <channel>/<format>`. |

A missing script (`tools/igpub/igpub.py`, `tools/ytc/ytc.py`) or a connector that
is not in this run's toolset is a `failed` with that sentence as the error, never
a workaround through another channel.

## Verification

- Every item you claimed ends the run as `published` (with its URL) or `failed`
  (with the error): `content_plan(action="get", id=<id>)` shows it. An item left
  in `publishing` becomes `stuck` and waits for the owner.
- No item was published that `claim` did not hand you in this run.
- The final text lists every item from `due` with its outcome.

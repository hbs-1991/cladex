---
name: content-radar
description: Daily AI news scan into three post topics.
---

# Content Radar

Sweeps the AI / AI-agents / agentic-development space for what actually happened
in the last day, then hands back exactly three post-worthy topics with the
evidence attached. It produces a brief, never a post — `linkedin-post` consumes
the brief when the user picks a topic. Source coverage is in
`references/sources.md`.

## When to Use

- The morning scheduled job fires (its prompt names this skill).
- The user asks what is worth posting about, or asks for a fresh brief on demand.
- NOT for writing the post — that is `linkedin-post`.
- NOT for answering a factual question about one tool; that is a plain search.

## Prerequisites

- `WebSearch` and `WebFetch`. Both exist in chat and in cron scope.
- A writable `content/briefs/` under `agent_home` (create it if missing).
- `wiki/content/linkedin.md` — the covered-topics list, so the brief does not keep
  proposing what was already posted.
- A scheduled run needs a raised time limit: `cron_manage` with
  `timeout_seconds=600`. The default 180s is not enough for a real sweep and the
  job dies mid-search with nothing delivered.

## How to Run

Not a script — a research procedure. Its output is one file plus one Telegram-sized
message:

    content/briefs/<YYYY-MM-DD>.md     # the full brief, with links
    <the reply>                        # three topics, ~60 words each

## Quick Reference

| Need | Where |
|---|---|
| Which sources to sweep, in what order | `references/sources.md` |
| What was already posted | `wiki/content/linkedin.md` |
| Yesterday's brief (to avoid repeats) | `content/briefs/` — newest dated file |
| This week's shipped-work proposal | `content/briefs/*-shipped.md` — newest |
| Where the brief goes | `content/briefs/<YYYY-MM-DD>.md` |
| Turning a topic into a post | the `linkedin-post` skill |

## Procedure

1. **Establish the window and read what is already known.** The window is the last
   24 hours (48 on a Monday). Read the newest dated file in `content/briefs/`, the
   newest `*-shipped.md` there (the weekly job's proposal about the user's own
   work), and the covered-topics list in `wiki/content/linkedin.md`. Done when you
   have a list of claims that must NOT be proposed again — including the one the
   Monday job already put on the table.

2. **Sweep, breadth first.** Work `references/sources.md` in order, one search per
   lane, and collect candidates without evaluating them yet. Aim for 12–20
   candidates. Done when every lane in that file has been queried at least once —
   a brief built from one lane is a brief about whatever that lane was loud about.

3. **Verify before shortlisting.** For each candidate that survives a first pass,
   `WebFetch` the primary source — the repo, the paper, the vendor's own post —
   not the aggregator that mentioned it. Drop anything whose only evidence is a
   thread claiming it. Done when every remaining candidate has a URL you actually
   opened.

4. **Score and cut to three.** Rank on: does the user have standing to comment
   (has he built in this area), is it non-obvious to a competent engineer, and is
   it still fresh in 24 hours. Take the top three and make them *different in
   kind* — not three model releases. Done when the three would not overlap as
   posts.

5. **Write the brief file.** One section per topic, in the shape below. Done when
   every claim in the file has its link next to it.

       ## <number>. <the claim in one line>
       - What happened: <2-3 sentences, concrete>
       - Source: <url you opened>
       - Why he can post this: <the standing — what he has built here>
       - Angle: <the specific take, not the summary>
       - Visual: <which archetype from linkedin-post/references/visuals.md>

6. **Deliver the Telegram-sized version.** Three numbered blocks, roughly 60 words
   each, each ending with its angle and its link. Close with one line telling the
   user how to act on it: reply with a number to get the post. Done when the
   message is readable on a phone without scrolling past the third topic.

## Pitfalls

- **A scheduled run cannot send files or images.** Cron composes no hermes MCP
  server, so there is no `send_document` and no picture. The brief is text; the
  post and its image happen later, in chat.
- **The brief is not the post.** Do not draft post copy here. A radar run that
  returns finished posts has spent the user's decision for him.
- **Aggregator-only evidence.** A post built on a thread that turned out to
  misread a changelog is the failure this skill's step 3 exists to prevent.
- **Three variations on one story.** Three model releases is one topic with three
  names. Force variety in kind: a release, a technique, a tool or repo, a failure
  report.
- **Recycling yesterday.** Without step 1 the same story gets proposed for three
  days running, because it is still being discussed.
- **Timing out silently.** At the default 180s a real sweep dies and the user gets
  a `⚠️` line instead of a brief. The job must carry `timeout_seconds=600`.
- **Padding to three.** A genuinely quiet day gets two topics and a sentence saying
  so. Inventing a third teaches the user to ignore the brief.

## Verification

Done when: `content/briefs/<today>.md` exists with three sections (or fewer, with
the shortfall stated), every claim in it has a URL that was actually fetched, none
of them duplicates the covered-topics list, and the chat message fits on a phone
screen and tells the user how to turn a number into a post.

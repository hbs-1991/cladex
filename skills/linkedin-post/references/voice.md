# LinkedIn voice

The rules a draft has to satisfy before it is shown to the user. This file is a
starting hypothesis about the user's voice, not a finding — it was written before
any post shipped. **Patch it whenever the user rewrites a draft**: the edit they
make is the real rule, and the next run should not need the same correction.

## Position

The author is a builder who ships, not an analyst who comments. Replace this
paragraph with what the user actually works on — the products, repos and tools a
post can draw from. That is the credibility: everything in a post either happened
in the user's own work or is something they tried this week.

Two consequences:

- **First-person and specific beats third-person and general.** "I gave the agent
  its own cron and it started scheduling work I forgot about" over "AI agents are
  becoming more autonomous."
- **Never posture as an authority on things not built.** Commenting on a new model
  or tool is fine — commenting as if it had been used in production when it has
  not is the one thing that does not survive a reply from someone who has.

## Anatomy

| Part | Budget | Job |
|---|---|---|
| Hook | 1–2 lines, ≤180 chars | Survive the "see more" fold and earn the tap |
| Setup | 2–3 short paragraphs | The concrete situation, with real names and numbers |
| Turn | 1 paragraph | What was surprising, wrong, or non-obvious |
| Payload | 3–5 lines | The transferable thing the reader keeps |
| Close | 1 line | A real question or a plain statement — never engagement bait |
| Hashtags | ≤3 | On their own line at the end |

Length: **900–1300 characters** is the target. The hard platform ceiling is 3000;
anything over ~1500 is a different format and should be split into two posts.

## Formatting

- **LinkedIn renders no Markdown.** `**bold**`, `_italic_`, `# heading` and
  `[text](url)` all publish literally. Write plain text only.
- One idea per paragraph, blank line between paragraphs. Walls of text die at the
  fold.
- Lists: use a leading `—` or `•` typed as a character. No `1.` auto-numbering
  assumptions.
- Links suppress reach in the body. Put a URL in the first comment and say so, or
  leave it out.
- No emoji spam. At most one, and only if it carries information.

## Ban list

These phrases mark a post as generated and are not to appear:

    game-changer · revolutionary · unlock the power of · in today's fast-paced
    excited to announce · humbled to share · thrilled to · delve into
    it's not just X, it's Y · let that sink in · here's the thing:
    Agree? · Thoughts? · What do you think? 👇 · comment "AI" below

Also out: fake vulnerability ("I failed so you don't have to"), the one-line-per-
sentence stunt for a whole post, and any number nobody measured.

## Numbers

A metric goes in only if it came from `ship-log.sh`, a source cited in the brief,
or the user's own words. If a claim wants a number and none exists, make the claim
without one — "the review pass now catches the class of bug I kept re-introducing"
is honest; "cut bugs 40%" invented for rhythm is not.

## The Russian version

A rewrite, not a translation. Same claim, same structure, native phrasing — the
Russian hook has to work as a Russian hook. Technical terms and product names stay
in their original form (`prompt caching`, `cron`, `Claude Agent SDK`), and the
Russian keeps its full orthography (ё where it belongs, proper dashes). The RU
version doubles as the Telegram/channel version, so it may be slightly more
direct than the EN one.

## Self-check before showing a draft

- [ ] The hook is under 180 characters and says something, not "I've been thinking
      about X lately".
- [ ] One idea. If two ideas are fighting, it is two posts.
- [ ] Every specific is real: repo, feature, tool version, or the user's own words.
- [ ] No phrase from the ban list.
- [ ] No Markdown syntax anywhere.
- [ ] 900–1300 characters, ≤3 hashtags.
- [ ] The last line is a real question or a statement — not bait.
- [ ] A reader who skims only the hook and the payload still got something.

## The covered-topics file

`wiki/content/linkedin.md` is the durable half of this skill. Create it with this
shape if it is missing, and keep it current — step 3 of the procedure reads it:

    # LinkedIn content

    ## Voice corrections
    - <date> — <what the user changed in a draft, and the rule it implies>

    ## Covered topics
    - <date> — <one-line claim> (post: content/posts/<file>.md)

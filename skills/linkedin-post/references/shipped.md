# Shipped-work mode

How to get from "what did I build lately" to one post-worthy story. The data comes
from `scripts/ship-log.sh`, which asks GitHub's search index three narrow
questions instead of dumping repo JSON the model then pays to read.

## Getting the raw material

    bash .claude/skills/linkedin-post/scripts/ship-log.sh --days 7

Output is three sections: merged pull requests, `feat`/`fix`/`perf` commit
subjects grouped by repo, and which repos moved at all. Exit codes: `3` = `gh` not
installed, `4` = not authenticated (the fix is a host-side
`docker compose exec -T agent gh auth login --with-token < token.txt`, never a
token typed into the chat).

Widen the window rather than inventing material: a quiet week with `--days 7` is a
reason to try `--days 14`, not a reason to write about something that did not ship.

## Picking the one thing

A week of commits is not a post. Rank the candidates by this order and take the
top one:

1. **A merged pull request with a descriptive title.** These are already
   feature-shaped, and the PR body usually contains the reasoning.
2. **A cluster of commits that solved one problem.** Six `fix(scan):` commits about
   the same screen are one story: "the layout fought me for a day, here is what it
   taught me about X."
3. **A single `feat` commit with a non-obvious idea in it.** Read the diff before
   claiming what it does.

Skip: dependency bumps, CI green-again fixes with no lesson, and anything whose
only content is "I did work." A post needs the reader to leave with something.

## Getting the detail that makes it real

Titles are too thin to write from. Before drafting, pull the actual reasoning:

    gh pr view <number> --repo <owner>/<repo> --json title,body,files,additions,deletions
    gh api repos/<owner>/<repo>/commits/<sha> --jq '.commit.message'

Read the body and the file list. The post's payload almost always comes from *why*
the obvious approach did not work, and that is in the PR body or the commit
message, not in the title.

## Confidentiality

The user's work spans private repositories and a startup. Before drafting, apply
these in order:

- **Never name a private repo, internal ticket id, or unreleased product surface.**
  Describe the shape of the problem, not the codebase: "a scanner that grades a
  business's local visibility" rather than the repo path and epic number.
- **No customer names, no revenue numbers, no unannounced launch dates.** If the
  material only works with one of those in it, the material is not postable — pick
  the next candidate.
- **Personal repos are fair game to name and link.** Public ones especially: a link
  in the first comment is the strongest proof a post can have.
- **When it is genuinely unclear whether something is public, ask.** One `ask_user`
  with the specific detail beats guessing in either direction.

## Turning it into a claim

The commit says what changed. The post needs what it means. Two questions convert
one into the other:

- **What did I believe before, that turned out to be wrong?** This is the highest-
  performing shape and the hardest to fake, which is why it works.
- **What would I tell someone about to build the same thing?** The answer is the
  payload; the shipped feature is only the evidence for it.

Done when the claim stands on its own without the repo — a reader who cannot see
the code still learns the thing.

## The proposal (the Monday job's whole output)

The weekly run stops here. Write `content/briefs/<YYYY-MM-DD>-shipped.md` using the
same fields `content-radar` uses, so step 1's brief mode reads either file without
caring which job wrote it:

    ## 1. <the claim in one line>
    - What shipped: <2-3 sentences. What landed, and what it does.>
    - Evidence: <PR url, or repo + commit subjects. Public links only.>
    - Why he can post this: <he built it — name the decision he made>
    - Angle: <the specific take: what he believed before that was wrong>
    - Visual: <archetype from linkedin-post/references/visuals.md>

Then reply with the same content, phone-sized, and one closing line telling the user
how to act on it ("скажи «делаем» — напишу пост и нарисую картинку").

Two or three candidates are fine when the week had genuinely separate stories —
number them and say which one is strongest and why. A quiet week gets one candidate,
or none plus a sentence saying the week was maintenance. Padding to a number teaches
the user to stop reading the Monday message.

Do **not** draft post prose here. The angle is a claim, not a hook; the visual is an
archetype name, not a prompt. Drafting costs the user the approval step and produces
a post whose image the scheduled run cannot deliver anyway.

## Pitfalls

- **`gh search` HTML-escapes titles.** `Scanner claim bridge &amp; onboarding`
  needs unescaping before it appears in a post.
- **`--author-date`, not push date.** A batch of old commits pushed today does not
  belong in this week's post; the script already filters on commit date, so do not
  "fix" a quiet week by switching to push dates.
- **Merge commits carry a branch name, not a description.** The script drops them
  on purpose.
- **Writing about volume.** "37 commits this week" is not interesting to anyone but
  the committer. One decision, explained, beats a changelog.

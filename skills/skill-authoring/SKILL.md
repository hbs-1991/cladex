---
name: skill-authoring
description: Deep checklist for authoring and maintaining Claude skills.
---

# Skill Authoring

The deep reference for writing and maintaining skills in this project. The
hard floor — name/description limits, section order, "commands must be
verbatim" — lives inline in every review and learn prompt as
`AUTHORING_STANDARDS` so a background fork can never miss it even if it
never loads this skill. This document is the depth: read it when actually
writing, patching, or consolidating a skill.

## When to use

- Authoring a new skill (`/learn`, or any fork writing to `.claude/skills/`).
- Patching an existing skill after a correction or a failed run.
- Running a curator consolidation pass and deciding what to merge.
- Reviewing a skill for quality before deciding it's done.

## Where skills live, here

- Runtime skills: `.claude/skills/<name>/SKILL.md` under the agent's home (the
  cwd of every turn and fork). Each user has their own home, so the relative
  path is the only one that is right everywhere.
- Bundled/shipped skills: this repo's `skills/<name>/SKILL.md` — seeded into
  the runtime tree at boot and refreshed while the runtime copy is untouched;
  a patched, deleted or archived copy is left as it is. The runtime copy is
  the one that gets edited; the repo copy is not live.
- Discovery is entirely the SDK's job (`setting_sources=["project"]`,
  `cwd=agent_home`) — there is no custom loader, no `skill_view`, no
  `skill_manage` tool. Skills are read and written with plain `Read` /
  `Write` / `Edit`, and loaded at use-time with the `Skill` tool.
- The only enforcement point for background/review/curator forks is a
  `PreToolUse` hook (`skills_write_guard_factory`) that denies writes outside
  the skills root and denies writes into `.usage.json` or `.archive/`. There
  is no tool-layer choke point to lean on — the hook is it.

## Validation checklist

Before considering a skill done, verify every item:

- [ ] Directory is `.claude/skills/<name>/` with lowercase-hyphenated `name`.
- [ ] Frontmatter has exactly `name` and `description`.
- [ ] `description` is ONE sentence, ends with a period, and is **60
      characters or fewer including the period**. Count it — anything past
      60 is silently truncated in the index and will never route correctly.
      No marketing adjectives ("powerful", "seamless", "robust").
- [ ] Body order: `# Title`, a 2-3 sentence intro, `## When to Use`,
      `## Prerequisites`, `## How to Run`, `## Quick Reference`,
      `## Procedure`, `## Pitfalls`, `## Verification`. Omit a section only
      if it is genuinely empty for this skill, never to save space.
- [ ] Target length ~100-200 lines. Longer means detail belongs in
      `references/`, not inline.
- [ ] Every command shown is VERBATIM from something that actually ran and
      worked — never an invented flag or a "should work" guess.
- [ ] Non-trivial helper logic lives in `scripts/`; long reference material
      lives in `references/`. `SKILL.md` itself stays a map, not a manual.
- [ ] Ends with a concrete completion criterion the reader can check against
      — not "you're done when it feels right."

## Peer-matched structure

A new skill should read like the others in this library, not like a diary
entry. Before writing, skim one or two existing `SKILL.md` files of a
similar shape and match their section headers, tone, and level of
abstraction. A reviewer merging skills later depends on this consistency —
umbrella consolidation is much cheaper when every candidate skill is shaped
the same way.

## Writing-quality principles

- **Optimize for process predictability.** A skill should make the next run
  of the same task more deterministic, not just describe what happened once.
  Prefer "always do X" over "I did X and it worked."
- **Prune duplication and no-ops.** If two steps say the same thing in
  different words, or a step doesn't change what the reader does next,
  delete it. Every line should earn its place.
- **Watch for premature completion.** A procedure that stops before the
  actual verification step (build passed, but did anyone check the output?)
  teaches the wrong stopping point. State the real finish line explicitly.
- **Co-locate rules with the concept they govern.** A constraint about
  retries belongs next to the retry step, not in a general notes section at
  the bottom where it will be missed under pressure.
- **End steps with completion criteria.** Each numbered step in `##
  Procedure` should make it obvious when that step is actually finished —
  not just "run the command" but what output or state means "done, move on."

## Common failure taxonomy

Recognize these patterns when reviewing a skill (yours or another's):

- **Premature completion.** The skill declares success at the first green
  signal (command exits 0) without checking the signal that actually
  matters (the output is correct, the file exists, the test covers the bug).
- **Duplication.** The same guidance restated in two sections, or the same
  fact captured in two separate skills that should be one umbrella.
- **Sediment.** Old, now-wrong detail left in place after the underlying
  system changed — a flag that no longer exists, a path that moved. Nobody
  removed it because nobody re-verified the whole skill, only patched around
  it.
- **Sprawl.** Many narrow, one-session skills instead of one class-level
  skill with a `references/` directory. This is the specific failure mode a
  curator consolidation pass exists to fix — see the curator prompt's
  umbrella-building framing.
- **No-op prose.** Sentences that describe intent without changing behavior
  ("be careful here", "this is important") instead of stating the concrete
  rule or check that intent implies.

## Verification

A skill is ready when: the checklist above is fully checked, it reads like
its peers, and a reader who has never seen the underlying task could follow
`## Procedure` to a correct, checkable finish.

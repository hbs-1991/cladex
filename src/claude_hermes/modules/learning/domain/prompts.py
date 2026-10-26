"""Every prompt the learning passes send, moved VERBATIM from
`learning/prompts.py` in Wave 2.

Prompt text is a cache key: the review fork resumes the turn's session, so a
byte that changes here is a cache miss the user pays for. Edit with that in
mind.
"""
from __future__ import annotations

AUTHORING_STANDARDS = """\
Skill authoring standards (mandatory):
- The skill is a directory under .claude/skills/<name>/ with a SKILL.md.
- Frontmatter: `name` (lowercase-hyphenated) and `description` — one sentence,
  MAXIMUM 60 characters, ending with a period. Count the characters: anything
  past 60 is silently cut from the index and never routes. No marketing words.
- Body section order: # Title, 2-3 sentence intro, ## When to Use,
  ## Prerequisites, ## How to Run, ## Quick Reference, ## Procedure,
  ## Pitfalls, ## Verification. Target ~100-200 lines.
- Commands must be VERBATIM from what actually worked — never invent flags.
- Non-trivial helper logic goes in scripts/ inside the skill directory;
  reference files in references/. Do not inline long scripts into SKILL.md.
- Full checklist, peer-matched structure, and failure taxonomy: load the
  skill-authoring skill. This constant is the floor, not the depth."""

_SKILL_SECTION = f"""\
SKILLS — review the conversation for durable know-how:
- Signals: the user corrected your approach, style, or verbosity (frustration
  is a first-class signal); a multi-step workflow succeeded after trial and
  error; a loaded skill turned out to be wrong or incomplete.
- STRICT preference order: (1) patch an existing skill that covers this class
  of task, (2) add a references/ or scripts/ file to an existing skill,
  (3) only then create a NEW class-level umbrella skill. Never create narrow
  one-session skills named after today's task or an error message.
- NEVER capture: environment-dependent failures (missing binaries, "command
  not found"), negative claims that a tool is broken (these harden into
  refusals you will cite against yourself for months), transient errors that
  resolved, or one-off task narratives. If a tool failed due to setup, capture
  the FIX, never "this tool is broken".
- Use Read/Glob/Grep to inspect .claude/skills/, and Write/Edit to change it.
{AUTHORING_STANDARDS}"""

_WIKI_SECTION = """\
WIKI — this is the ingest pass over the conversation above. Distil what is durable into the
wiki at ./wiki/ (you have Read, Glob, Grep, Write, Edit):
- Find the pages that should hold each fact — `Glob` the namespaces, `Grep` for the entity.
  Prefer updating an existing page over creating a near-duplicate one.
- `user/` who the user is; `projects/` a project and its decisions; `topics/` a concept;
  `env/` machines, paths, conventions. Sources live in `raw/` and are immutable.
- A page is frontmatter (`title`, `summary`, `tags`, `updated`) + prose + `[[links]]`.
  Keep `summary` to one line: it is what the agent sees in the index.
- Declarative facts only ("The user prefers concise replies"), never imperatives.
  If a fact will be stale in a week, it does not belong in the wiki. Procedures belong
  in skills, not here.
- Finish by updating `wiki/index.md` (one line per page) and appending to `wiki/log.md`.
  A hook will stop you if you forget."""

_FOOTER = """\
Work directly: make the writes, then reply with a one-line summary of what you
saved, or exactly: Nothing to save.
A smooth routine session legitimately produces nothing — but a session with
corrections, new workflows, or new durable facts should produce at least one
write. Do not ask questions; there is no user present."""


def build_review_prompt(kinds: set[str]) -> str:
    parts = [
        "You are running an out-of-band self-review of the conversation above. "
        "The user has already been answered; nothing you output will be shown "
        "to them."
    ]
    if "wiki" in kinds:
        parts.append(_WIKI_SECTION)
    if "skills" in kinds:
        parts.append(_SKILL_SECTION)
    parts.append(_FOOTER)
    return "\n\n".join(parts)


def build_learn_prompt(user_text: str) -> str:
    return (
        "The user wants you to learn a new skill. Their request:\n"
        f"<request>\n{user_text}\n</request>\n\n"
        "The request may MIX sources to gather (file paths, URLs, \"what we "
        "just did\" in this conversation, pasted notes) AND requirements that "
        "shape the resulting skill. Prose that comes after a path or link is "
        "NOT incidental — it is the user telling you what they want from that "
        "source. Example: `<url> focus on the auth flow, skip the deprecated "
        "endpoints` means gather the URL AND honor \"focus on auth, skip "
        "deprecated\" as authoring requirements. Never fetch the first source "
        "and ignore the rest.\n\n"
        "Gather every source named, respecting every requirement stated, then "
        "author ONE skill directory under .claude/skills/ with a SKILL.md.\n\n"
        f"{AUTHORING_STANDARDS}\n\n"
        "When done, reply with the skill name and a one-line summary."
    )


def build_curator_prompt(candidates: list[dict]) -> str:
    header = "| name | state | pinned | use_count | patch_count | last_activity |"
    sep = "|---|---|---|---|---|---|"
    rows = "\n".join(
        f"| {c['name']} | {c['state']} | {c['pinned']} | {c['use_count']} | "
        f"{c['patch_count']} | {c['last_activity']} |"
        for c in candidates
    )
    table = "\n".join([header, sep, rows]) if candidates else "\n".join(
        [header, sep, "(no candidates this pass)"]
    )
    return (
        "You are the skills curator running an UMBRELLA-BUILDING consolidation "
        "pass over the skill library below — this is NOT a passive audit and "
        "NOT a duplicate-finder. Hundreds of narrow one-session skills is a "
        "FAILURE of the library, not a feature. Target shape: CLASS-LEVEL "
        "skills, each a rich SKILL.md plus a references/ directory for "
        "session-specific detail — not a long flat list of narrow entries.\n\n"
        "Candidates (created_by=\"agent\" only; anything not listed here is "
        "user/foreground-created and is outside your jurisdiction):\n"
        f"{table}\n\n"
        "Hard rules:\n"
        "- NEVER touch a pinned skill (pinned=True above), and NEVER touch a "
        "skill that is not agent-created.\n"
        "- use_count == 0 is NOT a reason to prune a skill, and NOT a reason "
        "to skip consolidating it into an umbrella — absence of evidence is "
        "not evidence of staleness.\n"
        "- Prefer merging narrow skills into an existing or new class-level "
        "umbrella over leaving them standalone.\n\n"
        "Tools available to you: Read, Glob, Grep, Write, Edit. There is no "
        "skill_manage tool and no delete.\n\n"
        "CRITICAL DIVERGENCE: you CANNOT archive anything yourself — the "
        "write guard denies writes into .archive/. THE CODE performs the "
        "actual archiving after this pass, from your report below. Your job "
        "is to REWRITE/MERGE skill content with Write/Edit — fold the narrow "
        "skill's material into the umbrella's SKILL.md / references/ — and "
        "then report what you merged.\n\n"
        "End your final message with this YAML block, always. Empty lists "
        "are valid — emit the block anyway:\n\n"
        "```yaml\n"
        "consolidations:\n"
        "  - from: <old-skill>\n"
        "    into: <umbrella-skill>\n"
        "    reason: <one sentence>\n"
        "prunings:\n"
        "  - name: <skill>\n"
        "    reason: <one sentence>\n"
        "```\n\n"
        "Every skill you merge away or prune MUST appear in exactly one of "
        "the two lists. An `into:` naming a skill that does not exist on "
        "disk will be IGNORED and the source left alone — write the umbrella "
        "first, then name it."
    )

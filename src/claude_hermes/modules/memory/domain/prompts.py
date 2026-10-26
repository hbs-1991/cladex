"""The wiki lint prompt.

Copied byte-for-byte from learning's `build_wiki_lint_prompt` in Wave 2. Prompt text
is a contract with the model: change it and the lint pass changes behaviour, so it
moved verbatim.
"""
from __future__ import annotations


# Appended for a non-owner's pass. Without it the fork spends `curator_max_turns`
# fixing links on pages every write to which the fence denies — and reports the
# denials as findings.
_SHARED_READONLY = (
    "\n\nPages under `shared/` belong to the deploy owner and are read-only for "
    "you; do not try to edit them or fix their links."
)


def build_wiki_lint_prompt(dry_run: bool, *, shared_readonly: bool = False) -> str:
    action = (
        "Report only. You have NO write access this pass — the guard denies every write. "
        "Describe what you WOULD change."
        if dry_run else
        "Fix what you find, with Write/Edit, directly in ./wiki/."
    )
    shared = _SHARED_READONLY if shared_readonly else ""
    return (
        "You are linting the wiki at ./wiki/. Nobody is watching; there is no user to ask.\n\n"
        "Read wiki/index.md, then Glob and Read the pages. Hunt for:\n"
        "- CONTRADICTIONS: two pages asserting incompatible things. The newer `updated:` wins; "
        "say so on the page and delete the dead claim.\n"
        "- STALE CLAIMS: a page distilled from a source (`source: raw/...`) that no longer "
        "matches its source. Re-read the source in raw/ and correct the page — the source is "
        "immutable and always wins.\n"
        "- ORPHANS: pages nothing links to, and pages missing from the index.\n"
        "- BROKEN LINKS: a `[[link]]` whose page does not exist. Either write the page or drop "
        "the link.\n"
        "- AN OVERGROWN INDEX: if index.md has become a long flat list, group it by namespace "
        "and shorten the summaries. The index is injected into the agent's prompt — it has to "
        "stay skimmable.\n"
        "- PAGES CARRIED OVER FROM THE OLD FLAT MEMORY (`tags: [migrated]`): split them into "
        "real pages, one entity or concept each, and drop the migrated tag.\n\n"
        f"{action}\n\n"
        "Finish by appending one line to wiki/log.md, then reply with a short summary: what "
        "you found and what you did. If the wiki is clean, say exactly: Nothing to fix."
        + shared
    )

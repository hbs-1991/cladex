"""The authoring standard, enforced on the skills written to it.

skills/skill-authoring/SKILL.md:43 — a description over 60 characters is silently
truncated in the index and the skill never routes. All eight originals in the
nutrition agent broke it, which is why these are checked mechanically.

Scoped to CHECKED, not to every bundled skill, and deliberately: the shipped
`catalog-manifest` skill carries a 287-character description and `skill-authoring`
has no `## Procedure` section and spells its heading `## When to use`. Both predate
this work. Widening these assertions would make the suite fail on files the change
did not touch — fix them in their own change, with their routing re-checked.

Add a skill here when it is written to the standard, not before: a name in this
tuple is a claim that the file already passes, so a failure means the skill
regressed rather than that the list is aspirational.
"""
from pathlib import Path

import pytest
import yaml

SKILLS = Path(__file__).resolve().parents[1] / "skills"
CONTENT = ("linkedin-post", "content-radar", "gpt-image-2", "content-publish",
           "content-draft")
CHECKED = CONTENT


def _frontmatter(name: str) -> dict:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name} has no frontmatter"
    return yaml.safe_load(text.split("---\n", 2)[1])


@pytest.mark.parametrize("name", CHECKED)
def test_frontmatter_has_exactly_name_and_description(name):
    fm = _frontmatter(name)
    assert set(fm) == {"name", "description"}
    assert fm["name"] == name == name.lower()


@pytest.mark.parametrize("name", CHECKED)
def test_description_is_one_sentence_within_60_characters(name):
    d = _frontmatter(name)["description"]
    assert len(d) <= 60, f"{len(d)} chars: {d!r}"
    assert d.endswith("."), f"must end with a period: {d!r}"
    assert d.count(".") == 1, f"one sentence only: {d!r}"


@pytest.mark.parametrize("name", CHECKED)
def test_required_sections_appear_in_order(name):
    body = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    required = ["## When to Use", "## Procedure", "## Verification"]
    positions = [body.find(h) for h in required]
    missing = [h for h, p in zip(required, positions) if p < 0]
    assert not missing, f"{name} is missing {missing}"
    assert positions == sorted(positions), f"{name}: sections are out of order"


@pytest.mark.parametrize("name", CHECKED)
def test_the_skill_is_actually_bundled(name):
    assert (SKILLS / name / "SKILL.md").is_file()


CONTENT_REFERENCES = {
    "linkedin-post": ("references/voice.md", "references/visuals.md",
                      "references/shipped.md", "scripts/ship-log.sh"),
    "content-radar": ("references/sources.md",),
    "gpt-image-2": ("references/fundamentals.md", "references/generate.md",
                    "references/edit.md", "references/advanced.md",
                    "scripts/gen.sh", "scripts/extract_image.py"),
}


@pytest.mark.parametrize("name,paths", sorted(CONTENT_REFERENCES.items()))
def test_every_referenced_file_is_bundled(name, paths):
    """SKILL.md is a map; a map pointing at a file the image does not ship sends the
    model to Read a path that does not exist, mid-task, with no fallback."""
    for rel in paths:
        assert (SKILLS / name / rel).is_file(), f"{name}/{rel} is missing"


# Called by another shipped script rather than by the model, so SKILL.md has no
# reason to name it — its own caller is the documentation.
INTERNAL_HELPERS = {"extract_image.py"}


@pytest.mark.parametrize("name", sorted(CONTENT_REFERENCES))
def test_the_skill_body_mentions_what_it_ships(name):
    """An unreferenced reference file is dead weight the model pays for on every
    seed and never reads. If SKILL.md does not point at it, it should not ship."""
    body = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    for rel in CONTENT_REFERENCES[name]:
        leaf = rel.split("/")[-1]
        if leaf in INTERNAL_HELPERS:
            continue
        assert leaf in body, f"{name} ships {rel} but never points at it"


def test_no_bundled_skill_names_the_owners_agent_home():
    """Wave 4b, D13. A seeded skill lands in EVERY user's tree, and the cwd of
    every turn and fork is that user's `agent_home`; a `/data/agent_home/...`
    literal inside it is the deploy owner's path, which the read guard denies
    on a non-owner lane and `send_document` refuses. Whole tree on purpose,
    `CHECKED` or not: the literal is wrong in a reference file exactly as it
    is wrong in `SKILL.md`."""
    offenders = sorted(
        str(p.relative_to(SKILLS)) for p in SKILLS.rglob("*")
        if p.is_file() and "/data/agent_home" in p.read_text(encoding="utf-8",
                                                             errors="replace"))
    assert not offenders, f"/data/agent_home literal in: {offenders}"


def test_the_drafter_skill_is_the_procedure_the_tool_and_template_name():
    """The "Content drafter" template's prompt loads this skill by name, and the
    tool refuses `claim_draft`/`submit`/`draft_failed` outside the drafter run —
    the skill is where those three actions are spelled out, one item per run,
    under the attempt folder the claim names, and never as a publish."""
    body = (SKILLS / "content-draft" / "SKILL.md").read_text(encoding="utf-8")
    for action in ('action="claim_draft"', 'action="submit"', 'action="draft_failed"'):
        assert action in body, action
    assert "content/drafts/<item_id>/" in body
    assert "One item per run" in body
    for skill in ("social-carousel", "vibecode-trap-reel", "hyperframes-local",
                  "youtube-channel", "gpt-image-2", "linkedin-post"):
        assert skill in body, skill
    assert "Never publish, never approve" in body

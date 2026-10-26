from claude_hermes.modules.learning.domain.prompts import (
    AUTHORING_STANDARDS,
    build_curator_prompt,
    build_learn_prompt,
    build_review_prompt,
)


def test_review_prompt_variants():
    both = build_review_prompt({"wiki", "skills"})
    assert "wiki/index.md" in both and "SKILL.md" in both
    wiki = build_review_prompt({"wiki"})
    assert "wiki/index.md" in wiki and "SKILL.md" not in wiki
    sk = build_review_prompt({"skills"})
    assert "SKILL.md" in sk


def test_wiki_section_replaces_the_memory_tool_section():
    out = build_review_prompt({"wiki"})
    assert "memory_save" not in out          # the tool is gone
    assert "wiki/index.md" in out and "wiki/log.md" in out
    assert "SKILLS" not in out               # kinds are independent


def test_skills_section_alone_has_no_wiki():
    out = build_review_prompt({"skills"})
    assert "wiki/" not in out


def test_review_prompt_encodes_hermes_policies():
    p = build_review_prompt({"wiki", "skills"})
    # anti-patterns: never capture tool-is-broken or env failures
    assert "broken" in p
    # umbrella-first preference order
    assert "existing" in p.lower()
    # doing nothing is allowed
    assert "Nothing to save." in p


def test_authoring_standards_and_learn_prompt():
    assert "60" in AUTHORING_STANDARDS          # description length rule
    assert "Pitfalls" in AUTHORING_STANDARDS    # section order rule
    lp = build_learn_prompt("научись деплоить через ansible")
    assert "научись деплоить через ansible" in lp
    assert "SKILL.md" in lp


def test_authoring_standards_points_to_skill_authoring_skill():
    # the skill carries the depth; the constant stays the hard floor
    assert "skill-authoring" in AUTHORING_STANDARDS


def test_build_learn_prompt_sources_vs_requirements():
    request = "https://example.com/docs focus on the auth flow, skip deprecated endpoints"
    lp = build_learn_prompt(request)
    assert request in lp
    # prose after a path/link is load-bearing requirements, not incidental
    assert "NOT incidental" in lp
    assert "never fetch the first source and ignore the rest" in lp.lower()


def test_build_curator_prompt_renders_candidate_table():
    candidates = [
        {
            "name": "foo-bar-fix",
            "state": "active",
            "pinned": False,
            "use_count": 3,
            "patch_count": 1,
            "last_activity": "2026-07-01T00:00:00+00:00",
        },
        {
            "name": "deploy-umbrella",
            "state": "stale",
            "pinned": True,
            "use_count": 0,
            "patch_count": 0,
            "last_activity": "2026-01-01T00:00:00+00:00",
        },
    ]
    p = build_curator_prompt(candidates)
    assert "foo-bar-fix" in p and "deploy-umbrella" in p
    assert "2026-07-01T00:00:00+00:00" in p


def test_build_curator_prompt_umbrella_framing_and_hard_rules():
    p = build_curator_prompt([])
    assert "umbrella-building" in p.lower()
    assert "not a passive audit" in p.lower()
    assert "not a duplicate-finder" in p.lower()
    assert "class-level" in p.lower()
    assert "failure" in p.lower()
    # never touch pinned / non-agent-created
    assert "pinned" in p.lower()
    assert "agent-created" in p.lower()
    # use=0 is not a reason to prune or to skip consolidation
    assert "use_count == 0" in p or "use=0" in p
    assert "not a reason" in p.lower()


def test_build_curator_prompt_tools_and_archive_divergence():
    p = build_curator_prompt([])
    for tool in ("Read", "Glob", "Grep", "Write", "Edit"):
        assert tool in p
    assert "cannot archive" in p.lower()
    assert "the code" in p.lower()  # code performs the archiving, not the model


def test_build_curator_prompt_yaml_contract():
    p = build_curator_prompt([])
    assert "consolidations:" in p
    assert "prunings:" in p
    assert "from:" in p and "into:" in p and "reason:" in p
    assert "empty lists are valid" in p.lower()
    assert "ignored" in p.lower()
    assert "exactly one" in p.lower()

"""`SkillLibraryService` — the shared skills reads and writes.

The listing merges three sources that can disagree (live directories, `.archive/`,
the sidecar), so these tests pin what each one is allowed to decide.
"""
import pytest

import claude_hermes.core.workspace as paths_mod
from claude_hermes.modules.learning.application.services.library import SkillLibraryService
from claude_hermes.modules.learning.domain.constants import MAX_CONTENT_BYTES
from claude_hermes.modules.learning.domain.exceptions import SkillError, StaleEdit
from claude_hermes.modules.learning.domain.models import (
    etag_of,
    last_activity_iso,
    parse_front_matter,
)
from claude_hermes.modules.learning.infrastructure.skill_archive import SkillArchive
from claude_hermes.modules.learning.infrastructure.skill_repository import SkillRepository
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar

NOW = "2026-07-12T10:00:00+00:00"
BODY = "---\nname: {name}\ndescription: Does a thing.\n---\n\n# {name}\n"


def make_skill(skills_dir, name, body=None):
    d = skills_dir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body or BODY.format(name=name), encoding="utf-8")
    return d


def setup(tmp_path):
    skills_dir = tmp_path / "agent_home" / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    return skills_dir, UsageSidecar(skills_dir)


def by_name(rows):
    return {r["name"]: r for r in rows}


def service(skills_dir, sidecar=None) -> SkillLibraryService:
    """The service over one directory: the repository, the sidecar and the archive."""
    usage = sidecar if sidecar is not None else UsageSidecar(skills_dir)
    return SkillLibraryService(SkillRepository(skills_dir), usage,
                               SkillArchive(skills_dir, usage))


# ---------------------------------------------------------------- listing


def test_a_directory_with_no_sidecar_entry_still_lists(tmp_path):
    skills_dir, sc = setup(tmp_path)
    make_skill(skills_dir, "orphan")
    row = by_name(service(skills_dir, sc).list_skills())["orphan"]
    assert row["source"] == "user"        # UsageSidecar's own default for an untracked skill
    assert row["state"] == "active"
    assert row["use_count"] == 0 and row["patch_count"] == 0
    assert row["description"] == "Does a thing."
    assert row["broken"] is False
    assert row["files"] == 1 and row["bytes"] > 0


def test_the_source_tree_decides_bundled_not_the_sidecar(tmp_path, monkeypatch):
    skills_dir, sc = setup(tmp_path)
    make_skill(skills_dir, "shipped")
    make_skill(skills_dir, "learned")
    sc.mark_created("shipped", "agent", NOW)   # the sidecar is wrong; the tree wins
    sc.mark_created("learned", "agent", NOW)
    source = tmp_path / "bundled"
    (source / "shipped").mkdir(parents=True)
    monkeypatch.setattr(paths_mod, "_BUNDLED_SKILLS_DIR", source)

    rows = by_name(service(skills_dir, sc).list_skills())
    assert rows["shipped"]["source"] == "bundled"
    assert rows["learned"]["source"] == "agent"


def test_bundled_names_are_empty_when_the_source_is_absent(tmp_path):
    assert paths_mod.bundled_skill_names(source=tmp_path / "nope") == frozenset()


def test_a_skill_in_the_archive_lists_as_archived(tmp_path):
    skills_dir, sc = setup(tmp_path)
    make_skill(skills_dir, "old")
    sc.mark_created("old", "agent", NOW)
    (skills_dir / ".archive").mkdir()
    (skills_dir / "old").rename(skills_dir / ".archive" / "old")

    row = by_name(service(skills_dir, sc).list_skills())["old"]
    assert row["state"] == "archived"


def test_active_skills_sort_before_archived_ones(tmp_path):
    skills_dir, sc = setup(tmp_path)
    make_skill(skills_dir, "zeta")
    make_skill(skills_dir, "alpha")
    (skills_dir / ".archive").mkdir()
    make_skill(skills_dir / ".archive", "aardvark")

    assert [r["name"] for r in service(skills_dir, sc).list_skills()] == [
        "alpha", "zeta", "aardvark",
    ]


def test_unparseable_frontmatter_lists_as_broken_rather_than_raising(tmp_path):
    skills_dir, sc = setup(tmp_path)
    make_skill(skills_dir, "bad", body="---\nname: [unclosed\n---\n\nbody\n")
    row = by_name(service(skills_dir, sc).list_skills())["bad"]
    assert row["broken"] is True
    assert row["description"] == ""
    assert row["title"] == "bad"          # falls back to the directory name


def test_a_stale_sidecar_state_survives_into_the_wire(tmp_path):
    skills_dir, sc = setup(tmp_path)
    make_skill(skills_dir, "dusty")
    sc.mark_created("dusty", "agent", NOW)
    sc.set_state("dusty", "stale")
    assert by_name(service(skills_dir, sc).list_skills())["dusty"]["state"] == "stale"


# ---------------------------------------------------------------- read


def test_read_skill_returns_content_etag_and_tree(tmp_path):
    skills_dir, _ = setup(tmp_path)
    d = make_skill(skills_dir, "foo")
    (d / "references").mkdir()
    (d / "references" / "x.md").write_text("hello", encoding="utf-8")

    detail = service(skills_dir).read_skill("foo")
    # read verbatim, CRLF and all — the editor round-trips the bytes on disk
    assert detail["content"] == (d / "SKILL.md").read_bytes().decode("utf-8")
    assert detail["etag"] == etag_of((d / "SKILL.md").read_bytes())
    assert detail["tree"] == [{"path": "references/x.md", "bytes": 5}]


def test_read_skill_reads_an_archived_skill(tmp_path):
    skills_dir, _ = setup(tmp_path)
    (skills_dir / ".archive").mkdir()
    make_skill(skills_dir / ".archive", "gone")

    detail = service(skills_dir).read_skill("gone")
    assert detail is not None
    assert detail["state"] == "archived"
    assert "# gone" in detail["content"]


def test_read_skill_is_none_for_an_unknown_name(tmp_path):
    skills_dir, _ = setup(tmp_path)
    assert service(skills_dir).read_skill("ghost") is None


def test_read_skill_refuses_to_escape_the_library(tmp_path):
    skills_dir, _ = setup(tmp_path)
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "SKILL.md").write_text("no", encoding="utf-8")
    with pytest.raises(SkillError) as e:
        service(skills_dir).read_skill("../../secret")
    assert e.value.code == "bad_name"


# ---------------------------------------------------------------- write


def test_write_skill_with_the_right_etag_rewrites_and_returns_a_new_etag(tmp_path):
    skills_dir, _ = setup(tmp_path)
    d = make_skill(skills_dir, "foo")
    etag = etag_of((d / "SKILL.md").read_bytes())
    new_text = BODY.format(name="foo") + "\nmore\n"

    new_etag = service(skills_dir).write_skill("foo", new_text, etag)
    assert (d / "SKILL.md").read_text(encoding="utf-8") == new_text
    assert new_etag == etag_of(new_text.encode())


def test_write_skill_with_a_stale_etag_raises_and_carries_the_current_text(tmp_path):
    skills_dir, _ = setup(tmp_path)
    d = make_skill(skills_dir, "foo")
    (d / "SKILL.md").write_text(BODY.format(name="foo") + "\nthe agent got here first\n",
                                encoding="utf-8")
    with pytest.raises(StaleEdit) as e:
        service(skills_dir).write_skill("foo", BODY.format(name="foo"), "0" * 64)

    assert "the agent got here first" in e.value.content
    assert e.value.etag == etag_of((d / "SKILL.md").read_bytes())
    assert "the agent got here first" in (d / "SKILL.md").read_text(encoding="utf-8")


def test_write_skill_refuses_frontmatter_without_a_description(tmp_path):
    skills_dir, _ = setup(tmp_path)
    d = make_skill(skills_dir, "foo")
    etag = etag_of((d / "SKILL.md").read_bytes())
    with pytest.raises(SkillError) as e:
        service(skills_dir).write_skill("foo", "---\nname: foo\n---\n", etag)
    assert e.value.code == "bad_frontmatter"


def test_write_skill_on_an_unknown_name_raises_not_found(tmp_path):
    skills_dir, _ = setup(tmp_path)
    with pytest.raises(SkillError) as e:
        service(skills_dir).write_skill("ghost", BODY.format(name="ghost"), "x")
    assert e.value.code == "not_found"


# ---------------------------------------------------------------- create


def test_create_skill_writes_skill_md_and_marks_the_sidecar_user(tmp_path):
    skills_dir, sc = setup(tmp_path)
    detail = service(skills_dir, sc).create_skill("mine", BODY.format(name="mine"), NOW)

    assert (skills_dir / "mine" / "SKILL.md").is_file()
    assert sc.get("mine")["created_by"] == "user"
    assert detail["name"] == "mine" and detail["source"] == "user"
    assert detail["etag"] == etag_of(BODY.format(name="mine").encode())


@pytest.mark.parametrize("name", ["../evil", "Evil", "", "with space", ".hidden"])
def test_create_skill_refuses_a_bad_name(tmp_path, name):
    skills_dir, sc = setup(tmp_path)
    with pytest.raises(SkillError) as e:
        service(skills_dir, sc).create_skill(name, BODY.format(name="x"), NOW)
    assert e.value.code == "bad_name"
    assert not (tmp_path / "evil").exists()


def test_create_skill_refuses_an_existing_name(tmp_path):
    skills_dir, sc = setup(tmp_path)
    make_skill(skills_dir, "taken")
    with pytest.raises(SkillError) as e:
        service(skills_dir, sc).create_skill("taken", BODY.format(name="taken"), NOW)
    assert e.value.code == "name_taken"


def test_create_skill_refuses_a_name_the_archive_still_holds(tmp_path):
    skills_dir, sc = setup(tmp_path)
    (skills_dir / ".archive").mkdir()
    make_skill(skills_dir / ".archive", "taken")
    with pytest.raises(SkillError) as e:
        service(skills_dir, sc).create_skill("taken", BODY.format(name="taken"), NOW)
    assert e.value.code == "name_taken"


def test_create_skill_refuses_content_over_the_cap(tmp_path):
    skills_dir, sc = setup(tmp_path)
    body = BODY.format(name="big") + "x" * (MAX_CONTENT_BYTES + 1)
    with pytest.raises(SkillError) as e:
        service(skills_dir, sc).create_skill("big", body, NOW)
    assert e.value.code == "too_large"
    assert not (skills_dir / "big").exists()


def test_create_skill_refuses_frontmatter_without_a_description(tmp_path):
    skills_dir, sc = setup(tmp_path)
    with pytest.raises(SkillError) as e:
        service(skills_dir, sc).create_skill("thin", "---\nname: thin\n---\n", NOW)
    assert e.value.code == "bad_frontmatter"
    assert not (skills_dir / "thin").exists()


# ---------------------------------------------------------------- helpers


def test_parse_front_matter_reports_whether_it_parsed():
    meta, ok = parse_front_matter("---\nname: a\ndescription: B.\n---\nbody")
    assert ok and meta["description"] == "B."
    assert parse_front_matter("no frontmatter here") == ({}, False)
    assert parse_front_matter("---\nname: [oops\n---\n") == ({}, False)


def test_last_activity_iso_says_so_when_there_is_no_stamp():
    assert last_activity_iso({"created_at": NOW}) == NOW
    assert last_activity_iso({}) == "—"

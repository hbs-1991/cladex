from __future__ import annotations

from claude_hermes.modules.learning.infrastructure.skill_archive import (
    archive_skill,
    list_archived,
    restore_skill,
)
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar


def make_skill(skills_dir, name, body="hello"):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def test_archive_skill_moves_dir_and_marks_sidecar(tmp_path):
    make_skill(tmp_path, "foo")
    sc = UsageSidecar(tmp_path)
    sc.mark_created("foo", "agent", "2026-01-01T00:00:00+00:00")

    dest = archive_skill(tmp_path, sc, "foo", "2026-07-13T00:00:00+00:00")

    assert dest == tmp_path / ".archive" / "foo"
    assert not (tmp_path / "foo").exists()
    assert (tmp_path / ".archive" / "foo" / "SKILL.md").is_file()
    entry = sc.get("foo")
    assert entry["state"] == "archived"
    assert entry["archived_at"] == "2026-07-13T00:00:00+00:00"


def test_archive_skill_collision_suffix(tmp_path):
    sc = UsageSidecar(tmp_path)
    for i in range(3):
        name = "dup" if i == 0 else f"dup-src-{i}"
        make_skill(tmp_path, name, body=f"body-{i}")
        sc.mark_created(name, "agent", "2026-01-01T00:00:00+00:00")

    # pre-seed .archive/dup and .archive/dup-2 so the third archive attempt collides twice
    (tmp_path / ".archive").mkdir()
    (tmp_path / ".archive" / "dup").mkdir()
    (tmp_path / ".archive" / "dup-2").mkdir()

    dest = archive_skill(tmp_path, sc, "dup", "2026-07-13T00:00:00+00:00")

    assert dest == tmp_path / ".archive" / "dup-3"
    assert (tmp_path / ".archive" / "dup-3" / "SKILL.md").is_file()


def test_list_archived_returns_sorted_names(tmp_path):
    sc = UsageSidecar(tmp_path)
    make_skill(tmp_path, "b")
    make_skill(tmp_path, "a")
    sc.mark_created("a", "agent", "2026-01-01T00:00:00+00:00")
    sc.mark_created("b", "agent", "2026-01-01T00:00:00+00:00")
    archive_skill(tmp_path, sc, "b", "2026-07-13T00:00:00+00:00")
    archive_skill(tmp_path, sc, "a", "2026-07-13T00:00:00+00:00")

    assert list_archived(tmp_path) == ["a", "b"]


def test_list_archived_empty_when_no_archive_dir(tmp_path):
    assert list_archived(tmp_path) == []


def test_restore_skill_moves_back_and_sets_active(tmp_path):
    make_skill(tmp_path, "foo")
    sc = UsageSidecar(tmp_path)
    sc.mark_created("foo", "agent", "2026-01-01T00:00:00+00:00")
    archive_skill(tmp_path, sc, "foo", "2026-07-13T00:00:00+00:00")

    ok = restore_skill(tmp_path, sc, "foo", "2026-07-14T00:00:00+00:00")

    assert ok is True
    assert (tmp_path / "foo" / "SKILL.md").is_file()
    assert not (tmp_path / ".archive" / "foo").exists()
    assert sc.get("foo")["state"] == "active"


def test_restore_skill_returns_false_when_not_archived(tmp_path):
    sc = UsageSidecar(tmp_path)
    assert restore_skill(tmp_path, sc, "nope", "2026-07-14T00:00:00+00:00") is False


def test_restore_skill_refuses_when_live_dir_exists(tmp_path):
    make_skill(tmp_path, "foo", body="archived-version")
    sc = UsageSidecar(tmp_path)
    sc.mark_created("foo", "agent", "2026-01-01T00:00:00+00:00")
    archive_skill(tmp_path, sc, "foo", "2026-07-13T00:00:00+00:00")

    # a new live "foo" was created after archiving
    make_skill(tmp_path, "foo", body="new-live-version")

    ok = restore_skill(tmp_path, sc, "foo", "2026-07-14T00:00:00+00:00")

    assert ok is False
    assert (tmp_path / "foo" / "SKILL.md").read_text(encoding="utf-8") == "new-live-version"
    assert (tmp_path / ".archive" / "foo" / "SKILL.md").is_file()

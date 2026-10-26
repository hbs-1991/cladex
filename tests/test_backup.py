from __future__ import annotations

import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from claude_hermes.core import snapshots as backup_mod
from claude_hermes.core.snapshots import list_snapshots, rollback, snapshot


class _FakeDatetime:
    """Stand-in for the datetime class the module imports, with a movable clock."""

    _now = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls._now

    @classmethod
    def advance(cls, **kwargs):
        cls._now = cls._now + timedelta(**kwargs)


@pytest.fixture
def clock(monkeypatch):
    _FakeDatetime._now = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(backup_mod, "datetime", _FakeDatetime)
    return _FakeDatetime


def make_skill(skills_dir, name, body="hello"):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")


def test_snapshot_round_trips_skill_tree(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo", body="v1")

    snap_dir = snapshot(skills_dir, logs_dir, "test-reason")

    assert snap_dir is not None
    tar_path = snap_dir / "skills.tar.gz"
    assert tar_path.is_file()
    with tarfile.open(tar_path, "r:gz") as tf:
        names = tf.getnames()
        assert "foo/SKILL.md" in names
        member = tf.extractfile("foo/SKILL.md")
        assert member.read().decode("utf-8") == "v1"


def test_snapshot_manifest_fields(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo")

    snap_dir = snapshot(skills_dir, logs_dir, "my-reason")

    manifests = list_snapshots(logs_dir)
    assert len(manifests) == 1
    m = manifests[0]
    assert m["reason"] == "my-reason"
    assert m["id"] == snap_dir.name
    assert m["archive_bytes"] > 0
    assert m["files"] == 1
    assert "created_at" in m


def test_snapshot_returns_none_when_skills_dir_missing(tmp_path, clock):
    skills_dir = tmp_path / "nope"
    logs_dir = tmp_path / "logs"

    assert snapshot(skills_dir, logs_dir, "x") is None


def test_prune_keeps_exactly_newest_keep(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo")

    ids = []
    for i in range(4):
        snap = snapshot(skills_dir, logs_dir, f"reason-{i}", keep=2)
        ids.append(snap.name)
        clock.advance(minutes=1)

    remaining = list_snapshots(logs_dir)
    assert len(remaining) == 2
    remaining_ids = {m["id"] for m in remaining}
    assert remaining_ids == set(ids[-2:])


def test_list_snapshots_newest_first(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo")

    ids = []
    for i in range(3):
        snap = snapshot(skills_dir, logs_dir, f"r{i}", keep=10)
        ids.append(snap.name)
        clock.advance(minutes=1)

    manifests = list_snapshots(logs_dir)
    assert [m["id"] for m in manifests] == list(reversed(ids))


def test_rollback_restores_deleted_skill(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo", body="original")

    snapshot(skills_dir, logs_dir, "before-delete")
    clock.advance(minutes=1)

    # simulate a destructive action
    (skills_dir / "foo" / "SKILL.md").unlink()
    (skills_dir / "foo").rmdir()
    assert not (skills_dir / "foo").exists()

    ok, msg = rollback(skills_dir, logs_dir)

    assert ok is True
    assert (skills_dir / "foo" / "SKILL.md").is_file()
    assert (skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "original"


def test_rollback_no_snapshots_available(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo")

    ok, msg = rollback(skills_dir, logs_dir)

    assert ok is False
    assert "no snapshot" in msg.lower()


def test_rollback_unknown_backup_id(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo")
    snapshot(skills_dir, logs_dir, "only-one")

    ok, msg = rollback(skills_dir, logs_dir, backup_id="does-not-exist")

    assert ok is False


def test_rollback_with_corrupt_tarball_leaves_tree_intact(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo", body="good-snapshot-content")

    snap_dir = snapshot(skills_dir, logs_dir, "good")
    clock.advance(minutes=1)

    # corrupt the archive on disk after the fact
    tar_path = snap_dir / "skills.tar.gz"
    tar_path.write_bytes(b"not a real gzip tarball")

    # current tree diverges from the (corrupt) snapshot before we attempt rollback
    (skills_dir / "foo" / "SKILL.md").write_text("current-live-content", encoding="utf-8")

    ok, msg = rollback(skills_dir, logs_dir, backup_id=snap_dir.name)

    assert ok is False
    assert (skills_dir / "foo" / "SKILL.md").is_file()
    assert (skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "current-live-content"


def test_rollback_protects_target_snapshot_from_prune(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"

    ids = []
    for i in range(5):
        make_skill(skills_dir, "foo", body=f"version-{i}")
        snap = snapshot(skills_dir, logs_dir, f"reason-{i}", keep=5)
        ids.append(snap.name)
        clock.advance(minutes=1)

    target_id = ids[0]  # oldest of the 5 -- would be pruned by a naive keep=5 prune
    assert any(m["id"] == target_id for m in list_snapshots(logs_dir))

    ok, msg = rollback(skills_dir, logs_dir, backup_id=target_id)

    assert ok is True
    assert (skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "version-0"
    # the target snapshot must have survived the pre-rollback prune
    assert any(m["id"] == target_id for m in list_snapshots(logs_dir))


def test_rollback_extraction_rejects_path_traversal(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs_dir = tmp_path / "logs"
    make_skill(skills_dir, "foo", body="good-snapshot-content")

    snap_dir = snapshot(skills_dir, logs_dir, "good")
    clock.advance(minutes=1)

    # rebuild the archive with a malicious member
    tar_path = snap_dir / "skills.tar.gz"
    tar_path.unlink()
    evil = tmp_path / "evil.txt"
    evil.write_text("pwned", encoding="utf-8")
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(evil, arcname="../../evil.txt")

    (skills_dir / "foo" / "SKILL.md").write_text("current-live-content", encoding="utf-8")

    ok, msg = rollback(skills_dir, logs_dir, backup_id=snap_dir.name)

    assert ok is False
    assert not (tmp_path / "evil.txt.pwned").exists()
    assert not (tmp_path.parent / "evil.txt").exists()
    assert (skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "current-live-content"


def test_wiki_snapshots_live_in_their_own_store(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "user").mkdir(parents=True)
    (wiki / "user" / "profile.md").write_text("hi", encoding="utf-8")
    logs = tmp_path / "logs"

    snap = snapshot(wiki, logs, reason="pre-wiki-lint", kind="wiki")
    assert snap is not None
    assert (logs / "wiki_backups").is_dir()
    assert not (logs / "skills_backups").exists()      # the two stores never mix
    assert len(list_snapshots(logs, kind="wiki")) == 1
    assert list_snapshots(logs, kind="skills") == []


def test_wiki_rollback_restores_the_tree(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "user").mkdir(parents=True)
    page = wiki / "user" / "profile.md"
    page.write_text("original", encoding="utf-8")
    logs = tmp_path / "logs"
    snapshot(wiki, logs, reason="pre-wiki-lint", kind="wiki")

    page.write_text("lint ate it", encoding="utf-8")
    ok, msg = rollback(wiki, logs, kind="wiki")
    assert ok, msg
    assert page.read_text(encoding="utf-8") == "original"


def test_skill_edit_snapshots_live_in_their_own_store(tmp_path):
    skills_dir = tmp_path / "skills"
    logs = tmp_path / "logs"
    make_skill(skills_dir, "foo", body="v1")

    snap = snapshot(skills_dir, logs, reason="pre-owner-edit:foo", kind="skill_edits")

    assert snap is not None
    assert (logs / "skill_edit_backups").is_dir()
    # the curator's pre-curator-run snapshot lives in skills_backups and must not be
    # touched -- that is the entire reason this third kind exists
    assert not (logs / "skills_backups").exists()
    assert len(list_snapshots(logs, kind="skill_edits")) == 1
    assert list_snapshots(logs, kind="skills") == []
    assert (snap / "skills.tar.gz").is_file()


def test_skill_edit_prune_leaves_the_curator_store_alone(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs = tmp_path / "logs"
    make_skill(skills_dir, "foo", body="v0")

    curator_snap = snapshot(skills_dir, logs, reason="pre-curator-run", keep=2)
    clock.advance(minutes=1)
    for i in range(4):                       # enough browser saves to blow past keep=2
        snapshot(skills_dir, logs, reason=f"pre-owner-edit-{i}", keep=2,
                 kind="skill_edits")
        clock.advance(minutes=1)

    assert len(list_snapshots(logs, kind="skill_edits")) == 2
    surviving = [m["id"] for m in list_snapshots(logs, kind="skills")]
    assert surviving == [curator_snap.name]


def test_skill_edit_rollback_reads_its_own_store(tmp_path, clock):
    skills_dir = tmp_path / "skills"
    logs = tmp_path / "logs"
    make_skill(skills_dir, "foo", body="original")
    snapshot(skills_dir, logs, reason="pre-owner-edit:foo", kind="skill_edits")

    (skills_dir / "foo" / "SKILL.md").write_text("a bad save", encoding="utf-8")

    assert rollback(skills_dir, logs, kind="skills") == (False, "no snapshots available")
    ok, msg = rollback(skills_dir, logs, kind="skill_edits")
    assert ok, msg
    assert (skills_dir / "foo" / "SKILL.md").read_text(encoding="utf-8") == "original"

import json
from datetime import datetime, timezone

from claude_hermes.modules.learning.domain.usage import last_activity
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar

NOW = "2026-07-12T10:00:00+00:00"


def make_skill(skills_dir, name):
    d = skills_dir / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: X.\n---\n", encoding="utf-8")


def test_mark_created_and_get(tmp_path):
    sc = UsageSidecar(tmp_path)
    sc.mark_created("release-flow", "agent", NOW)
    entry = sc.get("release-flow")
    assert entry["created_by"] == "agent"
    assert entry["state"] == "active"
    assert entry["pinned"] is False
    assert entry["use_count"] == 0
    # persisted to disk
    data = json.loads((tmp_path / ".usage.json").read_text(encoding="utf-8"))
    assert "release-flow" in data["skills"]


def test_bump_use_and_state(tmp_path):
    sc = UsageSidecar(tmp_path)
    sc.mark_created("s", "agent", NOW)
    sc.bump_use("s", "2026-07-13T00:00:00+00:00")
    entry = sc.get("s")
    assert entry["use_count"] == 1
    assert entry["last_used"] == "2026-07-13T00:00:00+00:00"
    sc.set_state("s", "stale")
    assert sc.get("s")["state"] == "stale"
    sc.set_pinned("s", True)
    assert sc.get("s")["pinned"] is True


def test_bump_use_unknown_skill_creates_user_entry(tmp_path):
    sc = UsageSidecar(tmp_path)
    sc.bump_use("hand-written", NOW)  # skill existed before sidecar tracking
    assert sc.get("hand-written")["created_by"] == "user"


def test_list_skill_dirs_excludes_archive(tmp_path):
    make_skill(tmp_path, "a")
    make_skill(tmp_path / ".archive", "old")
    sc = UsageSidecar(tmp_path)
    assert sc.list_skill_dirs() == {"a"}


def test_corrupt_sidecar_starts_fresh(tmp_path):
    (tmp_path / ".usage.json").write_text("{broken", encoding="utf-8")
    sc = UsageSidecar(tmp_path)
    sc.mark_created("s", "agent", NOW)
    assert sc.get("s") is not None


def test_wrong_shape_sidecar_starts_fresh(tmp_path):
    (tmp_path / ".usage.json").write_text('{"skills": null}', encoding="utf-8")
    sc = UsageSidecar(tmp_path)
    sc.bump_use("s", NOW)
    assert sc.get("s")["use_count"] == 1


def test_new_entry_has_patch_and_archive_fields(tmp_path):
    sc = UsageSidecar(tmp_path)
    sc.mark_created("s", "agent", NOW)
    entry = sc.get("s")
    assert entry["patch_count"] == 0
    assert entry["last_patched"] is None
    assert entry["archived_at"] is None


def test_bump_patch_increments_and_creates_user_entry(tmp_path):
    sc = UsageSidecar(tmp_path)
    sc.bump_patch("hand-written", NOW)  # absent skill -> user-created entry
    entry = sc.get("hand-written")
    assert entry["created_by"] == "user"
    assert entry["patch_count"] == 1
    assert entry["last_patched"] == NOW
    sc.bump_patch("hand-written", "2026-07-13T00:00:00+00:00")
    assert sc.get("hand-written")["patch_count"] == 2
    assert sc.get("hand-written")["last_patched"] == "2026-07-13T00:00:00+00:00"


def test_set_archived(tmp_path):
    sc = UsageSidecar(tmp_path)
    sc.mark_created("s", "agent", NOW)
    sc.set_archived("s", "2026-07-13T00:00:00+00:00")
    entry = sc.get("s")
    assert entry["state"] == "archived"
    assert entry["archived_at"] == "2026-07-13T00:00:00+00:00"


def test_old_schema_entry_loads_without_keyerror(tmp_path):
    # simulate an install upgraded from before patch_count/last_patched/archived_at existed
    old = {
        "skills": {
            "legacy": {
                "created_by": "agent",
                "created_at": NOW,
                "last_used": None,
                "use_count": 0,
                "state": "active",
                "pinned": False,
            }
        }
    }
    (tmp_path / ".usage.json").write_text(json.dumps(old), encoding="utf-8")
    sc = UsageSidecar(tmp_path)
    entry = sc.get("legacy")
    assert entry is not None
    # must not raise, and bump_patch/bump_use must work on old-schema entries
    sc.bump_patch("legacy", "2026-07-13T00:00:00+00:00")
    sc.bump_use("legacy", "2026-07-13T00:00:00+00:00")
    entry = sc.get("legacy")
    assert entry["patch_count"] == 1
    assert entry["last_patched"] == "2026-07-13T00:00:00+00:00"
    assert entry["use_count"] == 1


def test_last_activity_prefers_last_used_over_created_at():
    entry = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "last_used": "2026-06-01T00:00:00+00:00",
        "last_patched": None,
    }
    dt = last_activity(entry)
    assert dt == datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_last_activity_prefers_last_patched_when_newest():
    entry = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "last_used": "2026-02-01T00:00:00+00:00",
        "last_patched": "2026-06-01T00:00:00+00:00",
    }
    dt = last_activity(entry)
    assert dt == datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_last_activity_falls_back_to_created_at_and_handles_naive():
    entry = {"created_at": "2026-01-01T00:00:00", "last_used": None, "last_patched": None}
    dt = last_activity(entry)
    assert dt == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert dt.tzinfo is not None

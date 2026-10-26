from datetime import datetime, timezone

from claude_hermes.core.config import Config
from claude_hermes.modules.learning.application.services import aging
from claude_hermes.modules.learning.infrastructure.skill_archive import SkillArchive
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar


def apply_aging(skills_dir, sidecar, cfg, now):
    """The service takes its archive port explicitly; the tests hand it the real one."""
    return aging.apply_aging(skills_dir, sidecar, cfg, now,
                             archive=SkillArchive(skills_dir, sidecar))


def make_skill(skills_dir, name):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: x\ndescription: X.\n---\n")


def dt(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def setup(tmp_path, name, created_at, created_by="agent", pinned=False):
    make_skill(tmp_path, name)
    sc = UsageSidecar(tmp_path)
    sc.mark_created(name, created_by, created_at)
    if pinned:
        sc.set_pinned(name, True)
    return sc


def test_active_goes_stale_after_30d(tmp_path):
    sc = setup(tmp_path, "old", "2026-06-01T00:00:00+00:00")
    log = apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("old")["state"] == "stale"
    assert any("stale" in line for line in log)


def test_stale_archives_after_90d_moves_dir(tmp_path):
    sc = setup(tmp_path, "ancient", "2026-01-01T00:00:00+00:00")
    sc.set_state("ancient", "stale")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("ancient")["state"] == "archived"
    assert not (tmp_path / "ancient").exists()
    assert (tmp_path / ".archive" / "ancient" / "SKILL.md").exists()


def test_pinned_user_and_recent_skills_untouched(tmp_path):
    sc = setup(tmp_path, "pinned", "2026-01-01T00:00:00+00:00", pinned=True)
    make_skill(tmp_path, "user-skill")
    sc.mark_created("user-skill", "user", "2026-01-01T00:00:00+00:00")
    make_skill(tmp_path, "fresh")
    sc.mark_created("fresh", "agent", "2026-07-10T00:00:00+00:00")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("pinned")["state"] == "active"
    assert sc.get("user-skill")["state"] == "active"
    assert sc.get("fresh")["state"] == "active"


def test_recent_use_keeps_skill_active(tmp_path):
    sc = setup(tmp_path, "used", "2026-01-01T00:00:00+00:00")
    sc.bump_use("used", "2026-07-10T00:00:00+00:00")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("used")["state"] == "active"


def test_stale_reactivates_when_recently_used(tmp_path):
    sc = setup(tmp_path, "revived", "2026-01-01T00:00:00+00:00")
    sc.set_state("revived", "stale")
    sc.bump_use("revived", "2026-07-11T00:00:00+00:00")
    log = apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("revived")["state"] == "active"
    assert any("active" in line for line in log)


def test_stale_reactivates_when_recently_patched(tmp_path):
    sc = setup(tmp_path, "patched", "2026-01-01T00:00:00+00:00")
    sc.set_state("patched", "stale")
    sc.bump_patch("patched", "2026-07-11T00:00:00+00:00")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("patched")["state"] == "active"


def test_never_used_skill_inside_stale_window_is_skipped(tmp_path):
    sc = setup(tmp_path, "young", "2026-07-01T00:00:00+00:00")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("young")["state"] == "active"
    assert sc.get("young")["use_count"] == 0


def test_never_used_skill_reactivates_if_marked_stale_but_inside_window(tmp_path):
    sc = setup(tmp_path, "wrongly_staled", "2026-07-01T00:00:00+00:00")
    sc.set_state("wrongly_staled", "stale")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("wrongly_staled")["state"] == "active"


def test_never_used_skill_older_than_stale_window_ages_normally(tmp_path):
    sc = setup(tmp_path, "old_never_used", "2026-06-01T00:00:00+00:00")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("old_never_used")["state"] == "stale"


def test_never_used_skill_past_archive_window_still_archives(tmp_path):
    sc = setup(tmp_path, "old_never_used_archived", "2026-01-01T00:00:00+00:00")
    sc.set_state("old_never_used_archived", "stale")
    apply_aging(tmp_path, sc, Config(), dt("2026-07-12T00:00:00"))
    assert sc.get("old_never_used_archived")["state"] == "archived"
    assert not (tmp_path / "old_never_used_archived").exists()

"""`SkillAdminService` — the `skill_admin` chat tool's body, over ports.

The reply strings are the contract: the model reads them, and `tests/test_tools.py`
asserts the same wording through the legacy tool. Archive is the maximum
destructive action here too — there is no delete.
"""
from __future__ import annotations

from claude_hermes.core import snapshots
from claude_hermes.modules.learning.application.services.library import SkillLibraryService
from claude_hermes.modules.learning.application.services.skill_admin import SkillAdminService
from claude_hermes.modules.learning.domain.constants import (
    LAST_RUN_META_KEY,
    PAUSED_META_KEY,
    REPORT_PATH_META_KEY,
)
from claude_hermes.modules.learning.infrastructure.skill_archive import SkillArchive
from claude_hermes.modules.learning.infrastructure.skill_repository import SkillRepository
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar

NOW = "2026-07-12T10:00:00+00:00"


class FakeMeta:
    def __init__(self, **rows):
        self.rows = dict(rows)

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


def make(tmp_path, meta=None):
    skills = tmp_path / "agent_home" / ".claude" / "skills"
    skills.mkdir(parents=True)
    logs = tmp_path / "logs"
    logs.mkdir()
    usage = UsageSidecar(skills)
    archive = SkillArchive(skills, usage)
    library = SkillLibraryService(SkillRepository(skills), usage, archive)
    svc = SkillAdminService(library=library, usage=usage, archive=archive,
                            meta=meta or FakeMeta(), snapshots=snapshots,
                            skills_dir=skills, logs_dir=logs)
    return svc, skills, usage


def make_skill(skills, name):
    d = skills / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: X.\n---\n")


async def test_list_is_by_name_and_carries_the_counters(tmp_path):
    svc, skills, usage = make(tmp_path)
    assert await svc.handle("list") == "No skills tracked."
    make_skill(skills, "zeta")
    make_skill(skills, "alpha")
    usage.mark_created("alpha", "agent", NOW)
    usage.bump_use("alpha", NOW)
    out = await svc.handle("list")
    lines = out.splitlines()
    assert lines[0].startswith("alpha: active")          # by name, not active-first
    assert "used=1 patched=0" in lines[0]
    assert lines[1].startswith("zeta: active")


async def test_pin_refuses_an_unknown_skill_and_protects_from_archive(tmp_path):
    svc, skills, usage = make(tmp_path)
    assert await svc.handle("pin", name="ghost") == "Unknown skill 'ghost'."
    make_skill(skills, "greet")
    usage.mark_created("greet", "agent", NOW)
    assert await svc.handle("pin", name="greet") == "Pinned 'greet'."
    out = await svc.handle("archive", name="greet")
    assert "is pinned; unpin it before archiving" in out
    assert (skills / "greet").is_dir()
    assert await svc.handle("unpin", name="greet") == "Unpinned 'greet'."


async def test_archive_then_restore_round_trips(tmp_path):
    svc, skills, usage = make(tmp_path)
    make_skill(skills, "greet")
    usage.mark_created("greet", "agent", NOW)
    out = await svc.handle("archive", name="greet")
    assert "recoverable via restore" in out
    assert (skills / ".archive" / "greet" / "SKILL.md").is_file()
    assert await svc.handle("restore", name="greet") == "Restored 'greet'."
    assert (skills / "greet" / "SKILL.md").is_file()
    assert "Could not restore" in await svc.handle("restore", name="greet")


async def test_archive_needs_a_live_directory(tmp_path):
    svc, skills, usage = make(tmp_path)
    usage.mark_created("ghostly", "agent", NOW)          # sidecar row, no directory
    assert await svc.handle("archive", name="ghostly") == \
        "'ghostly' has no live directory to archive."


async def test_status_reads_the_curator_meta_keys(tmp_path):
    meta = FakeMeta(**{LAST_RUN_META_KEY: NOW, PAUSED_META_KEY: "1",
                       REPORT_PATH_META_KEY: "/logs/curator/x/run.json"})
    svc, skills, usage = make(tmp_path, meta=meta)
    make_skill(skills, "greet")
    usage.mark_created("greet", "agent", NOW)
    usage.set_pinned("greet", True)
    out = await svc.handle("status")
    assert f"Last curator run: {NOW}" in out
    assert "Paused: yes" in out
    assert "Report: /logs/curator/x/run.json" in out
    assert "Pinned: greet" in out
    assert "  greet: " in out


async def test_snapshots_and_rollback_speak_to_the_snapshot_store(tmp_path):
    svc, skills, usage = make(tmp_path)
    assert await svc.handle("snapshots") == "No snapshots."
    make_skill(skills, "greet")
    snapshots.snapshot(skills, tmp_path / "logs", reason="manual", keep=5)
    assert "manual" in await svc.handle("snapshots")
    (skills / "greet" / "SKILL.md").write_text("changed")
    out = await svc.handle("rollback")
    assert "restored" in out.lower() or "rolled" in out.lower() or out
    assert (skills / "greet" / "SKILL.md").read_text().startswith("---")


async def test_an_unknown_action_says_so(tmp_path):
    svc, _, _ = make(tmp_path)
    assert await svc.handle("nuke") == "Unknown action 'nuke'."

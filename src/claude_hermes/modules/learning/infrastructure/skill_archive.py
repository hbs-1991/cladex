"""Archive and restore a skill directory. Moved from `learning/archive.py`.

The maximum destructive action anywhere in this module: a move into `.archive/`,
never a delete (PRD P4). The suffix loop matters — the curator and the browser can
both archive the same name in different weeks, and the older copy stays readable.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from claude_hermes.modules.learning.application.ports import SkillUsagePort
from claude_hermes.modules.learning.domain.constants import ARCHIVE_DIR


def _archive_target(skills_dir: Path, name: str) -> Path:
    base = skills_dir / ARCHIVE_DIR
    base.mkdir(exist_ok=True)
    target, n = base / name, 2
    while target.exists():
        target = base / f"{name}-{n}"
        n += 1
    return target


def archive_skill(skills_dir: Path, sidecar: SkillUsagePort, name: str,
                  now_iso: str) -> Path:
    """Move skills_dir/<name> -> skills_dir/.archive/<name-or-suffixed>, mark sidecar."""
    target = _archive_target(skills_dir, name)
    shutil.move(str(skills_dir / name), str(target))
    sidecar.set_archived(name, now_iso)
    return target


def restore_skill(skills_dir: Path, sidecar: SkillUsagePort, name: str,
                  now_iso: str) -> bool:
    archived = skills_dir / ARCHIVE_DIR / name
    if not archived.is_dir():
        return False
    if (skills_dir / name).exists():
        return False
    shutil.move(str(archived), str(skills_dir / name))
    sidecar.set_state(name, "active")
    return True


def list_archived(skills_dir: Path) -> list[str]:
    base = skills_dir / ARCHIVE_DIR
    if not base.is_dir():
        return []
    return sorted(d.name for d in base.iterdir() if d.is_dir())


class SkillArchive:
    """`SkillArchivePort` over one skills directory and its sidecar."""

    def __init__(self, skills_dir: Path, usage: SkillUsagePort):
        self._dir = skills_dir
        self._usage = usage

    def archive_skill(self, name: str, now_iso: str) -> Path:
        return archive_skill(self._dir, self._usage, name, now_iso)

    def restore_skill(self, name: str, now_iso: str) -> bool:
        return restore_skill(self._dir, self._usage, name, now_iso)

    def list_archived(self) -> list[str]:
        return list_archived(self._dir)

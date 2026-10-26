"""`.usage.json` — provenance and usage telemetry for skills, outside SKILL.md.

Moved from `learning/usage.py`. One of three writers into the skills directory
(the library and the archive are the others); all three replace atomically via
mkstemp + os.replace, because the review fork may be reading the same tree.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from claude_hermes.modules.learning.domain.constants import ARCHIVE_DIR, SKILL_FILE, USAGE_FILE
from claude_hermes.modules.learning.domain.usage import new_entry

_FILE = USAGE_FILE


class UsageSidecar:
    """Provenance + usage telemetry for skills, outside SKILL.md content."""

    def __init__(self, skills_dir: Path):
        self._dir = skills_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def skills_dir(self) -> Path:
        return self._dir

    def _load(self) -> dict:
        path = self._dir / _FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"skills": {}}
        if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
            return {"skills": {}}  # valid JSON, wrong shape → start fresh
        return data

    def _save(self, data: dict) -> None:
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self._dir))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._dir / _FILE)
        except OSError:
            pass  # telemetry is best-effort; never fail a turn over it

    def mark_created(self, name: str, created_by: str, now_iso: str) -> None:
        data = self._load()
        data["skills"][name] = new_entry(created_by, now_iso)
        self._save(data)

    def bump_use(self, name: str, now_iso: str) -> None:
        data = self._load()
        entry = data["skills"].setdefault(name, new_entry("user", now_iso))
        entry["use_count"] = entry.get("use_count", 0) + 1
        entry["last_used"] = now_iso
        self._save(data)

    def bump_patch(self, name: str, now_iso: str) -> None:
        data = self._load()
        entry = data["skills"].setdefault(name, new_entry("user", now_iso))
        entry["patch_count"] = entry.get("patch_count", 0) + 1
        entry["last_patched"] = now_iso
        self._save(data)

    def set_archived(self, name: str, now_iso: str) -> None:
        data = self._load()
        if name in data["skills"]:
            data["skills"][name]["state"] = "archived"
            data["skills"][name]["archived_at"] = now_iso
            self._save(data)

    def get(self, name: str) -> dict | None:
        return self._load()["skills"].get(name)

    def set_state(self, name: str, state: str) -> None:
        data = self._load()
        if name in data["skills"]:
            data["skills"][name]["state"] = state
            self._save(data)

    def set_pinned(self, name: str, pinned: bool) -> None:
        data = self._load()
        if name in data["skills"]:
            data["skills"][name]["pinned"] = pinned
            self._save(data)

    def all(self) -> dict[str, dict]:
        return self._load()["skills"]

    def list_skill_dirs(self) -> set[str]:
        return {
            d.name
            for d in self._dir.iterdir()
            if d.is_dir() and d.name != ARCHIVE_DIR and (d / SKILL_FILE).is_file()
        }

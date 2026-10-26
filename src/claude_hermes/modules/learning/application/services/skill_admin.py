"""The `skill_admin` agent tool, as a service.

The body moves from `tools.py`: same actions, same reply strings, one method per
call so the turns module can bind it to an MCP tool without importing learning.
Two things it deliberately does NOT do: delete anything (archive is the maximum),
and invent a second listing — `list` reads the same `SkillLibraryService` the
browser reads, only ordered by name because a chat reply is grepped, not scrolled.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from claude_hermes.modules.learning.application.ports import (
    MetaStore,
    SkillArchivePort,
    SkillLibraryPort,
    SkillUsagePort,
    SnapshotStorePort,
)
from claude_hermes.modules.learning.domain.constants import (
    LAST_RUN_META_KEY,
    PAUSED_META_KEY,
    REPORT_PATH_META_KEY,
)
from claude_hermes.modules.learning.domain.models import last_activity_iso
from claude_hermes.modules.learning.domain.usage import last_activity


class SkillAdminService:
    """One method per `skill_admin` action; `handle` dispatches like the tool does."""

    def __init__(self, *, library: SkillLibraryPort, usage: SkillUsagePort,
                 archive: SkillArchivePort, meta: MetaStore,
                 snapshots: SnapshotStorePort, skills_dir: Path, logs_dir: Path,
                 backup_keep: int = 5):
        self._library = library
        self._usage = usage
        self._archive = archive
        self._meta = meta
        self._snapshots = snapshots
        self._skills_dir = skills_dir
        self._logs_dir = logs_dir
        self._backup_keep = backup_keep

    async def handle(self, action: str = "list", *, name: str = "",
                     backup_id: str | None = None) -> str:
        now_iso = datetime.now(timezone.utc).isoformat()
        entries = self._usage.all()

        if action == "list":
            # One implementation of the read, two channels: this and the skills
            # router. The line format is this tool's own, and the by-name order is
            # kept — the wire sorts active-first for a screen, which is not what a
            # chat reply the model greps through wants.
            rows = self._library.list_skills()
            if not rows:
                return "No skills tracked."
            return "\n".join(
                f"{r['name']}: {r['state']}{' [pinned]' if r['pinned'] else ''} "
                f"used={r['use_count']} patched={r['patch_count']} "
                f"last_activity={last_activity_iso(r)}"
                for r in sorted(rows, key=lambda r: r["name"])
            )

        if action in ("pin", "unpin"):
            if name not in entries:
                return f"Unknown skill '{name}'."
            self._usage.set_pinned(name, action == "pin")
            return f"{'Pinned' if action == 'pin' else 'Unpinned'} '{name}'."

        if action == "archive":
            entry = entries.get(name)
            if entry is None:
                return f"Unknown skill '{name}'."
            if entry.get("pinned"):
                return (
                    f"'{name}' is pinned; unpin it before archiving. "
                    "Pinning protects a skill from manual archiving too, not just the curator."
                )
            if not (self._skills_dir / name).is_dir():
                return f"'{name}' has no live directory to archive."
            path = self._archive.archive_skill(name, now_iso)
            return f"Archived '{name}' to {path}. This is recoverable via restore."

        if action == "restore":
            if self._archive.restore_skill(name, now_iso):
                return f"Restored '{name}'."
            return (
                f"Could not restore '{name}': not found in .archive/, "
                "or a live copy already exists."
            )

        if action == "status":
            last_run = await self._meta.get(LAST_RUN_META_KEY)
            paused = await self._meta.get(PAUSED_META_KEY)
            report_path = await self._meta.get(REPORT_PATH_META_KEY)
            pinned = sorted(n for n, e in entries.items() if e.get("pinned"))
            lru = sorted(entries.items(), key=lambda kv: last_activity(kv[1]))[:5]
            lines = [
                f"Last curator run: {last_run or 'never'}",
                f"Paused: {'yes' if paused == '1' else 'no'}",
                f"Report: {report_path or '—'}",
                f"Pinned: {', '.join(pinned) if pinned else 'none'}",
                "Least recently used:",
            ]
            lines += [f"  {n}: {last_activity(e).isoformat()}" for n, e in lru]
            return "\n".join(lines)

        if action == "snapshots":
            snaps = self._snapshots.list_snapshots(self._logs_dir)
            if not snaps:
                return "No snapshots."
            return "\n".join(
                f"{s['id']}: {s['reason']} ({s.get('files', s.get('skill_files', 0))} files)"
                for s in snaps
            )

        if action == "rollback":
            _, msg = self._snapshots.rollback(self._skills_dir, self._logs_dir,
                                              backup_id or None, keep=self._backup_keep)
            return msg

        return f"Unknown action '{action}'."

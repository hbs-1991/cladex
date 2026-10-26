"""Aging agent-created skills: active -> stale -> archived. Moved from the curator.

Maximum destructive action: a move into `.archive/`. Pinned skills and anything
the user made are outside the pass's jurisdiction entirely.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from claude_hermes.modules.learning.application.ports import SkillArchivePort, SkillUsagePort
from claude_hermes.modules.learning.domain.usage import last_activity


def apply_aging(skills_dir: Path, sidecar: SkillUsagePort, cfg, now: datetime,
                archive: SkillArchivePort | None = None) -> list[str]:
    """Age agent-created skills. Max destructive action: move to .archive/.

    A use_count==0 skill is absence of evidence, not evidence of staleness:
    while its activity anchor sits inside the stale window it is skipped (and
    reactivated if some earlier pass wrongly staled it) exactly like a recently
    used skill would be. Once the anchor falls outside the window it ages like
    any other skill — the grace floor is a delay, not an exemption.
    """
    actions: list[str] = []
    for name, entry in sidecar.all().items():
        if entry["created_by"] != "agent" or entry["pinned"]:
            continue
        if not (skills_dir / name).is_dir():
            continue
        state = entry["state"]
        idle = now - last_activity(entry)
        if idle <= timedelta(days=cfg.stale_after_days):
            if state == "stale":
                sidecar.set_state(name, "active")
                actions.append(f"{name}: stale -> active (idle {idle.days}d, reactivated)")
            continue
        if state == "active":
            sidecar.set_state(name, "stale")
            actions.append(f"{name}: active -> stale (idle {idle.days}d)")
        elif state == "stale" and idle > timedelta(days=cfg.archive_after_days):
            if archive is None:
                raise ValueError("apply_aging needs an archive port to archive a skill")
            archive.archive_skill(name, now.isoformat())
            actions.append(f"{name}: stale -> archived (idle {idle.days}d)")
    return actions


def preview_aging(skills_dir: Path, sidecar: SkillUsagePort, cfg,
                  now: datetime) -> list[str]:
    """Read-only mirror of apply_aging for dry-run reports: never mutates state."""
    actions: list[str] = []
    for name, entry in sidecar.all().items():
        if entry["created_by"] != "agent" or entry["pinned"]:
            continue
        if not (skills_dir / name).is_dir():
            continue
        state = entry["state"]
        idle = now - last_activity(entry)
        if idle <= timedelta(days=cfg.stale_after_days):
            if state == "stale":
                actions.append(f"{name}: stale -> active (idle {idle.days}d) [dry-run]")
            continue
        if state == "active":
            actions.append(f"{name}: active -> stale (idle {idle.days}d) [dry-run]")
        elif state == "stale" and idle > timedelta(days=cfg.archive_after_days):
            actions.append(f"{name}: stale -> archived (idle {idle.days}d) [dry-run]")
    return actions


def candidates(sidecar: SkillUsagePort) -> list[dict]:
    """The rows the consolidation prompt is allowed to reason about."""
    rows = []
    for name, entry in sidecar.all().items():
        if entry.get("created_by") != "agent" or entry.get("pinned"):
            continue
        if entry.get("state") == "archived":
            continue
        rows.append({
            "name": name,
            "state": entry.get("state"),
            "pinned": entry.get("pinned"),
            "use_count": entry.get("use_count", 0),
            "patch_count": entry.get("patch_count", 0),
            "last_activity": last_activity(entry).isoformat(),
        })
    return rows

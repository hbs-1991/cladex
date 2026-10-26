"""What a turn did to the skills library and the wiki, read off the SDK's blocks.

It lives with `turns` because it parses the blocks the runner loop already has in
hand; `learning` reads the finished `TurnStats` (in `core.events`, so neither
module has to import the other).
"""
from __future__ import annotations

from claude_hermes.core.events import TurnStats

_SKILLS_MARKER = ".claude/skills"
_WIKI_MARKER = "/wiki/"

__all__ = ["TurnStats", "observe_block"]


def _skill_dir_name(path: str) -> str | None:
    idx = path.find(_SKILLS_MARKER)
    if idx == -1:
        return None
    rest = path[idx + len(_SKILLS_MARKER):].lstrip("/")
    if not rest:
        return None
    parts = rest.split("/", 1)
    if len(parts) < 2:
        return None  # write landed directly in the skills root, not inside a skill dir
    return parts[0]


def observe_block(stats: TurnStats, block) -> None:
    stats.tool_uses += 1
    name = getattr(block, "name", "")
    args = getattr(block, "input", None) or {}
    if name in ("Write", "Edit"):
        path = str(args.get("file_path", "")).replace("\\", "/")
        if _SKILLS_MARKER in path:
            stats.wrote_skill = True
            skill = _skill_dir_name(path)
            if skill:
                stats.skill_patches.append(skill)
        elif _WIKI_MARKER in path:
            stats.wrote_wiki = True
    elif name == "Skill":
        skill = args.get("skill") or args.get("name")
        if skill:
            stats.skill_invocations.append(str(skill))

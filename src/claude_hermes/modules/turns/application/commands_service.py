"""What can follow a `/` in the composer.

One list per logged-in user — `home` is the caller's tree and `owner_id` the caller,
so two users with different skills and different enabled plugins get different
menus — from the three places that each know a part of
it: the skills seeded into agent_home (every `<name>/SKILL.md` is a `/name` the CLI
registers), the enabled plugins (a plugin.json names what it adds, and the CLI
registers each as `<plugin>:<command>`), and the names the CLI itself reported in
the `init` message of the last turn (`TurnService.slash_commands`) — the built-ins
like `compact` that no file on disk describes. Before the first turn that last part
is empty and the list is skills + plugins alone. The browser inserts `/name ` and
sends the message as typed; the CLI does the dispatch.

Which plugins are enabled and where each was cloned is `extensions`' knowledge, so
it arrives through `SlashSourcePort` rather than by importing that module.
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog
import yaml

from claude_hermes.modules.turns.application.ports import SlashSourcePort

log = structlog.get_logger(__name__)

MAX_DESCRIPTION = 200
SOURCES = ("skill", "plugin", "sdk")


def _entry(name: str, description: str, source: str) -> dict:
    return {"name": name, "description": description, "source": source}


def _frontmatter(path: Path) -> dict:
    """The YAML block between the leading `---` fences, or {} for anything else."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        data = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _description(meta: dict) -> str:
    """One line, capped — the menu shows it beside the name."""
    raw = meta.get("description")
    if not isinstance(raw, str) or not raw.strip():
        return ""
    return raw.strip().splitlines()[0].strip()[:MAX_DESCRIPTION]


def _name(meta: dict, fallback: str) -> str:
    raw = meta.get("name")
    return raw.strip() if isinstance(raw, str) and raw.strip() else fallback


def skill_entries(skills_dir: Path, source: str = "skill", prefix: str = "") -> list[dict]:
    """One entry per `<dir>/SKILL.md`; the frontmatter name wins over the directory's."""
    if not skills_dir.is_dir():
        return []
    out = []
    for skill in sorted(skills_dir.iterdir()):
        md = skill / "SKILL.md"
        if md.is_file():
            meta = _frontmatter(md)
            out.append(_entry(prefix + _name(meta, skill.name), _description(meta), source))
    return out


def command_entries(commands_dir: Path, prefix: str) -> list[dict]:
    """One entry per `*.md` under a plugin's commands directory, named by the file stem."""
    if not commands_dir.is_dir():
        return []
    return [_entry(prefix + p.stem, _description(_frontmatter(p)), "plugin")
            for p in sorted(commands_dir.rglob("*.md"))]


def _inside(root: Path, raw: object) -> Path | None:
    """A path from plugin.json, only if it stays inside the plugin. The manifest is
    the plugin's own word, and a path is the one thing in it that can point elsewhere."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    p = (root / raw.strip()).resolve()
    return p if p == root or p.is_relative_to(root) else None


def _declared(manifest: dict, key: str, root: Path) -> list[Path]:
    """The paths a plugin.json names under `key` — a string or a list — or the
    conventional directory when it names none (naming some replaces the default)."""
    raw = manifest.get(key)
    if raw is None:
        return [root / key]
    items = raw if isinstance(raw, list) else [raw]
    return [p for p in (_inside(root, i) for i in items) if p is not None]


def plugin_entries(root: Path, fallback_ns: str) -> list[dict]:
    """What one installed plugin adds, namespaced `<plugin>:<command>` the way the
    CLI registers it. The namespace is plugin.json's `name`; the catalog name is
    only the fallback for a manifest that lacks one."""
    root = root.resolve()
    try:
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(manifest, dict):
        manifest = {}
    prefix = _name(manifest, fallback_ns) + ":"
    out: list[dict] = []
    for p in _declared(manifest, "commands", root):
        if p.is_dir():
            out.extend(command_entries(p, prefix))
        elif p.is_file() and p.suffix == ".md":
            out.append(_entry(prefix + p.stem, _description(_frontmatter(p)), "plugin"))
    for p in _declared(manifest, "skills", root):
        if (p / "SKILL.md").is_file():            # the path IS one skill
            meta = _frontmatter(p / "SKILL.md")
            out.append(_entry(prefix + _name(meta, p.name), _description(meta), "plugin"))
        elif p.is_dir():
            out.extend(skill_entries(p, "plugin", prefix))
    return out


class SlashCommandsService:
    def __init__(self, *, home: Path | None, sources: SlashSourcePort | None = None,
                 owner_id: int = 0):
        self._home = home
        self._sources = sources
        # Whose plugins. `home` already decides whose skills; this decides the other
        # half, because enablement is per user and the roots are asked for by owner.
        self._owner_id = owner_id

    async def collect(self, sdk_commands=()) -> list[dict]:
        """Skills, then plugins, then what the CLI reported; the first to claim a
        name keeps it, so a skill the CLI also lists carries the skill's description."""
        entries: list[dict] = []
        home = self._home
        if home is not None:
            entries.extend(skill_entries(home / "agent_home" / ".claude" / "skills"))
            if self._sources is not None:
                for root, fallback_ns in await self._sources.plugin_roots(
                        owner_id=self._owner_id):
                    entries.extend(plugin_entries(root, fallback_ns))
        for name in sdk_commands or []:
            # `__`-prefixed names are the CLI's own plumbing, not something to type.
            if isinstance(name, str) and name and not name.startswith("__"):
                entries.append(_entry(name, "", "sdk"))
        seen: set[str] = set()
        out: list[dict] = []
        for e in entries:
            if e["name"] not in seen:
                seen.add(e["name"])
                out.append(e)
        return out

"""What an installed plugin actually adds, read from the clone on disk.

The registry records *that* a plugin is installed, never *what it runs* — and it is
right not to: a listing is a page load, the files are local, and a value read from
disk cannot go stale the way a stored copy can. The opposite trade-off from
`tool_count`, which is stored precisely because reading it costs a network probe.

Nothing here raises. A plugin whose `plugin.json` is junk is still a plugin whose
files a human should be able to see, and a listing that dies on one bad clone is
worse than one that shows a blank author.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from ..domain.drafts import safe
from ..domain.exceptions import ComposeError
from ..domain.manifest import Manifest
from ..domain.paths import plugin_root
from ..domain.plugin_surface import BundledServer, _classify, render_surface, surface_token

log = structlog.get_logger(__name__)

MANIFEST_REL = Path(".claude-plugin") / "plugin.json"
MCP_REL = Path(".mcp.json")

# A plugin with more than a hundred commands is not a plugin anyone reads in a browser;
# the cap is there so a pathological clone cannot make the response unbounded.
_CAP = 100

__all__ = ["MANIFEST_REL", "MCP_REL", "BundledServer", "DiskPluginReader",
           "bundled_servers", "components", "is_present", "local_servers",
           "plugin_root", "safe", "surface", "surface_token"]


def _stems(directory: Path) -> list[str]:
    """Names of `*.md` files directly inside `directory`, sorted, capped.

    Not recursive: `commands/nested/deep.md` is namespaced by Claude Code and would
    list as a bare `deep` here, which reads as a command the plugin does not have.
    Showing the flat surface honestly beats showing a nested one wrongly.
    """
    try:
        return sorted(p.stem for p in directory.glob("*.md") if p.is_file())[:_CAP]
    except OSError:
        return []


def _skill_names(directory: Path) -> list[str]:
    """Subdirectories of `skills/` that actually carry a SKILL.md."""
    try:
        return sorted(
            d.name for d in directory.iterdir() if (d / "SKILL.md").is_file()
        )[:_CAP]
    except OSError:
        return []


def _describe(manifest_path: Path) -> tuple[str, str]:
    """`author` and `version` out of plugin.json, tolerating any shape.

    The file belongs to the plugin author, not to us: it may be absent, truncated
    mid-write, an array, or carry an `author` that is an object in one plugin and a
    plain string in the next. Every one of those reads as "no author".
    """
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""

    author = data.get("author")
    if isinstance(author, dict):
        author = author.get("name")
    version = data.get("version")
    return (
        author if isinstance(author, str) else "",
        version if isinstance(version, str) else "",
    )


def components(root: Path) -> dict | None:
    """Everything the plugin at `root` adds to a turn, or None if it is not a plugin.

    `root` is a `plugin_root` — the clone, or the subdirectory of it named by the
    manifest. None means `.claude-plugin/plugin.json` is absent, which is exactly
    the condition `ComposeService.for_scope` refuses to hand the SDK: not installed, or a
    re-install that failed and took the directory with it.
    """
    manifest_path = root / MANIFEST_REL
    try:
        if not manifest_path.is_file():
            return None
    except OSError:
        return None

    author, version = _describe(manifest_path)
    return {
        "commands": _stems(root / "commands"),
        "skills": _skill_names(root / "skills"),
        "agents": _stems(root / "agents"),
        "hooks": (root / "hooks" / "hooks.json").is_file(),
        "mcp": (root / ".mcp.json").is_file(),
        "author": author,
        "plugin_version": version,
    }


def _server_specs(root: Path) -> dict:
    """The `mcpServers` mappings a plugin declares, merged, tolerating any shape.

    `.mcp.json` at the plugin root is the documented mechanism and the only one seen
    across the marketplaces on this machine. `plugin.json` is read for the same key as
    well: if Claude Code ignores it there, the cost of looking is one refusal for a
    server the plugin declared and never ran — and if it does not ignore it, looking
    is the difference between a check and a hole.
    """
    merged: dict = {}
    for path in (root / MCP_REL, root / MANIFEST_REL):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            for name, spec in servers.items():
                merged.setdefault(str(name), spec)
    return merged


def bundled_servers(root: Path) -> list[BundledServer]:
    """Every MCP server the plugin at `root` brings, by name. Never raises."""
    return [_classify(name, spec) for name, spec in sorted(_server_specs(root).items())]


def local_servers(root: Path) -> list[BundledServer]:
    """The bundled servers that would run a command inside the container.

    A remote server the owner did not choose is a URL in a prompt they can read. A
    local one is an arbitrary argv — `bun run …`, `npx -y whatever` — arriving by a
    route that never passed the catalog's `npx`/`uvx`/`python` allowlist, which exists
    for exactly this. Install refuses on these; the remote ones are shown and approved.
    """
    return [s for s in bundled_servers(root) if s.local]


def surface(m: Manifest, root: Path) -> str:
    """What the plugin at `root` will add, in the form a human approves. The text is
    rendered by the domain (`render_surface`); reading the clone is this layer's job."""
    return render_surface(m, components(root) or {}, bundled_servers(root))


def is_present(home: Path, m: Manifest) -> bool:
    """Is this manifest's plugin really on disk right now?

    The registry can say `installed` while the clone is gone — a failed re-install, a
    wiped volume — and today nothing notices until the next turn composes the scope and
    records `last_error`. This is the cheap check that lets a listing say so first.
    """
    try:
        return (plugin_root(home, m) / MANIFEST_REL).is_file()
    except (ComposeError, OSError) as e:
        # A subdir that resolves outside the clone is not "absent", but for a reader it
        # amounts to the same thing: there is nothing here we would ever load.
        log.debug("plugin %s is not usable on disk: %s", m.name, e)
        return False


class DiskPluginReader:
    """The `PluginReader` port. Every method resolves through the module functions so a
    test patching `extensions.plugins.plugin_root` still reaches this reader."""

    def components(self, root: Path) -> dict | None:
        return components(root)

    def bundled_servers(self, root: Path) -> list[BundledServer]:
        return bundled_servers(root)

    def local_servers(self, root: Path) -> list[BundledServer]:
        return local_servers(root)

    def surface(self, m: Manifest, root: Path) -> str:
        return surface(m, root)

    def is_present(self, home: Path, m: Manifest) -> bool:
        return is_present(home, m)

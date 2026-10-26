"""Where a plugin's clone lives under CH_HOME, and which directory of it IS the plugin.

Pure path arithmetic plus one containment check; nothing here touches the filesystem
except `Path.resolve`, which is what the check is for.
"""
from __future__ import annotations

from pathlib import Path

from .exceptions import ComposeError
from .manifest import Manifest

PLUGINS_SUBDIR = "plugins"


def plugin_dir(home: Path, name: str) -> Path:
    """Where the repo is fetched. Not necessarily the plugin itself — see plugin_root.

    `CH_HOME/plugins/<name>` since Wave 4a, **not** `agent_home/plugins/<name>`
    (D8). A plugin is deploy-wide — the owner installed it, and it changes the
    cached prompt prefix and the MCP servers of every turn — while `agent_home`
    became one tree per user. Left where it was, a non-owner's turn could only
    load a plugin if its read guard admitted the OWNER's whole `agent_home`,
    which is exactly the boundary this wave draws. `Workspace.ensure()` performs
    the one-time `os.replace` for a volume that still has the old directory.
    """
    return home / PLUGINS_SUBDIR / name


def plugin_root(home: Path, m: Manifest) -> Path:
    """The directory that IS the plugin — the clone, or a subdirectory of it.

    A marketplace repo holds many plugins under plugins/<name>/, so the manifest may name
    one with `subdir`. catalog.parse already refuses a subdir that looks like an escape,
    but that rule is checked before anything exists on disk and so cannot see a symlink
    planted by the fetched repo. This resolves and re-checks containment.

    Honest about what this is: defense in depth, not a boundary. A plugin runs its own
    hooks, skills and commands the moment the SDK loads it, so a symlink escape grants it
    nothing it did not already have. It costs three lines, so we pay them.
    """
    base = plugin_dir(home, m.name)
    root = (base / m.subdir) if m.subdir else base
    resolved, base_resolved = root.resolve(), base.resolve()
    if resolved != base_resolved and not resolved.is_relative_to(base_resolved):
        raise ComposeError(f"subdir {m.subdir!r} resolves outside the plugin directory")
    return resolved

"""What an installed plugin adds, as a value and as the text a human approves.

The reading-from-disk half is `infrastructure/plugin_reader.py`; everything here is
pure so the classification rule and the approved text can be tested without a clone.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .drafts import safe
from .manifest import Manifest


@dataclass(frozen=True)
class BundledServer:
    """One MCP server a plugin brings with it.

    These never pass through `catalog.parse`: the CLI loads them itself from the
    plugin's own files (the SDK's `strict_mcp_config` docstring names
    "plugin-provided servers" among the configurations it would otherwise read, and
    we leave that flag at its default). So they get no registry row, no probe, and
    `compose.for_scope` cannot drop a broken one. The only place a human can see them
    is before the plugin is registered — which is what `surface` is for.
    """

    name: str
    transport: str          # http | sse | stdio
    target: str             # the URL, or "command arg arg"
    local: bool             # True when running it starts a process in our container


def _classify(name: str, spec: object) -> BundledServer:
    """Read one `mcpServers` entry and answer one question: will installing this start
    a process in our container?

    A process needs an argv, so `command` (or `args`, which is meaningless without one)
    is what makes an entry local — with no `type` at all, which is how every stdio server
    in the wild is actually written.

    Everything else is remote, including an entry that declares a transport and has
    nothing to dial. `{"type": "http", "url": ""}` is not a broken guess at a local
    server: it is how the official knowledge-work marketplace ships `gmail` and
    `google calendar`, leaving the endpoint to the client, and a `${VAR}` placeholder
    reads the same. The CLI has a URL to dial or it has nothing — either way nothing runs
    here, and refusing the whole plugin over it refused three plugins that spawn nothing.

    The one thing still treated as local is an entry we cannot read at all: there the
    honest answer to "I cannot tell" is still yes.
    """
    if not isinstance(spec, dict):
        return BundledServer(name, "stdio", "(unreadable entry)", True)
    transport = str(spec.get("type") or "").lower()
    url = str(spec.get("url") or "").strip()

    if "command" in spec or "args" in spec:
        argv = [str(spec.get("command") or ""), *(str(a) for a in spec.get("args") or [])]
        joined = " ".join(a for a in argv if a)
        return BundledServer(name, "stdio", joined or "(no command)", True)
    if url.startswith(("http://", "https://")):
        return BundledServer(name, transport or "http", url, False)
    # Declared remote (or carrying something that is not an address) with no argv: the
    # plugin ships the name of a server the CLI cannot start.
    return BundledServer(name, transport or "http", url or "(no url — not configured)", False)


def render_surface(m: Manifest, parts: dict, servers: list[BundledServer]) -> str:
    """What the plugin will add, in the form a human approves.

    The same idea as `drafts.describe`: everything that will run gets its own line, and
    every value passes through `drafts.safe`, because all of it was written by whoever
    owns the repository and it is about to be printed on the one screen the owner is
    supposed to trust. The text is the thing the confirmation token is computed over,
    so anything not on a line here is not covered by the approval.

    Takes the components and the bundled servers rather than a path: reading them is
    infrastructure's job (`plugin_reader.surface` passes them in), and keeping the text
    pure is what lets it be diffed without a clone on disk.
    """

    def listing(names: list[str]) -> str:
        return ", ".join(safe(n) for n in names) if names else "(none)"

    lines = [
        f"name:        {m.name}",
        f"repo:        {safe(m.repo)}",
        f"ref:         {safe(m.ref)}",
    ]
    if m.subdir:
        lines.append(f"subdir:      {safe(m.subdir)}")
    lines += [
        f"version:     {safe(parts.get('plugin_version') or m.version)}",
        f"author:      {safe(parts.get('author') or '')}",
        f"commands:    {listing(parts.get('commands') or [])}",
        f"skills:      {listing(parts.get('skills') or [])}",
        f"agents:      {listing(parts.get('agents') or [])}",
        f"hooks:       {'YES — runs its own hooks on every turn' if parts.get('hooks') else 'no'}",
    ]
    if servers:
        lines.append("servers:     (loaded by the CLI, not by this catalog)")
        lines += [f"  {safe(s.name)}  {s.transport}  {safe(s.target)}" for s in servers]
    else:
        lines.append("servers:     (none)")
    return "\n".join(lines)


def surface_token(text: str) -> str:
    """A token bound to the exact surface the owner read.

    `approvals.token_of` truncates to eight hex because a human retypes it into a chat
    message; nobody types this one — the browser holds it between two requests — so it
    keeps the whole digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

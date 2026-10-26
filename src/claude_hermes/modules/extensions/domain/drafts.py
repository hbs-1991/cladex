"""The pure half of the draft queue: what a drafted mapping is, and how a human
reads it. The queue itself (where the files live) is `infrastructure/draft_store.py`.

`safe` and `describe` are security-relevant text and move byte-identically: they are
what an operator reads on the one screen they are supposed to trust.
"""
from __future__ import annotations

from .exceptions import DraftError
from .manifest import Manifest

DRAFTS_SUBDIR = "catalog-drafts"

__all__ = ["DRAFTS_SUBDIR", "DraftError", "kind_of", "safe", "describe"]


def kind_of(data: dict) -> str:
    """A plugin is cloned from a repo; an MCP server is dialled or spawned."""
    if "repo" in data:
        return "plugin"
    if "transport" in data:
        return "mcp"
    raise DraftError(
        "cannot tell what this is: an MCP server needs 'transport', a plugin needs 'repo'"
    )


def safe(value: object) -> str:
    """Strip anything that could rewrite the operator's terminal.

    label and description were extracted from a README an attacker may have written, and
    they are printed on the one screen a human is supposed to trust. An ANSI escape can
    clear lines and redraw them, hiding the real `args` behind a benign-looking forgery.
    Control characters have no place in a manifest field, so they are removed rather than
    escaped: there is nothing here worth rendering.
    """
    text = str(value)
    return "".join(c for c in text if c.isprintable() or c == " ").strip()


def describe(m: Manifest) -> str:
    """The manifest's executable surface, in the form a human must actually read before
    approving it. Everything that will RUN goes on its own line — a reviewer who skims
    past 'args' is the failure mode this whole feature is built against."""
    lines = [f"name:        {m.name}", f"kind:        {m.kind}",
             f"label:       {safe(m.label)}"]
    if m.kind == "plugin":
        lines.append(f"repo:        {safe(m.repo)}")
        lines.append(f"ref:         {safe(m.ref)}")
        # Which directory of that repo becomes the plugin is part of what will run. An
        # approver shown only repo+ref would be approving a subdirectory they never saw.
        if m.subdir:
            lines.append(f"subdir:      {safe(m.subdir)}")
    elif m.transport == "stdio":
        lines.append(f"command:     {safe(m.command)}")
        lines.append(f"args:        {[safe(a) for a in m.args]}")
        if m.env:
            lines.append(f"env:         { {k: safe(v) for k, v in m.env.items()} }")
    else:
        lines.append(f"transport:   {safe(m.transport)}")
        lines.append(f"url:         {safe(m.url)}")
    for a in m.auth:
        lines.append(f"secret:      {safe(a.name)}"
                     + (f" -> {safe(a.header)}: {safe(a.format)}" if a.header else ""))
    lines.append(f"scopes:      {', '.join(m.scopes)}")
    return "\n".join(lines)

"""`mcp__hermes__ext_manage` — the extensions module's one agent-facing tool.

This file is the module's declaration of that tool: the name, the description and
the argument schema. The composition root puts them on the `ExtensionAdminPort`
object it hands to turns (`core.app_context.ExtensionAdminTool`), and the MCP server
registers what the port declares — the module that owns the body owns how the tool
presents itself.

The description is a prompt-cache invariant: it is part of the cached prefix and
`tests/test_ext_manage_guidance.py` asserts the wording, so it is byte-for-byte what
`tools.py` shipped. The name `ext_manage` (and therefore `mcp__hermes__ext_manage`)
is frozen too — `tests/test_tools.py` pins the exact tool-name list.

The handler is NOT here. It is the port: `ExtensionAdminService` is the body,
`ExtensionAdminTool` resolves the owner gate and the lane-reset signal, and
`modules/turns/.../tool_handlers/delegating.make_ext_manage` wraps it in the MCP
envelope. A second builder in this file would be a second `ext_manage`.
"""
from __future__ import annotations

TOOL_NAME = "ext_manage"

TOOL_DESCRIPTION = (
    "Install and manage extensions — MCP servers and Claude Code plugins. "
    "action=catalog|list|add|enable|disable|remove|test|draft|drafts. Only "
    "names that appear in action='catalog' can be installed; an arbitrary "
    "command or URL is refused by design, so do not try to pass one. When the "
    "user gives you a URL for something new, fetch it, then action='draft' with "
    "the manifest as YAML in 'manifest' — that only QUEUES it; the user must "
    "approve it with /approve in chat, and you cannot. If 'add' reports a "
    "missing secret, "
    "relay the /secret line to the user verbatim — you must never ask them to "
    "type a key into the conversation, and you must never repeat a key back. "
    "A missing-secret answer goes stale the moment the user acts on it: if they "
    "ask again, ALWAYS re-run action='add' and report what it says now. Never "
    "repeat an earlier missing-secret list from this conversation — /secret is "
    "handled outside the conversation, so a secret arriving is invisible to you, "
    "and the catalog can change under you too. The tool is the only source of "
    "truth for what is still missing. "
    "add/enable/disable/remove restart this thread's context: say so."
)

TOOL_SCHEMA = {"action": str, "name": str, "kind": str, "manifest": str}

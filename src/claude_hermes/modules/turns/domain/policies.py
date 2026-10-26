"""Constants that decide what a turn may do. Prompt-cache invariant: the order of
`_BUILTIN_TOOLS` is part of `allowed_tools`, which is part of the cached prefix.
"""
from __future__ import annotations

_BUILTIN_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch",
    "TodoWrite",
    # Subagent spawning: the tool was renamed Task -> Agent in CLI 2.1.63, and
    # the init handshake still advertises the old name. Allow both so the list
    # does not depend on which name this CLI build emits.
    "Agent", "Task",
]

_FALLBACK_REPLY = "I finished the turn but produced no text — please try rephrasing."

# What a lane is told when it sends `/model` or `/effort` as a message. The CLI
# runs both as its OWN local commands in non-interactive mode ("Set model to x"),
# which would move the session's model with nothing recorded in any row and
# nothing shown anywhere — so no such text ever reaches it. Every channel refuses
# it before the turn (`core.model_catalog.is_model_command`); this is the answer
# for the paths that have no earlier guard.
_MODEL_COMMAND_REPLY = (
    "Model changes go through the model picker on the web or /model in Telegram."
)

# What a lane is told when its shell grant moved since its session started (0009).
# The toolset is part of the cached prefix, so the session cannot carry on (P1) —
# and a conversation that silently forgot everything would be a lie by omission
# (P5). English, like the model-fallback notice; emitted as a `text.block` before
# the answer and prefixed to the reply Telegram sends.
SHELL_CHANGED_NOTICE = (
    "Your shell access (Bash, Agent, Task) was changed by the owner, "
    "so this conversation starts a fresh session."
)

# claude-agent-sdk >= 0.2.126 replaces the CLI's non-zero-exit ProcessError with a
# generic Exception carrying the CLI's own error-result text, prefixed like this
# (mirroring the TypeScript SDK). Same signal as ProcessError: the CLI died and the
# stored session may be stale.
_SDK_ERROR_RESULT_PREFIX = "Claude Code returned an error result:"

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024  # Telegram caps bot uploads at 50MB

# The name the agent sees every Hermes tool under. Frozen (spec req. 36): the
# Telegram presenter matches on `mcp__hermes__*` and a rename would silence it.
TOOL_PREFIX = "mcp__hermes__"

BUILTIN_TOOLS = _BUILTIN_TOOLS
FALLBACK_REPLY = _FALLBACK_REPLY
MODEL_COMMAND_REPLY = _MODEL_COMMAND_REPLY

# What a NON-OWNER lane does not get (Wave 4a, D6). Every turn in the container
# runs as the same uid, so a regex over a shell command would imply a boundary it
# cannot provide — the honest control is the one the CLI enforces, and that is
# `disallowed_tools`, NOT `allowed_tools`: under `permission_mode="bypassPermissions"`
# (every turn here) `allowed_tools` is an auto-approve list, not a toolset, and the
# live Wave 4a smoke watched a non-owner lane run `Bash` with `Bash` absent from
# it. `denied_tools` below is what removes the tool; `builtin_tools` only keeps the
# approve list byte-stable.
# `Agent`/`Task` are here because a subagent inherits the lane's toolset, shell
# included. (Verified on CLI 2.1.273 in Wave 4a: `PreToolUse` DOES fire for a
# subagent's own tool calls, so the read guard holds inside one; that is what
# lets a per-user shell grant (0009) hand all three back together.)
_SHELL_TOOLS = ("Bash", "Agent", "Task")


def builtin_tools(*, is_owner_lane: bool = True,
                  shell_enabled: bool = False) -> list[str]:
    """`_BUILTIN_TOOLS` for this lane, in `_BUILTIN_TOOLS` order.

    The ORDER is load-bearing and that is why this filters rather than rebuilds:
    `allowed_tools` is part of the cached prompt prefix, so a reordered list is a
    cache miss on every lane. The list is now per user rather than shared, which
    is a real prompt-cache cost recorded next to the NFR-1 target — decided once
    per turn, from `TurnContext.has_shell`. A shell grant CAN move between two
    turns of one lane since 0009; the turn then starts a fresh session (the
    session row carries the `has_shell` it was built with), so a given session's
    prefix is still byte-stable.
    """
    if is_owner_lane or shell_enabled:
        return list(_BUILTIN_TOOLS)
    return [t for t in _BUILTIN_TOOLS if t not in _SHELL_TOOLS]


def denied_tools(*, is_owner_lane: bool = True,
                 shell_enabled: bool = False) -> list[str]:
    """`ClaudeAgentOptions.disallowed_tools` for this lane: the names the CLI
    removes from the model's toolset outright (`--disallowedTools`), which is the
    only setting that means "cannot" under bypassPermissions. Empty for the
    owner's lanes and for a lane whose owner holds a shell grant."""
    if is_owner_lane or shell_enabled:
        return []
    return list(_SHELL_TOOLS)

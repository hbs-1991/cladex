"""How a turn's progress and its reply are shaped for a Telegram chat.

`status_for` and `_STATUS` moved verbatim from `gateway/presenter.py`;
`chunk_text` and `render_html` are re-exported from the domain, where the rich
sender (infrastructure) can also reach them.
"""
from __future__ import annotations

from ..domain.formatting import chunk_text, render_html

__all__ = ["chunk_text", "render_html", "status_for"]

_STATUS = {
    "WebSearch": "🔎 Searching the web…",
    "WebFetch": "🔎 Searching the web…",
    "Read": "🗂 Reading files…",
    "Glob": "🗂 Reading files…",
    "Grep": "🗂 Reading files…",
    "Write": "✏️ Writing files…",
    "Edit": "✏️ Writing files…",
    "Bash": "⚙️ Running a command…",
    "Agent": "🤖 Delegating to a subagent…",
    "Task": "🤖 Delegating to a subagent…",
    "TodoWrite": "📋 Planning…",
    "mcp__hermes__session_search": "🧠 Working with memory…",
    "mcp__hermes__cron_manage": "⏰ Scheduling…",
    "mcp__hermes__cost_report": "💰 Checking costs…",
    "mcp__hermes__skill_admin": "🛠 Editing skills…",
    "mcp__hermes__ext_manage": "🔌 Managing extensions…",
    "mcp__hermes__send_document": "📎 Sending a file…",
    "mcp__hermes__ask_user": "❓ Waiting for your answer…",
}
_WRITING = "✍️ Writing…"
_WORKING = "💭 Working…"


def status_for(tool_name: str, tool_input: dict | None = None) -> str:
    """One status line per tool. An empty name means the model is emitting prose.

    tool_input, when given, lets a few tools show a richer line: the Skill tool
    names the skill it is loading, and a Bash run of the image generator shows a
    dedicated 'generating' status (that run is long, so a generic 'running a
    command' would leave the user staring at nothing for a minute)."""
    if not tool_name:
        return _WRITING
    if tool_name == "Skill":
        name = ""
        if tool_input:
            name = str(tool_input.get("skill") or tool_input.get("name") or "").strip()
        return f"🧩 Using skill: {name}" if name else "🧩 Using a skill…"
    if tool_name == "Bash" and tool_input:
        command = str(tool_input.get("command", ""))
        if "gpt-image-2" in command or "gen.sh" in command:
            return "🎨 Generating image…"
    return _STATUS.get(tool_name, _WORKING)

from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions


# The SDK frames the CLI's NDJSON stdout and aborts the WHOLE turn if any single
# message exceeds this. Its default is 1 MiB, and one `Read` of a generated image
# blows straight past that: the CLI inlines the picture as base64 in the tool_result,
# so a ~800 KB PNG arrives as a ~1.15 MB line and the turn dies with "Failed to decode
# JSON" — after the work is done, and with no hint that an image was the cause. 16 MiB
# clears the API's own 5 MB-per-image ceiling (~6.7 MB once base64'd) with room to
# spare, while still bounding a runaway producer.
_MAX_BUFFER_SIZE = 16 * 1024 * 1024


def workspace_env(workspace) -> dict[str, str]:
    """The environment every agentic CLI process gets: two variables that make it
    per USER (Wave 4a, D6), and one that keeps the model the one we asked for.

    `CLAUDE_CONFIG_DIR` is where the CLI keeps its own transcript store, so it is
    what `resume` reads: leave it deploy-wide and one user's `--resume` replays
    another's conversation. `TMPDIR` moves the scratch files off the shared
    `/tmp`, where every lane in the container could read them and where the read
    guard's allowed roots do not reach.

    Duck-typed on purpose (anything with `cli_config_dir` and `tmp_dir`): an
    integration may lean on `core` but is happier not naming a dataclass it does
    not own. **Merge this over `NO_ADVISOR_ENV` at the background call sites**
    (`env={**NO_ADVISOR_ENV, **workspace_env(ws)}`) — the SDK merges `env` over
    `os.environ` but not over itself, so a replace silently re-enables the
    advisor tool on every fork.

    `CLAUDE_CODE_NO_MODEL_FALLBACK` turns off the CLI's own availability fallback
    chain, which would otherwise answer with a different model than the one the
    lane chose and say so nowhere. The runner has exactly one fallback of its own,
    which it announces; a second, silent one would make every "which model
    answered" label a guess. Every `base_options` call site passes this env, so
    this one line covers the turn, cron and every fork.
    """
    return {
        "CLAUDE_CONFIG_DIR": str(workspace.cli_config_dir),
        "TMPDIR": str(workspace.tmp_dir),
        "CLAUDE_CODE_NO_MODEL_FALLBACK": "1",
    }


def base_options(home: Path, **overrides) -> ClaudeAgentOptions:
    """The invariants every agentic call site shares — and nothing else.

    Since Wave 4a the argument is the LANE OWNER's root rather than `CH_HOME`:
    `cwd` is `root / "agent_home"` either way, and for the deploy owner
    `root == CH_HOME`, so every call site that passes `home` is byte-identical to
    what it was.

    Only four things must be identical across the turn, cron, review and curator
    forks: they all run in agent_home, they all load the project settings there
    (CLAUDE.md, .claude/settings.json, .claude/agents, .claude/skills), they
    all bypass permission prompts because the container is the sandbox, and they all
    read the CLI's stdout with a buffer big enough for an inlined image. Those four
    were restated by hand at four call sites and could drift apart silently.

    Everything else is legitimately per-call-site and stays where it is read:
    `tools`/`allowed_tools` (each fork wants a different reach), `hooks`, `resume`,
    `max_turns`, `mcp_servers`, `plugins`, and `env` — the foreground turn keeps the
    advisor tool while the background forks pass NO_ADVISOR_ENV (see config.py).

    The titler (gateway/titles.py) is deliberately NOT built through here: it runs
    with setting_sources=[] and no cwd on purpose, so it would override all three
    invariants. That is not reuse.
    """
    defaults: dict = {
        "cwd": str(home / "agent_home"),
        "setting_sources": ["project"],
        "permission_mode": "bypassPermissions",
        "max_buffer_size": _MAX_BUFFER_SIZE,
    }
    defaults.update(overrides)
    return ClaudeAgentOptions(**defaults)

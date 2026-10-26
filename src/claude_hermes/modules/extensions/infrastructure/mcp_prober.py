"""Bring one MCP server up through the bundled CLI and ask what happened.

Tests patch `ClaudeSDKClient` on this module; it is resolved at call time.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import structlog
from claude_agent_sdk import ClaudeSDKClient

from claude_hermes.core.config import NO_ADVISOR_ENV
from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.options import base_options, workspace_env

from ..domain.probe import ProbeResult

log = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 0.5   # how often to re-read a still-'pending' handshake

__all__ = ["POLL_INTERVAL_SECONDS", "ProbeResult", "SdkServerProber", "probe"]


async def probe(home: Path, name: str, spec: dict, timeout: float = 60.0) -> ProbeResult:
    """Bring one server up and ask the CLI what happened. No model call, no cost.

    Never raises: a probe that throws would turn "this server is broken" into "your
    message failed", which is a worse answer to the same question.
    """
    options = base_options(
        home,
        mcp_servers={name: spec},
        tools=[],            # a handshake, not a conversation
        allowed_tools=[],
        max_turns=1,
        # Wave 4a: MERGED over `NO_ADVISOR_ENV`, never replacing it — a replace
        # would drop `CLAUDE_CODE_DISABLE_ADVISOR_TOOL` and re-enable the advisor
        # on a handshake that makes no model call at all. A probe is the OWNER's
        # action against a deploy-wide install (D8), so the tree is the owner's.
        env={**NO_ADVISOR_ENV, **workspace_env(Workspace.owner(home))},
    )

    # The last status actually read, so a timeout can say WHICH state it stuck in.
    # None means the CLI never got far enough to report anything at all — a different
    # failure from a server that came up and then never finished handshaking.
    last: dict[str, str | None] = {"status": None}

    async def _run() -> ProbeResult:
        async with ClaudeSDKClient(options) as client:
            while True:
                status = await client.get_mcp_status()
                server = next(
                    # 'hermes' and any other server share this response
                    (s for s in status.get("mcpServers", []) if s.get("name") == name),
                    None,
                )
                if server is None:
                    return ProbeResult(
                        status="failed",
                        error="the CLI did not report this server at all")

                state = server.get("status", "failed")
                last["status"] = state
                # 'pending' means "not finished", not "failed". A stdio server started
                # through uvx has to resolve, build and boot before it can answer —
                # about a second even with a warm cache — so the first sample is
                # almost always 'pending'. Sampling once reports a healthy server as
                # broken and installs nothing. Poll until it settles; the outer
                # wait_for is what bounds this.
                if state != "pending":
                    tools = [t.get("name", "") for t in server.get("tools", []) or []]
                    return ProbeResult(
                        status=state,
                        tools=[t for t in tools if t],
                        error=server.get("error", "") or "",
                    )
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError:
        if last["status"] is None:
            detail = "the CLI never reported a status"
        else:
            detail = (f"still {last['status']!r} — it started but never completed "
                      "the MCP handshake")
        return ProbeResult(status="failed",
                           error=f"timed out after {timeout:.0f}s: {detail}")
    except Exception as e:                                   # noqa: BLE001
        log.warning("probe of %s failed", name, exc_info=True)
        return ProbeResult(status="failed", error=str(e))


class SdkServerProber:
    """The `ServerProber` port. Callable so a test can hand in a bare function instead
    (`WebDeps.probe` is exactly that seam)."""

    async def __call__(self, home: Path, name: str, spec: dict,
                       timeout: float = 60.0) -> ProbeResult:
        return await probe(home, name, spec, timeout=timeout)

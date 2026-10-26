"""The skills-only write guard the consolidation fork installs.

`write_guard_factory` itself moved to `integrations/claude_sdk/guards.py` in
Wave 2 (the cron executor and the curator install the same guard); what stays
learning's is the *scoping*: the curator may write inside the skills library and
nowhere else, and it may not touch `.usage.json` or `.archive/` at all — that is
what makes "the LLM proposes, the code disposes" true rather than aspirational.
"""
from __future__ import annotations

from pathlib import Path

from claude_hermes.integrations.claude_sdk.guards import write_guard_factory


def skills_write_guard_factory(skills_dir: Path, touched: set[str] | None = None,
                               *, cwd: Path | None = None):
    """Skills-only guard — kept for the curator's consolidation pass. `cwd` is
    the fork's `agent_home`, so a relative path resolves where the stacked read
    guard resolves it (advisor B-8)."""
    return write_guard_factory([skills_dir], skills_root=skills_dir, touched=touched,
                               cwd=cwd)

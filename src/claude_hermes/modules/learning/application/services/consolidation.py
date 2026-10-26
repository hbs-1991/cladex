"""The consolidation pass: the LLM proposes umbrella merges/prunes, the code disposes.

Moved from `learning/curator.py`. The divergence is deliberate and load-bearing:
the fork's write guard denies writes into `.archive/`, so the model can only
*rewrite* skill content and then report what it merged — the archiving happens
here, from the report, under the same eligibility rules the aging pass uses.

An unparseable or absent yaml block archives nothing. So does a failed fork.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from claude_hermes.modules.learning.application.ports import (
    ForkRunner,
    SkillArchivePort,
    SkillUsagePort,
)
from claude_hermes.modules.learning.application.services.aging import candidates
from claude_hermes.modules.learning.domain.prompts import build_curator_prompt

log = structlog.get_logger(__name__)

_YAML_BLOCK = re.compile(r"```yaml\s*(.*?)```", re.DOTALL)


def parse_consolidation_block(text: str) -> dict:
    """Tolerate a missing block, malformed YAML, or a partial block: fail safe."""
    empty = {"consolidations": [], "prunings": []}
    match = _YAML_BLOCK.search(text or "")
    if not match:
        return empty
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return empty
    if not isinstance(data, dict):
        return empty
    consolidations = data.get("consolidations")
    prunings = data.get("prunings")
    return {
        "consolidations": consolidations if isinstance(consolidations, list) else [],
        "prunings": prunings if isinstance(prunings, list) else [],
    }


class ConsolidationService:
    """`workspace` (Wave 4b) is the tree the fork runs in — this scope's, so a
    non-owner's consolidation rewrites THEIR library from THEIR `agent_home`;
    `None` is the deploy owner's, the pre-4b meaning."""

    def __init__(self, *, skills_dir: Path, usage: SkillUsagePort,
                 archive: SkillArchivePort, fork: ForkRunner, workspace: Any = None):
        self._skills_dir = skills_dir
        self._usage = usage
        self._archive = archive
        self._fork = fork
        self._workspace = workspace

    @property
    def workspace(self) -> Any:
        return self._workspace

    def _eligible(self, name: str) -> bool:
        """created_by=agent, unpinned, not already archived, AND still on disk.

        The state/dir checks matter beyond the initial gate: a name can appear
        in both consolidations and prunings (model contract violation), or name
        a skill archived by an earlier pass — either way the second archive_skill
        call must be skipped, not crash the pass (that would wedge the curator
        in a retry loop and evict good pre-run snapshots).
        """
        entry = self._usage.get(name)
        if not entry or entry.get("created_by") != "agent" or entry.get("pinned"):
            return False
        if entry.get("state") == "archived":
            return False
        return (self._skills_dir / name).is_dir()

    async def run(self, dry_run: bool = False) -> dict:
        rows = candidates(self._usage)
        try:
            final = await self._fork.run_consolidation(
                prompt=build_curator_prompt(rows), skills_dir=self._skills_dir,
                workspace=self._workspace,
            )
        except Exception:
            log.exception("curator: consolidation fork failed")
            return {"consolidated": [], "pruned": [], "skipped": [], "summary": None}

        parsed = parse_consolidation_block(final or "")
        now_iso = datetime.now(timezone.utc).isoformat()

        consolidated: list[str] = []
        pruned: list[str] = []
        skipped: list[str] = []

        for item in parsed["consolidations"]:
            if not isinstance(item, dict):
                continue
            src, into = item.get("from"), item.get("into")
            if not src or not into or src == into:  # "from: X, into: X" is a no-op, not a merge
                if src:
                    skipped.append(src)
                continue
            if not self._eligible(src) or not (self._skills_dir / into).is_dir():
                skipped.append(src)  # missing umbrella means the merge never happened
                continue
            if dry_run:
                consolidated.append(src)
                continue
            try:
                self._archive.archive_skill(src, now_iso)
            except OSError:
                log.exception("curator: consolidation archive failed for %s", src)
                skipped.append(src)
                continue
            consolidated.append(src)

        for item in parsed["prunings"]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not name:
                continue
            if not self._eligible(name):
                skipped.append(name)
                continue
            if dry_run:
                pruned.append(name)
                continue
            try:
                self._archive.archive_skill(name, now_iso)
            except OSError:
                log.exception("curator: pruning archive failed for %s", name)
                skipped.append(name)
                continue
            pruned.append(name)

        return {"consolidated": consolidated, "pruned": pruned, "skipped": skipped,
                "summary": final}

"""Hermes-style self-nudges: counters in `meta`, never injected text.

Cache-safe by construction — nothing here reaches the model's prompt, so a nudge
firing cannot invalidate a cached prefix. Moved from `learning/nudges.py`; the two
meta keys are unchanged (`domain/constants.py`).
"""
from __future__ import annotations

from claude_hermes.modules.learning.application.ports import MetaStore
from claude_hermes.modules.learning.domain.constants import (
    SKILL_NUDGE_META_KEY,
    WIKI_NUDGE_META_KEY,
)


class NudgeTracker:
    """Hermes-style self-nudges: counters, never injected text (cache-safe)."""

    def __init__(self, meta: MetaStore, cfg):
        self._meta = meta
        self._cfg = cfg

    async def _get(self, key: str) -> int:
        raw = await self._meta.get(key)
        return int(raw) if raw and raw.isdigit() else 0

    async def observe_turn(
        self, wrote_wiki: bool, wrote_skill: bool, tool_uses: int
    ) -> set[str]:
        due: set[str] = set()

        turns = 0 if wrote_wiki else await self._get(WIKI_NUDGE_META_KEY) + 1
        if turns >= self._cfg.wiki_nudge_interval:
            due.add("wiki")
            turns = 0
        await self._meta.set(WIKI_NUDGE_META_KEY, str(turns))

        iters = 0 if wrote_skill else await self._get(SKILL_NUDGE_META_KEY) + tool_uses
        if iters >= self._cfg.skill_nudge_interval:
            due.add("skills")
            iters = 0
        await self._meta.set(SKILL_NUDGE_META_KEY, str(iters))

        return due

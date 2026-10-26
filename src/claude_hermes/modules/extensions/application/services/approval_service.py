"""Agent proposes, human promotes — the two-step that stands between a drafted
manifest and something that runs in the container.

`begin` snapshots the draft's bytes and returns a token derived from them; `confirm`
promotes the SNAPSHOT, gated on that token. An edit between the two cannot smuggle
different bytes past an approval the human gave for what they read.
"""
from __future__ import annotations

import yaml

from ...domain.approvals import describe_with_banner, token_of
from ...domain.manifest import Manifest, parse
from ..ports import DraftStore, SnapshotStore

KINDS = ("mcp", "plugin")


class ApprovalService:
    def __init__(self, *, drafts: DraftStore, snapshots: SnapshotStore):
        self._drafts = drafts
        self._snapshots = snapshots

    async def _find_pending(self, name: str) -> Manifest | None:
        for m in await self._drafts.pending():
            if m.name == name:
                return m
        return None

    async def begin(self, name: str) -> tuple[Manifest, str, str] | None:
        """Snapshot the named draft's bytes and return (manifest, surface, token). The
        snapshot — not the live draft — is what confirm() will promote, so an edit after
        this point cannot smuggle different bytes past the token the human is shown."""
        m = await self._find_pending(name)
        if m is None:
            return None
        body = await self._drafts.read(m.kind, m.name)
        if body is None:
            return None
        raw = body.encode("utf-8")
        await self._snapshots.write(m.kind, m.name, raw)
        return m, describe_with_banner(m), token_of(raw)

    async def confirm(self, name: str, token: str) -> tuple[Manifest, bytes] | None:
        """Return (manifest, snapshot_bytes) iff a snapshot exists and its token matches."""
        for kind in KINDS:
            raw = await self._snapshots.read(kind, name)
            if raw is None:
                continue
            if token_of(raw) != token:
                return None
            return parse(yaml.safe_load(raw), kind), raw
        return None

    async def clear(self, kind: str, name: str) -> None:
        await self._snapshots.clear(kind, name)
        await self._drafts.delete(kind, name)

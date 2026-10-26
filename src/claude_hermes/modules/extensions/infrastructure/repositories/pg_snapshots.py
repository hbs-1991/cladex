"""`approval_snapshots`: the bytes a human was shown, held between `/approve
<name>` and `/approve <name> <token>` — the Wave 3a `SnapshotStore`.

What this replaces: `CH_HOME/pending-approvals/`. The guarantee is unchanged and
is the reason the table stores BYTEA and not a parsed manifest: the token is
`sha256(raw)[:8]`, so what `confirm` promotes has to be the exact bytes `begin`
snapshotted. Storing anything the parser produced would make the token check
compare a manifest to itself.

`token_of` and `describe_with_banner` are re-exported here from the domain, so a
caller of the store has one import.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ...domain.approvals import SNAP_SUBDIR, describe_with_banner, token_of
from ...domain.models import ApprovalSnapshot

__all__ = ["SNAP_SUBDIR", "PgSnapshots", "describe_with_banner", "token_of"]


class PgSnapshots:
    """The `SnapshotStore` port over `approval_snapshots`."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def write(self, kind: str, name: str, raw: bytes) -> None:
        """Snapshot, replacing any earlier one for the same entry.

        Replacing is load-bearing: a second `review` in another tab re-snapshots,
        which is exactly what leaves the first tab holding a token for bytes
        nobody will promote.
        """
        now = datetime.now(timezone.utc).isoformat()
        stmt = pg_insert(ApprovalSnapshot).values(
            kind=kind, name=name, raw=raw, created_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[ApprovalSnapshot.kind, ApprovalSnapshot.name],
            set_={"raw": stmt.excluded.raw, "created_at": now})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def read(self, kind: str, name: str) -> bytes | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(ApprovalSnapshot.raw)
                .where(ApprovalSnapshot.kind == kind, ApprovalSnapshot.name == name)
            )).first()
        return bytes(row[0]) if row else None

    async def clear(self, kind: str, name: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(ApprovalSnapshot).where(
                ApprovalSnapshot.kind == kind, ApprovalSnapshot.name == name))

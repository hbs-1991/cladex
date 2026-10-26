"""The only SQL that touches the `files` table.

Keeping it in one class is what made the Wave 4a owner scoping a single edit: a
row now names its owner (`files.owner_id`, the LANE's owner) and the download
route re-resolves the blob's mirror root against that id, so a guessed file id
cannot read out of somebody else's tree. The lane visibility check stays in front
of it — the owner column is defence in depth, not the first gate.

Statements copied from `db.py` in Wave 2.
"""
from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ..domain.models import FileRecord


class FileRepository:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def insert(self, file_id: str, session_key: str, path: str, name: str,
                     media_type: str, size: int, created_at: str, *,
                     owner_id: int | None = None) -> None:
        """Register one blob. `owner_id` is the lane's owner (`lane_owner`, decided
        by the caller — a `files` row never derives it), `None` only for the legacy
        `db.py` facade and an import script."""
        async with self._engine.begin() as conn:
            await conn.execute(insert(FileRecord).values(
                id=file_id, session_key=session_key, path=path, name=name,
                media_type=media_type, size=size, created_at=created_at,
                owner_id=owner_id))

    async def get(self, file_id: str) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(FileRecord).where(FileRecord.id == file_id))).mappings().first()
        return dict(row) if row else None

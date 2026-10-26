"""The `extensions` table: what is installed, never what it does."""
from __future__ import annotations

import json

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ...domain.models import Extension

__all__ = ["SqlExtensionRepository"]


class SqlExtensionRepository:
    """What is installed, for ONE owner — never what it does.

    Every method takes a keyword-only `owner_id` with no default. A caller that
    does not say whose install it means gets a `TypeError`, never the deploy
    owner's row: the same posture as an empty allowed-roots list denying
    everything rather than allowing everything.
    """

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def add(self, name: str, kind: str, catalog_version: str,
                  installed_by: int, now: str, tool_count: int | None = None,
                  tool_names: list[str] | None = None, *, owner_id: int) -> None:
        stmt = pg_insert(Extension).values(
            owner_id=owner_id, name=name, kind=kind,
            catalog_version=catalog_version, state="enabled",
            installed_at=now, installed_by=installed_by, last_error=None,
            tool_count=tool_count,
            tool_names=json.dumps(tool_names) if tool_names is not None else None)
        # Re-installing is a repair: the row goes back to enabled and the last
        # error is forgotten, whatever state the previous install left behind.
        stmt = stmt.on_conflict_do_update(
            index_elements=[Extension.owner_id, Extension.name], set_={
                "kind": stmt.excluded.kind,
                "catalog_version": stmt.excluded.catalog_version,
                "state": "enabled",
                "installed_at": stmt.excluded.installed_at,
                "installed_by": stmt.excluded.installed_by,
                "last_error": None,
                "tool_count": stmt.excluded.tool_count,
                "tool_names": stmt.excluded.tool_names})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    def _one(self, name: str, owner_id: int):
        return (Extension.owner_id == owner_id) & (Extension.name == name)

    async def set_tool_count(self, name: str, n: int | None, *, owner_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(Extension)
                               .where(self._one(name, owner_id))
                               .values(tool_count=n))

    async def set_tools(self, name: str, names: list[str], *, owner_id: int) -> None:
        """What the last probe saw: the count the list shows, the names the page lists."""
        async with self._engine.begin() as conn:
            await conn.execute(update(Extension)
                               .where(self._one(name, owner_id))
                               .values(tool_count=len(names),
                                       tool_names=json.dumps(names)))

    async def get(self, name: str, *, owner_id: int) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(Extension).where(self._one(name, owner_id)))).mappings().first()
        return dict(row) if row else None

    async def list(self, kind: str | None = None, *, owner_id: int) -> list[dict]:
        stmt = select(Extension).where(Extension.owner_id == owner_id)
        if kind is not None:
            stmt = stmt.where(Extension.kind == kind)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt.order_by(Extension.name))).mappings().all()
        return [dict(r) for r in rows]

    async def enabled(self, *, owner_id: int) -> list[dict]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                select(Extension)
                .where(Extension.owner_id == owner_id)
                .where(Extension.state == "enabled")
                .order_by(Extension.name))).mappings().all()
        return [dict(r) for r in rows]

    async def set_state(self, name: str, state: str, *, owner_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(Extension)
                               .where(self._one(name, owner_id))
                               .values(state=state))

    async def set_error(self, name: str, error: str | None, *,
                        owner_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(Extension)
                               .where(self._one(name, owner_id))
                               .values(last_error=error))

    async def remove(self, name: str, *, owner_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(Extension).where(self._one(name, owner_id)))

    async def owners_of(self, name: str) -> list[int]:
        """Every owner holding a row for this name, ascending.

        The one deliberately unscoped read in this class, and it answers ids and
        nothing else. It exists for the shared-clone cascade: deleting a plugin's
        clone has to clear every row that points at it and reset every affected
        lane. It must never be used to show one user another's state.
        """
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                select(Extension.owner_id)
                .where(Extension.name == name)
                .order_by(Extension.owner_id))).all()
        return [int(r[0]) for r in rows]

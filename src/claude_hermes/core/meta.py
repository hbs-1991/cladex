"""The `meta` key/value table, as one small store.

Owner id, the learning loop's counters, the curator's last-run stamps: a handful
of singleton facts that belong to no module's aggregate but that several modules
read. They lived on the `Database` facade; here they are a two-method port so a
module can take `MetaStore` as a dependency instead of the whole store.

Takes an `AsyncEngine`, never the facade — the repository rule from the layer
contracts applies to core too.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from claude_hermes.core.db.models import Meta


class MetaStore:
    """Read and write one `meta` row. Each call is its own short transaction."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def get(self, key: str) -> str | None:
        async with self._engine.connect() as conn:
            return (await conn.execute(
                select(Meta.value).where(Meta.key == key))).scalar_one_or_none()

    async def set(self, key: str, value: str) -> None:
        stmt = pg_insert(Meta).values(key=key, value=value)
        stmt = stmt.on_conflict_do_update(index_elements=[Meta.key],
                                          set_={"value": stmt.excluded.value})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

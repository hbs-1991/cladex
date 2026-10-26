"""Turn events and the cost ledger, as SQL over the engine.

Repositories take the `AsyncEngine` directly (Wave 2 rule): each owns its own
tables' statements and nothing else.
"""
from __future__ import annotations

from typing import Mapping

from sqlalchemy import func, insert, literal_column, select
from sqlalchemy.ext.asyncio import AsyncEngine

from claude_hermes.modules.turns.domain.models import TurnCost, TurnEventRow


class TurnEventRepository:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def append(self, session_key: str, turn_id: str, seq: int, ts: str,
                     type: str, payload: dict) -> int:
        stmt = (insert(TurnEventRow)
                .values(session_key=session_key, turn_id=turn_id, seq=seq, ts=ts,
                        type=type, payload=payload)
                .returning(TurnEventRow.id))
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one()

    async def after(self, session_key: str, after_id: int = 0,
                    limit: int = 1000) -> list[Mapping]:
        stmt = (select(TurnEventRow.id, TurnEventRow.session_key, TurnEventRow.turn_id,
                       TurnEventRow.seq, TurnEventRow.ts, TurnEventRow.type,
                       TurnEventRow.payload)
                .where(TurnEventRow.session_key == session_key, TurnEventRow.id > after_id)
                .order_by(TurnEventRow.id).limit(limit))
        async with self._engine.connect() as conn:
            # payload is JSONB: it comes back as a dict, no json.loads needed.
            return [dict(r) for r in (await conn.execute(stmt)).mappings().all()]


class TurnCostRepository:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def record(self, ts: str, session_key: str, cost_usd: float, *,
                     owner_id: int | None = None) -> None:
        """One turn's spend. `owner_id` is the LANE's owner (Wave 4a), not the
        sender: a stranger's message in a group lane costs the deploy owner."""
        async with self._engine.begin() as conn:
            await conn.execute(insert(TurnCost).values(
                ts=ts, session_key=session_key, cost_usd=cost_usd,
                owner_id=owner_id))

    async def by_day(self, days: int = 7, *,
                     owner_id: int | None = None) -> list[Mapping]:
        """Spend per day, for one owner when the caller names one.

        The predicate is inside the aggregate, not applied to its result: summing
        every owner's rows and then filtering would report the deploy's spend to
        whoever asked. `owner_id=None` is every owner, which is what a fork with
        no lane asks for.
        """
        # The day is the ISO timestamp's first ten characters: the ledger stores
        # UTC text, so slicing it is the same grouping a date cast would give.
        day = func.substr(TurnCost.ts, 1, 10).label("day")
        stmt = (select(day, func.sum(TurnCost.cost_usd).label("cost_usd"))
                .group_by(day).order_by(literal_column("day").desc()).limit(days))
        if owner_id is not None:
            stmt = stmt.where(TurnCost.owner_id == owner_id)
        async with self._engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(stmt)).mappings().all()]

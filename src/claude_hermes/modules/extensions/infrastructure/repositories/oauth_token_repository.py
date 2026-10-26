"""OAuth tokens for remote MCP servers — the source of truth.

The access token is mirrored into the `extension_secrets` table so `compose.server_spec`
can put it in a header like any other secret; the refresh token, the client credentials and the
issuer live only here. Values are never logged.
"""
from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ...domain.models import OauthToken

FIELDS = ("issuer", "client_id", "client_secret", "access_token", "refresh_token",
          "expires_at", "scope")

__all__ = ["FIELDS", "SqlOauthTokenRepository"]


class SqlOauthTokenRepository:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def get(self, name: str, *, owner_id: int) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(OauthToken)
                .where(OauthToken.owner_id == owner_id)
                .where(OauthToken.name == name))).mappings().first()
        return dict(row) if row else None

    async def put(self, name: str, *, owner_id: int, now: str, **fields: str) -> None:
        """Upsert: only the fields given change; the rest keep their value."""
        unknown = set(fields) - set(FIELDS)
        if unknown:
            raise ValueError(f"not token fields: {sorted(unknown)}")
        current = await self.get(name, owner_id=owner_id) or {f: "" for f in FIELDS}
        merged = {f: fields.get(f, current[f]) for f in FIELDS}
        stmt = pg_insert(OauthToken).values(
            owner_id=owner_id, name=name, **merged, updated_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[OauthToken.owner_id, OauthToken.name],
            set_={**merged, "updated_at": now})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def delete(self, name: str, *, owner_id: int) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(OauthToken)
                .where(OauthToken.owner_id == owner_id)
                .where(OauthToken.name == name))

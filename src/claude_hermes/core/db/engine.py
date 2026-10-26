"""Construction of the async engine. One place, so a pool setting is changed once
rather than at every call site."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def make_engine(url: str) -> AsyncEngine:
    # pool_pre_ping: a container that slept through a Postgres restart must not
    # hand the first request a dead connection.
    return create_async_engine(url, pool_pre_ping=True)

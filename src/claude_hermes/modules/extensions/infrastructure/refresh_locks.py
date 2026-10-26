"""One lock per connector, around an OAuth refresh.

Why a lock at all: `for_scope` runs for `turn` and `cron` concurrently, and a
provider that rotates refresh tokens (Notion does) refuses the second use of the
same one. Without the lock the loser records `needs_reauth` for a connector that
had just refreshed successfully.

Two implementations behind one `RefreshLocks` port, picked by `queue_mode`:
`InProcessLocks` (an `asyncio.Lock` per name, which is all one process needs) and
`RedisLocks` (`lock:oauth:<name>`, which is what makes the guarantee hold once the
worker refreshes the same connector the agent is composing).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import structlog

from claude_hermes.core.redis import Lock

log = structlog.get_logger(__name__)

__all__ = ["InProcessLocks", "RedisLocks"]

# Long enough for a token endpoint that is having a bad day, short enough that a
# killed process does not park every turn behind a dead lease.
_LOCK_TTL_MS = 30_000
_POLL_SECONDS = 0.05


class InProcessLocks:
    """The `RefreshLocks` port for inline mode: one `asyncio.Lock` per connector,
    created on first use and kept for the life of the process."""

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    def __call__(self, name: str):
        return self._locks.setdefault(name, asyncio.Lock())


class RedisLocks:
    """The `RefreshLocks` port for redis mode: `SET NX PX` on `lock:oauth:<name>`.

    Waits for the holder rather than reporting the miss, because the caller's
    contract is "the row is read inside the lock" — returning early would put us
    back in the double-refresh race. The wait is bounded by the lease: if the
    holder died, its TTL expires and we take the lock; if it is merely slow past
    the whole lease, we proceed and log, which is the same exposure the process
    had before any of this existed.
    """

    def __init__(self, client, *, ttl_ms: int = _LOCK_TTL_MS, prefix: str = "lock:oauth:"):
        self._client = client
        self._ttl_ms = ttl_ms
        self._prefix = prefix

    @asynccontextmanager
    async def __call__(self, name: str):
        lock = Lock(self._client, self._prefix + name, ttl_ms=self._ttl_ms)
        deadline = asyncio.get_running_loop().time() + self._ttl_ms / 1000
        held = await lock.acquire()
        while not held and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_POLL_SECONDS)
            held = await lock.acquire()
        if not held:
            log.warning("oauth refresh lock for %s never came free; refreshing anyway",
                        name)
        try:
            yield
        finally:
            if held:
                await lock.release()

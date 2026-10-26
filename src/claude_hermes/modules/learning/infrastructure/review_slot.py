"""`lock:review` — one review fork at a time, deploy-wide, in redis mode.

Before Wave 4b the `learning_review_until` lease was both the single-flight rule
and the concurrency bound, because there was one lease. With a lease per user
(D2) N users can each hold their own, and nothing would stop N review forks
running on the owner's account at once. This is the bound that replaces it
(advisor B-1): a `core.redis.Lock` keyed `lock:review`, TTL'd like the gate so
a worker killed mid-fork frees the slot on its own.

Inline mode has no Redis and needs none — `InlineQueue(limits={"learning_review":
1})` is the same bound in one process — so the composition root passes `None`
there and `ReviewService` reads that as "always free".

A fresh `Lock` per claim, deliberately. `Lock` carries one token and one
`held` flag; a taskiq worker runs several tasks concurrently in one process, and
two reviews sharing ONE `Lock` object would have the loser's failed `acquire()`
flip `held` to False under the winner, whose `release()` would then decline to
delete a key it does hold — a slot stuck for a full TTL after a clean pass.
"""
from __future__ import annotations

from claude_hermes.core.redis import Lock

REVIEW_SLOT_KEY = "lock:review"


class RedisReviewSlot:
    """`ReviewSlot` over `core.redis.Lock`."""

    def __init__(self, client, *, ttl_ms: int, key: str = REVIEW_SLOT_KEY) -> None:
        self._client = client
        self._ttl_ms = ttl_ms
        self._key = key
        self._lock: Lock | None = None

    async def acquire(self) -> bool:
        # One slot object is shared by every scope in the process. A pass that
        # already believes it holds the slot must never take it again: if A's
        # fork outlived the TTL and the key expired, B acquiring here would
        # overwrite `_lock`, and A's `finally: release()` would then delete B's
        # key — a third review starting while B still forks, which is exactly
        # the bound this class exists to hold (advisor risk 2, code pass).
        if self._lock is not None:
            return False
        lock = Lock(self._client, self._key, ttl_ms=self._ttl_ms)
        if not await lock.acquire():
            return False
        self._lock = lock
        return True

    async def release(self) -> None:
        lock, self._lock = self._lock, None
        if lock is not None:
            await lock.release()

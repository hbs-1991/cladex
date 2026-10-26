"""Started OAuth flows waiting for their callback, in Redis.

The reason this exists: the browser leaves for the provider and comes back to
whichever process the load balancer picks, and a redeploy in between used to lose
the flow entirely. A key with a ten-minute TTL survives both.

Redis owns the expiry (`SET ... PX`), so there is no sweep and no clock to get
wrong on restart; the stored `expires` is still checked on the way out, because a
`take` at exactly the boundary must answer the same way the in-memory store does.
"""
from __future__ import annotations

import json

from ..domain.oauth import PENDING_TTL, Pending, new_state

__all__ = ["PENDING_TTL", "Pending", "RedisPendingStates"]

_PREFIX = "oauth:pending:"


class RedisPendingStates:
    """The `PendingStateStore` port over Redis: ten minutes, single use, bound to
    the owner who pressed Connect."""

    def __init__(self, client, *, ttl: int = PENDING_TTL, prefix: str = _PREFIX):
        self._client = client
        self._ttl = ttl
        self._prefix = prefix

    async def issue(self, name: str, owner_id: int, verifier: str, now: float) -> str:
        state = new_state()
        payload = json.dumps({"name": name, "owner_id": owner_id,
                              "verifier": verifier, "expires": now + self._ttl})
        await self._client.set(self._prefix + state, payload, px=self._ttl * 1000)
        return state

    async def take(self, state: str, owner_id: int, now: float) -> Pending | None:
        """Single use, and consumed only by its owner.

        The owner check comes BEFORE the delete, exactly as the in-memory store
        has it: a stranger who guesses a state must not be able to burn the
        owner's flow, which is a denial of service dressed up as a 404.
        """
        raw = await self._client.get(self._prefix + state)
        if raw is None:
            return None
        data = json.loads(raw)
        if data["owner_id"] != owner_id:
            return None                      # someone else's callback consumes nothing
        await self._client.delete(self._prefix + state)
        p = Pending(data["name"], data["owner_id"], data["verifier"], data["expires"])
        return None if p.expires <= now else p

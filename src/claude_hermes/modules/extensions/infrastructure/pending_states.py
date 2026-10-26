"""Started OAuth flows waiting for their callback, in process memory.

Inline mode only: one process, so a dict is the whole story and a restart between
Connect and the callback means pressing Connect again — a cheap failure. The redis
implementation next door (`redis_pending_states.py`) is what a multi-process
deployment binds instead; the port is async so the two are interchangeable.
"""
from __future__ import annotations

from ..domain.oauth import PENDING_TTL, Pending, new_state

__all__ = ["PENDING_TTL", "Pending", "PendingStates"]


class PendingStates:
    """Started flows waiting for their callback: ten minutes, single use, bound to
    the owner who pressed Connect. Process memory — a restart means pressing again."""

    def __init__(self):
        self._states: dict[str, Pending] = {}

    async def issue(self, name: str, owner_id: int, verifier: str, now: float) -> str:
        self._states = {s: p for s, p in self._states.items() if p.expires > now}
        state = new_state()
        self._states[state] = Pending(name, owner_id, verifier, now + PENDING_TTL)
        return state

    async def take(self, state: str, owner_id: int, now: float) -> Pending | None:
        p = self._states.get(state)
        if p is None or p.owner_id != owner_id:
            return None                          # someone else's callback consumes nothing
        del self._states[state]
        return None if p.expires <= now else p

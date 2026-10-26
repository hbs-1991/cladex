"""The wire event one turn publishes, and what the bus does NOT persist.

The ORM row for the same table is `models.TurnEventRow` — the two used to share
the name `TurnEvent`, which made `from ... import TurnEvent` mean either one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

# Text and thinking deltas reach listeners and the in-flight snapshot, never the
# database — so turn_events grows per block, not per token.
TRANSIENT = frozenset({"text.delta", "thinking.delta"})

# The model keys EVERY `turn.end` carries, as nulls. A turn that never reached
# the CLI still ends with the same shape as one that did — the reaper's synthetic
# end, and the envelope a pre-body failure gets — because a consumer that reads
# `turn.end` must never need a special case for where the end came from.
NO_MODEL_END_KEYS = {"model": None, "effort": None, "fallback_from": None}


@dataclass(frozen=True)
class TurnEvent:
    session_key: str
    turn_id: str
    seq: int
    ts: str
    type: str
    payload: dict
    id: int | None = None

    def to_wire(self) -> dict:
        return {
            "id": self.id, "session_key": self.session_key, "turn_id": self.turn_id,
            "seq": self.seq, "ts": self.ts, "type": self.type, "payload": self.payload,
        }


Listener = Callable[[TurnEvent], Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

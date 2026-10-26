"""Frozen shapes this module hands out.

`MessageHit` is the recall wire shape: the `mcp__hermes__session_search` tool and the
Telegram recall block both render these four keys, so the field names are a
contract, not an implementation detail. The repositories return plain dicts with
exactly these keys; the dataclass is the written-down version of that promise.
"""
from __future__ import annotations

from dataclasses import dataclass

SEARCH_FIELDS = ("session_key", "role", "ts", "snippet")


@dataclass(frozen=True)
class MessageHit:
    session_key: str
    role: str
    ts: str
    snippet: str

    @classmethod
    def from_row(cls, row: dict) -> "MessageHit":
        return cls(**{f: row[f] for f in SEARCH_FIELDS})

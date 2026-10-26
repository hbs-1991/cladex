"""The shape of one `.usage.json` entry, and the aging anchor read off it.

Pure: the file this describes is written by `infrastructure/usage_sidecar.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone


def new_entry(created_by: str, now_iso: str) -> dict:
    return {
        "created_by": created_by,
        "created_at": now_iso,
        "last_used": None,
        "use_count": 0,
        "state": "active",
        "pinned": False,
        "patch_count": 0,
        "last_patched": None,
        "archived_at": None,
    }


def parse_aware(stamp: str) -> datetime:
    dt = datetime.fromisoformat(stamp)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def last_activity(entry: dict) -> datetime:
    """Newest of last_used/last_patched/created_at, tz-aware UTC. Aging anchor."""
    stamps = [
        entry.get("last_used"), entry.get("last_patched"), entry.get("created_at"),
    ]
    return max(parse_aware(s) for s in stamps if s)


# The old private names, kept because `learning/usage.py` re-exports them and the
# curator's own `_parse_aware` was a second copy of the same three lines.
_new_entry = new_entry
_parse_aware = parse_aware

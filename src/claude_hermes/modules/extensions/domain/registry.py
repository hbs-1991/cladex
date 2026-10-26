"""How a registry row reads: the state the SPA shows, the auth kind, and the tool
names stored alongside the count. Pure functions over a row mapping."""
from __future__ import annotations

import json
from collections.abc import Mapping

from .manifest import Manifest

STATES = ("available", "connected", "disabled", "needs_reauth", "error")


def state_of(row: Mapping | None) -> str:
    if row is None:
        return "available"
    if row["state"] == "disabled":
        return "disabled"
    if row["last_error"] == "needs_reauth":
        return "needs_reauth"
    return "error" if row["last_error"] else "connected"


def auth_kind(m: Manifest) -> str:
    if m.oauth_entries():
        return "oauth"
    return "secret" if m.auth else "none"


def tool_names(row: Mapping | None) -> list[str]:
    """The stored names, tolerant of a row from before the column existed."""
    raw = row.get("tool_names") if row else None
    if not raw:
        return []
    try:
        names = json.loads(raw)
    except ValueError:
        return []
    return [str(n) for n in names] if isinstance(names, list) else []

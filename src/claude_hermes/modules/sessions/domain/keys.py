"""The session-key grammar, re-exported from `core.session_keys`.

The functions themselves live in `core/` because turns, telegram, scheduler and
the web all speak the key format — it is an application-wide contract, not this
module's private vocabulary. They are re-exported here so the module's own layers
read `from ..domain.keys import web_session_key` like any other domain import,
and so a later move never has to touch the module.
"""
from __future__ import annotations

from claude_hermes.core.session_keys import (
    build_session_key,
    chat_id_from_key,
    chat_type_from_key,
    thread_id_from_key,
    web_session_key,
)

__all__ = [
    "build_session_key",
    "chat_id_from_key",
    "chat_type_from_key",
    "thread_id_from_key",
    "web_session_key",
]

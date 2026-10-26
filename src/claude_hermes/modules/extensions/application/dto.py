"""What a use case answers with, independent of the channel that asked."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Outcome:
    ok: bool
    code: str = ""              # '' | secrets_missing | oauth_required | compose_failed | probe_failed
    message: str = ""
    tools: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()

"""Shapes that cross a module boundary without belonging to any one module.

`Attachment` is produced by `files` (a web upload) and by `telegram` (a downloaded
photo or document), consumed by `turns` (it goes into the prompt) and rendered by
the web router. Three modules, one dataclass — so it lives in core, not in the
producer, or the consumers would have to import each other.

`CurrentUser` is the same story for identity: `core.auth` builds one per request,
every module's router reads one, and `modules.identity.domain.models` aliases it.

Moved verbatim from `gateway/attachments.py` in Wave 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# The four the API accepts. A Telegram `photo` is always JPEG; a `document`
# carrying one of these is treated as an image too, which is what a user who
# drags a PNG into the chat expects.
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


@dataclass(frozen=True)
class Attachment:
    """A file that landed on disk and is about to reach the turn."""

    path: Path          # absolute, under CH_HOME/agent_home/inbox/<lane>/
    filename: str       # the ORIGINAL Telegram name, for the model to refer to
    media_type: str
    is_image: bool
    file_id: str | None = None   # set for web uploads; Telegram attachments have none


class Role(str, Enum):
    """The two words the API puts on the wire (`/api/me`, the login responses)."""

    OWNER = "owner"
    USER = "user"


@dataclass(frozen=True)
class CurrentUser:
    """Who is asking, as every layer above the middleware sees them.

    Lives here rather than in `core/auth.py` because `modules.identity.domain` aliases
    it, and a domain layer that imported `core.auth` would import Starlette with it —
    the "domain layers are pure" contract forbids exactly that. `core.auth` re-exports
    the name, so `from claude_hermes.core.auth import CurrentUser` reads the way the
    auth dependencies that produce it do.

    No table backs it yet: the owner is a meta row and everyone else is an entry in
    `Config.allowed_users`, so it is built per request from the cookie plus one owner
    check. A real `users` table arrives in Step 4; until then this is the shape.
    """

    user_id: int
    is_owner: bool = False

    @property
    def role(self) -> Role:
        return Role.OWNER if self.is_owner else Role.USER

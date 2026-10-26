"""The two pure rules of approval: what binds a token to bytes, and what the human
is shown before they type it back."""
from __future__ import annotations

import hashlib

from .drafts import describe
from .manifest import Manifest

SNAP_SUBDIR = "pending-approvals"
_BANNER = (
    "⚠️ LOCAL COMMAND — this runs a program in your container. Read command/args below.\n"
)


def token_of(raw: bytes) -> str:
    """Short, content-bound approval token. Binds the exact bytes the human saw: if the
    draft changes, the token the human is holding no longer matches."""
    return hashlib.sha256(raw).hexdigest()[:8]


def describe_with_banner(m: Manifest) -> str:
    body = describe(m)                 # already ANSI-stripped via drafts.safe()
    return (_BANNER + body) if m.transport == "stdio" else body

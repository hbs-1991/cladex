"""Names a caller does not get to choose — the one regex two invariants derive from.

`safe_name` is the containment rule for anything a user typed (a Telegram document's
name, a browser upload's), and `lane_token` is the one string that identifies a
session lane in a path and in an object key. Both are `_UNSAFE` applied to a string:
they have to be derived in exactly one place, or the two drift and the absolute
paths already written into `files.path` stop resolving.

Why `core/` and not `modules/files/domain/`, where the rule was stated first: two
modules apply it and modules may not import each other. `telegram` names the
downloaded file (`infrastructure/attachments.py` builds `Spec.stored` with
`safe_name` before a byte is fetched) and `files` resolves it (`lane_token` is the
inbox directory and the object-key prefix). Core is the legal back door between
modules for an invariant like this one — a pure function with no collaborator —
never for a service; `modules/files/domain/naming.py` re-exports both, so the files
module keeps reading them as its own domain vocabulary.
"""
from __future__ import annotations

import re

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

__all__ = ["lane_token", "safe_name"]


def safe_name(message_id: int, raw: str) -> str:
    """A filename that cannot escape its directory.

    Every character outside [A-Za-z0-9._-] becomes '_', which neutralizes both
    separators, so no traversal survives — and the message-id prefix means the
    result is never bare '..' either. Stripping leading/trailing '.' and '_'
    keeps '../../etc/passwd' from turning into a name that merely *looks*
    alarming in a log line.
    """
    cleaned = _UNSAFE.sub("_", (raw or "").strip())[:120].strip("._") or "file"
    return f"{message_id}_{cleaned}"


def lane_token(session_key: str) -> str:
    """The session key flattened into one path/key segment.

    `web:1:x` -> `web-1-x`. This is the directory name under inbox/ and outbox/
    on disk and the object-key prefix in the bucket, so the mapping must never
    change: the absolute paths already written into `files.path` are resolved
    against it.
    """
    return _UNSAFE.sub("-", session_key) or "lane"

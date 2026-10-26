"""Where a lane's files sit on this host.

Everything lives under agent_home because that is the agent's cwd (see
`integrations.claude_sdk.options`), so its Read and Glob reach a file with no new
mount — and under CH_HOME, so `tar` on the data dir is still a complete backup
(NFR-5). This is the layer MinIO replaces in Wave 3: the lane token stays, the
`home / "agent_home" / ...` prefix does not.

Wave 4a changes what `home` means here and nothing else: it is the **lane
owner's workspace root**, so `blob_root` is that user's `agent_home` and a file
materializes inside the only tree their turn's read guard admits. For the deploy
owner `root is CH_HOME` and every path is byte-identical to before. The S3 KEY
grammar (`inbox/<lane>/…`) is untouched — only the mirror moves, so a bucket
written before this wave still reads.
"""
from __future__ import annotations

from pathlib import Path

from ..domain.naming import lane_token

__all__ = ["blob_root", "inbox_dir", "lane_dir", "outbox_dir", "resolve_within"]


def blob_root(home: Path) -> Path:
    """The containment boundary every stored path must stay inside.

    `home` is a workspace ROOT since Wave 4a (the owner's is `CH_HOME` itself).
    """
    return home / "agent_home"


def inbox_dir(home: Path, session_key: str) -> Path:
    """Where one session lane's incoming attachments live."""
    return blob_root(home) / "inbox" / lane_token(session_key)


def outbox_dir(home: Path, session_key: str) -> Path:
    """Where files the agent produced for one lane are copied."""
    return blob_root(home) / "outbox" / lane_token(session_key)


# The name telegram intake has always imported.
lane_dir = inbox_dir


def resolve_within(root: Path, rest: str) -> Path:
    """The on-disk path for a `local:` address, or PermissionError if it escaped.

    A filesystem security control, not a convenience: `files.path` is written
    from names a user supplied, and this endpoint serves whatever it points
    at. `agent_home in p.parents` after `resolve()` is what stops a row that
    says `/etc/shadow` — or one that traverses out through a symlink — from
    being served to anyone who can see the lane.

    Shared by both stores (`LocalBlobStore` and `S3BlobStore`'s mirror) so the
    check has exactly one body: a second copy is a second place to weaken it.
    `rest` is either a key relative to `root` or, for a pre-Wave-3a row, the
    absolute path that row still holds.
    """
    candidate = Path(rest)
    p = (candidate if candidate.is_absolute()
         else root.joinpath(*[part for part in rest.split("/") if part])).resolve()
    if root not in p.parents:
        raise PermissionError(f"{p} is outside agent_home")
    return p

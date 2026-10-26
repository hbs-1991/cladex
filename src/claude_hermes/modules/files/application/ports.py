"""What `files` needs from the outside, stated as Protocols it owns.

Consumer-side ports: the composition root hands in objects that happen to match.
Nothing here imports the implementations — that is the whole point, and it is
what lets `LocalBlobStore` become a MinIO client in Wave 3 without the service
noticing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    """Bytes addressed by an opaque key. `put`/`copy_in` return the *stored*
    address that goes into the row; `local_path` turns one back into a real file
    on disk, because two consumers need a path and not a stream: aiogram's
    FSInputFile, and the agent's own Read/Glob inside agent_home.

    Every method is async as of Wave 3a: behind the port there may be an object
    store, and a sync port would have forced either a blocking socket call on
    the event loop or two different ports for the two backends.

    Every method carries `owner_id` as of Wave 4a, because the on-disk root is
    per user: it is **the row's** owner, never the requester's, so a read
    re-resolves against whoever the row says owns the file. `0` means "the deploy
    owner" — the pre-4a tree — which is what a legacy row with a NULL column and
    every single-tree deployment get. The KEY is unchanged, so a bucket written
    before this wave still reads.
    """

    async def put(self, key: str, data: bytes, owner_id: int = 0) -> str:
        """Write `data` at `key`; return the stored address."""

    async def copy_in(self, key: str, src: Path, owner_id: int = 0) -> str:
        """Copy an existing file to `key`; return the stored address."""

    async def local_path(self, stored: str, owner_id: int = 0) -> Path:
        """The on-disk path for a stored address, materialized if need be.

        Raises PermissionError when the address points outside the OWNER's root —
        a security control, not a lookup miss: rows are written by code that can
        be tricked into a path, and this is the gate that catches it.
        """

    async def open(self, stored: str, owner_id: int = 0) -> bytes:
        """The bytes behind a stored address, without promising a local file.

        Declared now that one backend can answer it without a download: a
        consumer that only wants the content (a worker on a volume with a cold
        cache) should not pay for a mirror it will never read again.
        """


class FileRepository(Protocol):
    """The `files` table. No SQL for it lives anywhere else (see README)."""

    async def insert(self, file_id: str, session_key: str, path: str, name: str,
                     media_type: str, size: int, created_at: str, *,
                     owner_id: int | None = None) -> None: ...

    async def get(self, file_id: str) -> dict | None: ...


class ThreadVisibility(Protocol):
    """Whether this caller may see a lane at all. Owned by `sessions`; `files`
    only ever asks. Raises NotFound when the lane is invisible — the download
    endpoint must not distinguish "someone else's file" from "no such file"."""

    async def get_visible(self, session_key: str, user_id: int | None,
                          is_owner: bool) -> dict: ...

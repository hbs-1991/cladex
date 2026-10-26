"""The `BlobStore` port over the local filesystem.

Keys are `inbox/<lane>/<name>` / `outbox/<lane>/<name>`; the stored address is
`local:<key>` — relative, so a restore under a different `CH_HOME` still
resolves, which the absolute paths this store wrote before Wave 3a did not.
Those old rows are still read (see `domain.addressing.normalize`).

Async because the port is: the object store behind `S3BlobStore` cannot be
synchronous, and one port with two shapes would push the difference into every
caller. The bodies here stay plain disk calls — they are local, and wrapping a
`write_bytes` in a thread costs more than it saves.

Wave 4a: the root is no longer frozen at construction. Every call carries the
**row's** `owner_id` and the root is looked up per call (`infrastructure.roots`),
because one tree per user means the write path and the read path must agree about
which tree a given row lives in. A `Path` still works where the lookup goes, and
means "one tree, this one".
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..domain.addressing import split, split_s3
from .layout import blob_root, resolve_within
from .roots import RootLookup, fixed


class LocalBlobStore:
    """`roots` is either the Wave 4a per-owner lookup or the pre-4a `home`.

    A `Path` keeps its OLD meaning — a workspace root, whose `agent_home` is
    where blobs live — so `LocalBlobStore(home)` still writes exactly where it
    always did. A callable is `(owner_id) -> agent_home`, already resolved, which
    is what the composition root hands in. `S3BlobStore` takes the root itself in
    both spellings; that asymmetry is older than this wave and is preserved
    rather than tidied, because tidying it would move files on a live volume.
    """

    def __init__(self, roots: RootLookup | Path):
        self._roots = roots if callable(roots) else fixed(blob_root(Path(roots)))

    async def root(self, owner_id: int = 0) -> Path:
        """This owner's `agent_home`. Async since Wave 4a — see the module note."""
        return await self._roots(owner_id)

    async def put(self, key: str, data: bytes, owner_id: int = 0) -> str:
        dest = await self._dest(key, owner_id)
        dest.write_bytes(data)
        return f"local:{key}"

    async def copy_in(self, key: str, src: Path, owner_id: int = 0) -> str:
        dest = await self._dest(key, owner_id)
        if Path(src).resolve() != dest:
            shutil.copyfile(src, dest)
        return f"local:{key}"

    async def local_path(self, stored: str, owner_id: int = 0) -> Path:
        """The on-disk path, or PermissionError if it escaped agent_home.

        The containment check itself is `layout.resolve_within`; an `s3://` row
        reaching this store means the deployment was pointed at the bucket and
        then back again, and the mirror under `agent_home` is the honest answer
        for it — the same directory either backend materializes into.

        The root is the ROW OWNER's, so containment is checked against the tree
        the row belongs to and a file id that names another user's lane cannot
        resolve into this one.
        """
        scheme, rest = split(stored)
        if scheme == "s3":
            rest = split_s3(rest)[1]
        return resolve_within(await self._roots(owner_id), rest)

    async def open(self, stored: str, owner_id: int = 0) -> bytes:
        """The bytes behind a stored address. Only the containment-checked path
        is ever read, so a forged row cannot turn this into an arbitrary read."""
        return (await self.local_path(stored, owner_id)).read_bytes()

    async def _dest(self, key: str, owner_id: int) -> Path:
        root = await self._roots(owner_id)
        dest = root.joinpath(*key.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

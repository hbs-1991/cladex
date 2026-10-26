"""The `BlobStore` port over an object store, with a local mirror.

Two facts force the mirror. The agent's own `Read`/`Glob` run in `agent_home`
and the prompts name `inbox/<lane>/...` paths, and aiogram's `FSInputFile`
wants a real file — neither can be handed a key. So every write also lands
under the mirror root (which the composition root resolves to `blob_root` of the
ROW OWNER's workspace, the very directory `LocalBlobStore` uses), and
`local_path` downloads a key it does not find there. The bucket is the source of
truth; the mirror is a cache that may be empty after a redeploy on a fresh
volume.

The stored address is `s3://<bucket>/<key>`, and the key is exactly the key the
filesystem store used — `lane_token` is unchanged, and Wave 4a does **not**
touch the key grammar either — so the two backends read each other's layout, a
migration is an upload rather than a rewrite of `files.path`, and a bucket
written before the per-user split still reads afterwards. Only the mirror moved.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..domain.addressing import s3_address, split, split_s3
from .layout import resolve_within
from .roots import RootLookup, as_lookup


class S3BlobStore:
    def __init__(self, client: Any, cache_root: RootLookup | Path):
        self._client = client
        self._roots = as_lookup(cache_root)

    async def root(self, owner_id: int = 0) -> Path:
        """This owner's mirror root. Async since Wave 4a — see `roots.py`."""
        return await self._roots(owner_id)

    async def put(self, key: str, data: bytes, owner_id: int = 0) -> str:
        await self._client.put_object(key, data)
        (await self._mirror(key, owner_id)).write_bytes(data)
        return s3_address(self._client.bucket, key)

    async def copy_in(self, key: str, src: Path, owner_id: int = 0) -> str:
        await self._client.upload_file(key, src)
        dest = await self._mirror(key, owner_id)
        src = Path(src).resolve()
        # `publish` is handed a file the agent wrote, which may already be the
        # mirror path itself (an outbox key re-published); copyfile onto itself
        # raises SameFileError.
        if src != dest:
            shutil.copyfile(src, dest)
        return s3_address(self._client.bucket, key)

    async def local_path(self, stored: str, owner_id: int = 0) -> Path:
        """The mirror path for a stored address, downloaded if the cache is cold.

        The containment check runs BEFORE anything is fetched or written
        (`layout.resolve_within`): a forged row is refused, not materialized.
        Checked against the ROW OWNER's mirror root, so a file id naming another
        user's lane cannot be materialized into this user's tree.
        """
        root = await self._roots(owner_id)
        scheme, rest = split(stored)
        if scheme != "s3":
            # A row written while this deployment ran on the filesystem store.
            # The mirror root is the same directory, so it is already local.
            return resolve_within(root, rest)
        key = split_s3(rest)[1]
        path = resolve_within(root, key)
        if not path.is_file():
            await self._client.download_file(key, path)
        return path

    async def open(self, stored: str, owner_id: int = 0) -> bytes:
        """Bytes without touching the mirror — for a consumer that only wants the
        content, and for the worker process, whose volume may not have the file."""
        scheme, rest = split(stored)
        if scheme != "s3":
            return resolve_within(await self._roots(owner_id), rest).read_bytes()
        return await self._client.get_object(split_s3(rest)[1])

    async def _mirror(self, key: str, owner_id: int) -> Path:
        dest = resolve_within(await self._roots(owner_id), key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

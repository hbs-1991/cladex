"""Files that cross the turn boundary, addressed by opaque ids.

One orchestration in both directions — sanitize the name, write the blob,
register the row — so the browser upload and the file the agent produced end up
with identical records and the same `/api/files/{id}` URL. The size ceiling
lives here too: it is one rule for both channels, not a web-router check that a
tool call could walk around.

Moved from `filestore.py::LocalFileStore` in Wave 2; the storage half
is behind the `BlobStore` port — the filesystem or MinIO, chosen by
`blob_backend` at composition — and the row half behind `FileRepository`.
Everything that touches the store is async since Wave 3a, because the object
store behind the port speaks over a socket.
"""
from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path

from claude_hermes.core.contracts import IMAGE_TYPES, Attachment

from ..domain.addressing import normalize
from ..domain.exceptions import FileTooLarge, UnknownAttachment
from ..domain.naming import lane_token, safe_name
from .dto import to_wire
from .ports import BlobStore, FileRepository

# Python's table has no entry for .md on some hosts, and the web UI renders a
# report inline only when the media type says text/*.
mimetypes.add_type("text/markdown", ".md")


def _root_owner(owner_id) -> int:
    """Which tree a blob lives in, as the store wants it (Wave 4a, blocker B4).

    The stores look their root up per call (`infrastructure/roots.py`), because
    one tree per user means a frozen root would make the write path and the read
    path disagree: a file written into user B's `inbox/` would be looked up under
    the deploy owner's `agent_home`, and `resolve_within` would either raise or
    hand the model a path its own `Read` is denied.

    `None` — a row written before the column existed —
    becomes `0`, which the composition root resolves to the deploy owner's tree.
    That is where those files actually are.
    """
    try:
        return int(owner_id or 0)
    except (TypeError, ValueError):
        return 0


class FileService:
    def __init__(self, repo: FileRepository, blobs: BlobStore,
                 max_upload_bytes: int | None = None):
        self._repo = repo
        self._blobs = blobs
        self._max_upload_bytes = max_upload_bytes

    @property
    def max_upload_bytes(self) -> int | None:
        return self._max_upload_bytes

    # --- writes -------------------------------------------------------------

    async def publish(self, session_key: str, path: Path, *,
                      owner_id: int | None = None) -> dict:
        """Copy a file the agent produced into outbox/<lane>/ and register it.

        `owner_id` (Wave 4a) is the LANE's owner, decided by the caller with
        `lane_owner` — never the sender, and never a process default. It is what
        the row records and what the download route re-resolves the blob's mirror
        root against.
        """
        file_id = uuid.uuid4().hex
        key = f"outbox/{lane_token(session_key)}/{file_id[:8]}_{path.name}"
        stored = await self._blobs.copy_in(key, path, _root_owner(owner_id))
        return await self._register(file_id, session_key, stored, path.name,
                                    owner_id=owner_id)

    async def put_upload(self, session_key: str, filename: str, data: bytes,
                         media_type: str | None = None, *,
                         max_bytes: int | None = None,
                         owner_id: int | None = None) -> dict:
        """Write a browser upload into inbox/<lane>/ under a name that cannot escape."""
        limit = self._max_upload_bytes if max_bytes is None else max_bytes
        if limit is not None and len(data) > limit:
            raise FileTooLarge(f"file exceeds {limit // (1024 * 1024)} MB")
        file_id = uuid.uuid4().hex
        # The id's first bytes stand in for a Telegram message id: two uploads of
        # the same name in one lane must not collide.
        key = f"inbox/{lane_token(session_key)}/{safe_name(int(file_id[:6], 16), filename)}"
        stored = await self._blobs.put(key, data, _root_owner(owner_id))
        return await self._register(file_id, session_key, stored,
                                    Path(filename).name or "file", media_type,
                                    owner_id=owner_id)

    async def _register(self, file_id: str, session_key: str, stored: str, name: str,
                        media_type: str | None = None, *,
                        owner_id: int | None = None) -> dict:
        media = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        size = (await self._blobs.local_path(
            stored, _root_owner(owner_id))).stat().st_size
        now = datetime.now(timezone.utc).isoformat()
        await self._repo.insert(file_id, session_key, stored, name, media, size, now,
                                owner_id=owner_id)
        return await self._repo.get(file_id)

    # --- reads --------------------------------------------------------------

    async def get(self, file_id: str) -> dict | None:
        return await self._repo.get(file_id)

    async def resolve(self, record: dict) -> Path:
        """The on-disk path, or PermissionError if it escaped the store's root.

        The mirror root is the ROW's owner (Wave 4a), never the caller's: the
        download route hands this whatever the row says, so a guessed or forged
        file id cannot resolve into the requester's own tree, and a file that
        legitimately belongs to another user is found where it actually is.

        `normalize` is the compat gate: rows written before Wave 3a hold a bare
        absolute path, and a store that only understood `local:<key>` /
        `s3://…` would answer "no such file" for every file the agent has ever
        sent. The rule lives in `domain.addressing` so both stores read a row
        the same way.
        """
        return await self._blobs.local_path(normalize(record["path"]),
                                            _root_owner(record.get("owner_id")))

    async def open(self, record: dict) -> bytes:
        """The bytes, for a caller that does not need them on this host's disk."""
        return await self._blobs.open(normalize(record["path"]),
                                      _root_owner(record.get("owner_id")))

    @staticmethod
    def to_wire(record: dict) -> dict:
        return to_wire(record)

    async def attachments_for(self, session_key: str, ids: list, *,
                              owner_id: int | None = None) -> list[Attachment]:
        """Turn file ids a client sent into attachments for one lane.

        The `session_key` check is the authorization: a caller that may post to a
        lane must not be able to pull another lane's bytes into its prompt by id.

        `owner_id` (Wave 4a) is the second belt, checked as an AND and never as an
        OR: a row whose owner is not this lane's owner is refused even if the keys
        match, which is what catches a row written before the columns existed or
        one whose lane changed hands. `None` skips the check, for a
        caller that has no owner to offer; a row with a NULL `owner_id` is
        refused whenever one IS offered, because "belongs to nobody" must not read
        as "belongs to you".
        """
        out: list[Attachment] = []
        for fid in ids:
            rec = await self._repo.get(str(fid))
            if rec is None or rec["session_key"] != session_key:
                raise UnknownAttachment(f"unknown attachment {fid}")
            if owner_id is not None and rec.get("owner_id") != owner_id:
                raise UnknownAttachment(f"unknown attachment {fid}")
            out.append(Attachment(
                path=await self.resolve(rec), filename=rec["name"],
                media_type=rec["media_type"],
                is_image=rec["media_type"] in IMAGE_TYPES, file_id=rec["id"]))
        return out

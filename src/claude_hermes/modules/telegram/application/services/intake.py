"""Non-text intake: gate first, download second.

Moved verbatim from `gateway/telegram.py` in Wave 2; `home: Path` +
module-level `specs`/`download_all`/`lane_dir` became a `WorkspacePort` and an
`AttachmentDownloaderPort`, because the sanitising and the disk layout belong to
the `files` module.

Wave 4a makes the fallback lane-aware: `WorkspacePort.lane_dir` takes the channel
and the sender as well and is async, because which user's `inbox/` an attachment
belongs in is decided by the lane, not by one process-wide `home` (blocker B5).

Wave 3a adds `LaneFilesPort`. When it is wired the bytes are fetched into a
temporary directory and handed to `files` (`put_upload`), which names them,
registers the row and — on the S3 backend — puts them in the bucket. A Telegram
attachment is then the same kind of object as a browser upload, which is what
lets a worker on another host open it. Without it the service behaves exactly as
it did: straight into `workspace.lane_dir(key)`.
"""
from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import structlog

from claude_hermes.core.config import Config
from claude_hermes.core.session_keys import build_session_key

from ...domain.constants import MAX_ATTACHMENT_BYTES
from ...domain.messages import _ATTACH_FAIL, _ATTACH_TOO_BIG, _NO_CAPTION
from ...domain.models import Intake, MessageEvent
from ..ports import (
    AclPort,
    AttachmentDownloaderPort,
    LaneFilesPort,
    WorkspacePort,
)
from .gating import verdict as _verdict

log = structlog.get_logger(__name__)


class IntakeService:
    """Decides what a photo/document message is worth before spending a byte."""

    def __init__(self, cfg: Config, *, acl: AclPort, workspace: WorkspacePort,
                 downloader: AttachmentDownloaderPort,
                 files: LaneFilesPort | None = None):
        self._cfg = cfg
        self._acl = acl
        self._workspace = workspace
        self._downloader = downloader
        self._files = files

    def specs(self, message) -> list:
        """What is downloadable on this message — the dispatcher asks before it
        decides whether this is an attachment update at all."""
        return self._downloader.specs(message)

    async def intake(self, message, *, gate: MessageEvent, chat_action=None) -> Intake:
        """Gate first, download second.

        The order is the whole point. An attachment in a group we are not addressed
        in must cost nothing — no download, and no typing indicator either, since
        that would announce the bot in a chat it is supposed to be silent in. Voice
        notes gate the same way before spending a transcription call. The size check
        reads file_size off the Telegram object, so a refusal is also free.
        """
        decision = await _verdict(self._cfg, gate, self._acl)
        if decision != "run":
            return Intake(decision)

        items = self._downloader.specs(message)
        for s in items:
            if s.size > MAX_ATTACHMENT_BYTES:
                return Intake(decision, error=_ATTACH_TOO_BIG.format(
                    name=s.original, mb=s.size / (1024 * 1024)))

        if chat_action is not None:
            await chat_action()

        key = build_session_key(gate.chat_type, gate.chat_id,
                                user_id=gate.user_id, thread_id=gate.thread_id)
        try:
            saved = await self._fetch(message, items, key, gate)
        except Exception:
            log.exception("attachment download failed for chat %s", gate.chat_id)
            return Intake(decision, error=_ATTACH_FAIL)

        return Intake(decision, content=gate.text or _NO_CAPTION, attachments=saved)

    async def _fetch(self, message, items: list, key: str, gate: MessageEvent) -> list:
        """Download this message's attachments and return them as `Attachment`s.

        Through `files` when one is wired, so the bytes are registered and end up
        wherever the blob backend keeps them; straight into the lane directory
        otherwise. Either way the caller gets paths that exist on this host —
        `put_upload` writes the local materialization too.
        """
        if self._files is None:
            return await self._downloader.download_all(
                message.bot, items,
                # The lane, not one bound home: `lane_owner` reads the OWNER out
                # of the key (a private chat is its own user, a group belongs to
                # the deploy owner), so a non-owner's photo lands in their own
                # `inbox/` — the only tree their turn is allowed to read.
                await self._workspace.lane_dir(key, "telegram", gate.user_id))
        # A scratch directory, not the lane: `files` decides the final name, and
        # a half-downloaded attachment must not be left in the agent's inbox for
        # the next turn to trip over.
        with tempfile.TemporaryDirectory(prefix="ch-intake-") as scratch:
            fetched = await self._downloader.download_all(
                message.bot, items, Path(scratch))
            saved = []
            for att in fetched:
                # The ceiling is Telegram's, already enforced above from
                # `file_size`; passing it explicitly keeps the web upload limit
                # (a different, usually smaller number) from refusing a chat
                # attachment that the gate already accepted.
                rec = await self._files.put_upload(
                    key, att.filename, att.path.read_bytes(), att.media_type,
                    max_bytes=MAX_ATTACHMENT_BYTES,
                    # The LANE's owner, not the sender: a photo in a group chat
                    # belongs to the deploy owner's tree, because that is the
                    # workspace the group's turn runs in.
                    owner_id=await self._workspace.lane_owner(
                        key, "telegram", gate.user_id) or None)
                saved.append(replace(att, path=await self._files.resolve(rec),
                                     file_id=rec["id"]))
        return saved

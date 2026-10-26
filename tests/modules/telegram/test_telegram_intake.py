"""`IntakeService` against an `AttachmentDownloaderPort` fake.

The ordering — gate, then size check, then typing indicator, then download — is
the whole point of the service, so each step gets its own assertion that the
*later* steps cost nothing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_hermes.core.config import Config
from claude_hermes.core.contracts import Attachment
from claude_hermes.core.db.engine import make_engine
from claude_hermes.modules.files.application.service import FileService
from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore
from claude_hermes.modules.files.infrastructure.repository import FileRepository
from claude_hermes.modules.telegram.application.services.intake import IntakeService
from claude_hermes.modules.telegram.domain.models import MessageEvent


@dataclass
class Spec:
    original: str = "photo.jpg"
    size: int = 1000


class Downloader:
    """Records what it was asked for; `fail` makes the fetch blow up."""

    def __init__(self, items=None, fail=False):
        self.items = [Spec()] if items is None else items
        self.destinations = []
        self._fail = fail

    def specs(self, message):
        return list(self.items)

    async def download_all(self, bot, items, dest):
        self.destinations.append(dest)
        if self._fail:
            raise RuntimeError("telegram is down")
        return [Attachment(path=dest / s.original, filename=s.original,
                           media_type="image/jpeg", is_image=True) for s in items]


class Workspace:
    """The Wave 4a `WorkspacePort`: a lane, not one bound home.

    `owner` is what `lane_owner(key, channel, user_id, deploy_owner)` would
    answer; the fake records the whole tuple it was asked about, because "which
    lane did intake resolve" is the thing that went wrong before this wave.
    """

    def __init__(self, root=Path("/lanes"), owner=0):
        self.asked = []
        self._root = root
        self._owner = owner

    async def lane_dir(self, session_key, channel="telegram", user_id=0):
        self.asked.append(session_key)
        self.lane = (session_key, channel, user_id)
        return self._root / str(self._owner) / session_key if self._owner             else self._root / session_key

    async def lane_owner(self, session_key, channel="telegram", user_id=0):
        self.asked_owner = (session_key, channel, user_id)
        return self._owner


class Acl:
    def __init__(self, verdict="allow"):
        self._verdict = verdict

    async def check(self, user_id, chat_type, profile=None):
        return self._verdict

    async def is_owner(self, user_id):
        return True


def gate(text="", *, chat_type="private", mentioned=False):
    return MessageEvent(chat_type=chat_type, chat_id=1, user_id=7, text=text,
                        mentioned=mentioned)


def msg():
    """The aiogram message is opaque to the service — only `.bot` is read, and only
    to hand it to the downloader."""
    return SimpleNamespace(bot=object())


def service(downloader, *, acl=None, workspace=None, files=None):
    return IntakeService(Config(group_require_mention=True), acl=acl or Acl(),
                         workspace=workspace or Workspace(), downloader=downloader,
                         files=files)


async def test_a_gated_photo_lands_in_its_lane_directory():
    d, ws = Downloader(), Workspace()
    r = await service(d, workspace=ws).intake(msg(), gate=gate("what is this"))
    assert r.verdict == "run" and r.error is None
    assert r.content == "what is this"
    assert ws.asked == ["tg:private:1"]
    assert r.attachments[0].path.parent == Path("/lanes") / "tg:private:1"


async def test_a_caption_less_attachment_still_carries_placeholder_text():
    r = await service(Downloader()).intake(msg(), gate=gate(""))
    assert r.content.strip() and len(r.attachments) == 1


async def test_an_unaddressed_group_attachment_costs_nothing():
    d, seen = Downloader(), []

    async def chat_action():
        seen.append(1)

    r = await service(d).intake(
        object(), gate=gate("nice", chat_type="supergroup"), chat_action=chat_action)
    assert r.verdict == "ignore"
    assert d.destinations == []       # not one byte fetched
    assert seen == []                 # and no typing indicator leaked presence


async def test_a_denied_user_downloads_nothing():
    d = Downloader()
    r = await service(d, acl=Acl("deny")).intake(msg(), gate=gate("hi"))
    assert r.verdict == "deny" and d.destinations == []


async def test_an_oversize_file_is_refused_before_the_typing_indicator():
    d, seen = Downloader([Spec("Q3 report.pdf", 25 * 1024 * 1024)]), []

    async def chat_action():
        seen.append(1)

    r = await service(d).intake(msg(), gate=gate("read this"),
                                chat_action=chat_action)
    assert r.error and "20MB" in r.error and "Q3 report.pdf" in r.error
    assert d.destinations == [] and seen == []


async def test_a_failed_download_becomes_a_visible_error():
    r = await service(Downloader(fail=True)).intake(msg(), gate=gate("look"))
    assert r.verdict == "run" and r.attachments == []
    assert r.error and "download" in r.error.lower()


async def test_specs_is_delegated_so_the_dispatcher_asks_only_the_port():
    d = Downloader([Spec("a.jpg"), Spec("b.jpg")])
    assert len(service(d).specs(object())) == 2


# --- through the `files` module (Wave 3a) -----------------------------------


class WritingDownloader:
    """Like `Downloader`, but it actually puts bytes on disk — which is what the
    `files` path reads back before it registers the row."""

    def __init__(self, items=None):
        self.items = [Spec()] if items is None else items
        self.destinations = []

    def specs(self, message):
        return list(self.items)

    async def download_all(self, bot, items, dest):
        self.destinations.append(dest)
        out = []
        for s in items:
            path = Path(dest) / f"42_{s.original.replace(' ', '_')}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"payload")
            out.append(Attachment(path=path, filename=s.original,
                                  media_type="image/jpeg", is_image=True))
        return out


@pytest.fixture
async def files(tmp_path):
    (tmp_path / "agent_home").mkdir()
    engine = make_engine(os.environ["CH_DATABASE_URL"])
    yield FileService(FileRepository(engine), LocalBlobStore(tmp_path), 8)
    await engine.dispose()


async def test_a_telegram_attachment_is_registered_like_a_web_upload(files, tmp_path):
    """The point of the port: the photo gets a row and an id, so the file exists
    for the worker and for `/api/files/{id}` — not only in this host's inbox."""
    d, ws = WritingDownloader(), Workspace()
    r = await service(d, workspace=ws, files=files).intake(
        msg(), gate=gate("what is this"))

    assert r.verdict == "run" and r.error is None
    [att] = r.attachments
    assert att.filename == "photo.jpg"          # the ORIGINAL name, as before
    assert att.file_id and await files.get(att.file_id)
    assert att.path.parent == tmp_path / "agent_home" / "inbox" / "tg-private-1"
    assert att.path.read_bytes() == b"payload"
    # The lane directory was never asked for, and the scratch dir is gone.
    assert ws.asked == []
    assert not d.destinations[0].exists()


async def test_the_ceiling_that_applies_is_telegrams_not_the_web_uploads(files):
    """The service was built with an 8-byte web ceiling; a 7-byte payload that the
    gate already accepted must not be refused by it."""
    r = await service(WritingDownloader(), files=files).intake(msg(), gate=gate("hi"))
    assert r.error is None and len(r.attachments) == 1


async def test_a_failure_inside_the_files_module_is_the_same_visible_error(files):
    class Boom:
        def specs(self, message):
            return [Spec()]

        async def download_all(self, bot, items, dest):
            raise RuntimeError("telegram is down")

    r = await service(Boom(), files=files).intake(msg(), gate=gate("look"))
    assert r.verdict == "run" and r.attachments == []
    assert r.error and "download" in r.error.lower()


# --- whose inbox (Wave 4a, blocker B5) --------------------------------------


async def test_the_lane_decides_the_directory_not_one_bound_home():
    """`lane_dir` takes the key, the channel and the SENDER, so the destination
    is `lane_owner`'s tree. With one bound `home` a non-owner's photo landed in
    the deploy owner's `inbox/`, where the sender's own turn could not read it."""
    d, ws = Downloader(), Workspace(owner=7)
    r = await service(d, workspace=ws).intake(msg(), gate=gate("what is this"))
    assert r.verdict == "run" and r.error is None
    assert ws.lane == ("tg:private:1", "telegram", 7)   # the key and the SENDER
    assert r.attachments[0].path.parent == Path("/lanes") / "7" / "tg:private:1"


async def test_a_registered_attachment_asks_who_owns_the_lane(files):
    """Through `files`, the directory is the store's business and the OWNER is
    what decides it — so the owner is what intake has to supply."""
    d, ws = WritingDownloader(), Workspace()
    r = await service(d, workspace=ws, files=files).intake(
        msg(), gate=gate("look"))
    assert r.verdict == "run" and r.error is None
    assert ws.asked_owner == ("tg:private:1", "telegram", 7)


async def test_an_unknown_lane_owner_is_null_and_never_user_zero():
    """`0` is "nobody knows" — the legacy entry point, which has one tree. Writing
    it as an owner id would claim a user that does not exist and violate the FK."""
    recorded = {}

    class Files:
        async def put_upload(self, key, name, data, media_type, *, max_bytes=None,
                             owner_id=None):
            recorded["owner_id"] = owner_id
            return {"id": "f1", "path": "local:x"}

        async def resolve(self, rec):
            return Path("/tmp/x")

    d = WritingDownloader()
    await service(d, workspace=Workspace(owner=0), files=Files()).intake(
        msg(), gate=gate("look"))
    assert recorded["owner_id"] is None

from pathlib import Path
from types import SimpleNamespace

import pytest

# safe_name/lane_dir are files-module invariants and are tested in
# tests/modules/files/test_files_naming.py; lane_dir is imported here because the
# aiogram-facing helpers below still compose with it.
from claude_hermes.modules.files.infrastructure.layout import lane_dir
from claude_hermes.modules.telegram.infrastructure.attachments import (
    download_all,
    specs,
)


def test_specs_picks_the_largest_photo_size():
    # Telegram orders PhotoSize smallest -> largest.
    msg = SimpleNamespace(
        message_id=5,
        photo=[SimpleNamespace(file_size=100), SimpleNamespace(file_size=9000)],
        document=None,
    )
    [s] = specs(msg)
    assert s.size == 9000
    assert s.media_type == "image/jpeg"
    assert s.original == "photo.jpg" and s.stored == "5_photo.jpg"


def test_specs_keeps_the_document_name_and_mime():
    msg = SimpleNamespace(
        message_id=5, photo=None,
        document=SimpleNamespace(file_name="Q3 report.pdf",
                                 mime_type="application/pdf", file_size=10),
    )
    [s] = specs(msg)
    assert s.original == "Q3 report.pdf"
    assert s.stored == "5_Q3_report.pdf"
    assert s.media_type == "application/pdf"


def test_specs_is_empty_for_a_plain_text_message():
    assert specs(SimpleNamespace(message_id=1, photo=None, document=None)) == []


class FakeBot:
    """Records every download and writes a known payload."""

    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    async def download(self, media, destination):
        self.calls.append(destination)
        if self._fail:
            raise RuntimeError("telegram is down")
        Path(destination).write_bytes(b"payload")


async def test_download_all_creates_the_lane_dir_and_flags_an_image(tmp_path):
    msg = SimpleNamespace(
        message_id=5, photo=None,
        document=SimpleNamespace(file_name="a.png", mime_type="image/png",
                                 file_size=7),
    )
    bot = FakeBot()
    dest = lane_dir(tmp_path, "tg:private:1")
    assert not dest.exists()

    [att] = await download_all(bot, specs(msg), dest)

    assert att.path == dest / "5_a.png"
    assert att.path.read_bytes() == b"payload"
    assert att.filename == "a.png"          # the ORIGINAL name, not the stored one
    assert att.is_image is True


async def test_download_all_marks_a_pdf_as_not_an_image(tmp_path):
    msg = SimpleNamespace(
        message_id=5, photo=None,
        document=SimpleNamespace(file_name="r.pdf", mime_type="application/pdf",
                                 file_size=7),
    )
    [att] = await download_all(FakeBot(), specs(msg),
                               lane_dir(tmp_path, "tg:private:1"))
    assert att.is_image is False
    assert att.media_type == "application/pdf"


async def test_download_all_propagates_a_failure(tmp_path):
    msg = SimpleNamespace(message_id=5, photo=[SimpleNamespace(file_size=1)],
                          document=None)
    with pytest.raises(RuntimeError):
        await download_all(FakeBot(fail=True), specs(msg),
                           lane_dir(tmp_path, "tg:private:1"))

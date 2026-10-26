"""FileService over the real repository and the local blob store.

No legacy `Database` here on purpose: the module is meant to be constructible
from an engine and a home directory alone.
"""
import os

import pytest

from claude_hermes.core.db.engine import make_engine
from claude_hermes.modules.files.application.service import FileService
from claude_hermes.modules.files.domain.exceptions import FileTooLarge, UnknownAttachment
from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore
from claude_hermes.modules.files.infrastructure.repository import FileRepository


@pytest.fixture
async def files(tmp_path):
    (tmp_path / "agent_home").mkdir()
    engine = make_engine(os.environ["CH_DATABASE_URL"])
    yield FileService(FileRepository(engine), LocalBlobStore(tmp_path), 1024)
    await engine.dispose()


async def test_publish_copies_into_outbox_and_registers(files, tmp_path):
    src = tmp_path / "agent_home" / "reports" / "w36.md"
    src.parent.mkdir()
    src.write_text("# hi", encoding="utf-8")

    rec = await files.publish("web:1:x", src)

    assert rec["name"] == "w36.md" and rec["media_type"] == "text/markdown"
    assert rec["size"] == 4 and len(rec["id"]) == 32
    # The row holds a store address, not a host path: `local:<key>` survives a
    # restore under a different CH_HOME, and the key is what S3 uses too.
    assert rec["path"] == f"local:outbox/web-1-x/{rec['id'][:8]}_w36.md"
    stored = await files.resolve(rec)
    assert stored.read_text(encoding="utf-8") == "# hi"
    assert "outbox" in stored.parts and "web-1-x" in stored.parts
    assert await files.get(rec["id"]) == rec
    assert files.to_wire(rec) == {
        "file_id": rec["id"], "name": "w36.md", "media_type": "text/markdown",
        "size": 4, "url": f"/api/files/{rec['id']}"}


async def test_put_upload_lands_in_inbox_with_a_safe_name(files, tmp_path):
    rec = await files.put_upload("web:1:x", "../../evil name.png", b"\x89PNG", "image/png")
    p = await files.resolve(rec)
    assert p.parent == tmp_path / "agent_home" / "inbox" / "web-1-x"
    assert ".." not in p.name and p.name.endswith("evil_name.png")
    assert p.read_bytes() == b"\x89PNG"


async def test_upload_above_the_ceiling_is_refused_before_anything_is_written(files, tmp_path):
    with pytest.raises(FileTooLarge) as e:
        await files.put_upload("web:1:x", "big.bin", b"x" * 1025, None)
    assert e.value.status == 413 and e.value.code == "too_large"
    assert not (tmp_path / "agent_home" / "inbox").exists()


async def test_a_per_call_limit_overrides_the_constructor_one(files):
    # Telegram's intake passes its own ceiling (`MAX_ATTACHMENT_BYTES`) per call.
    rec = await files.put_upload("web:1:x", "a.txt", b"x" * 2000, "text/plain",
                                 max_bytes=4096)
    assert rec["size"] == 2000


async def test_the_constructor_ceiling_is_the_one_the_web_upload_gets(tmp_path, engine):
    """`web_upload_max_mb` reaches the service through the composition root and
    nowhere else: a browser upload passes no `max_bytes`, so the constructor's
    number is the ceiling it hits."""
    limited = FileService(FileRepository(engine), LocalBlobStore(tmp_path), 2)
    with pytest.raises(FileTooLarge):
        await limited.put_upload("web:1:x", "a.txt", b"abc", "text/plain")
    rec = await limited.put_upload("web:1:x", "a.txt", b"ab", "text/plain")
    assert rec["size"] == 2


async def test_resolve_refuses_paths_outside_agent_home(files, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(PermissionError):
        # No scheme: a row written before Wave 3a, read as `local:` — and still
        # refused, because the containment check is on the resolved path.
        await files.resolve({"path": str(outside)})


async def test_attachments_for_refuses_another_lanes_file(files, tmp_path):
    mine = await files.put_upload("web:1:x", "a.png", b"\x89PNG", "image/png")
    theirs = await files.put_upload("web:2:y", "b.txt", b"b", "text/plain")

    [att] = await files.attachments_for("web:1:x", [mine["id"]])
    assert att.filename == "a.png" and att.is_image is True
    assert att.file_id == mine["id"] and att.path.is_file()

    with pytest.raises(UnknownAttachment):
        await files.attachments_for("web:1:x", [theirs["id"]])
    with pytest.raises(UnknownAttachment):
        await files.attachments_for("web:1:x", ["nope"])


async def test_a_legacy_absolute_row_still_resolves(files, tmp_path):
    """Rows written before Wave 3a hold `<home>/agent_home/...`; `normalize` reads
    one as a `local:` address so the file the agent sent last month still opens."""
    legacy = tmp_path / "agent_home" / "outbox" / "web-1-x" / "old.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old", encoding="utf-8")
    assert await files.resolve({"path": str(legacy)}) == legacy


async def test_open_returns_the_bytes_without_a_path(files):
    rec = await files.put_upload("web:1:x", "a.txt", b"hello", "text/plain")
    assert await files.open(rec) == b"hello"

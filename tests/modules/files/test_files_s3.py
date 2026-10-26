"""`S3BlobStore` against the real MinIO from `make test-stack`.

Not a mock on purpose: what is being tested is the agreement between the bucket
and the local mirror — an upload that never reached the bucket, or a mirror that
is not written, both pass against a fake and lose files in production. The
containment refusal is here too, because it is the one check a forged `files`
row runs into, and it must behave the same on both backends.
"""
import os

import pytest

from claude_hermes.core.db.engine import make_engine
from claude_hermes.core.exceptions import NotFound
from claude_hermes.modules.files.application.service import FileService
from claude_hermes.modules.files.infrastructure.repository import FileRepository
from claude_hermes.modules.files.infrastructure.s3_blob_store import S3BlobStore


@pytest.fixture
def cache_root(tmp_path):
    """The mirror root is `blob_root(home)` — the same `agent_home` the local
    store writes into, so the agent's Read/Glob reach a file either way."""
    root = tmp_path / "agent_home"
    root.mkdir()
    return root


@pytest.fixture
def blobs(s3, cache_root):
    return S3BlobStore(s3, cache_root)


@pytest.fixture
async def files(blobs):
    engine = make_engine(os.environ["CH_DATABASE_URL"])
    yield FileService(FileRepository(engine), blobs, 1024)
    await engine.dispose()


async def test_put_writes_the_bucket_and_the_mirror(blobs, s3, cache_root):
    stored = await blobs.put("inbox/web-1-x/a.png", b"\x89PNG")

    assert stored == f"s3://{s3.bucket}/inbox/web-1-x/a.png"
    assert await s3.get_object("inbox/web-1-x/a.png") == b"\x89PNG"
    mirror = cache_root / "inbox" / "web-1-x" / "a.png"
    assert mirror.read_bytes() == b"\x89PNG"
    assert await blobs.local_path(stored) == mirror


async def test_copy_in_uploads_the_file_and_keeps_a_local_copy(blobs, s3, tmp_path):
    src = tmp_path / "w36.md"
    src.write_text("# hi", encoding="utf-8")

    stored = await blobs.copy_in("outbox/web-1-x/w36.md", src)

    assert await s3.get_object("outbox/web-1-x/w36.md") == b"# hi"
    assert (await blobs.local_path(stored)).read_text(encoding="utf-8") == "# hi"
    assert await blobs.open(stored) == b"# hi"


async def test_a_cold_cache_downloads_the_object_back(blobs, s3, cache_root):
    """A redeploy on a fresh volume: the row and the bucket survive, the mirror
    does not. `local_path` must fetch, not 404 — that is what makes the bucket
    the source of truth."""
    stored = await blobs.put("inbox/web-1-x/b.txt", b"payload")
    mirror = cache_root / "inbox" / "web-1-x" / "b.txt"
    mirror.unlink()

    back = await blobs.local_path(stored)

    assert back == mirror and back.read_bytes() == b"payload"


async def test_a_missing_object_is_not_found(blobs, s3):
    with pytest.raises(NotFound):
        await blobs.local_path(f"s3://{s3.bucket}/inbox/web-1-x/ghost.txt")


async def test_a_forged_key_is_refused_before_anything_is_fetched(blobs, s3, tmp_path):
    """The containment check, on the S3 backend. A row that traverses out of the
    mirror root must raise instead of materializing a file next to it."""
    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")

    with pytest.raises(PermissionError):
        await blobs.local_path(f"s3://{s3.bucket}/../secret.txt")
    with pytest.raises(PermissionError):
        await blobs.local_path(str(outside))      # a pre-Wave-3a row, forged
    assert await s3.list_prefix("") == []         # and nothing was uploaded either


async def test_a_legacy_local_row_resolves_without_the_bucket(blobs, cache_root):
    """Rows written before the backend was switched hold an absolute path under
    the very directory the mirror uses, so they need no download."""
    legacy = cache_root / "outbox" / "web-1-x" / "old.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old", encoding="utf-8")

    assert await blobs.local_path(str(legacy)) == legacy
    assert await blobs.open(str(legacy)) == b"old"


async def test_the_service_round_trips_an_upload_through_the_bucket(files, s3, cache_root):
    """End to end: the row holds an `s3://` address, the bytes are in the bucket,
    and `resolve` gives the web router a real file to stream."""
    rec = await files.put_upload("web:1:x", "../../evil name.png", b"\x89PNG",
                                 "image/png")

    assert rec["path"].startswith(f"s3://{s3.bucket}/inbox/web-1-x/")
    assert rec["size"] == 4
    key = rec["path"].split(f"{s3.bucket}/", 1)[1]
    assert await s3.get_object(key) == b"\x89PNG"

    path = await files.resolve(rec)
    assert path.parent == cache_root / "inbox" / "web-1-x"
    assert ".." not in path.name and path.name.endswith("evil_name.png")
    assert await files.open(rec) == b"\x89PNG"

    [att] = await files.attachments_for("web:1:x", [rec["id"]])
    assert att.path == path and att.is_image is True

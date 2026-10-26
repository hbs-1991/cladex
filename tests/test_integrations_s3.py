"""`S3Client` against the real MinIO from `make test-stack`.

No mocks here on purpose: the things that break an S3 adapter — addressing style,
the missing-key error shape, list pagination, the us-east-1 LocationConstraint
quirk — are exactly the things a stubbed boto3 would agree with you about.
"""
from __future__ import annotations

import pytest

from claude_hermes.core.exceptions import NotFound
from claude_hermes.integrations.s3 import S3Client


async def test_put_and_get(s3: S3Client):
    await s3.put_object("inbox/tg-1/note.txt", b"hello")
    assert await s3.get_object("inbox/tg-1/note.txt") == b"hello"


async def test_get_of_a_missing_key_is_not_found(s3: S3Client):
    """A forged or stale `files` row must surface as the app's 404, not as a
    botocore ClientError nobody in the routers knows how to render."""
    with pytest.raises(NotFound):
        await s3.get_object("outbox/tg-1/nothing-here.txt")


async def test_upload_and_download_round_trip(tmp_path, s3: S3Client):
    source = tmp_path / "report.md"
    source.write_bytes(b"# report\n")
    await s3.upload_file("outbox/tg-1/report.md", source)

    target = tmp_path / "pulled" / "report.md"      # parent does not exist yet
    await s3.download_file("outbox/tg-1/report.md", target)
    assert target.read_bytes() == b"# report\n"


async def test_download_of_a_missing_key_is_not_found(tmp_path, s3: S3Client):
    with pytest.raises(NotFound):
        await s3.download_file("outbox/tg-1/ghost.md", tmp_path / "ghost.md")


async def test_list_prefix_is_scoped_to_the_lane(s3: S3Client):
    for key in ("inbox/tg-1/a.txt", "inbox/tg-1/b.txt", "inbox/tg-2/c.txt"):
        await s3.put_object(key, b"x")
    assert sorted(await s3.list_prefix("inbox/tg-1/")) == [
        "inbox/tg-1/a.txt", "inbox/tg-1/b.txt"]
    assert len(await s3.list_prefix("")) == 3


async def test_delete_is_idempotent(s3: S3Client):
    await s3.put_object("inbox/tg-1/a.txt", b"x")
    await s3.delete_object("inbox/tg-1/a.txt")
    # Deleting what is already gone must not raise: cleanup paths run twice.
    await s3.delete_object("inbox/tg-1/a.txt")
    assert await s3.list_prefix("inbox/") == []


async def test_ensure_bucket_is_idempotent(s3: S3Client):
    """Every process calls this at boot; N workers starting together all race,
    and the losers must not crash the boot."""
    await s3.ensure_bucket()
    await s3.ensure_bucket()
    await s3.put_object("k", b"v")
    assert await s3.get_object("k") == b"v"


async def test_ensure_bucket_creates_a_bucket_that_is_not_there(s3_bucket, s3_for):
    fresh = s3_for(f"{s3_bucket}-fresh")
    await fresh.ensure_bucket()
    try:
        await fresh.put_object("k", b"v")
        assert await fresh.get_object("k") == b"v"
    finally:
        await fresh.delete_object("k")
        # Drop the bucket this test made; the fixture only owns s3_bucket.
        await fresh._call("delete_bucket", Bucket=fresh.bucket)


async def test_aclose_lets_the_client_be_rebuilt(s3: S3Client):
    await s3.put_object("k", b"v")
    await s3.aclose()
    # Lazy creation means a closed client simply builds a new one on next use.
    assert await s3.get_object("k") == b"v"

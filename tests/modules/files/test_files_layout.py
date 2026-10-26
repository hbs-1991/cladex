"""One blob store, one tree per owner (Wave 4a, advisor blocker B4).

Before this wave a store froze `blob_root(home)` in `__init__`, which was right
while there was one tree. With a tree per user a frozen root means the write path
and the read path disagree: a file written into user B's `inbox/` is looked up
under the deploy owner's `agent_home`, and `resolve_within` either raises or
hands the model a path its own `Read` is denied.

What is pinned here: the root is asked for per call with the ROW's `owner_id`;
the S3 **key** grammar does not move, so a bucket written before the split still
reads; and a file written as B is read back as B, byte for byte, through the
service rather than through the function that produced the path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claude_hermes.modules.files.application.service import FileService
from claude_hermes.modules.files.infrastructure.layout import (
    blob_root,
    inbox_dir,
    outbox_dir,
    resolve_within,
)
from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore
from claude_hermes.modules.files.infrastructure.roots import as_lookup, fixed
from claude_hermes.modules.files.infrastructure.s3_blob_store import S3BlobStore

OWNER = 1
USER = 2


class Repo:
    """`FileRepository` in a dict, so the store — not the SQL — is what is tested.

    Takes `owner_id` because Phase C's column does; the row is what the read side
    re-resolves the mirror root against.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def insert(self, file_id, session_key, path, name, media_type, size,
                     created_at, *, owner_id=None):
        self.rows[file_id] = {
            "id": file_id, "session_key": session_key, "path": path, "name": name,
            "media_type": media_type, "size": size, "created_at": created_at,
            "owner_id": owner_id,
        }

    async def get(self, file_id):
        row = self.rows.get(file_id)
        return dict(row) if row else None


@pytest.fixture
def trees(tmp_path):
    """The two `agent_home`s `WorkspaceResolver.mirror_root` would answer with."""
    roots = {OWNER: tmp_path / "agent_home",
             USER: tmp_path / "users" / str(USER) / "agent_home"}
    for path in roots.values():
        path.mkdir(parents=True)
    return roots


@pytest.fixture
def lookup(trees):
    asked: list[int] = []

    async def roots(owner_id: int = 0) -> Path:
        asked.append(owner_id)
        return trees[owner_id or OWNER]

    roots.asked = asked
    return roots


def test_a_lane_directory_hangs_off_whichever_root_it_is_given():
    """`blob_root` takes a WORKSPACE root since Wave 4a. The owner's is `CH_HOME`
    itself, so their paths are byte-identical to before; a user's is their own."""
    home, user = Path("/data"), Path("/data/users/2")
    assert blob_root(home) == Path("/data/agent_home")
    assert inbox_dir(home, "web:1:x") == Path("/data/agent_home/inbox/web-1-x")
    assert inbox_dir(user, "web:2:y") == Path(
        "/data/users/2/agent_home/inbox/web-2-y")
    assert outbox_dir(user, "web:2:y") == Path(
        "/data/users/2/agent_home/outbox/web-2-y")


def test_a_path_lookup_accepts_a_fixed_root_or_a_callable(tmp_path):
    """One representation inside the stores, two spellings at the door: every
    test and the legacy `filestore` shim still hand over a `Path`."""
    assert callable(as_lookup(tmp_path))
    assert as_lookup(fixed(tmp_path)) is not None


async def test_the_root_is_asked_for_per_call_with_the_rows_owner(lookup, trees):
    store = LocalBlobStore(lookup)
    await store.put("inbox/web-2-y/a.txt", b"hi", USER)
    assert lookup.asked[-1] == USER
    assert (trees[USER] / "inbox" / "web-2-y" / "a.txt").read_bytes() == b"hi"
    await store.put("inbox/web-1-x/a.txt", b"ho", OWNER)
    assert (trees[OWNER] / "inbox" / "web-1-x" / "a.txt").read_bytes() == b"ho"


async def test_a_users_blob_never_lands_in_the_owners_tree(lookup, trees):
    store = LocalBlobStore(lookup)
    await store.put("inbox/web-2-y/only-mine.txt", b"secret", USER)
    assert not (trees[OWNER] / "inbox" / "web-2-y" / "only-mine.txt").exists()


async def test_containment_is_checked_against_the_rows_own_root(lookup, trees):
    """A forged row that names another tree is refused, not materialized: the
    check runs against the root the ROW belongs to."""
    store = LocalBlobStore(lookup)
    escape = "local:" + str(trees[OWNER] / "secret.md")
    with pytest.raises(PermissionError):
        await store.local_path(escape, USER)


async def test_a_path_root_keeps_its_pre_wave_meaning(tmp_path):
    """`LocalBlobStore(home)` still writes under `home/agent_home`, which is what
    `filestore.LocalFileStore` and every existing test pass."""
    store = LocalBlobStore(tmp_path)
    await store.put("inbox/web-1-x/a.txt", b"hi")
    assert (tmp_path / "agent_home" / "inbox" / "web-1-x" / "a.txt").is_file()


# --- the service, both directions --------------------------------------------


async def test_a_file_written_as_b_is_read_back_as_b(lookup, trees):
    """Through the SERVICE both ways, never through the function that produced
    the path: the read side re-resolves the mirror root from the row, which is
    the thing that used to be a process global."""
    files = FileService(Repo(), LocalBlobStore(lookup))
    record = await files.put_upload("web:2:y", "note.txt", b"payload",
                                    owner_id=USER)
    assert record["owner_id"] == USER
    path = await files.resolve(record)
    assert path.is_relative_to(trees[USER])
    assert path.read_bytes() == b"payload"
    assert await files.open(record) == b"payload"


async def test_the_owners_upload_still_lands_where_it_always_did(lookup, trees):
    files = FileService(Repo(), LocalBlobStore(lookup))
    record = await files.put_upload("web:1:x", "note.txt", b"x", owner_id=OWNER)
    assert (await files.resolve(record)).is_relative_to(trees[OWNER])


async def test_a_row_with_no_owner_resolves_to_the_deploy_owners_tree(lookup,
                                                                      trees):
    """Every row written before 0005 has a NULL column, and the deploy owner's
    tree is exactly where those files are."""
    files = FileService(Repo(), LocalBlobStore(lookup))
    record = await files.put_upload("web:1:x", "legacy.txt", b"old")
    assert record["owner_id"] is None
    assert (await files.resolve(record)).is_relative_to(trees[OWNER])


async def test_publish_puts_the_outbox_copy_in_the_lane_owners_tree(lookup,
                                                                    trees):
    files = FileService(Repo(), LocalBlobStore(lookup))
    produced = trees[USER] / "report.md"
    produced.write_text("hello", encoding="utf-8")
    record = await files.publish("web:2:y", produced, owner_id=USER)
    path = await files.resolve(record)
    assert path.is_relative_to(trees[USER] / "outbox")
    assert path.read_text(encoding="utf-8") == "hello"


# --- S3: the mirror moves, the key does not ----------------------------------


class FakeS3:
    bucket = "hermes"

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    async def put_object(self, key, data):
        self.objects[key] = data

    async def upload_file(self, key, src):
        self.objects[key] = Path(src).read_bytes()

    async def get_object(self, key):
        return self.objects[key]

    async def download_file(self, key, path):
        Path(path).write_bytes(self.objects[key])


async def test_the_s3_key_is_unchanged_and_only_the_mirror_moves(lookup, trees):
    """The key grammar is what a bucket written before this wave holds. If it
    moved, every existing object would become unreadable — and the migration
    would be a rewrite of `files.path`, not an upload."""
    s3 = FakeS3()
    files = FileService(Repo(), S3BlobStore(s3, lookup))
    record = await files.put_upload("web:2:y", "note.txt", b"payload",
                                    owner_id=USER)
    key = record["path"].split("/", 3)[3]
    assert key.startswith("inbox/web-2-y/") and key.endswith("note.txt")
    assert s3.objects[key] == b"payload"
    # the mirror is B's tree, under exactly that key
    assert (trees[USER] / Path(key)).read_bytes() == b"payload"
    assert not (trees[OWNER] / Path(key)).exists()


async def test_a_cold_mirror_downloads_into_the_rows_own_tree(lookup, trees):
    s3 = FakeS3()
    files = FileService(Repo(), S3BlobStore(s3, lookup))
    record = await files.put_upload("web:2:y", "note.txt", b"payload",
                                    owner_id=USER)
    (await files.resolve(record)).unlink()          # a fresh volume, cold cache
    path = await files.resolve(record)
    assert path.read_bytes() == b"payload"
    assert path.is_relative_to(trees[USER])


def test_resolve_within_is_still_the_one_containment_body(tmp_path):
    """Shared by both stores on purpose: a second copy is a second place to
    weaken it."""
    root = tmp_path / "agent_home"
    root.mkdir()
    assert resolve_within(root, "inbox/a.txt") == root / "inbox" / "a.txt"
    with pytest.raises(PermissionError):
        resolve_within(root, "../../etc/passwd")

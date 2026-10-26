"""Uploads and downloads: size limit, lane visibility, inline vs attachment."""
from claude_hermes.modules.files.application.service import FileService
from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore
from claude_hermes.modules.files.infrastructure.repository import FileRepository
from claude_hermes.modules.sessions.infrastructure.repositories import ThreadRepository

from conftest import ensure_users
from tests.web_helpers import ORIGIN, json_headers, login, make_client

NOW = "2026-07-12T10:00:00+00:00"


async def test_upload_then_download(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    r = await client.post("/api/uploads", data={"thread_id": tid},
                          files={"file": ("notes.md", b"# n", "text/markdown")},
                          headers={"Origin": ORIGIN})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "notes.md" and body["size"] == 3 and body["media_type"] == "text/markdown"
    r = await client.get(body["url"])
    assert r.status_code == 200 and r.content == b"# n"
    assert r.headers["content-disposition"].startswith("inline")


async def test_upload_size_limit(tmp_path, engine):
    """The ceiling is the `FileService`'s own (the composition root builds it from
    `web_upload_max_mb`); the route only translates its refusal into a 413."""
    client, deps = await make_client(tmp_path, engine=engine)
    deps.file_store = FileService(FileRepository(engine), LocalBlobStore(tmp_path),
                                  1024 * 1024)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    r = await client.post("/api/uploads", data={"thread_id": tid},
                          files={"file": ("big.bin", b"x" * (1024 * 1024 + 1))},
                          headers={"Origin": ORIGIN})
    assert r.status_code == 413


async def test_files_of_foreign_lanes_are_404(tmp_path, engine):
    client, deps = await make_client(tmp_path, engine=engine)
    login(client, 1)
    # `threads.owner_user_id` references `users(id)` since 0005.
    await ensure_users(engine, 2)
    await ThreadRepository(engine).upsert("web:2:zzz", "web", 2, NOW)
    rec = await deps.file_store.put_upload("web:2:zzz", "x.txt", b"x", "text/plain")
    assert (await client.get(f"/api/files/{rec['id']}")).status_code == 404
    r = await client.post("/api/uploads", data={"thread_id": "web:2:zzz"},
                          files={"file": ("a.txt", b"a")}, headers={"Origin": ORIGIN})
    assert r.status_code == 404


async def test_binary_files_are_attachments(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    rec = await deps.file_store.put_upload(tid, "a.zip", b"PK", "application/zip")
    r = await client.get(f"/api/files/{rec['id']}")
    assert r.headers["content-disposition"].startswith("attachment")

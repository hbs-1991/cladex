"""The files router's seams: the service and the lane guard.

Each is a named provider, and `app.dependency_overrides[get_files]` replaces one for
a single app without touching the bundle, the disk or the other nine routers. The
upload ceiling is the service's own (`test_files_service.py`); the router passes
none.
"""
import httpx

from claude_hermes.http.app import build_web_app
from claude_hermes.modules.files.presentation.dependencies import get_files
from claude_hermes.modules.sessions.infrastructure.repositories import ThreadRepository

from conftest import ensure_users
from tests.web_helpers import ORIGIN, json_headers, login, make_client

RECORD = {"id": "f-1", "session_key": "web:1:aaa", "name": "fake.txt",
          "media_type": "text/plain", "size": 3,
          "path": "/nowhere/fake.txt", "created_at": "2026-09-15T00:00:00+00:00"}


class FakeFiles:
    """A `FileService` by shape only — no blob store, no rows, no disk."""

    def __init__(self):
        self.uploads = []

    async def put_upload(self, session_key, filename, data, media_type=None, *,
                         max_bytes=None, owner_id=None):
        # `owner_id` is the lane's owner, read off the thread row by the route
        # (Wave 4a). Recorded separately so the tuple the assertions below unpack
        # keeps its shape.
        self.owners = getattr(self, "owners", [])
        self.owners.append(owner_id)
        self.uploads.append((session_key, filename, data, media_type, max_bytes))
        return dict(RECORD, name=filename, size=len(data))

    async def get(self, file_id):
        return None

    @staticmethod
    def to_wire(record):
        return {"id": record["id"], "name": record["name"], "size": record["size"],
                "media_type": record["media_type"],
                "url": f"/api/files/{record['id']}"}


async def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def test_the_file_service_is_swappable_without_rebuilding_the_bundle(tmp_path):
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    fake = FakeFiles()
    app.dependency_overrides[get_files] = lambda: fake
    client = await client_for(app)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]

    r = await client.post("/api/uploads", data={"thread_id": tid},
                          files={"file": ("notes.md", b"# n", "text/markdown")},
                          headers={"Origin": ORIGIN})
    assert r.status_code == 201
    assert r.json() == {"id": "f-1", "name": "notes.md", "size": 3,
                        "media_type": "text/plain", "url": "/api/files/f-1"}
    # The lane guard still ran: the service was handed the real thread's key.
    assert fake.uploads[0][0] == tid and fake.uploads[0][3] == "text/markdown"
    # And the real download path still 404s, because `get` answered None.
    assert (await client.get("/api/files/f-1")).status_code == 404


async def test_the_router_passes_no_ceiling_of_its_own(tmp_path):
    """The ceiling is the service's (`web_upload_max_mb`, bound by the composition
    root): the route hands the service the bytes and no `max_bytes`, so there is one
    number in one place."""
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    fake = FakeFiles()
    app.dependency_overrides[get_files] = lambda: fake
    client = await client_for(app)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]

    r = await client.post("/api/uploads", data={"thread_id": tid},
                          files={"file": ("a.txt", b"abc")}, headers={"Origin": ORIGIN})
    assert r.status_code == 201 and fake.uploads[0][4] is None


async def test_a_missing_file_part_is_bad_request_not_a_schema_error(tmp_path):
    """`File(None)` plus the handler's own check: the old 400 survives the model."""
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    client = await client_for(app)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]

    r = await client.post("/api/uploads", data={"thread_id": tid},
                          files={"nothing": ("a.txt", b"a")}, headers={"Origin": ORIGIN})
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "bad_request", "message": "file is required"}}


async def test_the_lane_guard_runs_before_the_file_check(tmp_path, engine):
    """A foreign lane is a 404 whether or not the form carried a file — the order the
    hand-written handler had, kept."""
    _c, deps = await make_client(tmp_path, engine=engine)
    app = build_web_app(deps)
    client = await client_for(app)
    login(client, 1)
    # `threads.owner_user_id` references `users(id)` since 0005.
    await ensure_users(engine, 2)
    await ThreadRepository(engine).upsert("web:2:zzz", "web", 2,
                                          "2026-09-15T00:00:00+00:00")

    r = await client.post("/api/uploads", data={"thread_id": "web:2:zzz"},
                          files={"nothing": ("a.txt", b"a")}, headers={"Origin": ORIGIN})
    assert r.status_code == 404

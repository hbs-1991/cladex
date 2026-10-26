"""The `/api/content*` routes: the wire contract, the error codes, the owner gate,
isolation between owners, and the media path guard.

The router is mounted onto the real app shell (`build_web_app`) with a real
`ContentService` over the test database; only the publisher routine is a fake
(`FakePublisher` from `test_content_service`).
"""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from claude_hermes.http.app import build_web_app
from claude_hermes.modules.content.presentation.router import router as content_router

from tests.test_content_service import (
    JOB,
    NOW,
    OTHER,
    OWNER,
    FakePublisher,
    force,
    make_service,
    write_media,
)
from tests.web_helpers import ORIGIN, json_headers, login, make_client

WIRE_KEYS = {"id", "channel", "format", "title", "body", "media", "publish_at", "status",
             "group_key", "source_path", "thread_id", "created_via", "approved_at",
             "attempts", "published_url", "error", "needs_attention", "created_at",
             "updated_at", "theme_approved_at", "draft_attempts", "redrafts",
             "draft_comment", "card_state"}


def _mount(app) -> None:
    """Put the content routes in front of the `/api/*` catch-all. Once the shell
    mounts this router itself (`deps.content is not None`) its copy answers first —
    the same router object, so the same routes — and this one is never reached."""
    routes = app.router.routes
    before = len(routes)
    app.include_router(content_router)
    # However many entries this FastAPI adds for an included router (one wrapper
    # in recent versions), they move as a block.
    added = routes[before:]
    del routes[before:]
    at = next(i for i, r in enumerate(routes) if getattr(r, "path", "") == "/api/{rest:path}")
    routes[at:at] = added


@pytest.fixture
async def app_env(engine, tmp_path):
    _c, deps = await make_client(tmp_path, owner=OWNER, allowed=[OTHER], engine=engine)
    publisher = FakePublisher()
    service = make_service(engine, tmp_path, publisher)
    deps.content = service
    app = build_web_app(deps)
    _mount(app)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    login(client, OWNER)
    try:
        yield client, service, publisher, tmp_path
    finally:
        await client.aclose()


def _code(r) -> str:
    return r.json()["error"]["code"]


async def test_create_get_and_the_wire_shape(app_env):
    client, _svc, _pub, home = app_env
    rel = write_media(home)
    r = await client.post("/api/content", headers=json_headers(), json={
        "channel": "instagram", "format": "reel", "title": "Hi", "body": "Body",
        "media": [rel], "publish_at": "2026-10-01T05:00:00+00:00", "nonsense": 1})
    assert r.status_code == 201, r.text
    item = r.json()
    assert set(item) == WIRE_KEYS
    assert item["media"] == [{"path": rel, "sha256": None}]
    assert item["status"] == "draft" and item["created_via"] == "web"
    assert item["needs_attention"] is False
    got = await client.get(f"/api/content/{item['id']}")
    assert got.status_code == 200 and got.json() == item


@pytest.mark.parametrize(("body", "code"), [
    ({"channel": "myspace"}, "bad_channel"),
    ({"channel": "x", "media": [".claude/settings.png"]}, "bad_media"),
    ({"channel": "x", "publish_at": "next tuesday"}, "bad_time"),
    ({"channel": "x", "status": "scheduled"}, "bad_status"),
])
async def test_create_refusals_carry_the_fields_code(app_env, body, code):
    client, *_ = app_env
    r = await client.post("/api/content", headers=json_headers(), json=body)
    assert r.status_code == 400 and _code(r) == code
    assert r.json()["error"]["message"]


async def test_range_read_and_backlog(app_env):
    client, svc, *_ = app_env
    dated = await svc.create(OWNER, {"channel": "x", "publish_at": NOW.isoformat()})
    undated = await svc.create(OWNER, {"channel": "x"})
    r = await client.get("/api/content", params={
        "from": NOW.isoformat(), "to": (NOW + timedelta(days=7)).isoformat()})
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == [dated["id"]]
    assert [i["id"] for i in r.json()["backlog"]] == [undated["id"]]
    r = await client.get("/api/content", params={
        "from": NOW.isoformat(), "to": (NOW + timedelta(days=63)).isoformat()})
    assert r.status_code == 400 and _code(r) == "bad_range"
    assert _code(await client.get("/api/content")) == "bad_range"


async def test_patch_approve_unapprove_retry_delete(app_env):
    client, svc, _pub, home = app_env
    item = await svc.create(OWNER, {"channel": "x", "body": "text",
                                    "media": [write_media(home)]})
    url = f"/api/content/{item['id']}"
    r = await client.post(url + "/approve", headers=json_headers())
    assert r.status_code == 400 and _code(r) == "no_publish_at"
    r = await client.patch(url, headers=json_headers(),
                           json={"publish_at": NOW.isoformat()})
    assert r.status_code == 200 and r.json()["publish_at"] == NOW.isoformat()
    r = await client.post(url + "/approve", headers=json_headers())
    assert r.status_code == 200 and r.json()["status"] == "scheduled"
    assert r.json()["media"][0]["sha256"]
    # Editing a scheduled item keeps it scheduled.
    r = await client.patch(url, headers=json_headers(), json={"body": "edited"})
    assert r.json()["status"] == "scheduled"
    r = await client.post(url + "/unapprove", headers=json_headers())
    assert r.json()["status"] == "review" and r.json()["approved_at"] is None
    r = await client.post(url + "/unapprove", headers=json_headers())
    assert r.status_code == 400 and _code(r) == "not_approved"
    r = await client.post(url + "/retry", headers=json_headers(), json={"mode": "repost"})
    assert r.status_code == 400 and _code(r) == "not_retryable"
    await force(svc, item["id"], status="failed", error="boom")
    assert (await client.get(url)).json()["needs_attention"] is True
    r = await client.post(url + "/retry", headers=json_headers(),
                          json={"mode": "published", "url": "https://x.com/p/1"})
    assert r.json()["status"] == "published"
    assert r.json()["published_url"] == "https://x.com/p/1"
    r = await client.patch(url, headers=json_headers(), json={"title": "late"})
    assert r.status_code == 409 and _code(r) == "locked"
    r = await client.delete(url, headers=json_headers())
    assert r.status_code == 409 and _code(r) == "published_item"
    draft = await svc.create(OWNER, {"channel": "x"})
    r = await client.delete(f"/api/content/{draft['id']}", headers=json_headers())
    assert r.status_code == 204
    assert (await client.get(f"/api/content/{draft['id']}")).status_code == 404


async def test_media_missing_names_the_file(app_env):
    client, svc, *_ = app_env
    item = await svc.create(OWNER, {"channel": "x", "publish_at": NOW.isoformat(),
                                    "body": "text", "media": ["content/nothere.mp4"]})
    r = await client.post(f"/api/content/{item['id']}/approve", headers=json_headers())
    assert r.status_code == 400 and _code(r) == "media_missing"
    assert "content/nothere.mp4" in r.json()["error"]["message"]


async def test_a_non_owner_is_refused_everywhere(app_env):
    client, svc, *_ = app_env
    item = await svc.create(OWNER, {"channel": "x"})
    login(client, OTHER)
    calls = [
        client.get("/api/content", params={"from": NOW.isoformat(),
                                           "to": (NOW + timedelta(days=1)).isoformat()}),
        client.get(f"/api/content/{item['id']}"),
        client.post("/api/content", headers=json_headers(), json={"channel": "x"}),
        client.patch(f"/api/content/{item['id']}", headers=json_headers(), json={}),
        client.delete(f"/api/content/{item['id']}", headers=json_headers()),
        client.post(f"/api/content/{item['id']}/approve", headers=json_headers()),
        client.get(f"/api/content/{item['id']}/media/0"),
        client.get("/api/content/publisher"),
        client.put("/api/content/publisher", headers=json_headers(), json={"job_id": 1}),
    ]
    for call in calls:
        r = await call
        assert r.status_code == 403 and _code(r) == "forbidden", r.request.url


async def test_a_foreign_or_unknown_id_is_404(app_env):
    client, svc, *_ = app_env
    theirs = await svc.create(OTHER, {"channel": "x"})
    for path in (f"/api/content/{theirs['id']}", "/api/content/999999"):
        assert (await client.get(path)).status_code == 404
        assert (await client.patch(path, headers=json_headers(),
                                   json={"title": "x"})).status_code == 404
        assert (await client.post(path + "/approve",
                                  headers=json_headers())).status_code == 404
        assert (await client.delete(path, headers=json_headers())).status_code == 404
    assert (await svc.require(theirs["id"], OTHER))["title"] == ""


async def test_media_is_served_and_path_guarded(app_env):
    client, svc, _pub, home = app_env
    rel = write_media(home, data=b"picture bytes")
    item = await svc.create(OWNER, {"channel": "x", "media": [rel, "content/gone.png"]})
    r = await client.get(f"/api/content/{item['id']}/media/0")
    assert r.status_code == 200 and r.content == b"picture bytes"
    assert (await client.get(f"/api/content/{item['id']}/media/1")).status_code == 404
    assert (await client.get(f"/api/content/{item['id']}/media/2")).status_code == 404
    # A row forged to point outside agent_home, or into a dot-directory, serves
    # nothing — the path is re-guarded on every request.
    secret = home / "secrets.png"
    secret.write_bytes(b"not yours")
    dotted = home / "agent_home" / ".claude" / "x.png"
    dotted.parent.mkdir(parents=True, exist_ok=True)
    dotted.write_bytes(b"nope")
    await force(svc, item["id"], media=[{"path": "../secrets.png", "sha256": None},
                                        {"path": ".claude/x.png", "sha256": None}])
    for n in (0, 1):
        assert (await client.get(f"/api/content/{item['id']}/media/{n}")).status_code == 404


async def test_the_publisher_route(app_env):
    client, _svc, publisher, _home = app_env
    r = await client.get("/api/content/publisher")
    assert r.json() == {"job_id": None, "name": None, "state": None}
    publisher.add(job_id=3, kind="cron")
    r = await client.put("/api/content/publisher", headers=json_headers(), json={"job_id": 3})
    assert r.status_code == 400 and _code(r) == "bad_publisher"
    publisher.add()
    r = await client.put("/api/content/publisher", headers=json_headers(),
                         json={"job_id": JOB})
    assert r.status_code == 200
    assert r.json() == {"job_id": JOB, "name": "Content publisher", "state": "active"}
    assert (await client.get("/api/content/publisher")).json()["job_id"] == JOB


async def test_the_drafter_route(app_env):
    client, svc, publisher, _home = app_env
    r = await client.get("/api/content/drafter")
    assert r.json() == {"job_id": None, "name": None, "state": None}
    publisher.add(job_id=3, kind="cron")
    r = await client.put("/api/content/drafter", headers=json_headers(), json={"job_id": 3})
    assert r.status_code == 400 and _code(r) == "bad_drafter"
    publisher.add()
    await svc.set_publisher(OWNER, JOB)
    r = await client.put("/api/content/drafter", headers=json_headers(),
                         json={"job_id": JOB})
    assert r.status_code == 400 and _code(r) == "bad_drafter"      # the publisher
    publisher.add(job_id=8, name="Content drafter")
    r = await client.put("/api/content/drafter", headers=json_headers(), json={"job_id": 8})
    assert r.status_code == 200
    assert r.json() == {"job_id": 8, "name": "Content drafter", "state": "active"}
    assert (await client.get("/api/content/drafter")).json()["job_id"] == 8


async def test_theme_routes(app_env):
    client, svc, *_ = app_env
    future = datetime.now(timezone.utc) + timedelta(days=2)
    item = await svc.create(OWNER, {"channel": "x", "status": "idea",
                                    "publish_at": future.isoformat()})
    url = f"/api/content/{item['id']}"
    r = await client.post(url + "/approve-theme", headers=json_headers())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "planned" and r.json()["theme_approved_at"]
    assert r.json()["card_state"] == "none" and r.json()["needs_attention"] is False
    r = await client.post(url + "/approve-theme", headers=json_headers())
    assert r.status_code == 400 and _code(r) == "not_an_idea"
    r = await client.post(url + "/retry-drafting", headers=json_headers())
    assert r.status_code == 400 and _code(r) == "not_retryable"
    await force(svc, item["id"], error="No drafter routine is set")
    assert (await client.get(url)).json()["needs_attention"] is True
    r = await client.post(url + "/retry-drafting", headers=json_headers())
    assert r.status_code == 200 and r.json()["error"] is None
    assert r.json()["status"] == "planned"
    r = await client.post(url + "/unapprove-theme", headers=json_headers())
    assert r.json()["status"] == "idea" and r.json()["theme_approved_at"] is None
    r = await client.post(url + "/unapprove-theme", headers=json_headers())
    assert r.status_code == 400 and _code(r) == "not_planned"
    backlog = await svc.create(OWNER, {"channel": "x", "status": "idea"})
    r = await client.post(f"/api/content/{backlog['id']}/approve-theme",
                          headers=json_headers())
    assert r.status_code == 400 and _code(r) == "no_publish_at"


async def test_approving_a_weeks_themes(app_env):
    client, svc, *_ = app_env
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    inside = await svc.create(OWNER, {"channel": "x", "status": "idea",
                                      "publish_at": (start + timedelta(days=1)).isoformat()})
    await svc.create(OWNER, {"channel": "x", "status": "idea",
                             "publish_at": (start + timedelta(days=9)).isoformat()})
    body = {"from": start.isoformat(), "to": (start + timedelta(days=7)).isoformat()}
    r = await client.post("/api/content/approve-themes", headers=json_headers(), json=body)
    assert r.status_code == 200, r.text
    assert r.json()["approved"] == 1
    assert [i["id"] for i in r.json()["items"]] == [inside["id"]]
    assert r.json()["items"][0]["status"] == "planned"
    r = await client.post("/api/content/approve-themes", headers=json_headers(), json={
        "from": start.isoformat(), "to": (start + timedelta(days=63)).isoformat()})
    assert r.status_code == 400 and _code(r) == "bad_range"
    r = await client.post("/api/content/approve-themes", headers=json_headers(), json={})
    assert r.status_code == 400 and _code(r) == "bad_range"


async def test_approve_refuses_an_empty_post_and_missing_format_media(app_env):
    client, svc, _pub, home = app_env
    item = await svc.create(OWNER, {"channel": "instagram", "format": "carousel",
                                    "publish_at": NOW.isoformat(),
                                    "media": [write_media(home)]})
    url = f"/api/content/{item['id']}/approve"
    r = await client.post(url, headers=json_headers())
    assert r.status_code == 400 and _code(r) == "empty_body"
    await svc.edit(OWNER, item["id"], {"body": "Slides"})
    r = await client.post(url, headers=json_headers())
    assert r.status_code == 400 and _code(r) == "media_required"
    assert "2-10 images" in r.json()["error"]["message"]


async def test_the_new_routes_are_the_owners(app_env):
    client, svc, *_ = app_env
    item = await svc.create(OWNER, {"channel": "x", "status": "idea"})
    login(client, OTHER)
    calls = [
        client.post(f"/api/content/{item['id']}/approve-theme", headers=json_headers()),
        client.post(f"/api/content/{item['id']}/unapprove-theme", headers=json_headers()),
        client.post(f"/api/content/{item['id']}/retry-drafting", headers=json_headers()),
        client.post("/api/content/approve-themes", headers=json_headers(), json={}),
        client.get("/api/content/drafter"),
        client.put("/api/content/drafter", headers=json_headers(), json={"job_id": 1}),
    ]
    for call in calls:
        r = await call
        assert r.status_code == 403 and _code(r) == "forbidden", r.request.url
    login(client, OWNER)
    for path in ("approve-theme", "unapprove-theme", "retry-drafting"):
        r = await client.post(f"/api/content/999999/{path}", headers=json_headers())
        assert r.status_code == 404, path

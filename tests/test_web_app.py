"""The app shell (`http.app.build_web_app`): the cookie gate and the transport
policy as middleware, the SPA fallback, `/healthz`, the one API error shape, the
400-not-422 translation and the OpenAPI document.

The login and claim routes themselves are identity's, and their tests live in
`tests/modules/identity/test_identity_router.py`; what stays here is what only the
assembled app can show — the middleware in front of a real router, over a real ACL.
"""
from tests.web_helpers import ORIGIN, login, make_client


async def test_me_requires_a_cookie(tmp_path):
    client, deps = await make_client(tmp_path)
    r = await client.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "login required"}}
    login(client, 1)
    r = await client.get("/api/me")
    assert r.status_code == 200
    # Wave 4a adds the two display fields the `users` row carries; "" is the
    # documented answer for a row nobody has told us a name for.
    # The model switcher adds the personal default, never conditionally absent.
    assert r.json() == {"user_id": 1, "role": "owner", "display_name": "",
                        "username": "", "default_model": None, "default_effort": None}


async def test_mutations_need_origin_and_json_content_type(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    r = await client.post("/api/auth/logout", headers={"Content-Type": "application/json"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "bad_origin"
    r = await client.post("/api/auth/logout", headers={"Origin": "https://evil.test",
                                                       "Content-Type": "application/json"})
    assert r.status_code == 403
    r = await client.post("/api/auth/logout", headers={"Origin": ORIGIN,
                                                       "Content-Type": "text/plain"})
    assert r.status_code == 415


async def test_spa_fallback_serves_index_for_unknown_paths(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>CladeX</title>", encoding="utf-8")
    (dist / "app.js").write_text("console.log(1)", encoding="utf-8")
    client, deps = await make_client(tmp_path, dist=dist)
    js = await client.get("/app.js")
    assert js.text == "console.log(1)" and "cache-control" not in js.headers
    r = await client.get("/routines/3")
    assert r.status_code == 200 and "CladeX" in r.text
    # index.html names the hashed bundles, so a deploy must not be hidden behind
    # the browser's heuristic cache; the fallback and the root both say so.
    assert r.headers["cache-control"] == "no-cache"
    assert (await client.get("/")).headers["cache-control"] == "no-cache"
    login(client, 1)
    r = await client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_bodies_are_compressed_and_the_event_stream_is_not(tmp_path):
    """Nothing in front of this process compresses — the Caddyfile has no `encode`
    and a tunnel or a bare port has no proxy at all — so the shell does.

    Both halves matter. The SPA entry chunk and a thread's `GET …/events` history
    are what a person waits on, and both are text that compresses several-fold. The
    SSE tail must stay raw: a gzip stream holds each frame until its window fills,
    so a compressed `text/event-stream` would deliver a whole turn in one lump at
    the end instead of token by token. Starlette excludes that content type itself;
    this test is what says the app depends on it.
    """
    import httpx
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    from claude_hermes.http.app import build_web_app

    body = "the recorded history of a long thread " * 400

    async def history(request):
        return JSONResponse({"events": body})

    async def tail(request):
        async def frames():
            for _ in range(20):
                yield f"event: turn\ndata: {body}\n\n".encode()
        return StreamingResponse(frames(), media_type="text/event-stream")

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>CladeX</title>",
                                     encoding="utf-8")
    (dist / "app.js").write_text(f"/* {body} */", encoding="utf-8")
    _client, deps = await make_client(tmp_path, dist=dist)
    app = build_web_app(deps, extra_routes=[Route("/api/history", history),
                                            Route("/api/tail", tail)])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    login(client, 1)

    # httpx decodes the body and drops the Content-Length it was sent with, so the
    # encoding header and a body that survives the round trip are what can be
    # asserted here; the ratio itself is gzip's business, not this app's.
    r = await client.get("/api/history")
    assert r.headers["content-encoding"] == "gzip"
    assert r.json()["events"] == body

    js = await client.get("/app.js")             # the bundle, through the SPA mount
    assert js.headers["content-encoding"] == "gzip"
    assert js.text == f"/* {body} */"

    s = await client.get("/api/tail")
    assert "content-encoding" not in s.headers
    assert s.text.count("event: turn") == 20


async def test_healthz_is_public_and_shallow(tmp_path):
    """The probe a compose healthcheck and a proxy call. No cookie, no Origin, no
    database — a liveness check that fails when Postgres is busy turns a hiccup
    into a restart loop, and the deep question already has the asyncpg probe in
    `docker-compose.prod.yml`."""
    client, _deps = await make_client(tmp_path)
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    # Outside `/api/`, so neither middleware has anything to say about it: no
    # cookie was ever set on this client, and the answer is not the 401 that every
    # `/api/*` path gives an anonymous caller.
    assert (await client.get("/api/me")).status_code == 401


async def test_healthz_is_not_swallowed_by_the_spa_mount(tmp_path):
    """The SPA mount answers index.html for anything it does not recognise, so a
    probe registered after it would get 200 and an HTML body — and report a
    healthy process forever, including one whose routes are all gone."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>CladeX</title>",
                                     encoding="utf-8")
    client, _deps = await make_client(tmp_path, dist=dist)
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert "CladeX" not in r.text


async def test_application_exceptions_render_as_the_api_error_shape(tmp_path):
    """A service raising a core exception must reach the browser as the one API
    error shape — routers no longer have to translate NotFound into an ApiError."""
    import httpx
    from starlette.routing import Route

    from claude_hermes.core.exceptions import NotFound
    from claude_hermes.http.app import build_web_app

    async def boom(request):
        raise NotFound("no such thread")

    _client, deps = await make_client(tmp_path)
    app = build_web_app(deps, extra_routes=[Route("/api/boom", boom)])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    login(client, 1)
    r = await client.get("/api/boom")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "no such thread"}}


async def test_a_rejected_request_body_is_a_400_in_the_api_error_shape(tmp_path):
    """FastAPI answers 422 for a value its schema refuses; this API answers 400.

    Every hand-written handler that parsed a body by hand replied `400 bad_request`,
    and `client.ts` branches on the code, so the shell translates. The probe route
    below is the smallest thing that can fail validation at all — a typed query
    parameter — because no converted router has a body yet.
    """
    import httpx
    from fastapi.routing import APIRoute

    from claude_hermes.http.app import build_web_app

    async def probe(n: int) -> dict:
        return {"n": n}

    _client, deps = await make_client(tmp_path)
    app = build_web_app(deps, extra_routes=[APIRoute("/api/probe", probe, methods=["GET"])])
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    login(client, 1)
    assert (await client.get("/api/probe", params={"n": 3})).json() == {"n": 3}
    r = await client.get("/api/probe", params={"n": "twelve"})
    assert r.status_code == 400
    body = r.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "bad_request"
    # "<loc>: <msg>" — the field that was refused, then pydantic's reason. The
    # location is pinned; the wording after it belongs to pydantic and is not.
    assert body["error"]["message"].startswith("query.n: ")
    # And the payload never comes back out: a rejected body may be a login hash.
    assert "twelve" not in r.text


# Every `/api/*` path this deployment answers, by the module that owns it. The
# document is generated, but this list is not: it is the wire contract `web/src/api/
# client.ts` is written against, so a path appearing or disappearing here is a
# frontend change and has to be a deliberate edit rather than a regenerated fixture.
# Path converters are gone in the document (`{id:path}` and `{id:int}` are both
# `{id}`); the converter still lives on the route, where matching happens.
FROZEN_PATHS = {
    "identity": ["/api/auth/telegram", "/api/auth/webapp", "/api/auth/session",
                 "/api/auth/logout", "/api/me", "/api/me/model-default", "/api/config",
                 "/api/users", "/api/users/{user_id}", "/api/users/{user_id}/disable",
                 "/api/users/{user_id}/enable"],
    "sessions": ["/api/threads", "/api/threads/{id}", "/api/threads/{id}/archive",
                 "/api/threads/{id}/unarchive", "/api/threads/{id}/model"],
    "turns": ["/api/threads/{id}/messages", "/api/threads/{id}/cancel",
              "/api/threads/{id}/events", "/api/threads/{id}/stream",
              "/api/asks/{qid}", "/api/commands", "/api/voice/transcribe",
              "/api/voice/speak", "/api/models"],
    "files": ["/api/uploads", "/api/files/{id}"],
    "scheduler": ["/api/routines", "/api/routines/{id}", "/api/routines/{id}/pause",
                  "/api/routines/{id}/resume", "/api/routines/{id}/run",
                  "/api/routines/{id}/runs", "/api/routines/calendar"],
    "learning": ["/api/skills", "/api/skills/{name}", "/api/skills/{name}/pin",
                 "/api/skills/{name}/unpin", "/api/skills/{name}/archive",
                 "/api/skills/{name}/restore"],
    "memory": ["/api/wiki/graph", "/api/wiki/pages/{path}"],
    "extensions": ["/api/connectors", "/api/connectors/oauth/callback",
                   "/api/connectors/{name}", "/api/connectors/{name}/secrets",
                   "/api/connectors/{name}/connect",
                   "/api/connectors/{name}/oauth/start", "/api/connectors/{name}/test",
                   "/api/connectors/{name}/enable", "/api/connectors/{name}/disable",
                   "/api/plugins", "/api/plugins/{name}", "/api/plugins/{name}/install",
                   "/api/plugins/{name}/install/confirm", "/api/plugins/{name}/enable",
                   "/api/plugins/{name}/disable",
                   "/api/drafts", "/api/drafts/{name}", "/api/drafts/{name}/review",
                   "/api/drafts/{name}/approve",
                   "/api/secrets", "/api/secrets/{name}"],
}
# Mounted only when `content_enabled` (`deps.content is not None`), so the default
# bundle above does not describe it; `test_the_content_router_is_mounted_with_the_plan`
# holds it to the same rules.
CONTENT_PATHS = ["/api/content", "/api/content/publisher", "/api/content/{id}",
                 "/api/content/{id}/approve", "/api/content/{id}/unapprove",
                 "/api/content/{id}/retry", "/api/content/{id}/media/{n}",
                 # v2: theme approval, the drafter, retry drafting
                 "/api/content/drafter", "/api/content/approve-themes",
                 "/api/content/{id}/approve-theme", "/api/content/{id}/unapprove-theme",
                 "/api/content/{id}/retry-drafting"]


async def test_the_openapi_document_describes_every_router(tmp_path):
    """The schema is served under the cookie gate, and names every `/api/*` route.

    One document, nine modules: each router carries `tags=[<module>]` and every
    operation a stable `operation_id` (`<module>_<verb>_<noun>`), because a generated
    client names its methods after it. The document is also the check that the shell
    still mounts everything — a router dropped from `build_web_app` disappears here
    before anyone notices the 404.
    """
    client, _deps = await make_client(tmp_path)
    login(client, 1)
    r = await client.get("/api/openapi.json")
    assert r.status_code == 200
    doc = r.json()
    expected = {p for paths in FROZEN_PATHS.values() for p in paths}
    assert {p for p in doc["paths"] if p.startswith("/api/")} == expected
    # Every operation is tagged by the module that owns it and named after it.
    seen: dict[str, str] = {}
    for module, paths in FROZEN_PATHS.items():
        for path in paths:
            for method, spec in doc["paths"][path].items():
                assert spec["tags"] == [module], (path, method, spec["tags"])
                op = spec["operationId"]
                assert op.startswith(module + "_"), (path, method, op)
                assert op not in seen, (op, seen.get(op), path)
                seen[op] = path
    assert len(seen) == 75                      # 63 paths, 75 operations
    assert doc["paths"]["/api/wiki/graph"]["get"]["operationId"] == "memory_get_graph"
    assert (await client.get("/api/docs")).status_code == 200
    # The catch-all and the SPA mount are plain Starlette routes and describe nothing;
    # the webhook is not mounted at all outside webhook mode.
    assert "/api/{rest}" not in doc["paths"]
    assert "/telegram/webhook/{secret}" not in doc["paths"]


async def test_the_content_router_is_mounted_with_the_plan(tmp_path):
    """`content_enabled` is the shell's one mounting condition for `/api/content*`:
    with a service on the bundle the router is there, tagged and named like every
    other module's; without one (every test above) the paths fall to the catch-all,
    whose 404 the SPA reads as "content disabled"."""
    import httpx

    from claude_hermes.http.app import build_web_app

    client, deps = await make_client(tmp_path)
    login(client, 1)
    assert (await client.get("/api/content/publisher")).status_code == 404
    deps.content = object()          # the document never calls a provider
    app = build_web_app(deps)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    login(client, 1)
    doc = (await client.get("/api/openapi.json")).json()
    assert {p for p in doc["paths"] if p.startswith("/api/content")} == set(CONTENT_PATHS)
    ops = [spec for path in CONTENT_PATHS for spec in doc["paths"][path].values()]
    assert len(ops) == 17
    for spec in ops:
        assert spec["tags"] == ["content"] and spec["operationId"].startswith("content_")


async def test_the_openapi_pages_need_a_login_and_nothing_more(tmp_path):
    """Neither page is in `PUBLIC_PATHS` — the schema lists every route this deploy
    has, and that is a map of the owner's house. A logged-in user may read it though:
    the routes it describes already answer to them, so hiding it would only mean the
    SPA's own users cannot see what they are allowed to call."""
    client, _deps = await make_client(tmp_path, owner=1, allowed=[2])
    for path in ("/api/openapi.json", "/api/docs"):
        r = await client.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    login(client, 2)                              # allowed, not the owner
    for path in ("/api/openapi.json", "/api/docs"):
        assert (await client.get(path)).status_code == 200

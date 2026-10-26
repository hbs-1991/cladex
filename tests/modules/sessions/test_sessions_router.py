"""The threads router's seams: the services it asks for, and the tolerances it keeps.

`routes(deps)` built its handlers around a `ThreadService` captured in a closure, so a
test that wanted another one had to build another app out of another bundle. The
collaborators are named providers now: the router below is mounted on a **bare
FastAPI app** whose `app.state.deps` is a `SimpleNamespace`, so every assertion here
is about this router and nothing else — no middleware, no database, no SPA, no other
module's routes. The end-to-end versions live in `tests/test_web_threads.py`.

The second half of the file is about leniency. Converting query parameters and a
request body to a schema is exactly where a wire contract quietly gets stricter — a
`bool` that refuses `?archived=maybe`, a `Query(le=200)` that refuses `?limit=500`, a
`title: str` that answers pydantic's 400 instead of the router's. None of those
refusals existed, and none of them exist now; `PUT …/model` is held to the same rule.
"""
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.exceptions import ApplicationException, NotFound
from claude_hermes.core.model_catalog import Choice
from claude_hermes.modules.sessions.presentation.router import router

NOW = "2026-07-12T10:00:00+00:00"
ORIGIN = "https://cladex.test"


def row(key: str, title=None, archived_at=None, owner=1, model=None, effort=None) -> dict:
    return {"session_key": key, "title": title, "channel": "web",
            "archived_at": archived_at, "last_event_at": NOW, "created_at": NOW,
            "owner_user_id": owner, "model": model, "effort": effort}


class FakeThreads:
    """A `ThreadService` by shape only — no repository, no engine, no SQL."""

    def __init__(self, *rows):
        self.rows = {r["session_key"]: r for r in rows}
        self.listed = []
        self.titled = []
        self.archived = []

    async def running_lanes(self, session_keys) -> set:
        return {k for k in session_keys if k == "web:1:running"}

    async def list_visible(self, user_id, is_owner, *, include_archived=False,
                           before=None, limit=50):
        self.listed.append({"user_id": user_id, "is_owner": is_owner,
                            "include_archived": include_archived, "before": before,
                            "limit": limit})
        return list(self.rows.values()), None

    async def create_web(self, user_id, now):
        created = row(f"web:{user_id}:new", owner=user_id)
        self.rows[created["session_key"]] = created
        return created

    async def get(self, session_key):
        return self.rows.get(session_key)

    async def get_visible(self, session_key, user_id, is_owner):
        thread = self.rows.get(session_key)
        if thread is None:
            raise NotFound("no such thread")
        return thread

    async def set_title(self, session_key, title, *, manual):
        self.titled.append((session_key, title, manual))
        self.rows[session_key]["title"] = title

    async def set_archived(self, session_key, archived_at):
        self.archived.append((session_key, archived_at))


class FakeLanes:
    """A `SessionService` by shape: the gatherer and the one writer of a lane's pair.

    It keeps the resolution order out of the picture entirely — the router's job is
    to pass the lane and the owner through and to spell the two refusals, and a
    router test that re-implemented `resolve` would only prove itself right.
    """

    def __init__(self, threads: FakeThreads, *, default=("claude-sonnet-5", None)):
        self._threads = threads
        self._default = default
        self.asked: list[tuple] = []
        self.wrote: list[tuple] = []

    async def effective(self, session_key, owner_id=None):
        self.asked.append((session_key, owner_id))
        thread = self._threads.rows.get(session_key) or {}
        model = thread.get("model") or self._default[0]
        effort = thread.get("effort") if thread.get("model") else self._default[1]
        return Choice(model, model, effort)

    async def set_model(self, session_key, model, effort):
        if model is not None and model not in ("claude-opus-5-5", "claude-sonnet-5"):
            raise ValueError("bad_model")
        if model is not None and effort not in (None, "low", "high", "xhigh"):
            raise ValueError("bad_effort")
        pair = (model, effort) if model else (None, None)
        self.wrote.append((session_key, *pair))
        self._threads.rows[session_key]["model"] = pair[0]
        self._threads.rows[session_key]["effort"] = pair[1]
        return pair


def build_app(threads: FakeThreads, lanes=None, *, user=CurrentUser(1, True)) -> FastAPI:
    """The router plus the two exception handlers the shell registers.

    `app.state.deps` is what `core.di.bundle` returns, so the providers in
    `presentation/dependencies.py` resolve exactly as they do in the real app, and
    `current_user` is overridden because the identity middleware is the shell's,
    not this router's.
    """
    app = FastAPI()
    app.state.deps = SimpleNamespace(threads=threads,
                                     lane_models=lanes or FakeLanes(threads))
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: user

    async def _render(request, exc: ApplicationException):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status)

    async def _invalid(request, exc: RequestValidationError):
        return JSONResponse({"error": {"code": "bad_request", "message": "invalid"}},
                            status_code=400)

    app.add_exception_handler(ApplicationException, _render)
    app.add_exception_handler(RequestValidationError, _invalid)
    return app


def make_client(threads: FakeThreads, lanes=None, *, user=CurrentUser(1, True)):
    app = build_app(threads, lanes, user=user)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


def test_the_routes_and_their_operation_ids_are_frozen():
    """A generated client names its methods after these, and the ORDER is part of
    the contract: `{id:path}` matches `web:1:a/model` too, Starlette serves the
    first full match, and `…/model` is registered ahead of the generic routes so
    that stays true the day one of them grows a PUT."""
    assert [(r.path, sorted(r.methods), r.operation_id) for r in router.routes] == [
        ("/api/threads", ["GET"], "sessions_list_threads"),
        ("/api/threads", ["POST"], "sessions_create_thread"),
        ("/api/threads/{id:path}/archive", ["POST"], "sessions_archive_thread"),
        ("/api/threads/{id:path}/unarchive", ["POST"], "sessions_unarchive_thread"),
        ("/api/threads/{id:path}/model", ["PUT"], "sessions_set_thread_model"),
        ("/api/threads/{id:path}", ["GET"], "sessions_get_thread"),
        ("/api/threads/{id:path}", ["PATCH"], "sessions_rename_thread"),
        ("/api/threads/{id:path}", ["DELETE"], "sessions_delete_thread"),
    ]
    assert {tag for r in router.routes for tag in r.tags} == {"sessions"}


async def test_the_thread_service_is_swappable_without_rebuilding_the_bundle():
    threads = FakeThreads(row("web:1:a", "Plan"), row("web:1:running"))
    client = make_client(threads)

    r = await client.get("/api/threads")
    assert r.status_code == 200
    # The list route is unchanged: no model keys per row. Resolving one would be a
    # session read and an owner-default read for every line of the sidebar.
    assert r.json() == {"threads": [
        {"id": "web:1:a", "title": "Plan", "channel": "web", "archived": False,
         "running": False, "last_event_at": NOW, "created_at": NOW},
        {"id": "web:1:running", "title": None, "channel": "web", "archived": False,
         "running": True, "last_event_at": NOW, "created_at": NOW}], "next": None}
    # The identity the middleware proved, not a request attribute the handler read.
    assert threads.listed[-1]["user_id"] == 1 and threads.listed[-1]["is_owner"] is True

    r = await client.post("/api/threads", json={})
    assert r.status_code == 201 and r.json()["id"] == "web:1:new"

    r = await client.get("/api/threads/web:1:a")
    assert r.status_code == 200 and r.json()["title"] == "Plan"

    r = await client.post("/api/threads/web:1:a/archive")
    assert r.status_code == 204 and threads.archived[-1][0] == "web:1:a"
    r = await client.post("/api/threads/web:1:a/unarchive")
    assert r.status_code == 204 and threads.archived[-1] == ("web:1:a", None)
    r = await client.delete("/api/threads/web:1:a")
    assert r.status_code == 204 and threads.archived[-1][1] is not None   # DELETE archives


async def test_overriding_the_service_does_not_open_the_lane_guard():
    """`owned_thread` is its own dependency over the same service: a thread the
    service will not hand over is 404, never 403 — a 403 would confirm it exists."""
    client = make_client(FakeThreads(row("web:1:a")))
    for method, path, body in (("get", "/api/threads/web:2:zzz", None),
                               ("patch", "/api/threads/web:2:zzz", {"title": "x"}),
                               ("post", "/api/threads/web:2:zzz/archive", None),
                               ("put", "/api/threads/web:2:zzz/model",
                                {"model": "claude-opus-5-5"})):
        r = await getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 404, path
        assert r.json() == {"error": {"code": "not_found", "message": "no such thread"}}


async def test_the_listing_query_keeps_its_old_tolerances():
    """`archived` is a string compared to "1" and `limit` is clamped, not refused."""
    threads = FakeThreads()
    client = make_client(threads)

    assert (await client.get("/api/threads?archived=1")).status_code == 200
    assert threads.listed[-1]["include_archived"] is True
    # Anything else is false — and 200, where a `bool` parameter would be a 400.
    for query in ("archived=0", "archived=maybe", "archived="):
        assert (await client.get(f"/api/threads?{query}")).status_code == 200
        assert threads.listed[-1]["include_archived"] is False

    assert (await client.get("/api/threads?limit=500")).status_code == 200
    assert threads.listed[-1]["limit"] == 200            # clamped, not rejected
    assert (await client.get("/api/threads",
                             params={"limit": 2, "before": NOW})).status_code == 200
    assert threads.listed[-1] == {"user_id": 1, "is_owner": True, "include_archived": False,
                                  "before": NOW, "limit": 2}


async def test_the_rename_body_is_the_routers_own_check_not_a_schemas():
    """An empty title is `400 bad_request` / "title is required" — the message the
    handler has always written, not whatever pydantic would have said instead."""
    threads = FakeThreads(row("web:1:a"))
    client = make_client(threads)

    for body in ({}, {"title": ""}, {"title": "   "}):
        r = await client.patch("/api/threads/web:1:a", json=body)
        assert r.status_code == 400
        assert r.json() == {"error": {"code": "bad_request", "message": "title is required"}}

    # A non-string title was stringified, never refused; extra keys are ignored.
    r = await client.patch("/api/threads/web:1:a", json={"title": 5, "colour": "red"})
    assert r.status_code == 200 and r.json()["title"] == "5"
    assert threads.titled[-1] == ("web:1:a", "5", True)

    r = await client.patch("/api/threads/web:1:a", json={"title": "x" * 200})
    assert r.status_code == 200 and len(r.json()["title"]) == 120


# --- the model routes --------------------------------------------------------


async def test_the_detail_route_carries_both_pairs():
    """Two pairs, never one: what the lane was explicitly given, and what its next
    turn will actually run. The picker re-reads this when it opens."""
    threads = FakeThreads(row("web:1:a", "Plan"))
    lanes = FakeLanes(threads, default=("claude-fable-5-1", "low"))
    client = make_client(threads, lanes)

    body = (await client.get("/api/threads/web:1:a")).json()
    assert body["model"] is None and body["effort"] is None
    assert body["effective_model"] == "claude-fable-5-1"
    assert body["effective_effort"] == "low"
    # The frozen keys are all still there.
    assert body["id"] == "web:1:a" and body["title"] == "Plan" and body["running"] is False
    # Resolved for the LANE's owner, not for whoever is reading it.
    assert lanes.asked[-1] == ("web:1:a", 1)


async def test_choosing_a_model_answers_with_both_pairs():
    threads = FakeThreads(row("web:1:a"))
    lanes = FakeLanes(threads)
    client = make_client(threads, lanes)

    r = await client.put("/api/threads/web:1:a/model",
                         json={"model": "claude-opus-5-5", "effort": "xhigh"})
    assert r.status_code == 200
    assert r.json() == {"model": "claude-opus-5-5", "effort": "xhigh",
                        "effective_model": "claude-opus-5-5", "effective_effort": "xhigh"}
    assert lanes.wrote == [("web:1:a", "claude-opus-5-5", "xhigh")]


async def test_a_null_model_clears_the_lanes_choice():
    """`model: null` is a value, not a missing field — it is how the picker says
    "back to my default", and the effort goes with it."""
    threads = FakeThreads(row("web:1:a", model="claude-opus-5-5", effort="high"))
    lanes = FakeLanes(threads)
    client = make_client(threads, lanes)

    r = await client.put("/api/threads/web:1:a/model",
                         json={"model": None, "effort": "high"})
    assert r.status_code == 200
    assert r.json() == {"model": None, "effort": None,
                        "effective_model": "claude-sonnet-5", "effective_effort": None}
    assert lanes.wrote == [("web:1:a", None, None)]


async def test_the_two_model_refusals_are_the_catalogs_own_codes():
    threads = FakeThreads(row("web:1:a"))
    client = make_client(threads)

    r = await client.put("/api/threads/web:1:a/model", json={"model": "claude-opus-9"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_model"
    r = await client.put("/api/threads/web:1:a/model",
                         json={"model": "claude-opus-5-5", "effort": "turbo"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_effort"


async def test_the_model_body_is_lenient_like_every_other_body_here():
    """`extra="ignore"`, and a non-string model reaches the router's own
    `400 bad_model` rather than pydantic's `400 bad_request`."""
    threads = FakeThreads(row("web:1:a"))
    client = make_client(threads)

    r = await client.put("/api/threads/web:1:a/model",
                         json={"model": "claude-opus-5-5", "colour": "red"})
    assert r.status_code == 200 and r.json()["model"] == "claude-opus-5-5"

    r = await client.put("/api/threads/web:1:a/model", json={"model": 5})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_model"

    # A body with neither key clears the lane, which is what `{}` means here.
    r = await client.put("/api/threads/web:1:a/model", json={})
    assert r.status_code == 200 and r.json()["model"] is None


async def test_a_lane_whose_key_has_slashes_still_reaches_the_model_route():
    """A lane key is not URL-safe and `{id:path}` is what lets it through — the
    `/model` suffix has to survive a key that contains slashes of its own."""
    threads = FakeThreads(row("tg:supergroup:-100/9"))
    client = make_client(threads)
    r = await client.put("/api/threads/tg:supergroup:-100/9/model",
                         json={"model": "claude-opus-5-5"})
    assert r.status_code == 200 and r.json()["model"] == "claude-opus-5-5"

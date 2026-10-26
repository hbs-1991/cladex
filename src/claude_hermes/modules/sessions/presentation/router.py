"""The threads router: list/page, create, read, rename, archive, unarchive.

Wave 2b turned `routes(deps)` into a module-level `router: APIRouter`. The one
collaborator the closure captured (`deps.threads`) is `Depends(get_threads)`, the
404-or-the-thread line every per-thread route started with is `Depends(owned_thread)`,
and `request.state.user_id` / `.is_owner` are one `CurrentUser`. Route paths, the
registration order and the wire shape are unchanged and frozen.

The query parameters keep the old *tolerances*, not a stricter schema: `archived` is
the string the frontend sends and `limit` is clamped rather than refused. Both are
spelled out where they are read.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, Response

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.exceptions import ApplicationException

from ..application.services import SessionService, ThreadService
from ..domain.constants import MANUAL_TITLE_MAX_CHARS
from .dependencies import get_lane_models, get_threads, owned_thread
from .schemas import ThreadModelPut, ThreadPatch, detail_wire, model_wire, to_wire

# The threads router registers `/api/threads/{id:path}`, which swallows anything
# under a thread. The app shell mounts the turns router BEFORE this one so
# `/api/threads/{id}/messages` and friends still match.

router = APIRouter(tags=["sessions"])

# The two model refusals, spelled once. The `code` is what the SPA and the i18n
# catalogue branch on; the message is for a human reading a log.
_BAD = {"bad_model": "that model is not offered",
        "bad_effort": "that model does not take that effort"}


@router.get("/api/threads", operation_id="sessions_list_threads")
async def list_threads(archived: str | None = None, before: str | None = None,
                       limit: int = 50,
                       threads: ThreadService = Depends(get_threads),
                       user: CurrentUser = Depends(current_user)) -> JSONResponse:
    # `archived` stays the raw string and `limit` stays clamped: the old handler read
    # `query_params.get("archived") == "1"` and `min(int(...), 200)`, so anything but
    # "1" meant false and an over-large limit was cut down, never refused. A `bool`
    # parameter would answer 400 for `?archived=maybe`, and `Query(le=200)` would
    # answer 400 for `?limit=500` — two new refusals on a route that had none.
    page, next_cursor = await threads.list_visible(
        user.user_id, user.is_owner,
        include_archived=archived == "1", before=before, limit=min(limit, 200))
    # One activity query for the whole page. "Running" lives in the turns
    # module's table now, so a per-row check would be one round trip per thread.
    running = await threads.running_lanes([t["session_key"] for t in page])
    return JSONResponse({
        "threads": [to_wire(t, t["session_key"] in running) for t in page],
        "next": next_cursor,
    })


@router.post("/api/threads", operation_id="sessions_create_thread", status_code=201)
async def create_thread(threads: ThreadService = Depends(get_threads),
                        user: CurrentUser = Depends(current_user)) -> JSONResponse:
    now = datetime.now(timezone.utc).isoformat()
    thread = await threads.create_web(user.user_id, now)
    return JSONResponse(to_wire(thread, False), status_code=201)


@router.post("/api/threads/{id:path}/archive", operation_id="sessions_archive_thread",
             status_code=204)
async def archive(t: dict = Depends(owned_thread),
                  threads: ThreadService = Depends(get_threads)) -> Response:
    await threads.set_archived(t["session_key"], datetime.now(timezone.utc).isoformat())
    return Response(status_code=204)


@router.post("/api/threads/{id:path}/unarchive", operation_id="sessions_unarchive_thread",
             status_code=204)
async def unarchive(t: dict = Depends(owned_thread),
                    threads: ThreadService = Depends(get_threads)) -> Response:
    await threads.set_archived(t["session_key"], None)
    return Response(status_code=204)


# Declared BEFORE the generic `{id:path}` routes, like `…/archive` and for the same
# reason: `{id:path}` matches `web:1:a/model` too, so the two overlap and Starlette
# serves the FIRST full match. Today the generic routes carry no PUT, so this one
# would be reached either way — the order is what stops that from being luck the day
# somebody adds `PUT /api/threads/{id:path}`.
@router.put("/api/threads/{id:path}/model", operation_id="sessions_set_thread_model")
async def set_thread_model(body: ThreadModelPut, t: dict = Depends(owned_thread),
                           lanes: SessionService = Depends(get_lane_models)
                           ) -> JSONResponse:
    """Choose this lane's (model, effort), or clear the choice with `model: null`.

    `owned_thread` first: a lane the caller may not see is a 404 before anything
    here is read, so a stranger cannot learn that a key exists by watching which
    error comes back. The two 400s are the catalog's own words — a model that is
    not offered and an effort the model does not take are refusals the resolver
    would otherwise swallow silently.
    """
    model = str(body.model) if body.model is not None else None
    effort = str(body.effort) if body.effort is not None else None
    try:
        pair = await lanes.set_model(t["session_key"], model, effort)
    except ValueError as e:                    # "bad_model" | "bad_effort"
        code = str(e)
        raise ApplicationException(400, code, _BAD.get(code, code)) from e
    choice = await lanes.effective(t["session_key"], t.get("owner_user_id"))
    return JSONResponse(model_wire(pair[0], pair[1], choice))


@router.get("/api/threads/{id:path}", operation_id="sessions_get_thread")
async def get_thread(t: dict = Depends(owned_thread),
                     threads: ThreadService = Depends(get_threads),
                     lanes: SessionService = Depends(get_lane_models)) -> JSONResponse:
    running = await threads.running_lanes([t["session_key"]])
    # Resolved per read rather than stored: the answer depends on the lane's live
    # session and on the owner's default, either of which can have changed since
    # the last turn. This is what the picker re-reads when it opens.
    choice = await lanes.effective(t["session_key"], t.get("owner_user_id"))
    return JSONResponse(detail_wire(t, t["session_key"] in running, choice))


@router.patch("/api/threads/{id:path}", operation_id="sessions_rename_thread")
async def patch_thread(body: ThreadPatch, t: dict = Depends(owned_thread),
                       threads: ThreadService = Depends(get_threads)) -> JSONResponse:
    title = str(body.title).strip()[:MANUAL_TITLE_MAX_CHARS]
    if not title:
        raise ApplicationException(400, "bad_request", "title is required")
    await threads.set_title(t["session_key"], title, manual=True)
    t = await threads.get(t["session_key"])
    running = await threads.running_lanes([t["session_key"]])
    return JSONResponse(to_wire(t, t["session_key"] in running))


# DELETE is the same handler as POST …/archive — the SPA's "delete" button archives.
# Registered last, exactly where `routes(deps)` put it, and with its own
# `operation_id` because a generated client needs two distinct method names.
router.add_api_route("/api/threads/{id:path}", archive, methods=["DELETE"],
                     operation_id="sessions_delete_thread", status_code=204)

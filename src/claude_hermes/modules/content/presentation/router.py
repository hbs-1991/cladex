"""Content router: the owner's face of the content plan (`/api/content*`).

**Every route is the owner's** (`require_owner`), reads included: in v1 the plan
exists for the deploy owner alone, and a plan item names files in the owner's
`agent_home`. Every route is also scoped to the asking user (`content_items.owner_id`),
so an id that is not theirs — or does not exist — is a 404, never a 403, the rule
the Routines router follows.

`require_owner` sits in each decorator's `dependencies=`, where FastAPI solves it
first: the 403 is raised before any other provider runs. The handlers that need the
user's id declare it again as a parameter; FastAPI caches a dependency by its
callable within one request, so that is one call and one 403.

Validation is the service's (`ContentService._fields`), shared with the agent tool:
the request models are `Any`-typed so a bad field comes back as its own code
(`bad_channel`, `bad_media`, `bad_time`, `bad_status`, …) with a message the item
dialog shows verbatim, rendered by the shell's `ApplicationException` handler.

The shell mounts this router only when `deps.content is not None` (`content_enabled`);
with the flag off every path here falls through to the `/api/*` catch-all and its
404, which the SPA reads as "content disabled".

`{id:int}` keeps its converter so `/api/content/publisher`, `/api/content/drafter`
and `/api/content/approve-themes` can never be read as an item id, and a
non-numeric id falls through to the catch-all.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from starlette.responses import FileResponse, JSONResponse, Response

from claude_hermes.core.auth import CurrentUser, require_owner

from ..application.service import ContentService
from .dependencies import get_content
from .schemas import ContentBody, PublisherBody, RetryBody, ThemesBody, to_wire

router = APIRouter(tags=["content"])

_OWNER = [Depends(require_owner)]


@router.get("/api/content", operation_id="content_list", dependencies=_OWNER)
async def list_items(
        start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"),
        user: CurrentUser = Depends(require_owner),
        content: ContentService = Depends(get_content)) -> JSONResponse:
    """The dated items with `from <= publish_at < to`, plus the whole backlog (no
    `publish_at`, not published or skipped). A range over 62 days is 400 `bad_range`;
    a naive `from`/`to` is read in the deploy's timezone."""
    lo, hi = content.parse_range(start, end)
    plan = await content.calendar(user.user_id, lo, hi)
    return JSONResponse({"items": [to_wire(i) for i in plan["items"]],
                         "backlog": [to_wire(i) for i in plan["backlog"]]})


@router.get("/api/content/publisher", operation_id="content_get_publisher",
            dependencies=_OWNER)
async def get_publisher(user: CurrentUser = Depends(require_owner),
                        content: ContentService = Depends(get_content)) -> JSONResponse:
    return JSONResponse(await content.publisher(user.user_id))


@router.put("/api/content/publisher", operation_id="content_set_publisher",
            dependencies=_OWNER)
async def set_publisher(body: PublisherBody,
                        user: CurrentUser = Depends(require_owner),
                        content: ContentService = Depends(get_content)) -> JSONResponse:
    """400 `bad_publisher` unless the job is the owner's and scheduled `manual`."""
    return JSONResponse(await content.set_publisher(user.user_id, body.job_id))


@router.get("/api/content/drafter", operation_id="content_get_drafter",
            dependencies=_OWNER)
async def get_drafter(user: CurrentUser = Depends(require_owner),
                      content: ContentService = Depends(get_content)) -> JSONResponse:
    return JSONResponse(await content.drafter(user.user_id))


@router.put("/api/content/drafter", operation_id="content_set_drafter",
            dependencies=_OWNER)
async def set_drafter(body: PublisherBody,
                      user: CurrentUser = Depends(require_owner),
                      content: ContentService = Depends(get_content)) -> JSONResponse:
    """400 `bad_drafter` unless the job is the owner's, scheduled `manual`, and not
    the publisher."""
    return JSONResponse(await content.set_drafter(user.user_id, body.job_id))


@router.post("/api/content/approve-themes", operation_id="content_approve_themes",
             dependencies=_OWNER)
async def approve_themes(body: ThemesBody,
                         user: CurrentUser = Depends(require_owner),
                         content: ContentService = Depends(get_content)) -> JSONResponse:
    """Every future `idea` with `from <= publish_at < to` -> `planned`. 400
    `bad_range` as the calendar read (missing, reversed, over 62 days)."""
    items = await content.approve_themes(user.user_id, body.start, body.end)
    return JSONResponse({"approved": len(items), "items": [to_wire(i) for i in items]})


@router.post("/api/content", operation_id="content_create", status_code=201,
             dependencies=_OWNER)
async def create(body: ContentBody,
                 user: CurrentUser = Depends(require_owner),
                 content: ContentService = Depends(get_content)) -> JSONResponse:
    item = await content.create(user.user_id, body.model_dump(exclude_unset=True))
    return JSONResponse(to_wire(item), status_code=201)


@router.get("/api/content/{id:int}", operation_id="content_get", dependencies=_OWNER)
async def get_one(id: int,
                  user: CurrentUser = Depends(require_owner),
                  content: ContentService = Depends(get_content)) -> JSONResponse:
    return JSONResponse(to_wire(await content.require(id, user.user_id)))


@router.patch("/api/content/{id:int}", operation_id="content_edit", dependencies=_OWNER)
async def patch(id: int, body: ContentBody,
                user: CurrentUser = Depends(require_owner),
                content: ContentService = Depends(get_content)) -> JSONResponse:
    """A partial edit (`exclude_unset=True`: an absent key stays absent). A
    `scheduled` item stays scheduled; `queued/publishing/published` are 409 `locked`."""
    item = await content.edit(user.user_id, id, body.model_dump(exclude_unset=True))
    return JSONResponse(to_wire(item))


@router.delete("/api/content/{id:int}", operation_id="content_delete",
               status_code=204, dependencies=_OWNER)
async def delete(id: int,
                 user: CurrentUser = Depends(require_owner),
                 content: ContentService = Depends(get_content)) -> Response:
    await content.delete(user.user_id, id)
    return Response(status_code=204)


@router.post("/api/content/{id:int}/approve", operation_id="content_approve",
             dependencies=_OWNER)
async def approve(id: int,
                  user: CurrentUser = Depends(require_owner),
                  content: ContentService = Depends(get_content)) -> JSONResponse:
    """`scheduled`; 400 `not_approvable`, `no_publish_at`, `empty_body`,
    `media_required`, `media_missing`."""
    return JSONResponse(to_wire(await content.approve(user.user_id, id)))


@router.post("/api/content/{id:int}/unapprove", operation_id="content_unapprove",
             dependencies=_OWNER)
async def unapprove(id: int,
                    user: CurrentUser = Depends(require_owner),
                    content: ContentService = Depends(get_content)) -> JSONResponse:
    return JSONResponse(to_wire(await content.unapprove(user.user_id, id)))


@router.post("/api/content/{id:int}/approve-theme", operation_id="content_approve_theme",
             dependencies=_OWNER)
async def approve_theme(id: int,
                        user: CurrentUser = Depends(require_owner),
                        content: ContentService = Depends(get_content)) -> JSONResponse:
    """`idea -> planned`; 400 `not_an_idea`, `no_publish_at` (none, or passed)."""
    return JSONResponse(to_wire(await content.approve_theme(user.user_id, id)))


@router.post("/api/content/{id:int}/unapprove-theme",
             operation_id="content_unapprove_theme", dependencies=_OWNER)
async def unapprove_theme(id: int,
                          user: CurrentUser = Depends(require_owner),
                          content: ContentService = Depends(get_content)) -> JSONResponse:
    """`planned -> idea`; 400 `not_planned`."""
    return JSONResponse(to_wire(await content.unapprove_theme(user.user_id, id)))


@router.post("/api/content/{id:int}/retry-drafting",
             operation_id="content_retry_drafting", dependencies=_OWNER)
async def retry_drafting(id: int,
                         user: CurrentUser = Depends(require_owner),
                         content: ContentService = Depends(get_content)) -> JSONResponse:
    """A `planned` item with an `error`: its drafter wakes and attempts start
    over; 400 `not_retryable` for anything else."""
    return JSONResponse(to_wire(await content.retry_drafting(user.user_id, id)))


@router.post("/api/content/{id:int}/retry", operation_id="content_retry",
             dependencies=_OWNER)
async def retry(id: int, body: RetryBody,
                user: CurrentUser = Depends(require_owner),
                content: ContentService = Depends(get_content)) -> JSONResponse:
    item = await content.retry(user.user_id, id, body.mode, body.url)
    return JSONResponse(to_wire(item))


@router.get("/api/content/{id:int}/media/{n:int}", operation_id="content_media",
            dependencies=_OWNER)
async def media(id: int, n: int,
                user: CurrentUser = Depends(require_owner),
                content: ContentService = Depends(get_content)) -> FileResponse:
    """The n-th media file, re-resolved inside the owner's `agent_home` on every
    request — the row is data, and a path that no longer passes the guard is a 404
    like a missing file."""
    return FileResponse(await content.media_file(user.user_id, id, n))

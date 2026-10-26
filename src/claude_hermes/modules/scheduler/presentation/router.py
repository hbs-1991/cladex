"""Routines router: the web face of the cron jobs the `cron_manage` tool creates.

Create goes through `SchedulerService.create_job`, the same coroutine the tool
calls, so the two channels can never validate a schedule, a prompt or a timeout
differently. Reads are open to any logged-in user; every mutation is owner-only.

Since Wave 4a **every route is scoped to the asking user** (`jobs.owner_id`, D7).
The reads matter most: they were never owner-gated, so before the column existed
`GET /api/routines` listed the whole deploy's routines to anybody who could log
in, and `GET /api/routines/{id}` read any of them. A routine of somebody else is
now a 404, never a 403 — the same rule the thread guard follows, for the same
reason.

The owner gate is `core.auth.require_owner`, which refuses with the same
`403 forbidden` the hand-written `owner_only(request)` answered. It stays in the
decorator's `dependencies=` on every mutation, where FastAPI puts it at the front
of the dependant list — the 403 is raised before any other provider runs, which is
where `owner_only(request)` used to be. The handlers that need the id as well
declare `Depends(require_owner)` a second time; FastAPI caches a dependency by its
callable within one request, so that is one call and one 403, not two.

A routine's (model, effort) is validated HERE rather than in the service, because
this is the only channel that sets one: `cron_manage`'s input schema is prompt-cache
surface and was not reopened, so the tool creates routines with no pair at all. The
executor is the second line of defence — `resolve` skips a model that has since left
`models:` — and this is the first: a pair the resolver would silently ignore is a
`400` with the field's own code (`bad_model`, `bad_effort`) so the form can point at
the select that is wrong.

Dependencies: `get_scheduler` (`deps.scheduler`) and `get_cfg` (`deps.cfg`, for the
offered `models:`); see `dependencies.py`. The **shell** decides whether this router
is mounted at all (`deps.scheduler is not None`).

`{id:int}` stays spelled with its converter: a non-numeric id never reaches a
handler, it falls through to the app's `/api/*` catch-all and its 404. That is
also why `/api/routines/calendar` cannot be mistaken for a routine id.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from starlette.responses import JSONResponse, Response

from claude_hermes.core.auth import CurrentUser, current_user, require_owner
from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.core.model_catalog import is_model_command, lookup, offered, validate

from ..application.service import SchedulerService
from .dependencies import get_cfg, get_scheduler
from .schemas import RoutineCreate, RoutinePatch, to_wire

router = APIRouter(tags=["scheduler"])


def _clean(value: Any) -> str | None:
    """A wire value as the column stores it: a null, a blank and a missing key are
    all the same "inherit the deploy pair"."""
    return str(value or "").strip() or None


def _checked_pair(cfg: Any, model: Any, effort: Any) -> tuple[str | None, str | None]:
    """The routine's pair, refused if the resolver would not apply it.

    Raised as a bare `ApplicationException` with the field's own code rather than
    `ScheduleRejected`: that one is frozen on `400 bad_request`, which the Routines
    page renders as one sentence over the whole form, and the picker needs to know
    WHICH select it has to reopen.
    """
    model, effort = _clean(model), _clean(effort)
    if model is None:
        # An effort with no model is not a choice `resolve` could apply — it drops
        # the pair whole — so it is not one we store either.
        effort = None
    try:
        validate(model, effort, [m.id for m in offered(cfg.models, cfg.model)])
    except ValueError as e:
        code = str(e)
        raise ApplicationException(400, code, (
            f"'{model}' is not one of the models this deployment offers."
            if code == "bad_model" else
            f"'{effort}' is not an effort {lookup(model).label} takes."
        )) from e
    return model, effort


def _checked_prompt(prompt: Any) -> None:
    """A routine whose prompt IS `/model` or `/effort` would never run: the CLI
    executes both as its own local commands in non-interactive mode. Refused at the
    door, where the form can say so, as well as at the executor, which cannot."""
    if is_model_command(prompt):
        raise ApplicationException(400, "bad_prompt", (
            "A /model or /effort prompt is a CLI command, not a job. Choose the "
            "routine's model in its own field and give it a real prompt."))


@router.get("/api/routines", operation_id="scheduler_list_routines")
async def list_routines(
        user: CurrentUser = Depends(current_user),
        scheduler: SchedulerService = Depends(get_scheduler)) -> JSONResponse:
    """The caller's own routines, newest schedule state included.

    Open to any logged-in user, and since Wave 4a scoped to them: the route was
    never owner-gated, so before `jobs.owner_id` existed it showed every routine on
    the deploy to anybody who could log in. The mutations below stay `require_owner`
    — a non-owner creates routines through `cron_manage`, not through this page.
    """
    jobs = await scheduler.list_jobs(user.user_id)
    return JSONResponse({"routines": [to_wire(j) for j in jobs]})


def _instant(value: str | None, name: str) -> datetime:
    """One calendar bound as an aware UTC instant. A naive one is read as UTC —
    the SPA always sends an offset, so a naive bound is a hand-typed URL, and UTC
    is what every stored timestamp is in."""
    try:
        at = datetime.fromisoformat(str(value or ""))
    except ValueError as e:
        raise ApplicationException(400, "bad_range", (
            f"'{name}' must be an ISO-8601 time.")) from e
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


@router.get("/api/routines/calendar", operation_id="scheduler_routines_calendar")
async def calendar(
        start: str | None = Query(None, alias="from"),
        end: str | None = Query(None, alias="to"),
        user: CurrentUser = Depends(current_user),
        scheduler: SchedulerService = Depends(get_scheduler)) -> JSONResponse:
    """The caller's routines on a timeline, `from <= t < to`: future fires, the
    routines too dense to expand, and the runs recorded in the window.

    Scoped exactly like `list_routines` — any logged-in user, their own routines
    only. The content plan is a separate endpoint the SPA merges with this one; no
    route joins the two modules. A window over 62 days, reversed, empty or
    unparseable is `400 bad_range`.
    """
    body = await scheduler.calendar(
        user.user_id, _instant(start, "from"), _instant(end, "to"))
    return JSONResponse(body)


@router.post("/api/routines", operation_id="scheduler_create_routine", status_code=201)
async def create(body: RoutineCreate,
                 user: CurrentUser = Depends(require_owner),
                 cfg: Any = Depends(get_cfg),
                 scheduler: SchedulerService = Depends(get_scheduler)) -> JSONResponse:
    """A routine delivered to the owner's own private lane.

    `create_or_raise` is the tool's path with an exception instead of a reason string,
    so a schedule the chat would have refused is refused here in the same words.
    """
    uid = user.user_id
    _checked_prompt(body.prompt)
    model, effort = _checked_pair(cfg, body.model, body.effort)
    job = await scheduler.create_or_raise(
        name=str(body.name),
        schedule=str(body.schedule),
        prompt=str(body.prompt),
        deliver_chat_id=uid, origin_session_key=f"tg:private:{uid}",
        timeout_seconds=body.timeout_seconds,
        model=model, effort=effort,
        # The route is `require_owner`, so the requesting user IS the owner (D7);
        # spelling it out rather than reaching for a deploy-wide default is what
        # keeps this the same rule the tool applies.
        owner_id=uid,
    )
    return JSONResponse(to_wire(job), status_code=201)


@router.post("/api/routines/{id:int}/pause", operation_id="scheduler_pause_routine",
             status_code=204, dependencies=[Depends(require_owner)])
async def pause(id: int,
                user: CurrentUser = Depends(require_owner),
                scheduler: SchedulerService = Depends(get_scheduler)) -> Response:
    job = await scheduler.require(id, user.user_id)
    await scheduler.set_state(job["id"], "paused")
    return Response(status_code=204)


@router.post("/api/routines/{id:int}/resume", operation_id="scheduler_resume_routine",
             status_code=204, dependencies=[Depends(require_owner)])
async def resume(id: int,
                 user: CurrentUser = Depends(require_owner),
                 scheduler: SchedulerService = Depends(get_scheduler)) -> Response:
    job = await scheduler.require(id, user.user_id)
    await scheduler.set_state(job["id"], "active")
    return Response(status_code=204)


@router.post("/api/routines/{id:int}/run", operation_id="scheduler_run_routine",
             status_code=204, dependencies=[Depends(require_owner)])
async def run_now(id: int,
                  user: CurrentUser = Depends(require_owner),
                  scheduler: SchedulerService = Depends(get_scheduler)) -> Response:
    """Not a synchronous run: it moves `next_run_at` into the past and the ticker
    picks the job up on its next pass."""
    job = await scheduler.require(id, user.user_id)
    await scheduler.run_now(job["id"])
    return Response(status_code=204)


@router.get("/api/routines/{id:int}/runs", operation_id="scheduler_list_runs")
async def runs(id: int,
               user: CurrentUser = Depends(current_user),
               scheduler: SchedulerService = Depends(get_scheduler)) -> JSONResponse:
    job = await scheduler.require(id, user.user_id)
    return JSONResponse({"runs": await scheduler.runs(job["id"], limit=30)})


@router.get("/api/routines/{id:int}", operation_id="scheduler_get_routine")
async def get_one(id: int,
                  user: CurrentUser = Depends(current_user),
                  scheduler: SchedulerService = Depends(get_scheduler)) -> JSONResponse:
    job = await scheduler.require(id, user.user_id)
    return JSONResponse(to_wire(job))


@router.patch("/api/routines/{id:int}", operation_id="scheduler_edit_routine",
              dependencies=[Depends(require_owner)])
async def patch(id: int, body: RoutinePatch,
                user: CurrentUser = Depends(require_owner),
                cfg: Any = Depends(get_cfg),
                scheduler: SchedulerService = Depends(get_scheduler)) -> JSONResponse:
    """A partial edit. `exclude_unset=True` is what keeps it partial: `edit` branches
    on `"name" in body`, and a model dumped whole would rewrite every field."""
    fields = body.model_dump(exclude_unset=True)
    if "prompt" in fields:
        _checked_prompt(fields["prompt"])
    if "model" in fields or "effort" in fields:
        # The pair travels whole even when the form sent half of it: an effort
        # belongs to a model, so `edit` is handed both keys or neither.
        fields["model"], fields["effort"] = _checked_pair(
            cfg, fields.get("model"), fields.get("effort"))
    job = await scheduler.edit(id, fields, owner_id=user.user_id)
    return JSONResponse(to_wire(job))


@router.delete("/api/routines/{id:int}", operation_id="scheduler_delete_routine",
               status_code=204, dependencies=[Depends(require_owner)])
async def delete(id: int,
                 user: CurrentUser = Depends(require_owner),
                 scheduler: SchedulerService = Depends(get_scheduler)) -> Response:
    job = await scheduler.require(id, user.user_id)
    await scheduler.delete(job["id"])
    return Response(status_code=204)

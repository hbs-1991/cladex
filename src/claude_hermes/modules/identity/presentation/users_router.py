"""The owner's user list: who may talk to this agent, what each of them may do, and
the ways to change both.

Until now admission had no door in the browser. A person reached the agent because
their id was in `allowed_users` in `config.yaml` — a file the owner edits on the
host, that the process reads once at boot — or because they were the first private
chat and claimed the deployment. Taking access away had no door at all: the
`disabled_at` column has been read since Wave 4a and written by nothing.

So this router is both. `POST /api/users` writes the row `check` would have written
on that person's first message, which admits them everywhere at once and needs no
restart; disable and enable set and clear `disabled_at`, and the web middleware asks
`admitted()` on every `/api/*` request, so a revoked cookie stops working on the
next one rather than at its expiry.

`PATCH /api/users/{id}` (0009) is the third door: per-person grants of a shell
(`Bash`/`Agent`/`Task`) and of the learning loop, which until then were two
deploy-wide `config.yaml` switches read once at boot. The two keys stay as the
default for anybody the owner has not chosen for, and every row on the list says
which of its two values is its own and which is that default (`*_inherited`).
A grant is written to the row and nothing else happens here: the person's next
turn reads it — in whichever process runs that turn, with no restart — and a lane
whose shell moved starts a fresh session there and says so. Resetting sessions
from this request instead could race a turn already running on that lane. A grant
needs a row: an id that is only in `allowed_users` is a 404 until its first
message, because creating a row here would be an admission.

Owner-only on the `APIRouter` itself, the read included: who else can reach the
agent is the owner's business, and an allowlisted user asking for the roster is
asking a question about other people.

What the browser cannot do here and deliberately does not pretend to: look a
@username up. The Bot API resolves an id to a name, never the other way round, so
an invitation is a number a person sends you, not a handle you type.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from claude_hermes.core.auth import require_owner
from claude_hermes.core.exceptions import ApplicationException as ApiError

from ..application.services import AccessControl
from ..domain.exceptions import OwnerImmutable, UnknownUser, UserExists
from .dependencies import get_acl
from .schemas import UserCreate, UserGrantsPatch, member_wire, members_wire

router = APIRouter(tags=["identity"], dependencies=[Depends(require_owner)])


def _user_id(value: object) -> int:
    """A Telegram user id out of a body or a path, or `400 bad_request`.

    `bool` is excluded on purpose: it is an `int` in Python, and `{"user_id": true}`
    would otherwise invite user 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ApiError(400, "bad_request", "user_id must be a Telegram user id")
    try:
        uid = int(value)
    except ValueError:
        raise ApiError(400, "bad_request", "user_id must be a Telegram user id") from None
    if uid <= 0:
        raise ApiError(400, "bad_request", "user_id must be a Telegram user id")
    return uid


@router.get("/api/users", operation_id="identity_list_users")
async def list_users(acl: AccessControl = Depends(get_acl)) -> JSONResponse:
    """Everyone with a row, owner first, plus every `allowed_users` id without one."""
    return JSONResponse({"users": members_wire(await acl.list_users(),
                                               await acl.allowlisted_ids(),
                                               await acl.grant_defaults())})


@router.post("/api/users", status_code=201, operation_id="identity_invite_user")
async def invite_user(body: UserCreate,
                      acl: AccessControl = Depends(get_acl)) -> JSONResponse:
    """Admit a Telegram id that has not been here yet. `409` if it already has a row."""
    uid = _user_id(body.user_id)
    try:
        user = await acl.invite(uid)
    except UserExists as e:
        raise ApiError(409, "user_exists", f"user {uid} is already on the list") from e
    except UnknownUser as e:
        raise ApiError(400, "bad_request", str(e)) from e
    return JSONResponse(member_wire(user, await acl.grant_defaults()), status_code=201)


@router.post("/api/users/{user_id}/disable", operation_id="identity_disable_user")
async def disable_user(user_id: str,
                       acl: AccessControl = Depends(get_acl)) -> JSONResponse:
    """Revoke access. The row, the threads and the workspace stay; only the door shuts."""
    return await _set_disabled(user_id, True, acl)


@router.post("/api/users/{user_id}/enable", operation_id="identity_enable_user")
async def enable_user(user_id: str,
                      acl: AccessControl = Depends(get_acl)) -> JSONResponse:
    """Give it back, to exactly what it was."""
    return await _set_disabled(user_id, False, acl)


@router.patch("/api/users/{user_id}", operation_id="identity_set_user_grants")
async def set_user_grants(user_id: str, body: UserGrantsPatch,
                          acl: AccessControl = Depends(get_acl)) -> JSONResponse:
    """Give or withdraw a shell and/or learning. Only the named grants are written.

    `400` for a body naming neither (a PATCH that changes nothing is a client bug,
    not a success), `404` for an id with no row — including an `allowed_users` id
    nobody has met yet, and no row is created — and `409` for the owner, whose
    lanes get everything by construction.
    """
    uid = _user_id(user_id)
    if body.shell is None and body.learning is None:
        raise ApiError(400, "bad_request", "name at least one of shell, learning")
    try:
        user = await acl.set_grants(uid, shell=body.shell, learning=body.learning)
    except OwnerImmutable as e:
        raise ApiError(409, "owner_immutable", str(e)) from e
    except UnknownUser as e:
        raise ApiError(404, "not_found", str(e)) from e
    return JSONResponse(member_wire(user, await acl.grant_defaults()))


async def _set_disabled(user_id: str, disabled: bool, acl: AccessControl) -> JSONResponse:
    uid = _user_id(user_id)
    try:
        user = await acl.set_disabled(uid, disabled)
    except OwnerImmutable as e:
        raise ApiError(409, "owner_immutable", str(e)) from e
    except UnknownUser as e:
        raise ApiError(404, "not_found", str(e)) from e
    return JSONResponse(member_wire(user, await acl.grant_defaults()))

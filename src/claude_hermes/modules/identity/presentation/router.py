"""The auth routes: two logins, the local session link, logout, me, the model
default, config.

Wave 2b turned `auth_routes(deps)` into a module-level `router: APIRouter`. The
bundle a closure used to capture is six named providers in `dependencies.py`; the two
login bodies arrive through `Depends(login_payload)` rather than a model, for the
reason that dependency's docstring gives; and `/api/me` reads
`Depends(current_user)` where it used to read `request.state`. Route paths, the
registration order, wire shapes and error `code` strings are unchanged and frozen.

`PUBLIC_PATHS` stays here and stays exported: `build_web_app` unions every router's
set into `CurrentUserMiddleware`, and identity owns these four — they must answer
before anyone holds a cookie.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, RedirectResponse, Response

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.config import Config
from claude_hermes.core.model_catalog import offered, validate

from ..application.services import AccessControl
from ..domain.exceptions import LoginError, UnknownUser
from ..domain.models import Profile
from ..domain.session_token import COOKIE, read_session, sign_session
from ..domain.telegram_proof import verify_login, verify_webapp_user
from .dependencies import (
    get_acl,
    get_bot_username,
    get_cfg,
    get_login_key,
    get_now,
    get_webapp_key,
    login_payload,
)
from .schemas import ModelDefaultPut, user_wire

# The paths that must answer before anyone holds a cookie. The app shell unions
# every router's set; identity owns these four.
PUBLIC_PATHS = frozenset({"/api/auth/telegram", "/api/auth/webapp", "/api/auth/session",
                          "/api/config"})
LOCAL_HOSTS = ("http://localhost", "http://127.0.0.1")

# The two model refusals, in the same `code` every channel uses.
_BAD = {"bad_model": "that model is not offered",
        "bad_effort": "that model does not take that effort"}

router = APIRouter(tags=["identity"])


def _offered_ids(cfg: Config) -> list[str]:
    """What a user may choose from — `config.yaml`'s `models:` plus the deploy
    default, in `core.model_catalog`'s own order. The same list the picker shows
    and the same one the lane's own resolver validates against: a personal
    default is not a private list."""
    return [info.id for info in offered(cfg.models, cfg.model,
                                        cfg.review_model or cfg.model)]


def is_local_origin(origin: str) -> bool:
    """A developer's own machine: the Login Widget cannot render there (Telegram
    never links `localhost`), so the session link is the way in."""
    return any(origin == h or origin.startswith(h + ":") for h in LOCAL_HOSTS)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    """The one API error shape, spelled out here rather than imported from the
    HTTP shell: a module may not import a composition root."""
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _cookie(response: Response, value: str, max_age: int, samesite: str = "lax") -> None:
    response.set_cookie(COOKIE, value, max_age=max_age, httponly=True, secure=True,
                        samesite=samesite, path="/")


async def _admit(uid: int, *, acl: AccessControl, cfg: Config, now: Callable[[], int],
                 samesite: str = "lax", profile: Profile | None = None) -> Response:
    """A verified Telegram identity becomes a session — or not. "web", not
    "private": chat_type is what lets AccessControl CLAIM an unowned deploy,
    and the public URL must never be where that happens — otherwise the first
    stranger to find the tunnel hostname owns the agent. Ownership is claimed
    in a Telegram DM; here only "allow" gets in.

    `profile` is the widget's own `first_name`/`last_name`/`username`, signed by
    Telegram in the very check string that got us here — so a browser login is a
    perfectly good place to learn somebody's name, and for a web-only user it is
    the only place. It reaches the `users` row through `check`; the response body
    is unchanged."""
    if await acl.check(uid, "web", profile) != "allow":
        return error_response(401, "login_failed", "login failed")
    max_age = cfg.web_session_days * 86_400
    resp = JSONResponse(user_wire(uid, await acl.is_owner(uid)))
    _cookie(resp, sign_session(uid, now() + max_age, cfg.web_secret), max_age, samesite)
    return resp


@router.post("/api/auth/telegram", operation_id="identity_telegram_login")
async def telegram_login(payload: Any = Depends(login_payload),
                         acl: AccessControl = Depends(get_acl),
                         cfg: Config = Depends(get_cfg),
                         now: Callable[[], int] = Depends(get_now),
                         login_key: bytes = Depends(get_login_key)) -> Response:
    try:
        # The payload exactly as it arrived: the check string is an HMAC over every
        # field Telegram signed, so a schema that coerced or dropped one would turn a
        # real Login Widget POST into a 401.
        uid = verify_login(payload, login_key, now())
    except (LoginError, ValueError, TypeError, AttributeError):
        # TypeError/AttributeError: a body that is JSON but not an object (`[1]`)
        # used to fail this way inside the verifier and answer 401, not 400.
        return error_response(401, "login_failed", "login failed")
    return await _admit(uid, acl=acl, cfg=cfg, now=now,
                        profile=Profile.of(payload))


@router.post("/api/auth/webapp", operation_id="identity_webapp_login")
async def webapp_login(payload: Any = Depends(login_payload),
                       acl: AccessControl = Depends(get_acl),
                       cfg: Config = Depends(get_cfg),
                       now: Callable[[], int] = Depends(get_now),
                       webapp_key: bytes = Depends(get_webapp_key)) -> Response:
    """The Mini App's way in: `{"init_data": Telegram.WebApp.initData}`. The
    cookie is SameSite=None because Telegram's web clients open a Mini App in
    an iframe under web.telegram.org, where a Lax cookie never travels; the
    native apps load the page top-level and are indifferent. CSRF is covered
    the same way for every session: a mutation needs the exact Origin.

    The `user` object comes back with the id (Wave 4b, D10): it carries the same
    `first_name`/`last_name`/`username` the widget does, signed inside the same
    check string, and for a Mini-App-only user this is the only place their
    `users` row will ever learn a name."""
    if not webapp_key:
        return error_response(401, "login_failed", "login failed")
    try:
        uid, user = verify_webapp_user(payload.get("init_data"), webapp_key, now())
    except (LoginError, ValueError, AttributeError, TypeError):
        return error_response(401, "login_failed", "login failed")
    return await _admit(uid, acl=acl, cfg=cfg, now=now, samesite="none",
                        profile=Profile.of(user))


@router.get("/api/auth/session", operation_id="identity_session_link")
async def session_link(t: str = "", cfg: Config = Depends(get_cfg),
                       now: Callable[[], int] = Depends(get_now)) -> Response:
    """Local development only: a signed session token pasted into the
    address bar becomes the HttpOnly cookie, and the browser lands in the
    chat. The token is minted from the same secret the cookie is checked
    against, so this grants nothing the token holder did not already have;
    on any non-local origin the route does not exist."""
    if not is_local_origin(cfg.web_public_origin):
        return error_response(404, "not_found", "no such endpoint")
    token = t
    moment = now()
    if read_session(token, cfg.web_secret, moment) is None:
        return error_response(401, "login_failed", "bad or expired token")
    resp = RedirectResponse("/", status_code=303)
    _cookie(resp, token, int(token.split(".")[1]) - moment)
    return resp


@router.post("/api/auth/logout", operation_id="identity_logout", status_code=204)
async def logout() -> Response:
    resp = Response(status_code=204)
    _cookie(resp, "", 0)
    return resp


@router.get("/api/me", operation_id="identity_get_me")
async def me(user: CurrentUser = Depends(current_user),
             acl: AccessControl = Depends(get_acl)) -> JSONResponse:
    """`{"user_id", "role"}` plus the two display fields the `users` row carries.

    An empty `Profile()` when there is no row — the two keys are part of this
    route's shape, so they are never conditionally absent, and `""` is the
    documented answer for "we have never been told". The cookie, not the row, is
    still what says who this is: a deploy that lost its table must not log
    everybody out.
    """
    row = await acl.user(user.user_id)
    profile = (Profile(row.display_name, row.username) if row is not None
               else Profile())
    # A second, narrow read rather than two more fields on `User`: `User` is a
    # frozen snapshot the workspace resolver caches per process, and the model
    # resolver wants these two columns on every turn of every private lane
    # without the rest of the row.
    return JSONResponse(user_wire(user.user_id, user.is_owner, profile,
                                  await acl.default_of(user.user_id)))


@router.put("/api/me/model-default", operation_id="identity_set_model_default")
async def set_model_default(body: ModelDefaultPut,
                            user: CurrentUser = Depends(current_user),
                            acl: AccessControl = Depends(get_acl),
                            cfg: Config = Depends(get_cfg)) -> Response:
    """The model this person's NEW conversations start on — `model: null` clears it.

    Open to every admitted user, not just the owner: it is a preference about
    their own lanes, from the same offered list the owner picks from. It never
    moves a conversation that is already running — a live session keeps its own
    pair until `/new` or a reset, because that session's cached prefix is scoped
    to the model it started on (P1). Shared lanes (a group, a forum topic, a
    `cron:` job) never read it at all.
    """
    model = str(body.model) if body.model is not None else None
    effort = str(body.effort) if body.effort is not None else None
    try:
        validate(model, effort, _offered_ids(cfg))
    except ValueError as e:                     # "bad_model" | "bad_effort"
        code = str(e)
        return error_response(400, code, _BAD.get(code, code))
    try:
        pair = await acl.set_default(user.user_id, model, effort)
    except UnknownUser as e:
        return error_response(404, "not_found", str(e))
    return JSONResponse({"default_model": pair[0], "default_effort": pair[1]})


@router.get("/api/config", operation_id="identity_get_config")
async def config(bot_username: str = Depends(get_bot_username)) -> JSONResponse:
    """What the login page needs before anyone is logged in. The bot username
    is public (it is in the widget's own markup), and serving it from here
    instead of baking it into the bundle keeps `docker build` ignorant of
    which bot a deployment runs."""
    return JSONResponse({"bot_username": bot_username})

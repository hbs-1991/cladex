"""What identity's six routes ask for, and where it comes from.

One provider per collaborator, built **once at module scope** out of
`core.di.provide` — FastAPI keys `dependency_overrides` by the callable object, so a
provider rebuilt per request could never be overridden. A handler writes
`acl: AccessControl = Depends(get_acl)` and never sees the bundle; a test swaps the
access policy with `app.dependency_overrides[get_acl]` instead of building a second
app out of a second bundle.

Bundle attributes read by this module — the same six `auth_routes(deps)` read when it
closed over the bundle, and the whole list:

| provider | attribute | read by |
|---|---|---|
| `get_acl` | `acl` | both logins (`check`, `is_owner`); the users router (`list_users`, `allowlisted_ids`, `invite`, `set_disabled`, `grant_defaults`, `set_grants`) |
| `get_cfg` | `cfg` | `web_session_days`, `web_secret`, `web_public_origin` |
| `get_now` | `now` | the cookie's expiry and both freshness checks |
| `get_login_key` | `login_key` | the Login Widget proof |
| `get_webapp_key` | `webapp_key` | the Mini App proof |
| `get_bot_username` | `bot_username` | `GET /api/config` |

`login_payload` is the odd one out: it reads nothing off the bundle. It is here
because it is a `Depends` like the rest — see its own docstring for why the two login
bodies are not Pydantic models.

`webapp_key` and `bot_username` are `required=False`: `b""` is a documented answer
(no bot token → the Mini App door is shut, and the handler answers `401 login_failed`
itself), and a bundle that left either unset must reach that handler rather than a
503 the old closure would never have produced.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.requests import Request

from claude_hermes.core.config import Config
from claude_hermes.core.di import provide
from claude_hermes.core.exceptions import ApplicationException

from ..application.services import AccessControl

__all__ = ["get_acl", "get_bot_username", "get_cfg", "get_login_key", "get_now",
           "get_webapp_key", "login_payload"]

_acl = provide("acl")
_cfg = provide("cfg")
_now = provide("now")
_login_key = provide("login_key")
_webapp_key = provide("webapp_key", required=False)
_bot_username = provide("bot_username", required=False)


def get_acl(request: Request) -> AccessControl:
    """`deps.acl` — the access policy. Only `check` and `is_owner` are called, so any
    object with that pair satisfies it (the tests hand in a recording fake)."""
    return _acl(request)


def get_cfg(request: Request) -> Config:
    """`deps.cfg`. Read per request, never captured: `test_web_app.py` flips
    `web_public_origin` to a localhost value on a *built* app and expects
    `/api/auth/session` to start answering."""
    return _cfg(request)


def get_now(request: Request) -> Callable[[], int]:
    """`deps.now` — epoch seconds, injectable so a test can freeze the clock the
    cookie expiry and both proof freshness windows are measured against."""
    return _now(request)


def get_login_key(request: Request) -> bytes:
    """`deps.login_key` — sha256(bot token). Never the token itself: `main.run`
    derives the two keys and pops the token out of the environment."""
    return _login_key(request)


def get_webapp_key(request: Request) -> bytes:
    """`deps.webapp_key` — HMAC("WebAppData", bot token), a different key on purpose
    so neither proof replays as the other. `b""` shuts that door."""
    return _webapp_key(request)


def get_bot_username(request: Request) -> str:
    """`deps.bot_username` — public (it is in the Login Widget's own markup), served
    at runtime so `docker build` stays ignorant of which bot a deployment runs."""
    return _bot_username(request)


async def login_payload(request: Request) -> Any:
    """The body of the two login routes, parsed but not validated.

    **Deliberately not a Pydantic model.** Both handlers answer `401 login_failed`
    for anything that does not verify, and a body that is not even JSON — or is JSON
    but not an object (`[1]`) — is exactly that case: the old handlers caught
    `json.JSONDecodeError` around `await request.json()` and the `TypeError` /
    `AttributeError` a non-object payload raises inside the verifier, and answered
    401. A declared model would hand those to the shell's `RequestValidationError`
    handler instead, which answers `400 bad_request` — a status `client.ts` branches
    on differently. So the parse happens here, and a failure is spelled as the same
    401 the handlers spell.

    Whatever JSON arrived is returned untouched: the Login Widget's check string is
    an HMAC over *all* the fields Telegram signed, so coercing or dropping one turns
    a valid login into a 401.
    """
    try:
        return await request.json()
    except Exception:
        raise ApplicationException(401, "login_failed", "login failed") from None

"""Current-user middleware and the two dependencies every router asks it for.

The auth half of `web/app.ApiMiddleware`, lifted into core so that no module owns
the request pipeline. It knows nothing about cookies, secrets or the meta table:
the composition root binds those into the two callables it takes. The transport
half (Origin and content-type policy) stays in the app shell — it is not identity.

The middleware puts `user_id` / `is_owner` on `request.state`; `current_user` is how a
handler reads them (`user: CurrentUser = Depends(current_user)`) and `require_owner`
is the owner gate every `if not request.state.is_owner` used to be. A router that
needs a *different* refusal message than "owner only" writes its own two-line
dependency over `current_user` — the message is part of that router's wire contract.

`CurrentUser` itself is defined in `core/contracts.py` and re-exported here: a domain
layer aliases it, and importing this module would drag Starlette into a layer the
"domain layers are pure" contract keeps free of it.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Container, Mapping

from fastapi import Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from .contracts import CurrentUser, Role
from .exceptions import Forbidden, Unauthorized

__all__ = ["Admitted", "CurrentUser", "CurrentUserMiddleware", "IsOwner", "ReadSession",
           "Role", "current_user", "require_owner"]

ReadSession = Callable[[Request], int | None]
IsOwner = Callable[[int], Awaitable[bool]]
Admitted = Callable[[int], Awaitable[bool]]


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class CurrentUserMiddleware(BaseHTTPMiddleware):
    """Guards `/api/*`: every path outside `public_paths` needs a readable session.

    `read_session(request) -> int | None` is injected whole (not a cookie name plus
    a secret) so core stays ignorant of how a session is proved — the shell binds
    the cookie name, the secret and the clock. `redirect_on_401` maps a path to a
    location for the requests that belong to a person's browser rather than a
    fetch(): the OAuth callback is a top-level navigation back from the provider,
    and a JSON error body is all the owner would see.

    `admitted(uid) -> bool` (Wave 4b, R-8) is asked on EVERY request, not at login.
    A cookie outlives the decision that issued it: a user taken off the allowlist
    or disabled keeps a valid signature for the rest of the cookie's life, and
    after 4b a logged-in user may write prompt text into a tree (`/api/skills`),
    so "was admitted once" is no longer enough. The shell binds it to the ACL's
    `check`; `None` — every pre-4b constructor call, and the tests that build the
    middleware bare — means "not checked", which is what it was.
    """

    def __init__(self, app, *, read_session: ReadSession, is_owner: IsOwner,
                 public_paths: Container[str] = frozenset(),
                 redirect_on_401: Mapping[str, str] | None = None,
                 admitted: Admitted | None = None):
        super().__init__(app)
        self._read_session = read_session
        self._is_owner = is_owner
        self._admitted = admitted
        self._public = public_paths
        self._redirect = dict(redirect_on_401 or {})

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/") or path in self._public:
            return await call_next(request)
        uid = self._read_session(request)
        if uid is None or (self._admitted is not None
                           and not await self._admitted(uid)):
            # A no-longer-admitted user gets the same answer as no cookie at all,
            # redirect included: the browser's next move is the login page either
            # way, and a distinct body would tell a removed user they are removed.
            if path in self._redirect:
                return RedirectResponse(self._redirect[path], status_code=302)
            return _error(401, "unauthorized", "login required")
        request.state.user_id = uid
        request.state.is_owner = await self._is_owner(uid)
        return await call_next(request)


def current_user(request: Request) -> CurrentUser:
    """Who is asking, from what `CurrentUserMiddleware` left on `request.state`.

    The `Unauthorized` is unreachable on a mounted `/api/*` route — the middleware
    answered 401 long before the handler — and is here for the path that is not: a
    public route, or a router mounted without the middleware, would otherwise read a
    bare `State` and 500. The message is the middleware's own, so the two doors give
    the browser the same answer.
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise Unauthorized("login required")
    return CurrentUser(int(user_id), bool(getattr(request.state, "is_owner", False)))


def require_owner(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    """The owner, or 403 `forbidden`. An allowlisted user may chat; only the owner
    reads the memory, edits a skill or installs an extension."""
    if not user.is_owner:
        raise Forbidden("owner only")
    return user

"""What the threads router asks for, and where it comes from.

One provider per collaborator, built **once at module scope** out of
`core.di.provide` — FastAPI keys `dependency_overrides` by the callable object, so a
provider rebuilt per request could never be overridden. A handler writes
`threads: ThreadService = Depends(get_threads)` and never sees the bundle.

Bundle attributes read by this module: **`threads`** (`ThreadService`) and
**`lane_models`** (`SessionService` — which model a lane runs, and the one writer
of that choice). `threads` was the whole list when `routes(deps)` read
`deps.threads` out of the closure; `lane_models` is the model switcher's addition
and the composition root wires it beside `threads`.

`owned_thread` is the second dependency and the one line every per-thread route used
to start with: turn the request's authenticated identity into "the thread, or 404".
It stays a *dependency* rather than a service method because the question is about
*this request* — `ThreadService` knows nothing about who is asking until it is told.
"""
from __future__ import annotations

from fastapi import Depends
from starlette.requests import Request

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.di import provide

from ..application.services import SessionService, ThreadService

__all__ = ["get_lane_models", "get_threads", "owned_thread"]

_threads = provide("threads")
_lane_models = provide("lane_models")


def get_threads(request: Request) -> ThreadService:
    """`deps.threads` — thread metadata, the visibility rule and the paging loop.

    The shell does not mount this router when `deps.threads` is `None`, so the 503
    guard inside `provide` is unreachable in a correctly assembled app.
    """
    return _threads(request)


def get_lane_models(request: Request) -> SessionService:
    """`deps.lane_models` — the model gatherer, and the one writer of a lane's pair.

    Required: this router is mounted when `deps.threads` is set, and a deployment
    that has threads but cannot say which model they run is a wiring mistake, not
    a configuration. Every route that reads it declares `Depends(owned_thread)`
    FIRST, so a lane the caller may not see is still a 404 — a 503 there would
    tell a stranger the lane exists.
    """
    return _lane_models(request)


async def owned_thread(id: str, threads: ThreadService = Depends(get_threads),
                       user: CurrentUser = Depends(current_user)) -> dict:
    """The thread `{id:path}` names, or `NotFound` — never "it exists but is not
    yours". A 403 would tell a stranger that a lane with that key exists.

    `id` is the path parameter: FastAPI resolves a sub-dependency's parameters
    against the route's own path, so the same dependency serves
    `/api/threads/{id:path}` and `/api/threads/{id:path}/archive`.
    """
    return await threads.get_visible(id, user.user_id, user.is_owner)

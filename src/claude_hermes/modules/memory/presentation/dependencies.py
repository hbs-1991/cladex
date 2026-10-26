"""What the wiki router asks for, and where it comes from.

One provider per collaborator, built once at module scope out of `core.di.provide`
(FastAPI keys `dependency_overrides` by the callable object, so a provider rebuilt per
request could not be overridden). A handler writes
`service: WikiGraphService = Depends(get_graph)` and never sees the bundle.

Bundle attributes read by this module: **`wiki_root`** (`Path`). That is the whole
list — it was the whole list when `routes(deps)` closed over `deps`, and the contract
did not change when the closure became a dependency.
"""
from __future__ import annotations

from starlette.requests import Request

from claude_hermes.core.di import provide

from ..application.services.graph import WikiGraphService

__all__ = ["get_graph"]

_wiki_root = provide("wiki_root")


def get_graph(request: Request) -> WikiGraphService:
    """The read side of `agent_home/wiki`, rooted at `deps.wiki_root`.

    Built per request rather than once per app: the service owns no state beyond the
    root path, and a fresh one is what lets a test point the same app at another tree.
    The shell does not mount this router when `wiki_root` is `None`, so the provider's
    503 guard is unreachable in a correctly assembled app.

    **That root is the DEPLOY OWNER's, deliberately, and stays so after Wave 4a.**
    Both wiki routes are `require_owner`, so the only person who can reach this
    provider is the person whose tree it names — resolving the requester's
    workspace here would be a per-user lookup for a route only one user can call.
    A turn is the other story: it reads its LANE owner's wiki, built per turn from
    `TurnContext.workspace` (see `turns`' `wiki_for`). A non-owner's wiki has no
    route in 4a and no curator; that is the 4b debt in this module's README.
    """
    return WikiGraphService(_wiki_root(request))

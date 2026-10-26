"""Wiki router: the agent's declarative memory, read-only, shaped as a graph.

Two endpoints and no writes. `agent_home/wiki` is the one place the model owns
every byte — a browser that could edit it would be a second author of a file whose
only author is supposed to be the agent, and the etag dance the skills router needs
would follow. So: GET, GET, nothing else.

**Both routes are owner-only.** An allowlisted user may chat with the agent; the
wiki is where it writes down who the owner is, what they eat and what they are
working on, so it stays with the owner the way secrets do. The gate is
`core.auth.require_owner`, which refuses with the same `403 forbidden` / "owner only"
the hand-written `owner_only(request)` answered.

Dependencies: `get_graph` (a `WikiGraphService` over `deps.wiki_root`) — see
`dependencies.py`. The **shell** decides whether this router is mounted at all: a
deployment without a wiki (`deps.wiki_root is None`) registers no wiki routes and the
`/api/*` catch-all answers the 404, exactly as when `routes(deps)` returned `[]`.
"""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse

from claude_hermes.core.auth import require_owner

from ..application.services.graph import WikiGraphService
from .dependencies import get_graph
from .schemas import graph_wire, page_wire

router = APIRouter(tags=["memory"])


@router.get("/api/wiki/graph", operation_id="memory_get_graph",
            dependencies=[Depends(require_owner)])
async def graph(service: WikiGraphService = Depends(get_graph)) -> JSONResponse:
    """The whole wiki as pages, namespaces and the links that miss a page."""
    return JSONResponse(graph_wire(service.graph()))


@router.get("/api/wiki/pages/{path:path}", operation_id="memory_get_page",
            dependencies=[Depends(require_owner)])
async def page(path: str, service: WikiGraphService = Depends(get_graph)) -> JSONResponse:
    """One page's node, its bytes and their etag. `resolve()` proves containment."""
    detail, content = service.page(path)
    return JSONResponse(page_wire(detail, content, hashlib.sha256(content).hexdigest()))

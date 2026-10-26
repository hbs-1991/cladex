"""What the files router asks for, and where it comes from.

One typed provider per collaborator, built once at module scope out of
`core.di.provide` (FastAPI keys `dependency_overrides` by the callable *object*, so
a provider rebuilt per request could not be overridden). A handler writes
`files: FileService = Depends(get_files)` and never sees the bundle.

Bundle attributes read by this module: **`file_store`** (the `FileService`),
**`visibility`** (the `ThreadVisibility` port) and **`owned`** (the lane-guard
callable a bundle may carry instead). The upload ceiling is the service's own,
built by the composition root from `web_upload_max_mb`; no provider reads it.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends
from starlette.requests import Request

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.di import provide

from ..application.service import FileService

__all__ = ["LaneGuard", "get_files", "get_lane", "lane_owner_of"]


def lane_owner_of(thread) -> int | None:
    """Whose lane this thread row is — the value `files.owner_id` is stamped with.

    Read off the row rather than re-derived from the key here: `threads.owner_user_id`
    is already `lane_owner(...)` at every 4a write site, and re-applying the rule in
    the router would need a deploy owner this layer has no business knowing. `None`
    for a row from before 4a that never got an owner, which writes NULL rather than
    guessing.
    """
    return (thread or {}).get("owner_user_id")

_file_store = provide("file_store")
# Both may legitimately be absent: `visibility` is the port, `owned` the bare
# callable, and a bundle carries one or the other.
_visibility = provide("visibility", required=False)
_owned = provide("owned", required=False)


def get_files(request: Request) -> FileService:
    """`deps.file_store` — the `FileService` both directions go through.

    The shell does not mount this router when `file_store` is `None`, so `provide`'s
    503 guard is unreachable in a correctly assembled app.
    """
    return _file_store(request)


class LaneGuard:
    """The lane visibility check, already bound to who is asking.

    Not a plain dependency because the thing it checks — the lane — arrives in the
    form or the file row, not in the request line: the handler calls
    `await lane(session_key)`. It raises `NotFound`, which is why a file id can never
    confirm the existence of someone else's lane.
    """

    def __init__(self, visibility: Any, owned: Any, request: Request,
                 user: CurrentUser):
        self._visibility = visibility
        self._owned = owned
        self._request = request
        self._user = user

    async def __call__(self, session_key: str) -> dict:
        if self._visibility is not None:
            return await self._visibility.get_visible(session_key, self._user.user_id,
                                                      self._user.is_owner)
        return await self._owned(self._request, session_key)


def get_lane(request: Request,
             user: CurrentUser = Depends(current_user)) -> LaneGuard:
    """`deps.visibility` (the `ThreadVisibility` port), or `deps.owned` where a bundle
    carries only the callable — one guard either way."""
    return LaneGuard(_visibility(request), _owned(request), request, user)

"""What the skills router asks for, and where it comes from.

One typed provider per collaborator, built once at module scope out of
`core.di.provide` (FastAPI keys `dependency_overrides` by the callable *object*, so a
provider rebuilt per request could not be overridden). A handler writes
`library: SkillLibraryService = Depends(get_library)` and never sees the bundle.

**Since Wave 4b every provider is the REQUESTING user's** (D6). The bundle carries
`learning`, a `LearningScopes`, and `get_scope` resolves the `CurrentUser`'s
`LearningScope` through it — `library`, `usage`, `skills_dir` and `logs_dir` are
then read off that scope, never off a bundle-wide field. The
scope IS the authorization: no route can name another user's library, so the
mutations no longer carry `require_owner`. In 4a `GET /api/skills` served the
owner's library to any logged-in user; this is what closes it.

Bundle attributes read by this module: **`learning`**, **`cfg`** (only
`backup_keep`) and **`runner`** (only `reset_owned`).

The `library is None -> no routes` condition that used to live in `routes(deps)` is
the **shell's** now: mounting is the shell's decision, so a module states what it
needs rather than whether it exists.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends
from starlette.requests import Request

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.di import provide
from claude_hermes.core.exceptions import Unauthorized

from ..application.services.library import SkillLibraryService

__all__ = ["get_backup_keep", "get_invalidate", "get_library", "get_logs_dir",
           "get_scope", "get_skills_dir", "get_usage"]

_learning = provide("learning")
_cfg = provide("cfg")
_runner = provide("runner")


async def get_scope(request: Request,
                    user: CurrentUser = Depends(current_user)) -> Any:
    """`deps.learning.for_user(user.user_id)` — the caller's own `LearningScope`.

    Async because the scopes registry resolves the deploy owner's id and builds
    the scope lazily; the result is cached per user on the registry, so this is
    one dict lookup on every request after the first.
    """
    try:
        return await _learning(request).for_user(user.user_id)
    except LookupError as exc:
        # A cookie whose `users` row is gone (deleted by an operator between
        # the middleware's check and here, or never materialised): the resolver
        # raises identity's `UnknownUser` rather than guessing the owner's tree,
        # and the answer to the browser is the same as no session at all.
        raise Unauthorized("login required") from exc


def get_library(scope: Any = Depends(get_scope)) -> SkillLibraryService:
    """`scope.library` — the same service `skill_admin` drives for this user, so
    the browser and the chat tool cannot drift on what "stale", "bundled" or
    "archived" mean."""
    return scope.library


def get_usage(scope: Any = Depends(get_scope)) -> Any:
    """`scope.sidecar` — the `UsageSidecar` the turn loop bumps for this user, so
    a browser read sees a fresh count."""
    return scope.sidecar


def get_skills_dir(scope: Any = Depends(get_scope)) -> Path:
    """`scope.skills_dir` — the snapshot *source* for a save."""
    return scope.skills_dir


def get_logs_dir(scope: Any = Depends(get_scope)) -> Path:
    """`scope.logs_dir` — where this user's `skill_edits` snapshot store lives."""
    return scope.logs_dir


def get_backup_keep(request: Request) -> int:
    """`deps.cfg.backup_keep` — how many pre-edit snapshots the store keeps.

    The number, not the whole `Config`: it is the only field this router reads, and a
    provider that handed over `cfg` would invite the next one.
    """
    return _cfg(request).backup_keep


def get_invalidate(request: Request, scope: Any = Depends(get_scope)) -> Callable:
    """The prompt-cache reset for THIS user's lanes, awaited after every mutation
    that changes text the model sees. Pinning is not one of them.

    `runner.reset_owned(scope.user_id)`, not `reset_all()` (B-4): after 4b a
    user's skills reach nobody else's prefix, and evicting every other user's
    warm cache for one person's edit would be the NFR-1 regression the per-user
    prefix was meant to avoid. The deploy owner also sweeps the NULL-owner rows —
    lanes bound before the deploy was claimed are theirs.
    """
    runner = _runner(request)

    async def invalidate() -> None:
        await runner.reset_owned(scope.user_id, include_unowned=scope.is_owner)

    return invalidate

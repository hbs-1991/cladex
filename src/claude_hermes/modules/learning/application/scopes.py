"""A learning SCOPE per workspace, and the registry that hands them out.

Until Wave 4b the learning loop was one set of objects over one tree: the deploy
owner's sidecar, library, nudge counters, review gate and curator. Every user has
a tree since 4a, so every user gets a set — a `LearningScope` — built lazily the
first time somebody asks for it and cached for the life of the process.

The registry, not the bundle, is what a consumer holds. `LearningLoop.after_turn`
asks for the lane owner's scope, the `learning_review` task asks for the scope the
nudge was dispatched with, the curator sweep walks `user_ids()`, and the
`skill_admin` tool and the skills API ask for the requesting user's. None of them
may hold a scope across calls, because the one they want is decided per call.

Two rules are load-bearing here and easy to lose:

* **`for_user(0)` IS the owner** (advisor R-6). `0` is what every pre-4b caller
  and every 4a-queued payload carries, and the resolved owner id is the cache
  key, so `for_user(0)` and `for_user(<owner>)` return one object — two sidecars
  over one `.usage.json` would each overwrite the other's counts. On an
  unclaimed deploy (owner still `0`) the scope is built over `Workspace.owner`
  and NOT cached, for the same reason the workspace resolver does not cache a
  zero owner: the claim happens later and the cache would outlive it.
* **`user_ids()` honours each user's learning grant.** The owner always comes
  first — their pass never depends on a lookup — and every other non-disabled
  user follows, deduplicated (the owner is a `users` row too), iff
  `learning_of(uid)` says their effective grant is on (the per-user column,
  else the `users_learning_enabled` default; identity resolves it, this module
  only asks). A lookup that raises for one id leaves THAT id out of this sweep
  and is logged: one bad row must not cost the owner's pass or anybody else's.
  `users` is `AccessControl.active_user_ids` — no limit (R-5): `list_users(
  limit=100)` would have silently dropped user 101 from every curator sweep.

`observes(user_id)` is the same grant asked for one id, by the `learning_review`
task: a review queued while the grant was on must not run a fork after the owner
withdrew it. Unlike the sweep it lets a failed lookup raise — that caller has a
gate to hand back and decides what a failure means there.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from claude_hermes.core.workspace import Workspace

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LearningScope:
    """One user's learning objects, over one workspace. Frozen: a scope is cached
    per process and shared by every caller that resolves the same user."""

    user_id: int
    workspace: Workspace
    is_owner: bool            # workspace.root == workspace.home
    skills_dir: Path
    logs_dir: Path
    meta: Any                 # ScopedMeta (D2) — THE store nudges/gate/curator/admin read
    sidecar: Any              # UsageSidecar
    archive: Any              # SkillArchive
    library: Any              # SkillLibraryService
    admin: Any                # SkillAdminService (meta=scope.meta, B-6)
    nudges: Any               # NudgeTracker (meta=scope.meta)
    gate: Any                 # MetaReviewGate (meta=scope.meta)
    review: Any               # ReviewService
    curator: Any              # CuratorService (meta=scope.meta, logs_dir=scope.logs_dir)


class LearningScopes:
    """The registry. `build(user_id, workspace) -> LearningScope` is
    `wiring.build_scopes`' closure; the registry only decides WHICH workspace and
    whether the answer may be cached."""

    def __init__(self, *, workspaces: Any,
                 build: Callable[[int, Workspace], LearningScope],
                 users: Callable[[], Awaitable[list[int]]],
                 learning_of: Callable[[int], Awaitable[bool]]) -> None:
        self._workspaces = workspaces
        self._build = build
        self._users = users
        # "Is learning on for this user?" — identity's effective grant, injected
        # as a plain callable (modules are independent) and asked at call time,
        # never cached: the owner flips it from Settings → Users with no restart,
        # and the agent and the worker must both see the flip on the next ask.
        self._learning_of = learning_of
        self._cache: dict[int, LearningScope] = {}

    async def owner(self) -> LearningScope:
        return await self.for_user(0)

    async def for_user(self, user_id: int) -> LearningScope:
        uid = int(user_id or 0)
        if not uid:
            uid = int(await self._workspaces.owner_id() or 0)
            if not uid:
                # Unclaimed deploy: the one tree there is, uncached (see above).
                return self._build(0, Workspace.owner(self._workspaces.home))
        cached = self._cache.get(uid)
        if cached is not None:
            return cached
        workspace = await self._workspaces.for_user(uid)
        scope = self._build(uid, workspace)
        # Cached AFTER a successful build, like the resolver: a scope over a tree
        # that failed to seed must not be what every later turn trusts.
        self._cache[uid] = scope
        return scope

    async def _owner_id(self) -> int:
        """The resolved deploy owner, `0` on an unclaimed deploy."""
        return int(await self._workspaces.owner_id() or 0)

    async def user_ids(self) -> list[int]:
        """`[owner, *granted others]`, deduplicated.

        The owner is first and unconditional; `0` when the deploy is unclaimed,
        so the sweep still runs the one tree 4a swept — `for_user(0)` builds the
        owner scope over it. Every other id is asked `learning_of` one at a
        time: a lookup that raises is logged with the id and that user sits
        this sweep out (the next tick asks again), instead of aborting a sweep
        whose first pass is the owner's.
        """
        owner = await self._owner_id()
        ids = [owner]
        for uid in await self._users():
            uid = int(uid)
            if not uid or uid in ids:
                continue
            try:
                granted = await self._learning_of(uid)
            except Exception:
                log.exception("learning: grant lookup failed for user %s; "
                              "leaving them out of this sweep", uid)
                continue
            if granted:
                ids.append(uid)
        return ids

    async def observes(self, user_id: int) -> bool:
        """Is learning on for `user_id` right now?

        The owner (and `0`, which IS the owner — see `for_user`) is always
        observed and never costs a lookup; anybody else is their effective
        learning grant at the moment of asking. A lookup that raises is NOT
        turned into an answer here: the caller holds a review gate and decides
        what a failure means for it.
        """
        uid = int(user_id or 0)
        if not uid:
            return True
        if uid == await self._owner_id():
            return True
        return bool(await self._learning_of(uid))

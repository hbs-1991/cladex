"""The learning module's composition: one `LearningScope` per workspace, and the
registry that hands them out.

It sits outside the four layers on purpose, like `extensions/wiring.py`: the
scope holds application services AND the infrastructure adapters that satisfy
their ports (`UsageSidecar`, `SkillArchive`, `MetaReviewGate`, `ScopedMeta`,
`ReportWriter`), and something has to introduce them. `main.build_app()` calls
`build_scopes` once and everything else — the loop, the `learning_review` task,
the curator sweep, the `skill_admin` tool, the skills API — goes through the
registry it returns.

What crosses a module boundary arrives as an argument, never as an import:
`sessions` (the sessions module's row the review fork resumes), `activity_for`
(the curator's quiet-hours check over sessions' messages), `wiki_lint_for`
(memory's lint pass over that workspace's wiki), `users` (identity's
non-disabled ids) and `learning_of` (identity's effective per-user learning
grant). The module contract says modules are independent, and this file is a
module file.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from claude_hermes.core.workspace import Workspace

from .application.scopes import LearningScope, LearningScopes
from .application.services.consolidation import ConsolidationService
from .application.services.curator import CuratorService
from .application.services.library import SkillLibraryService
from .application.services.nudges import NudgeTracker
from .application.services.review import ReviewService
from .application.services.skill_admin import SkillAdminService
from .infrastructure.report import ReportWriter
from .infrastructure.review_gate import MetaReviewGate
from .infrastructure.scoped_meta import ScopedMeta
from .infrastructure.skill_archive import SkillArchive
from .infrastructure.skill_repository import SkillRepository
from .infrastructure.usage_sidecar import UsageSidecar

__all__ = ["build_scope", "build_scopes", "meta_suffix"]


def meta_suffix(user_id: int, workspace: Workspace) -> str:
    """`""` for the owner, `":<id>"` for everybody else (D2).

    Decided by the WORKSPACE, not the id: the owner is whoever's `root` is
    `home`, and that is the tree whose seven legacy `meta` rows must stay
    byte-identical — an unclaimed deploy (id 0) included.
    """
    return "" if workspace.root == workspace.home else f":{user_id}"


def build_scope(cfg: Any, user_id: int, workspace: Workspace, *, meta: Any,
                sessions: Any, activity_for: Callable[[int], Any],
                wiki_lint_for: Callable[[Workspace, int], Any], fork: Any,
                snapshots: Any, review_slot: Any = None) -> LearningScope:
    """One user's learning objects over one tree.

    One `ScopedMeta` per scope, handed to all four readers (advisor B-6): the
    nudge tracker, the review gate, the admin tool and the curator all read the
    same suffixed rows, and there is no second instance to disagree with.
    """
    scoped = ScopedMeta(meta, meta_suffix(user_id, workspace))
    skills_dir = workspace.skills_dir
    logs_dir = workspace.logs_dir
    sidecar = UsageSidecar(skills_dir)
    archive = SkillArchive(skills_dir, sidecar)
    library = SkillLibraryService(SkillRepository(skills_dir), sidecar, archive)
    # This user's review lease. What it no longer bounds is concurrency — that
    # is `review_slot`, shared by every scope (D3).
    gate = MetaReviewGate(scoped, timeout_seconds=cfg.review_timeout_seconds)
    review = ReviewService(sessions=sessions, usage=sidecar, fork=fork,
                           skills_dir=skills_dir, wiki_root=workspace.wiki_root,
                           gate=gate, workspace=workspace, slot=review_slot,
                           # the fork flushes the pages it rewrote to the wiki
                           # index under THIS user's id; 0 would mean "do not"
                           owner_id=user_id)
    nudges = NudgeTracker(scoped, cfg)
    admin = SkillAdminService(
        library=library, usage=sidecar, archive=archive, meta=scoped,
        # `SnapshotStorePort` is satisfied by the module itself: a Protocol does
        # not care whether the methods hang off an object or a namespace.
        snapshots=snapshots, skills_dir=skills_dir, logs_dir=logs_dir,
        backup_keep=cfg.backup_keep)
    curator = CuratorService(
        cfg=cfg, logs_dir=logs_dir, skills_dir=skills_dir,
        wiki_root=workspace.wiki_root, usage=sidecar, meta=scoped,
        # "Has THIS user talked recently?" — their own quiet hours, so a
        # neighbour chatting at 3am holds nobody else's pass off (B-7).
        activity=activity_for(user_id),
        snapshots=snapshots, archive=archive,
        consolidation=ConsolidationService(skills_dir=skills_dir, usage=sidecar,
                                           archive=archive, fork=fork,
                                           workspace=workspace),
        reports=ReportWriter(logs_dir),
        # memory owns the wiki, so the lint pass arrives as a port over THIS tree
        # rather than as a fork this module builds.
        wiki_lint=wiki_lint_for(workspace, user_id),
        # the review lease: a curator write must not race a review fork,
        # whichever process that fork is running in.
        gate=gate)
    return LearningScope(
        user_id=user_id, workspace=workspace,
        is_owner=workspace.root == workspace.home,
        skills_dir=skills_dir, logs_dir=logs_dir, meta=scoped, sidecar=sidecar,
        archive=archive, library=library, admin=admin, nudges=nudges, gate=gate,
        review=review, curator=curator,
    )


def build_scopes(cfg: Any, *, workspaces: Any, meta: Any, sessions: Any,
                 activity_for: Callable[[int], Any],
                 wiki_lint_for: Callable[[Workspace, int], Any], fork: Any,
                 snapshots: Any, users: Callable[[], Awaitable[list[int]]],
                 learning_of: Callable[[int], Awaitable[bool]],
                 review_slot: Any = None) -> LearningScopes:
    """The registry `main.build_app()` holds.

    `learning_of(user_id)` is the user's EFFECTIVE learning grant — their own
    `users.learning_enabled`, else the `users_learning_enabled` default — as
    identity resolves it. This module never reads the column or the config key:
    the rule lives once, in identity, and arrives here as a callable asked per
    sweep and per queued review, so a flip in Settings → Users reaches the
    agent and the worker alike with no restart. It is required, not defaulted:
    a composition that forgot it should fail at boot, not quietly stop (or
    start) learning over other people's conversations.
    """

    def build(user_id: int, workspace: Workspace) -> LearningScope:
        return build_scope(cfg, user_id, workspace, meta=meta, sessions=sessions,
                           activity_for=activity_for, wiki_lint_for=wiki_lint_for,
                           fork=fork, snapshots=snapshots, review_slot=review_slot)

    return LearningScopes(workspaces=workspaces, build=build, users=users,
                          learning_of=learning_of)

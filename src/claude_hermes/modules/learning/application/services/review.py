"""The out-of-band review pass: fork the finished turn, let it write what it learnt.

Moved from `learning/review.py`. Two invariants survive the move unchanged:

* The fork **resumes the turn's own SDK session** with the byte-identical
  `system_append`, so it reads the same conversation and hits the same warm cache.
* Provenance is claimed only for skill directories the fork's own write guard
  recorded. A `/learn` running in the foreground at the same moment creates a
  directory too, and claiming that one would hand the curator permission to age
  away something the user made by hand.

Wave 3a added a third: **this is where the review gate is marked**, not in the
loop that dispatched it. The claim has to happen in the process actually running
the fork, because in redis mode the loop has already returned by then — it put a
name on a queue and went back to answering the user.

Wave 4b adds a fourth: **one review at a time, deploy-wide, still.** The gate is
per user now (one lease per scope), so it no longer bounds concurrency; the
`ReviewSlot` does — claimed AFTER the gate, and a held slot releases the gate
and returns `None`. A dropped review is the designed outcome, the same one a
nudge gets when it finds the gate claimed, and the counters fire again next
interval. The fork itself runs in `workspace` — this scope's tree.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from claude_hermes.modules.learning.application.ports import (
    ForkRunner,
    ReviewGate,
    ReviewSlot,
    SessionSnapshotPort,
    SkillUsagePort,
)
from claude_hermes.modules.learning.domain.prompts import build_review_prompt

log = structlog.get_logger(__name__)


class ReviewService:
    """One review pass over one lane's last turn, in one scope's workspace."""

    def __init__(self, *, sessions: SessionSnapshotPort, usage: SkillUsagePort,
                 fork: ForkRunner, skills_dir: Path, wiki_root: Path,
                 gate: ReviewGate | None = None, workspace: Any = None,
                 slot: ReviewSlot | None = None, owner_id: int = 0):
        self._sessions = sessions
        self._usage = usage
        self._fork = fork
        self._skills_dir = skills_dir
        self._wiki_root = wiki_root
        self._gate = gate
        self._workspace = workspace
        self._slot = slot
        # This scope's user id. The fork writes the pages it touched into the
        # wiki index keyed `(owner_id, path)`, and the workspace alone does not
        # say whose tree it is — `users/<id>` is a directory name, not an
        # identity. `0` is the pre-index default and means "do not index".
        self._owner_id = owner_id

    @property
    def workspace(self) -> Any:
        """The tree this service's forks run in; `None` means the deploy owner's."""
        return self._workspace

    async def run(self, session_key: str, kinds: Iterable[str]) -> str | None:
        """`kinds` is an iterable, not a `set`: the task body hands over the
        `list[str]` that came off the queue, and the prompt builder wants a set."""
        kinds = set(kinds)
        # Claimed around the WHOLE pass, including the lookup that may find
        # nothing: a review that fails fast still has to keep the curator from
        # starting a pass behind it. Releasing in the `finally` is what keeps the
        # lease from sitting for its full `review_timeout_seconds` after a clean
        # end — the expiry is only there for the worker that never gets here.
        if self._gate is not None:
            await self._gate.claim()
        # The slot comes second: the gate is this user's single-flight rule, the
        # slot is everybody's concurrency bound. A slot held by another user's
        # review means THIS review does not happen — so the gate goes back at
        # once, or the curator would sit behind a lease for a pass that never
        # ran.
        # A Redis blip inside `acquire` must hand the gate back too: a lease
        # left behind would drop this user's nudges and block their curator for
        # the full `review_timeout_seconds`, for a review that never started —
        # the failed-enqueue shape `LearningLoop` already guards.
        try:
            acquired = self._slot is None or await self._slot.acquire()
        except Exception:
            if self._gate is not None:
                await self._gate.release()
            raise
        if not acquired:
            if self._gate is not None:
                await self._gate.release()
            log.info("review slot held by another pass; dropping %s for %s",
                     sorted(kinds), session_key)
            return None
        try:
            return await self._run(session_key, kinds)
        finally:
            if self._slot is not None:
                await self._slot.release()
            if self._gate is not None:
                await self._gate.release()

    async def _run(self, session_key: str, kinds: set[str]) -> str | None:
        row = await self._sessions.get(session_key)
        if row is None or not row["sdk_session_id"]:
            return None

        before = self._usage.list_skill_dirs()
        touched: set[str] = set()  # skill dirs THIS review wrote, via the guard

        try:
            return await self._fork.run_review(
                prompt=build_review_prompt(kinds),
                system_append=row["system_append"],
                resume=row["sdk_session_id"],
                skills_dir=self._skills_dir,
                wiki_root=self._wiki_root,
                touched=touched,
                workspace=self._workspace,
                owner_id=self._owner_id,
            )
        finally:
            # Mark only dirs the review itself wrote (guard-recorded), never dirs
            # that appeared concurrently (e.g. a foreground /learn) — those stay
            # user-owned. Runs even if the query failed mid-way.
            now_iso = datetime.now(timezone.utc).isoformat()
            for name in (touched - before) & self._usage.list_skill_dirs():
                self._usage.mark_created(name, "agent", now_iso)
                log.info("review created skill %s", name)

"""What learning needs from the outside, as consumer-side Protocols.

Nothing here imports a provider: a collaborator satisfies these structurally and
is handed in at composition (Wave 2 locked decision). The two that cross a module
boundary are `SessionSnapshotPort` (sessions owns the row the review fork resumes)
and `WikiLintPort` (memory owns the wiki) — learning must never read the sessions
table or the wiki directory itself, and these two ports are how that stays true.

`SkillLibraryPort` points the other way: it is the shape learning PROMISES, so the
turns module and the web layer can depend on a Protocol rather than on this
package. `SkillLibraryService` satisfies it.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


class SkillUsagePort(Protocol):
    """Provenance and usage telemetry per skill. Satisfied by `UsageSidecar`.

    Every write is best-effort by design: telemetry must never fail a turn.
    """

    def mark_created(self, name: str, created_by: str, now_iso: str) -> None: ...

    def bump_use(self, name: str, now_iso: str) -> None: ...

    def bump_patch(self, name: str, now_iso: str) -> None: ...

    def set_archived(self, name: str, now_iso: str) -> None: ...

    def set_state(self, name: str, state: str) -> None: ...

    def set_pinned(self, name: str, pinned: bool) -> None: ...

    def get(self, name: str) -> dict | None: ...

    def all(self) -> dict[str, dict]: ...

    def list_skill_dirs(self) -> set[str]: ...


class SkillArchivePort(Protocol):
    """Move a skill directory into `.archive/` and back. Satisfied by
    `infrastructure/skill_archive.py`. The maximum destructive action in this
    module: nothing deletes a skill (PRD P4)."""

    def archive_skill(self, name: str, now_iso: str) -> Path: ...

    def restore_skill(self, name: str, now_iso: str) -> bool: ...

    def list_archived(self) -> list[str]: ...


class SkillRepositoryPort(Protocol):
    """The skills directory as files. Satisfied by `SkillRepository`.

    `locate`/`resolve` carry the containment check — a name from a URL may not
    escape the library — so every caller here may hand the raw name on.
    """

    @property
    def skills_dir(self) -> Path: ...

    def resolve(self, name: str, *, archived: bool = False) -> Path: ...

    def locate(self, name: str) -> tuple[Path, bool] | None: ...

    # The live-directory set is the sidecar's (`SkillUsagePort.list_skill_dirs`);
    # the repository only says where a name's files sit given that set.
    def placement(self, name: str, live: set[str]) -> tuple[Path, bool]: ...

    def info(self, name: str, directory: Path, entry: Mapping, state: str) -> Any: ...

    def detail(self, name: str, directory: Path, is_archived: bool) -> dict: ...

    def read_bytes(self, path: Path) -> bytes: ...

    def write_atomic(self, path: Path, data: bytes) -> None: ...

    def create_dir(self, directory: Path) -> None: ...

    def exists(self, path: Path) -> bool: ...


class SkillLibraryPort(Protocol):
    """What learning promises the other channels: one implementation of list,
    read, write, create, archive and restore, so chat and browser cannot drift."""

    def list_skills(self) -> list[dict]: ...

    def read_skill(self, name: str) -> dict | None: ...

    def write_skill(self, name: str, content: str, etag: str,
                    before_write: Callable[[], None] | None = None) -> str: ...

    def create_skill(self, name: str, content: str, now_iso: str) -> dict: ...


class MetaStore(Protocol):
    """The `meta` key/value table. Satisfied by `core.meta.MetaStore`."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


class ReviewDispatcher(Protocol):
    """Start one review pass, wherever review passes run. Satisfied by
    `QueueReviewDispatcher` over `core.tasks.TaskQueue`.

    `kinds` is a sorted `list[str]`, not the `set` the nudge tracker produces:
    the arguments have to survive being JSON on a Redis list, so the port speaks
    primitives and the service on the other end rebuilds the set. That is also
    why there is no `Reviewer` here any more — handing the loop a service object
    is what made the review something only the agent process could run.

    `owner_id` (Wave 4b) travels with the nudge as a primitive too: the worker
    that drains the task has to know WHOSE scope to run the review in, and the
    session key alone does not say (a `tg:private:<chat>` lane names its owner,
    a `cron:<job>` lane does not). `0` is the deploy owner, which is what a
    payload queued by a 4a agent carries by omission.
    """

    async def dispatch(self, session_key: str, kinds: list[str],
                       owner_id: int = 0) -> None: ...


class ReviewSlot(Protocol):
    """The deploy-wide "one review at a time" bound (Wave 4b, B-1).

    Satisfied by `infrastructure/review_slot.py` over `core.redis.Lock`. The
    per-user `ReviewGate` below stopped bounding concurrency the moment there
    was one lease per user: N users can each hold their own gate, and N review
    forks on one account is the spend this slot exists to refuse. In inline
    mode the queue limit is the bound and the slot is `None` ("always free").

    `acquire()` answers rather than waits: a review that finds the slot held is
    DROPPED, the same designed behaviour as a nudge that finds the gate claimed,
    and the counters fire again next interval.
    """

    async def acquire(self) -> bool: ...

    async def release(self) -> None: ...


class ReviewGate(Protocol):
    """Is a review in flight, in any process? Satisfied by `MetaReviewGate`.

    A lease, not a flag. Wave 2's single-flight was an `asyncio.Task` handle,
    which is worth nothing once the review runs somewhere else — and a plain
    boolean row would stay true forever the first time a worker is killed
    mid-pass, silently disabling the curator. So whoever runs the review claims
    the gate for `review_timeout_seconds` and clears it at the end, and a dead
    worker's claim simply runs out.
    """

    async def running(self) -> bool: ...

    async def claim(self) -> None: ...

    async def release(self) -> None: ...


class SessionSnapshotPort(Protocol):
    """One session row, as the review fork needs it: the SDK session to resume and
    the system-prompt append that made it. Owned by the sessions module.

    Returns None when the lane has never run a turn — there is nothing to fork.
    """

    async def get(self, session_key: str) -> Mapping | None: ...


class ActivityPort(Protocol):
    """When the deployment last saw a message, ISO-8601. Owned by sessions.

    None means nothing was ever recorded; the curator reads that as infinitely
    idle rather than as "busy", because a fresh install has nothing to protect.
    """

    async def last_message_ts(self) -> str | None: ...


class SnapshotStorePort(Protocol):
    """Tar-and-prune snapshots of a directory. Satisfied by `core.snapshots`."""

    def snapshot(self, source_dir: Path, logs_dir: Path, reason: str,
                 keep: int = 5, _protect: set[str] | None = None,
                 kind: str = "skills") -> Path | None: ...

    def list_snapshots(self, logs_dir: Path, kind: str = "skills") -> list[dict]: ...

    def rollback(self, target_dir: Path, logs_dir: Path, backup_id: str | None = None,
                 keep: int = 5, kind: str = "skills") -> tuple[bool, str]: ...


class WikiLintPort(Protocol):
    """A health-check pass over the wiki, owned by memory.

    Never raises into the curator: an unattended pass must survive a broken fork,
    so a failure is None, exactly like "there is no wiki on disk".
    """

    async def lint(self, dry_run: bool = False) -> str | None: ...


class ForkRunner(Protocol):
    """A detached agent fork. Satisfied by `SdkForkRunner`.

    Two methods rather than a generic `run(prompt, options)`: the options differ in
    ways the application must not know about (resume vs fresh context, which guards
    are installed), and `claude_agent_sdk` may not be imported from this layer.

    `workspace` (Wave 4b) is the tree the fork runs in — its `cwd`, its
    `CLAUDE_CONFIG_DIR` (so `resume` reads THAT user's transcript store) and the
    roots its file tools are confined to. `None` keeps the deploy owner's, which
    is what every pre-4b caller means.

    `owner_id` is whose wiki index the fork's Stop hook writes into. It is the
    scope's user id, not the workspace's, because a workspace does not name its
    owner; `0` means "nobody resolved one", which memory reads as "do not
    index" rather than as the deploy owner.
    """

    async def run_review(self, *, prompt: str, system_append: str, resume: str,
                         skills_dir: Path, wiki_root: Path, touched: set[str],
                         workspace: Any = None,
                         owner_id: int = 0) -> str | None: ...

    async def run_consolidation(self, *, prompt: str, skills_dir: Path,
                                workspace: Any = None) -> str | None: ...


class WikiGuardsPort(Protocol):
    """Memory's turn hooks, as a fork that writes wiki pages needs them.

    The hook objects are opaque here on purpose — only `infrastructure` knows they
    are SDK callables. `raw/` immutability and the "update the index" stop gate are
    memory's rules; learning installs them, it does not define them.

    `index_flush` is the same shape as the rest — a hook or `None` — and `None`
    is a wiring answer, not a failure: a composition with no wiki index wired
    runs the review fork exactly as it ran before there was one. The fork reads
    it defensively (`getattr`), because this method is younger than the two
    forks that install it.
    """

    def new_turn_state(self) -> object: ...

    def raw_guard(self, wiki_root: Path) -> object: ...

    def page_touch(self, state: object, wiki_root: Path) -> object: ...

    def stop_gate(self, state: object) -> object: ...

    def index_flush(self, state: object, owner_id: int,
                    workspace: Any) -> Any | None: ...


class PromptCacheInvalidator(Protocol):
    """Drop ONE user's cached prompt prefixes. Called after a mutation that changes
    the skill index that user's model sees — and NOT after a pin, which changes no
    text. A skill edit is one user's: their lanes rebind, nobody else's cache is
    touched (B-4). `include_unowned` sweeps the NULL-owner rows too, which is the
    deploy owner's case — a lane bound before the deploy was claimed is theirs.
    Bound to turns' `TurnService.reset_owned`."""

    async def reset_owned(self, owner_id: int, *,
                          include_unowned: bool = False) -> None: ...


class ReportWriterPort(Protocol):
    """Write a curator run's report to disk; returns the run.json path."""

    def write(self, result: dict) -> Path: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...

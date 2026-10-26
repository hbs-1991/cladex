"""What memory needs from the outside, and what it promises to others.

Consumer-side Protocols (Wave 2 locked decision): a collaborator satisfies these
structurally, so nothing here imports the provider. `WikiIndexPort` and
`WikiLintPort` are the two ports OTHER modules consume — they are declared here as
the shape memory guarantees, and the turns / learning streams declare their own
copies. `ForkRunner` and `LintOptionsFactory` are the shapes memory consumes: the
SDK lives in infrastructure, so the application layer never sees `claude_agent_sdk`.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


class WikiIndexPort(Protocol):
    """The system prompt's view of the wiki. Satisfied by `WikiStore`."""

    @property
    def root(self) -> Path: ...

    def index(self) -> str: ...

    def index_mtime(self) -> float: ...


class WikiLintPort(Protocol):
    """A health-check pass over the wiki. Satisfied by `WikiLintService`.

    Returns the fork's final message, or None when there is no wiki on disk or the
    fork failed — a lint pass never raises into its caller (the curator runs
    unattended and must survive a broken fork).
    """

    async def lint(self, dry_run: bool = False) -> str | None: ...


class WikiPageStore(Protocol):
    """Where the derived index rows live. Satisfied by `PgWikiIndex`.

    A store, not a repository of record: the markdown tree is the source of truth
    and every row here can be rebuilt from it. `page` is the node
    `domain.page.read_page` produces, passed as a mapping so the application layer
    hands the infrastructure data rather than a type it would have to import back.
    """

    async def upsert(self, owner_id: int, page: Mapping[str, Any], *, content: str,
                     shared: bool, now: datetime) -> None: ...

    async def delete(self, owner_id: int, path: str) -> None: ...

    async def paths(self, owner_id: int) -> set[str]: ...


class WikiIndexerPort(Protocol):
    """What turns' hooks and the lint pass call. Satisfied by `WikiIndexService`.

    `workspace` is duck-typed (`.root`, `.wiki_root`, `.is_owner`) rather than
    typed as `core.workspace.Workspace`: the application layer stays core-free,
    the same way `TurnContext.workspace` does.

    Neither method raises. A missing, stale or failed index row is never an error
    for a turn, a lint or boot — the markdown is still there.
    """

    async def touch(self, owner_id: int, workspace: Any,
                    rel_paths: Iterable[str]) -> None: ...

    async def reindex(self, owner_id: int, workspace: Any) -> None: ...


class ForkRunner(Protocol):
    """One fresh agent fork: run a prompt, hand back its final result text."""

    async def run(self, prompt: str, options: object) -> str | None: ...


class LintOptionsFactory(Protocol):
    """Builds the opaque SDK options for a lint fork (tools, guards, model).

    Opaque on purpose: the application decides *whether* this is a dry run, the
    infrastructure decides what a dry run means to the SDK (an empty write-root
    list, so the guard denies every write).
    """

    def build(self, *, dry_run: bool) -> object: ...

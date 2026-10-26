"""Keeping the derived index honest: read a page from disk, write one row.

The markdown tree is the source of truth and this service is the only thing that
writes the cache over it. Three triggers fill it — a Stop hook flushing what a
turn (or a review fork) touched, a lint pass reindexing the tree it just fixed,
and boot reindexing every user whose wiki exists — and all three are allowed to
fail. **An index failure never propagates**: a turn that wrote a page and could
not index it has still written the page, and the next lint or the next boot
converges the table. So every public method here is one `try` that logs and
returns.

`workspace` is duck-typed on `.root`, `.wiki_root` and `.is_owner`: the
application layer never imports `core.workspace` (import-linter), and the store
needs three paths, not a layout.

The `owner_id`/`workspace` agreement check is a tripwire, not a guard against
anything today: both values come from `lane_owner`, resolved once per turn, and
cannot diverge. It is here because the failure it would catch — user A's tree
paired with owner B's id — makes A's private pages searchable by B, silently, and
would be found by nobody.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from ...domain.page import BOOKKEEPING, read_page, split_frontmatter
from ..ports import WikiPageStore
from .graph import page_paths

log = structlog.get_logger(__name__)


def _rel_page(rel: str) -> str | None:
    """A `page_touch` path (`topics/foo.md`) as an index path (`topics/foo`).

    `None` for anything that is not a page: the two bookkeeping files, the
    immutable sources under `raw/`, a dot-directory somebody's tooling left
    behind, and anything that is not markdown at all.
    """
    rel = rel.replace("\\", "/").strip("/")
    if not rel or rel in BOOKKEEPING or not rel.lower().endswith(".md"):
        return None
    if rel.startswith("raw/") or any(part.startswith(".") for part in rel.split("/")):
        return None
    return rel[:-3]


class WikiIndexService:
    """Satisfies `WikiIndexerPort`. Holds a `WikiPageStore` and nothing else."""

    def __init__(self, store: WikiPageStore, *,
                 clock: Callable[[], datetime] | None = None):
        self._store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def touch(self, owner_id: int, workspace: Any,
                    rel_paths: Iterable[str]) -> None:
        """Re-read the pages a turn changed and rewrite their rows.

        A path whose file is gone becomes a `delete` — a page the model deleted
        must stop being a search hit in the same turn, or `memory_search` quotes a
        note that no longer exists as memory.
        """
        try:
            root = self._root(owner_id, workspace)
            if root is None:
                return
            now = self._clock()
            for rel in sorted(set(rel_paths)):
                path = _rel_page(rel)
                if path is None:
                    continue
                file = root / f"{path}.md"
                # One page at a time: a page that cannot be read or stored is
                # logged and skipped, never the reason the rest of the turn's
                # pages stay stale.
                try:
                    if not file.is_file():
                        await self._store.delete(owner_id, path)
                        continue
                    await self._write(owner_id, workspace, root, path, file, now)
                except Exception:
                    log.warning("wiki index: page skipped", owner_id=owner_id,
                                path=path, exc_info=True)
        except Exception:
            log.exception("wiki index touch failed", owner_id=owner_id)

    async def reindex(self, owner_id: int, workspace: Any) -> None:
        """Converge the table to the tree: every page upserted, every row whose
        page is gone deleted. What a lint pass and boot call."""
        try:
            root = self._root(owner_id, workspace)
            if root is None or not root.is_dir():
                return
            now = self._clock()
            # The same notion of "a page" as `touch`: `page_paths` is the graph's
            # scan and still draws `raw/` sources as nodes, but a source is not
            # memory and never gets a row — from either trigger.
            found = [(path, file) for path, file in page_paths(root)
                     if not path.startswith("raw/")]
            for path, file in found:
                try:
                    await self._write(owner_id, workspace, root, path, file, now)
                except Exception:
                    log.warning("wiki index: page skipped", owner_id=owner_id,
                                path=path, exc_info=True)
            for stale in (await self._store.paths(owner_id)) - {p for p, _ in found}:
                await self._store.delete(owner_id, stale)
        except Exception:
            log.exception("wiki index reindex failed", owner_id=owner_id)

    # --- internals -----------------------------------------------------------

    def _root(self, owner_id: int, workspace: Any) -> Path | None:
        """The wiki root to index, or `None` when this call must write nothing."""
        if owner_id <= 0:
            # A bare fork context with no lane owner. Indexing under a made-up id
            # would be worse than not indexing: the rows would be searchable by
            # whoever owns that id.
            return None
        if workspace is None:
            return None
        if not getattr(workspace, "is_owner", True):
            # `users/<id>` is the convention identity's `user_workspace_dir`
            # writes, so the tree names its owner and the two can be compared.
            if Path(workspace.root).name != str(owner_id):
                log.error("wiki index: owner_id does not match the workspace tree",
                          owner_id=owner_id, root=str(workspace.root))
                return None
        return Path(workspace.wiki_root)

    async def _write(self, owner_id: int, workspace: Any, root: Path, path: str,
                     file: Path, now: datetime) -> None:
        try:
            # `errors="replace"`: a note copied over SSH in Windows-1251 is still
            # a page with a title and links, and a `UnicodeDecodeError` is a
            # `ValueError`, not an `OSError`.
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        # `read_page` with an empty index text: `in_index` is not a column, and
        # reading `index.md` once per touched page would be a file read per page
        # for a flag nothing stores.
        page = read_page(path, file, "")
        # The owner's `shared/` is the one namespace everybody reads. A non-owner's
        # `shared/` folder, should they invent one, is private to them.
        shared = bool(getattr(workspace, "is_owner", False)) and path.startswith("shared/")
        await self._store.upsert(owner_id, page, content=split_frontmatter(text)[1],
                                 shared=shared, now=now)

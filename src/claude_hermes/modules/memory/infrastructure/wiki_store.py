"""`<root>/agent_home/wiki` on disk. Moved verbatim from `wiki.py` in
Wave 2; **one store per user** since Wave 4a.

The constructor argument used to be `CH_HOME` and is now a WORKSPACE ROOT — the
same path for the deploy owner (`Workspace.owner(home).root is home`), a user's
own `users/<id>` for everybody else. Nothing else changed: the composition root
builds the deployment's store from `home` exactly as before, and `TurnService`
builds the lane's from `workspace.root` per turn, because the index that goes
into a cached prompt prefix has to be the index of the user whose prefix it is.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..domain.constants import (
    _INDEX,
    _INDEX_HEADER,
    _LOG,
    _LOG_HEADER,
    _MIGRATIONS,
    _NAMESPACES,
    TRUNCATION_NOTE,
)


class WikiStore:
    """Thin accessor for ONE user's agent_home/wiki.

    The model owns every byte of the content. This class only seeds the tree,
    reads the index for the system prompt, and reports the index's mtime so a
    hook can tell when a background pass changed it.

    Cheap enough to build per turn (`index()` is one file read), which is what
    `TurnService.wiki_for` does: a lane's system append and its `index_mtime`
    watermark must come from the lane OWNER's wiki, or user B's byte-stable
    cached prefix carries user A's index and B's Stop gate nags about A's pages.

    `shared_root` is the one namespace a NON-owner may read but not write: the
    owner's `wiki/shared/`. When it is set, `index()` appends a second section
    naming it, so a user's frozen prompt prefix says the household pages exist and
    where they are. The owner's store never has one — their `shared/` pages are in
    their own index already, and a second copy would move a cached prefix that has
    been byte-stable for months (P1).

    Satisfies `application.ports.WikiIndexPort`.
    """

    def __init__(self, root: Path, index_max_chars: int = 8000, *,
                 shared_root: Path | None = None):
        # A WORKSPACE root (`Workspace.root`), not necessarily `CH_HOME`. The name
        # is the only thing that changed: for the owner they are the same path.
        self._home = root
        self._max = index_max_chars
        self._shared = shared_root

    @classmethod
    def for_workspace(cls, workspace, index_max_chars: int = 8000) -> "WikiStore":
        """This user's wiki. The binding `TurnService`'s `wiki_for` is built from.

        Duck-typed on `workspace.root` rather than importing `core.workspace`:
        the store is a path object and has no use for the rest of the layout.
        `is_owner` defaults to True on a workspace that does not have it, so a
        fake carrying only `.root` keeps meaning what it meant.
        """
        shared = (None if getattr(workspace, "is_owner", True)
                  else workspace.shared_wiki_root)
        return cls(workspace.root, index_max_chars, shared_root=shared)

    @property
    def root(self) -> Path:
        return self._home / "agent_home" / "wiki"

    def index(self) -> str:
        own = self._read(self.root / _INDEX, self._max)
        shared = self._shared_section()
        return own + shared if shared else own

    def _shared_section(self) -> str:
        """The read-only namespace, or `""`.

        Truncated at a QUARTER of the budget: a non-owner pays for their own index
        on every call already, and the shared wiki is a household noticeboard, not
        a second memory. A section with nothing under it is tokens for nothing, so
        an empty or unreadable shared index produces no section at all.
        """
        if self._shared is None:
            return ""
        text = self._read(self._shared / _INDEX, max(1, self._max // 4))
        if not text.strip():
            return ""
        return (f"\n\n## Shared wiki (read-only; on disk at {self._shared})\n\n"
                + text)

    @staticmethod
    def _read(path: Path, limit: int) -> str:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ""
        if len(text) <= limit:
            return text
        return text[:limit] + TRUNCATION_NOTE

    def index_mtime(self) -> float:
        """The later of the two indexes.

        The refresh hook watches this number, so the owner editing
        `shared/index.md` has to re-inject for everybody — otherwise a non-owner's
        frozen prefix keeps a snapshot only the owner can see is wrong.
        """
        own = self._mtime(self.root / _INDEX)
        if self._shared is None:
            return own
        return max(own, self._mtime(self._shared / _INDEX))

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def seed(self) -> None:
        """Create the tree and, once, carry the legacy flat memory into it.

        Idempotent: an existing index.md means the wiki is the model's now, and
        nothing here may touch it again.
        """
        if (self.root / _INDEX).is_file():
            return
        for name in _NAMESPACES:
            (self.root / name).mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).date().isoformat()
        migrated = self._migrate_legacy(today)

        index = _INDEX_HEADER + "\n" + "".join(
            f"- {rel} — {summary}\n" for rel, summary in migrated
        )
        (self.root / _INDEX).write_text(index, encoding="utf-8")

        log = _LOG_HEADER
        if migrated:
            pages = ", ".join(rel for rel, _ in migrated)
            log += f"\n- {today} seed: migrated legacy memory into {pages}\n"
        (self.root / _LOG).write_text(log, encoding="utf-8")

    def _migrate_legacy(self, today: str) -> list[tuple[str, str]]:
        """Copy MEMORY.md/USER.md in verbatim, wrapped in frontmatter.

        Code does not restructure them — the first lint pass does. The originals
        are left on disk untouched.
        """
        out: list[tuple[str, str]] = []
        for legacy, rel, title, summary in _MIGRATIONS:
            try:
                body = (self._home / legacy).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not body:
                continue
            page = (
                "---\n"
                f"title: {title}\n"
                f"summary: {summary}\n"
                "tags: [migrated]\n"
                f"updated: {today}\n"
                "---\n\n"
                f"{body}\n"
            )
            (self.root / rel).write_text(page, encoding="utf-8")
            out.append((rel, summary))
        return out

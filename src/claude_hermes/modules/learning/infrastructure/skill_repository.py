"""The skills directory as files: resolve, locate, size, read, write, detail.

Moved from `learning/library.py`. The listing merges three sources that can
disagree — the live directories, `.archive/`, and `.usage.json` — and this layer
owns the disk half of that: which directory holds a name, and what is in it.

`resolve` carries the containment check and moves VERBATIM: the regex already
rejects `../evil`, but only writes go through it — reads take whatever the URL
carried.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

import structlog

from claude_hermes.core.workspace import bundled_skill_names
from claude_hermes.modules.learning.domain.constants import (
    ARCHIVE_DIR,
    LIVE_STATES,
    MAX_TREE_ENTRIES,
    SKILL_FILE,
    SOURCES,
)
from claude_hermes.modules.learning.domain.exceptions import SkillError
from claude_hermes.modules.learning.domain.models import (
    SkillInfo,
    etag_of,
    parse_front_matter,
)
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar

log = structlog.get_logger(__name__)


class SkillRepository:
    """One skills library on disk. Holds the directory; takes the sidecar only
    where a row needs the entry that goes with a file."""

    def __init__(self, skills_dir: Path):
        self._dir = skills_dir

    @property
    def skills_dir(self) -> Path:
        return self._dir

    # ------------------------------------------------------------ containment
    def resolve(self, name: str, *, archived: bool = False) -> Path:
        """Build the skill directory and prove it stayed inside the library.

        The regex already rejects `../evil`, but only writes go through it: reads
        take whatever the URL carried. This is the `FileStore.resolve` rule — check
        the *resolved* path, so a name that survives by accident still cannot escape.
        """
        root = self._dir.resolve()
        base = (root / ARCHIVE_DIR) if archived else root
        candidate = (base / name).resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise SkillError("bad_name", f"'{name}' is not a name inside the skills library")
        return candidate

    def locate(self, name: str) -> tuple[Path, bool] | None:
        """The directory holding this skill's SKILL.md — live first, then `.archive/`.

        Reading the archive matters: a skill can be archived while its page is open,
        and a page that 404s the moment the curator ages something out is a page that
        cannot offer Restore.
        """
        live = self.resolve(name)
        if (live / SKILL_FILE).is_file():
            return live, False
        gone = self.resolve(name, archived=True)
        if (gone / SKILL_FILE).is_file():
            return gone, True
        return None

    def placement(self, name: str, live: set[str]) -> tuple[Path, bool]:
        """Where the skill's files are, and whether it counts as archived.

        A name with no live directory is archived whatever the sidecar says: `state`
        describes what the model can load this turn, and a directory that is not
        there is not loaded. `.archive/` holding it only decides whether Restore can
        work.
        """
        if name in live:
            return self._dir / name, False
        return self._dir / ARCHIVE_DIR / name, True

    # ------------------------------------------------------------------ reads
    def dir_stats(self, directory: Path) -> tuple[int, int]:
        files = total = 0
        try:
            for p in directory.rglob("*"):
                if p.is_file():
                    files += 1
                    total += p.stat().st_size
        except OSError:  # a directory that vanished mid-listing is a zero, not a 500
            log.debug("could not size %s", directory, exc_info=True)
        return files, total

    def read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def exists(self, path: Path) -> bool:
        return path.exists()

    def create_dir(self, directory: Path) -> None:
        directory.mkdir(parents=True)

    def info(self, name: str, directory: Path, entry: Mapping, state: str) -> SkillInfo:
        text = self.read_text(directory / SKILL_FILE) or ""
        meta, parsed = parse_front_matter(text)
        title = meta.get("name") if isinstance(meta.get("name"), str) else None
        raw_desc = meta.get("description")
        description = raw_desc.strip() if isinstance(raw_desc, str) else ""
        created_by = entry.get("created_by")
        files, size = self.dir_stats(directory)
        bundled = bundled_skill_names()
        return SkillInfo(
            name=name,
            title=title or name,
            description=description,
            # Both halves are "will not route": unparseable frontmatter and a missing
            # description look identical to the index, so they read identically here.
            broken=not parsed or not description,
            source="bundled" if name in bundled else (
                created_by if created_by in SOURCES else "user"),
            state=state,
            pinned=bool(entry.get("pinned")),
            use_count=int(entry.get("use_count") or 0),
            patch_count=int(entry.get("patch_count") or 0),
            created_at=entry.get("created_at"),
            last_used=entry.get("last_used"),
            last_patched=entry.get("last_patched"),
            files=files,
            bytes=size,
        )

    def detail(self, name: str, directory: Path, is_archived: bool,
               entry: Mapping | None = None) -> dict:
        """The wire row plus content, etag and the other files.

        `entry` defaults to a fresh read of the sidecar: a detail read needs no more
        than the path and the name, because the sidecar is a file inside the library.
        """
        if entry is None:
            entry = UsageSidecar(self._dir).get(name) or {}
        raw_state = entry.get("state")
        state = "archived" if is_archived else (
            raw_state if raw_state in LIVE_STATES else "active")
        info = self.info(name, directory, entry, state)
        raw = (directory / SKILL_FILE).read_bytes()
        tree = []
        try:
            for p in sorted(directory.rglob("*")):
                if len(tree) >= MAX_TREE_ENTRIES:
                    break
                if p.is_file() and p.name != SKILL_FILE:
                    tree.append({"path": p.relative_to(directory).as_posix(),
                                 "bytes": p.stat().st_size})
        except OSError:
            log.debug("could not walk %s", directory, exc_info=True)
        return {**info.to_wire(),
                "content": raw.decode("utf-8", errors="replace"),
                "etag": etag_of(raw),
                "tree": tree}

    # ----------------------------------------------------------------- writes
    def write_atomic(self, path: Path, data: bytes) -> None:
        """Replace in one step. The review pass may be reading this file right now;
        a truncate-then-write would hand it half a skill."""
        fd, tmp = tempfile.mkstemp(dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise

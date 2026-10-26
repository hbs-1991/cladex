"""The wiki as a graph, and one page in full. Read-only by construction.

Moved from `web/wiki.py` in Wave 2; the handlers stayed behind in
presentation and this is what they call.

Two rules worth knowing:

* **The graph is built by scanning the directory, never from a namespace list.**
  `domain.constants._NAMESPACES` is what `seed()` creates on a fresh volume; it is
  not what the wiki *is*. The agent invents folders — `content/` exists on a live
  deployment — and a graph built from the constant would quietly hide whole
  namespaces. Every hub here comes from a directory that has a page in it.
* **A page the agent forgot to index is still a page.** `in_index` reports whether
  `index.md` mentions the file; hiding an unindexed page would hide exactly the
  drift the flag exists to surface.
"""
from __future__ import annotations

from pathlib import Path

from claude_hermes.core.exceptions import NotFound

from ...domain.page import BOOKKEEPING, read_page


def page_paths(root: Path) -> list[tuple[str, Path]]:
    """Every page in the wiki as `(path-without-extension, file)`, sorted.

    Sorted, not `rglob` order, because the ring the browser draws is positional:
    the same wiki must produce the same picture twice. Dot-directories are skipped
    (a `.git` under the wiki is not memory).
    """
    out: list[tuple[str, Path]] = []
    for file in root.rglob("*.md"):
        rel = file.relative_to(root).as_posix()
        if rel in BOOKKEEPING or any(part.startswith(".") for part in rel.split("/")):
            continue
        if not file.is_file():
            continue
        out.append((rel[:-3], file))
    return sorted(out)


class WikiGraphService:
    """Read side of `agent_home/wiki`. Owns no state beyond the root path."""

    def __init__(self, root: Path):
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def index_text(self) -> str:
        """The raw index, not `WikiStore.index()`: that one truncates for the prompt's
        token budget, and a truncated index would mark real pages as unindexed."""
        try:
            return (self._root / "index.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    def resolve(self, raw: str) -> Path:
        """A request path to a file inside the wiki, or a 404.

        Containment is proved twice: the segment check refuses `..`, an empty
        segment and a drive-qualified path before touching the disk, and the
        `is_relative_to` check refuses anything that resolved out of the root
        anyway — a symlink the agent planted, for instance.
        """
        cleaned = raw.replace("\\", "/").strip("/")
        if cleaned.lower().endswith(".md"):
            cleaned = cleaned[:-3]
        parts = cleaned.split("/") if cleaned else []
        bad = not parts or any(p in ("", ".", "..") or ":" in p for p in parts)
        if bad or cleaned in ("index", "log"):
            raise NotFound(f"no wiki page '{raw}'")
        target: Path | None = None
        try:
            target = (self._root / f"{cleaned}.md").resolve()
            inside = target.is_relative_to(self._root.resolve())
        except OSError:
            inside = False
        if target is None or not inside or not target.is_file():
            raise NotFound(f"no wiki page '{raw}'")
        return target

    def graph(self) -> dict:
        index = self.index_text()
        found = page_paths(self._root)
        pages = [read_page(path, file, index) for path, file in found]
        known = {path for path, _ in found}
        # A hub exists because a page lives in it. `""` — the legacy root — sorts
        # last so the named namespaces keep the top of the ring.
        namespaces = sorted({p["ns"] for p in pages}, key=lambda n: (n == "", n))
        missing = sorted({link for p in pages for link in p["links"] if link not in known})
        return {"pages": pages, "namespaces": namespaces, "missing": missing}

    def page(self, raw: str) -> tuple[dict, bytes]:
        """One page's node plus the bytes on disk, so the caller can etag them."""
        file = self.resolve(raw)
        path = file.relative_to(self._root.resolve()).as_posix()[:-3]
        return read_page(path, file, self.index_text()), file.read_bytes()

"""What a media path in the plan may look like, before anybody touches a disk.

A plan item names its files as paths relative to the owner's `agent_home`; only the
publish scripts ever host one, at publish time. The syntactic half of the guard
lives here so the resolver (`infrastructure/media.py`) and anything else that reads
a stored path share it:

* no segment starting with `.` — which rules out `..` and, just as deliberately,
  `.claude/` (skills, settings, the CLI's own state) and any other dot-directory;
* one of `MEDIA_EXTENSIONS`, so a plan item cannot point the media route at
  `secrets.env`, a transcript or a script.

Containment in `agent_home`, existence and hashing are the resolver's job.
"""
from __future__ import annotations

from pathlib import PurePosixPath

MEDIA_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "mp4", "mov", "webm"})
# A carousel is at most ten on every network this plan targets.
MAX_MEDIA = 10


def media_problem(path: str) -> str | None:
    """Why `path` (relative, POSIX or Windows separators) is not a media path, or
    `None`. Phrased for the item dialog and the agent alike."""
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return "an empty media path"
    parts = PurePosixPath(text).parts
    if any(part.startswith(".") for part in parts):
        return f"{text}: a path through a dot-directory (or '..') is refused"
    suffix = PurePosixPath(text).suffix.lower().lstrip(".")
    if suffix not in MEDIA_EXTENSIONS:
        allowed = " ".join(sorted(MEDIA_EXTENSIONS))
        return f"{text}: only {allowed} files can be media"
    return None


def split_paths(text: str | None) -> list[str]:
    """The tool's `media` argument — newline-separated paths — as a list."""
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def stored_media(raw) -> list[dict]:
    """The `media` column as a list of `{"path", "sha256"}`, whatever a row holds."""
    out: list[dict] = []
    for entry in raw or []:
        if isinstance(entry, dict) and entry.get("path"):
            out.append({"path": str(entry["path"]), "sha256": entry.get("sha256") or None})
    return out

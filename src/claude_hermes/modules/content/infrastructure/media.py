"""A plan item's media files, resolved inside the owner's `agent_home`.

The containment rule is `send_document`'s — resolve, then `is_relative_to` the
resolved `agent_home` (`modules/turns/infrastructure/tool_handlers/documents.py`) —
mirrored here rather than imported, because modules do not import each other.
`resolve()` follows symlinks, so a link inside `agent_home` pointing out of it is
refused like the path it points at.

On top of that, `domain.media.media_problem`: no dot segment (so `.claude/` and
`..` are unreachable) and a media extension, applied to the path RELATIVE to
`agent_home` — the part the plan stores and the media route serves.

Hashing a video reads the whole file, so it runs in a thread: the tick, the web
routes and every turn share this event loop.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from claude_hermes.modules.content.domain.media import media_problem

_CHUNK = 1024 * 1024


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


class WorkspaceMedia:
    """`MediaFiles` over `core.workspace.WorkspaceResolver`.

    `workspaces` is anything with `async for_user(owner_id) -> Workspace` (the
    object `main.build_app` builds as `workspaces`); only `.agent_home` is read. The
    OWNER's tree, always: the item's owner is who the path is relative to.
    """

    def __init__(self, workspaces: Any):
        self._workspaces = workspaces

    async def _boundary(self, owner_id: int) -> Path:
        return (await self._workspaces.for_user(owner_id)).agent_home.resolve()

    @staticmethod
    def _resolve(boundary: Path, raw: str) -> tuple[Path, str]:
        """`(absolute path, path relative to agent_home)` or `ValueError(reason)`."""
        text = str(raw or "").strip()
        if not text:
            raise ValueError("an empty media path")
        given = Path(text)
        if not given.is_absolute():
            # The raw relative spelling is checked too: `img/../.claude/x.png`
            # resolves to a dot-directory, but `a/../b.png` must not slip through
            # as `b.png` either — a stored path is the path the owner approved.
            problem = media_problem(text)
            if problem:
                raise ValueError(problem)
            given = boundary / given
        try:
            path = given.resolve()
        except (OSError, RuntimeError, ValueError):
            raise ValueError(f"{text}: not a usable path") from None
        if not path.is_relative_to(boundary):
            raise ValueError(f"{text}: outside the agent's home")
        relative = path.relative_to(boundary).as_posix()
        problem = media_problem(relative)
        if problem:
            raise ValueError(problem)
        return path, relative

    async def normalize(self, owner_id: int, path: str) -> str:
        """The stored spelling of `path`: relative to `agent_home`, POSIX. Existence
        is not required — a draft may name a file that is still being rendered."""
        _abs, relative = self._resolve(await self._boundary(owner_id), path)
        return relative

    async def locate(self, owner_id: int, path: str) -> Path | None:
        """The existing file behind a stored path, or None. Re-validated on every
        call: the row is data, and the media route serves whatever this returns."""
        try:
            resolved, _rel = self._resolve(await self._boundary(owner_id), path)
        except ValueError:
            return None
        return resolved if resolved.is_file() else None

    async def sha256(self, owner_id: int, path: str) -> str | None:
        found = await self.locate(owner_id, path)
        if found is None:
            return None
        try:
            return await asyncio.to_thread(_digest, found)
        except OSError:
            return None

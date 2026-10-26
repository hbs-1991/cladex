"""Fetching a catalog plugin with git, at its pinned ref.

Tests patch `asyncio.create_subprocess_exec` and `clone_plugin` on this module, and
`GitPluginInstaller` resolves both at call time so the patch is what runs.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
from pathlib import Path

import structlog

from ..domain.exceptions import InstallError
from ..domain.manifest import Manifest
from ..domain.paths import plugin_dir, plugin_root

log = structlog.get_logger(__name__)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

__all__ = ["GitPluginInstaller", "InstallError", "clone_plugin", "plugin_dir",
           "plugin_root", "remove_plugin"]


def _unlock(func, path, _exc_info) -> None:
    """Retry one removal after clearing the read-only bit, then give up quietly.

    git marks everything under `.git/objects` read-only. On Linux that is irrelevant —
    unlinking a file needs write permission on its *directory* — but on Windows it makes
    `rmtree` fail with WinError 5, and with `ignore_errors=True` it failed **silently**:
    Remove answered 204 with the pack files still there, and the next install died on
    `git remote add origin` re-initialising a repository that was supposed to be gone.
    Best-effort is still the contract (a clone we cannot delete must not fail a request),
    so what this adds is one honest attempt before the shrug.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError as e:
        log.debug("could not remove %s: %s", path, e)


def remove_plugin(home: Path, name: str) -> None:
    shutil.rmtree(plugin_dir(home, name), onexc=_unlock)


async def _git(argv: list[str], cwd: Path, deadline: float) -> None:
    """One git call, against a deadline shared by the whole install.

    The deadline is shared, not per-process: an install is three git calls now, and giving
    each the full configured timeout would silently turn a 120s budget into 360s — on the
    user's turn, while they wait.

    stdin is closed and GIT_TERMINAL_PROMPT=0: a repo that asks for credentials must fail,
    not sit on a prompt nobody can answer until the timeout expires.
    """
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise InstallError(f"git {argv[1]} timed out")

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=remaining)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()          # reap it; a zombie would outlive the container
        raise InstallError(f"git {argv[1]} timed out") from None
    if proc.returncode != 0:
        raise InstallError(err.decode(errors="replace").strip() or f"git {argv[1]} failed")


async def clone_plugin(home: Path, m: Manifest, timeout: float = 120.0) -> Path:
    """Fetch a catalog plugin at its pinned ref and return the directory that IS the plugin.

    Fetch, not `clone --branch`: --branch takes a branch or a tag and silently cannot take
    a commit sha, while the validator accepts a sha and the skill recommends one as the
    strongest pin. That combination meant the safest manifest anyone could write was the
    one guaranteed to fail. `fetch <sha>` pins exactly, stays shallow, and a server that
    refuses to serve an arbitrary sha errors instead of hanging.

    A tag is fetched as refs/tags/<ref> so a branch of the same name cannot answer for it.

    The repo and ref come from the manifest, never from chat, and both are validated
    (`repo` is https://, `ref` is a tag or a sha) — so neither can arrive as a git option.
    `--` is belt and braces on top of that.

    On any failure the whole directory is removed: a half-fetched tree that compose would
    later hand to the SDK is worse than nothing at all.
    """
    target = plugin_dir(home, m.name)
    remove_plugin(home, m.name)                     # a re-install starts clean
    target.mkdir(parents=True, exist_ok=True)

    deadline = asyncio.get_running_loop().time() + timeout
    fetch_ref = m.ref if _SHA_RE.match(m.ref) else f"refs/tags/{m.ref}"

    try:
        await _git(["git", "init", "-q"], target, deadline)
        await _git(["git", "remote", "add", "origin", "--", m.repo], target, deadline)
        await _git(["git", "fetch", "--depth", "1", "origin", "--", fetch_ref],
                   target, deadline)
        await _git(["git", "checkout", "-q", "FETCH_HEAD"], target, deadline)

        root = plugin_root(home, m)                 # raises if subdir escapes the clone
        if not (root / ".claude-plugin" / "plugin.json").is_file():
            where = f" under {m.subdir!r}" if m.subdir else ""
            raise InstallError(
                f"{m.repo}@{m.ref} has no .claude-plugin/plugin.json{where} — "
                "it is not a Claude Code plugin"
                + ("" if m.subdir else
                   " (if it is a marketplace repo, the plugin is in a subdirectory: "
                   "set 'subdir')")
            )
    except InstallError:
        remove_plugin(home, m.name)
        raise
    except Exception as e:                                   # noqa: BLE001
        remove_plugin(home, m.name)
        raise InstallError(str(e)) from e

    return root


class GitPluginInstaller:
    """The `PluginInstaller` port, bound to one home.

    The two methods resolve `clone_plugin` / `remove_plugin` through the module at call
    time, not at import: that is what keeps `monkeypatch.setattr(install, "clone_plugin",
    …)` working.
    """

    def __init__(self, home: Path):
        self._home = home

    async def clone(self, m: Manifest, timeout: float = 120.0) -> Path:
        return await clone_plugin(self._home, m, timeout=timeout)

    def remove(self, name: str) -> None:
        remove_plugin(self._home, name)

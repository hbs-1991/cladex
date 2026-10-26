import stat

import pytest

import claude_hermes.modules.extensions.infrastructure.git_installer as install_mod
from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.domain.paths import plugin_dir
from claude_hermes.modules.extensions.infrastructure.git_installer import (
    InstallError,
    clone_plugin,
    remove_plugin,
)

PLUGIN = {
    "manifest_version": 1, "name": "thing", "label": "Thing", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}
SHA = "a" * 40


def fake_git(rc: int = 0, stderr: str = "", *, materialize=None, fail_on: str = ""):
    """Stand in for asyncio.create_subprocess_exec.

    An install is a SEQUENCE of git calls now (init, remote add, fetch, checkout), so this
    records every argv and can fail one specific subcommand — a fake that answers the same
    way to all of them would hide which step broke.
    """
    calls: list[tuple] = []

    async def run(*argv, **kw):
        calls.append(argv)
        failed = bool(fail_on) and argv[1] == fail_on
        if materialize is not None and argv[1] == "checkout" and not failed:
            materialize()

        class P:
            returncode = rc if (not fail_on or failed) else 0

            async def communicate(self):
                return b"", (stderr.encode() if (not fail_on or failed) else b"")

            def kill(self):
                pass

        return P()

    run.calls = calls
    return run


def _plant(target, subdir=""):
    root = target / subdir if subdir else target
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")


async def test_clone_succeeds_and_returns_the_plugin_root(tmp_path, monkeypatch):
    m = parse(PLUGIN, "plugin")
    target = plugin_dir(tmp_path, "thing")
    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec",
                        fake_git(0, materialize=lambda: _plant(target)))
    got = await clone_plugin(tmp_path, m)
    assert got == target.resolve()
    assert (got / ".claude-plugin" / "plugin.json").is_file()


async def test_a_tag_is_fetched_as_a_tag_not_a_bare_name(tmp_path, monkeypatch):
    """`fetch origin v1.0.0` lets a BRANCH named v1.0.0 answer for the tag — which is
    exactly the moving-ref hole the validator refuses elsewhere."""
    m = parse(PLUGIN, "plugin")
    target = plugin_dir(tmp_path, "thing")
    git = fake_git(0, materialize=lambda: _plant(target))
    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec", git)
    await clone_plugin(tmp_path, m)

    fetch = next(c for c in git.calls if c[1] == "fetch")
    assert "refs/tags/v1.0.0" in fetch
    assert "--depth" in fetch and "1" in fetch          # still shallow
    assert "--" in fetch                                # ref can never arrive as an option


async def test_a_sha_is_fetched_verbatim(tmp_path, monkeypatch):
    """The regression this exists for: `clone --branch <sha>` always failed, so the
    strongest pin the validator accepts was the one that could never install."""
    m = parse({**PLUGIN, "ref": SHA}, "plugin")
    target = plugin_dir(tmp_path, "thing")
    git = fake_git(0, materialize=lambda: _plant(target))
    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec", git)
    await clone_plugin(tmp_path, m)

    fetch = next(c for c in git.calls if c[1] == "fetch")
    assert SHA in fetch and f"refs/tags/{SHA}" not in fetch
    assert not any(c[1] == "clone" for c in git.calls)   # never `clone --branch` again


async def test_git_is_never_left_able_to_ask_for_credentials(tmp_path, monkeypatch):
    """A prompt nobody can answer would burn the whole timeout while the user waits."""
    m = parse(PLUGIN, "plugin")
    target = plugin_dir(tmp_path, "thing")
    seen = {}

    async def run(*argv, **kw):
        seen.update(kw)
        _plant(target) if argv[1] == "checkout" else None

        class P:
            returncode = 0

            async def communicate(self):
                return b"", b""

            def kill(self):
                pass

        return P()

    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec", run)
    await clone_plugin(tmp_path, m)
    assert seen["stdin"] is install_mod.asyncio.subprocess.DEVNULL
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


async def test_a_failed_fetch_leaves_nothing_behind(tmp_path, monkeypatch):
    m = parse(PLUGIN, "plugin")
    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec",
                        fake_git(128, "fatal: repository not found", fail_on="fetch"))
    with pytest.raises(InstallError, match="repository not found"):
        await clone_plugin(tmp_path, m)
    assert not plugin_dir(tmp_path, "thing").exists()


async def test_a_repo_without_a_plugin_manifest_is_rejected(tmp_path, monkeypatch):
    """Fetched fine, but it is not a Claude Code plugin. Do not register it."""
    m = parse(PLUGIN, "plugin")
    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec", fake_git(0))
    with pytest.raises(InstallError, match="plugin.json"):
        await clone_plugin(tmp_path, m)
    assert not plugin_dir(tmp_path, "thing").exists()


async def test_a_marketplace_repo_is_told_to_name_its_subdir(tmp_path, monkeypatch):
    """The failure that actually happened: the official marketplace has no plugin.json at
    the root, and the old message just said 'not a Claude Code plugin' — true, unhelpful."""
    m = parse(PLUGIN, "plugin")
    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec", fake_git(0))
    with pytest.raises(InstallError, match="subdir"):
        await clone_plugin(tmp_path, m)


async def test_a_subdir_plugin_installs_and_returns_the_subdir(tmp_path, monkeypatch):
    """A monorepo plugin: the repo root is a marketplace, the plugin is one directory in."""
    m = parse({**PLUGIN, "subdir": "plugins/plugin-dev"}, "plugin")
    target = plugin_dir(tmp_path, "thing")
    monkeypatch.setattr(
        install_mod.asyncio, "create_subprocess_exec",
        fake_git(0, materialize=lambda: _plant(target, "plugins/plugin-dev")),
    )
    got = await clone_plugin(tmp_path, m)
    assert got == (target / "plugins" / "plugin-dev").resolve()
    assert (got / ".claude-plugin" / "plugin.json").is_file()


async def test_the_whole_install_shares_one_deadline(tmp_path, monkeypatch):
    """Four git calls each granted the full timeout would turn a 120s budget into 480s —
    silently, on the user's turn."""
    m = parse(PLUGIN, "plugin")
    budgets = []

    async def run(*argv, **kw):
        class P:
            returncode = 0

            async def communicate(self):
                return b"", b""

            def kill(self):
                pass

        return P()

    real_wait_for = install_mod.asyncio.wait_for

    async def spy_wait_for(aw, timeout=None):
        budgets.append(timeout)
        return await real_wait_for(aw, timeout)

    monkeypatch.setattr(install_mod.asyncio, "create_subprocess_exec", run)
    monkeypatch.setattr(install_mod.asyncio, "wait_for", spy_wait_for)
    with pytest.raises(InstallError):          # no plugin.json planted — fine, we want budgets
        await clone_plugin(tmp_path, m, timeout=100.0)

    assert budgets == sorted(budgets, reverse=True)   # each step gets what's left, not 100
    assert budgets[0] <= 100.0 and budgets[-1] < budgets[0]


async def test_remove_plugin_is_idempotent(tmp_path):
    remove_plugin(tmp_path, "thing")                     # nothing there — no raise
    d = plugin_dir(tmp_path, "thing")
    d.mkdir(parents=True)
    remove_plugin(tmp_path, "thing")
    assert not d.exists()


async def test_remove_plugin_deletes_a_read_only_git_object(tmp_path):
    """git leaves everything under `.git/objects` read-only. On Windows that made the
    whole removal fail silently — Remove answered 204 and the next install then died on
    `git remote add` in a repository that was supposed to be gone. On Linux the unlink
    never needed the bit, so the test passes there by construction and is here to keep
    the retry from being dropped as dead code."""
    d = plugin_dir(tmp_path, "thing")
    (d / ".git" / "objects" / "pack").mkdir(parents=True)
    pack = d / ".git" / "objects" / "pack" / "pack-1.pack"
    pack.write_bytes(b"x")
    pack.chmod(stat.S_IREAD)
    try:
        remove_plugin(tmp_path, "thing")
        assert not d.exists()
    finally:
        if pack.exists():
            pack.chmod(stat.S_IWRITE)

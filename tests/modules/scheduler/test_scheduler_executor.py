"""A routine runs as the person who created it (Wave 4a, D6).

`SdkJobExecutor` is the unattended fork; what is pinned here is the three things
Wave 4a changed about it and nothing else: whose tree it runs in, whose CLI
transcript store and `TMPDIR` it uses, and that the advisor stays off.

The last one is the trap the advisor amendments called out by name: `env` is one
dict the SDK lays over `os.environ`, so `env=workspace_env(ws)` alone would drop
`CLAUDE_CODE_DISABLE_ADVISOR_TOOL` and quietly re-enable the advisor on every
scheduled run — at the advisor's rates, with nobody there to be advised.

Per-user grants added one more: a non-owner's routine gets `Bash`/`Agent`/`Task`
exactly when that user's shell grant says so at fire time (the `OwnerGrants`
port), and fails closed — no port, or a lookup that raises — without losing the run.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from claude_hermes.core.config import NO_ADVISOR_ENV, Config
from claude_hermes.core.workspace import Workspace, WorkspaceResolver
from claude_hermes.modules.scheduler.infrastructure import executor as executor_mod

OWNER = 1
USER = 2
DIRS = {OWNER: "", USER: f"users/{USER}"}


class Result:
    result = "done"


@pytest.fixture
def captured(monkeypatch):
    box: dict = {}

    async def query(*, prompt, options):
        box["options"] = options
        box["prompt"] = prompt
        yield Result()

    monkeypatch.setattr(executor_mod, "query", query)
    return box


@pytest.fixture
def resolver(tmp_path):
    async def workspace_dir_of(user_id: int) -> str:
        return DIRS[user_id]

    async def deploy_owner() -> int:
        return OWNER

    for uid in (OWNER, USER):
        Workspace.for_user(tmp_path, DIRS[uid]).ensure()
    return WorkspaceResolver(tmp_path, workspace_dir_of=workspace_dir_of,
                             deploy_owner=deploy_owner)


def job(**kw) -> dict:
    return {"id": 7, "name": "brief", "prompt": "do it",
            "timeout_seconds": None, **kw}


def build(tmp_path, resolver=None, grants=None, recorder=None):
    return executor_mod.SdkJobExecutor(Config(), tmp_path, workspaces=resolver,
                                       grants=grants, recorder=recorder)


@dataclass(frozen=True)
class Granted:
    """The shape identity's `Grants` answers with: `.shell`, `.learning`."""
    shell: bool
    learning: bool = False


class Grants:
    """`OwnerGrants` fake: one answer for everybody, and every id it was asked
    about — so a test can say who was never asked."""

    def __init__(self, shell: bool = False, *, boom: bool = False):
        self.shell = shell
        self.boom = boom
        self.asked: list[int] = []

    async def grants(self, user_id: int) -> Granted:
        self.asked.append(user_id)
        if self.boom:
            raise RuntimeError("database is down")
        return Granted(shell=self.shell)


class Recorder:
    """`MessageRecorder` fake: the `cron:<id>` transcript, in a list."""

    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    async def record(self, session_key, role, content, ts, *, owner_id=None):
        self.rows.append((session_key, role, content))


async def test_the_fork_runs_in_the_job_owners_tree(tmp_path, resolver, captured):
    assert (await build(tmp_path, resolver).run(job(owner_id=USER))).status == "ok"
    assert captured["options"].cwd == str(
        tmp_path / "users" / "2" / "agent_home")


async def test_the_owners_job_runs_where_it_always_did(tmp_path, resolver,
                                                       captured):
    await build(tmp_path, resolver).run(job(owner_id=OWNER))
    assert captured["options"].cwd == str(tmp_path / "agent_home")


async def test_a_job_with_no_owner_falls_back_to_the_deploy_owner(tmp_path,
                                                                  resolver,
                                                                  captured):
    """`jobs.owner_id` is NULL on every row created before this wave, and the
    deploy owner's tree is where those jobs have been running all along.
    Resolving `0` would raise `UnknownUser` and break every legacy routine."""
    await build(tmp_path, resolver).run(job(owner_id=None))
    assert captured["options"].cwd == str(tmp_path / "agent_home")


async def test_without_a_resolver_nothing_moves(tmp_path, captured):
    """A deployment (or a test) that wires no resolver keeps the pre-4a tree."""
    await build(tmp_path).run(job(owner_id=USER))
    assert captured["options"].cwd == str(tmp_path / "agent_home")


async def test_the_advisor_stays_off_and_the_user_env_is_merged_in(
        tmp_path, resolver, captured):
    await build(tmp_path, resolver).run(job(owner_id=USER))
    env = captured["options"].env
    for key, value in NO_ADVISOR_ENV.items():
        assert env[key] == value
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "users" / "2" / ".claude-cli")
    assert env["TMPDIR"] == str(tmp_path / "users" / "2" / "tmp")


async def test_a_failing_resolution_is_the_jobs_error_not_a_crash(tmp_path,
                                                                  captured):
    """An owner with no `users` row must not send the job to somebody else's
    tree. `UnknownUser` propagating into the job's own error is the right
    outcome; silently running in the deploy owner's would be the leak."""
    class Boom(WorkspaceResolver):
        pass

    async def missing(user_id):
        raise KeyError(user_id)

    async def owner():
        return OWNER

    resolver = Boom(tmp_path, workspace_dir_of=missing, deploy_owner=owner)
    with pytest.raises(KeyError):
        await build(tmp_path, resolver).run(job(owner_id=USER))
    assert "options" not in captured


async def test_a_users_routine_gets_no_shell_and_a_guarded_read(tmp_path, resolver,
                                                                captured):
    """Advisor blocker B-1 (code pass): `cron_manage` exists on every lane, so a
    routine created from a non-owner lane must run under the turn's own rules —
    no `Bash` (taken away by `disallowed_tools`, the only knob that means "cannot"
    under bypassPermissions) and a `PreToolUse` read guard on the owner's root."""
    from claude_hermes.integrations.claude_sdk.guards import PATH_TOOLS

    await build(tmp_path, resolver).run(job(owner_id=USER))
    options = captured["options"]
    assert "Bash" not in options.allowed_tools
    assert options.disallowed_tools == ["Bash", "Agent", "Task"]
    matchers = {m.matcher: m for m in options.hooks["PreToolUse"]}
    assert PATH_TOOLS in matchers and "Bash" in matchers
    [guard] = matchers[PATH_TOOLS].hooks
    verdict = await guard({"tool_name": "Read", "tool_input": {
        "file_path": str(tmp_path / "agent_home" / "CLAUDE.md")}}, "t1", None)
    assert verdict["hookSpecificOutput"]["permissionDecision"] == "deny"
    verdict = await guard({"tool_name": "Read", "tool_input": {
        "file_path": str(tmp_path / "users" / "2" / "agent_home" / "x.md")}}, "t2", None)
    assert (verdict or {}).get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


async def test_the_owners_routine_keeps_the_shell_and_gets_the_guard_too(tmp_path,
                                                                        resolver,
                                                                        captured):
    from claude_hermes.integrations.claude_sdk.guards import PATH_TOOLS

    await build(tmp_path, resolver).run(job(owner_id=OWNER))
    options = captured["options"]
    assert "Bash" in options.allowed_tools and options.disallowed_tools == []
    assert PATH_TOOLS in {m.matcher for m in options.hooks["PreToolUse"]}


# --- A routine's shell is its owner's grant at fire time (per-user grants, AC #7) ---


async def test_a_users_routine_with_the_shell_grant_gets_the_shell(tmp_path, resolver,
                                                                   captured):
    grants = Grants(shell=True)
    await build(tmp_path, resolver, grants).run(job(owner_id=USER))
    options = captured["options"]
    assert "Bash" in options.allowed_tools and options.disallowed_tools == []
    assert grants.asked == [USER]


async def test_a_users_routine_without_the_shell_grant_gets_none(tmp_path, resolver,
                                                                captured):
    """`disallowed_tools` is what takes a tool away under bypassPermissions, so a
    withdrawn grant must land there — not only drop `Bash` off the approve list."""
    grants = Grants(shell=False)
    await build(tmp_path, resolver, grants).run(job(owner_id=USER))
    options = captured["options"]
    assert "Bash" not in options.allowed_tools
    assert options.disallowed_tools == ["Bash", "Agent", "Task"]
    assert grants.asked == [USER]


async def test_the_grant_is_read_at_fire_time_not_at_construction(tmp_path, resolver,
                                                                  captured):
    """A change in Settings → Users reaches the routine's next run with no
    restart: the executor asks on every fire and keeps no copy."""
    grants = Grants(shell=False)
    executor = build(tmp_path, resolver, grants)
    await executor.run(job(owner_id=USER))
    assert captured["options"].disallowed_tools == ["Bash", "Agent", "Task"]
    grants.shell = True
    await executor.run(job(owner_id=USER))
    assert captured["options"].disallowed_tools == []
    assert grants.asked == [USER, USER]


@pytest.mark.parametrize("owner_id", [OWNER, None])
async def test_the_owners_routine_never_asks_for_a_grant(tmp_path, resolver, captured,
                                                         owner_id):
    """The deploy owner's reach comes from their lane (a NULL `owner_id` is
    theirs too); a port that would refuse them must never be consulted."""
    grants = Grants(shell=False, boom=True)
    await build(tmp_path, resolver, grants).run(job(owner_id=owner_id))
    options = captured["options"]
    assert "Bash" in options.allowed_tools and options.disallowed_tools == []
    assert grants.asked == []


async def test_a_failing_grant_lookup_runs_the_job_without_a_shell(tmp_path, resolver,
                                                                   captured, caplog):
    """Unlike a turn, a routine has no session a wrong answer could reset, so a
    failed lookup fails CLOSED and the run still happens — reported, recorded and
    returned like any other — instead of being lost before the fork's `try`."""
    grants = Grants(shell=True, boom=True)
    recorder = Recorder()
    with caplog.at_level(logging.ERROR):
        outcome = await build(tmp_path, resolver, grants, recorder).run(
            job(owner_id=USER))
    assert outcome.status == "ok" and outcome.text == "done"
    options = captured["options"]
    assert "Bash" not in options.allowed_tools
    assert options.disallowed_tools == ["Bash", "Agent", "Task"]
    assert recorder.rows == [("cron:7", "user", "do it"),
                             ("cron:7", "assistant", "done")]
    [failure] = [r for r in caplog.records if "shell grant" in r.getMessage()]
    assert str(USER) in failure.getMessage()


async def test_with_no_grants_port_a_users_routine_gets_no_shell(tmp_path, resolver,
                                                                captured):
    """No port wired means no shell for a non-owner — never a fallback to
    `users_shell_enabled`, which only identity may read; even a deploy that
    set the key to true does not leak it past a missing port."""
    executor = executor_mod.SdkJobExecutor(Config(users_shell_enabled=True), tmp_path,
                                           workspaces=resolver)
    await executor.run(job(owner_id=USER))
    options = captured["options"]
    assert "Bash" not in options.allowed_tools
    assert options.disallowed_tools == ["Bash", "Agent", "Task"]

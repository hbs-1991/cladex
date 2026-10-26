"""The review fork: what it may write, and who gets the credit for it.

The `query` monkeypatch lives where the SDK call does:
`modules.learning.infrastructure.fork_runner`. The service is built here the way
the composition root builds it — over the sessions module's repository and
`core.app_context.WikiForkGuards`.

Wave 4b: the fork runs in ITS workspace (D4). The second half of this file pins
what that buys — the user's `cwd` and `CLAUDE_CONFIG_DIR`, a path guard that
denies the owner's tree, two write hooks that agree about a relative path, and
`Bash`/`Agent`/`Task` removed — and the deploy-wide review slot (D3).
"""
from types import SimpleNamespace

import pytest

import claude_hermes.modules.learning.infrastructure.fork_runner as fork_mod
from claude_hermes.core.config import Config
from claude_hermes.core.app_context import WikiForkGuards
from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.guards import write_guard_factory
from claude_hermes.modules.learning.application.services.review import ReviewService
from claude_hermes.modules.learning.infrastructure.fork_runner import SdkForkRunner
from claude_hermes.modules.learning.infrastructure.guards import skills_write_guard_factory
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar
from claude_hermes.modules.memory.infrastructure.turn_hooks import (
    WikiTurnState,
    page_touch_factory,
    raw_guard_factory,
    stop_gate_factory,
)
from claude_hermes.modules.sessions.infrastructure.repositories import SessionRepository

NOW = "2026-07-12T10:00:00+00:00"


def _write(path):
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def _denied(out) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _matcher(options, matcher: str):
    """The `PreToolUse` hook list registered for one matcher string."""
    return next(m for m in options.hooks["PreToolUse"] if m.matcher == matcher)


async def _all_hooks_allow(options, call) -> bool:
    """Every `PreToolUse` hook that matches a Write — the path guard AND the
    write guard, stacked — must answer `{}` for the call to go through."""
    for matcher in options.hooks["PreToolUse"]:
        if "Write" not in matcher.matcher.split("|"):
            continue
        for hook in matcher.hooks:
            if _denied(await hook(call, "t", None)):
                return False
    return True


async def test_guard_allows_both_roots(tmp_path):
    skills, wiki = tmp_path / "skills", tmp_path / "wiki"
    skills.mkdir()
    wiki.mkdir()
    guard = write_guard_factory([skills, wiki])
    assert await guard(_write(skills / "s" / "SKILL.md"), None, None) == {}
    assert await guard(_write(wiki / "user" / "profile.md"), None, None) == {}


async def test_guard_denies_outside_every_root(tmp_path):
    skills, wiki = tmp_path / "skills", tmp_path / "wiki"
    skills.mkdir()
    wiki.mkdir()
    guard = write_guard_factory([skills, wiki])
    assert _denied(await guard(_write(tmp_path / "escape.md"), None, None))


async def test_guard_still_protects_usage_and_archive(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    guard = write_guard_factory([skills], skills_root=skills)
    assert _denied(await guard(_write(skills / ".usage.json"), None, None))
    assert _denied(await guard(_write(skills / ".archive" / "x" / "SKILL.md"), None, None))


async def test_guard_fails_closed_on_an_unparseable_path(tmp_path):
    guard = write_guard_factory([tmp_path])
    assert _denied(await guard({"tool_name": "Write", "tool_input": {"file_path": "\0"}},
                               None, None))


async def test_guard_records_touched_skill_dirs_only(tmp_path):
    skills, wiki = tmp_path / "skills", tmp_path / "wiki"
    skills.mkdir()
    wiki.mkdir()
    touched: set[str] = set()
    guard = write_guard_factory([skills, wiki], skills_root=skills, touched=touched)
    await guard(_write(skills / "my-skill" / "SKILL.md"), None, None)
    await guard(_write(wiki / "topics" / "x.md"), None, None)
    assert touched == {"my-skill"}          # wiki pages are not skills


async def test_guard_denying_everything_when_no_roots(tmp_path):
    """A dry-run lint gets roots=[] — read-only by construction, not by prompt."""
    guard = write_guard_factory([])
    assert _denied(await guard(_write(tmp_path / "anything.md"), None, None))


async def test_write_guard_scopes_to_skills_dir(tmp_path):
    guard = skills_write_guard_factory(tmp_path / "skills")
    deny = await guard(
        {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "other.txt")}},
        "t1", None,
    )
    assert deny["hookSpecificOutput"]["permissionDecision"] == "deny"
    ok = await guard(
        {"tool_name": "Write",
         "tool_input": {"file_path": str(tmp_path / "skills" / "s" / "SKILL.md")}},
        "t2", None,
    )
    assert ok == {}
    sidecar_deny = await guard(
        {"tool_name": "Edit",
         "tool_input": {"file_path": str(tmp_path / "skills" / ".usage.json")}},
        "t3", None,
    )
    assert sidecar_deny["hookSpecificOutput"]["permissionDecision"] == "deny"


async def make_env(tmp_path, engine):
    await SessionRepository(engine).upsert("tg:private:1", "sdk-abc", "APPEND", NOW)
    skills = tmp_path / "agent_home" / ".claude" / "skills"
    skills.mkdir(parents=True)
    return UsageSidecar(skills), skills


def review_service(cfg, home, engine, sidecar, *, workspace=None, gate=None, slot=None,
                   skills_dir=None):
    """The wiring `main.build_app()` does, in one helper. `workspace=None` is
    the deploy owner's tree — the legacy meaning every pre-4b caller has."""
    guards = WikiForkGuards(new_state=WikiTurnState, raw_guard=raw_guard_factory,
                            page_touch=page_touch_factory, stop_gate=stop_gate_factory)
    ws = workspace if workspace is not None else Workspace.owner(home)
    return ReviewService(
        sessions=SessionRepository(engine), usage=sidecar,
        fork=SdkForkRunner(cfg, home, guards),
        skills_dir=skills_dir or ws.skills_dir, wiki_root=ws.wiki_root,
        gate=gate, workspace=workspace, slot=slot,
    )


async def test_run_review_forks_and_marks_provenance(tmp_path, engine, monkeypatch):
    sidecar, skills = await make_env(tmp_path, engine)
    captured = {}

    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        # the "review agent" creates a skill mid-run: the SDK fires the
        # PreToolUse guard for the Write, then the file lands on disk
        guard = _matcher(options, "Write|Edit").hooks[0]
        d = skills / "release-flow"
        assert await guard(
            {"tool_name": "Write",
             "tool_input": {"file_path": str(d / "SKILL.md")}},
            "t", None,
        ) == {}
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: release-flow\ndescription: R.\n---\n")
        # a concurrent foreground /learn creates a skill WITHOUT the guard —
        # it must NOT be claimed as agent-created
        u = skills / "user-made"
        u.mkdir()
        (u / "SKILL.md").write_text("---\nname: user-made\ndescription: U.\n---\n")

        class R:
            session_id = "fork-1"
            result = "Saved skill release-flow"

        yield R()

    monkeypatch.setattr(fork_mod, "query", fake_query)
    out = await review_service(Config(), tmp_path, engine, sidecar).run(
        "tg:private:1", {"wiki", "skills"})
    assert out == "Saved skill release-flow"
    opts = captured["options"]
    assert opts.resume == "sdk-abc"
    assert opts.fork_session is True
    assert opts.system_prompt["append"] == "APPEND"       # byte-identical → warm cache
    assert opts.tools == ["Read", "Glob", "Grep", "Write", "Edit"]  # hard restriction
    assert "Bash" not in opts.tools
    assert "mcp__hermes__memory_save" not in opts.allowed_tools   # the tool is gone
    assert sidecar.get("release-flow")["created_by"] == "agent"
    assert sidecar.get("user-made") is None               # concurrent dir untouched


async def test_run_review_skips_when_session_missing(tmp_path, engine, monkeypatch):
    sidecar, _ = await make_env(tmp_path, engine)
    assert await review_service(Config(), tmp_path, engine, sidecar).run(
        "tg:private:404", {"wiki"}) is None


# ------------------------------------------------- the fork's workspace (4b) --

def _user_tree(home, uid: int) -> Workspace:
    ws = Workspace.for_user(home, f"users/{uid}")
    for d in (ws.skills_dir, ws.wiki_root, ws.logs_dir, ws.tmp_dir, ws.cli_config_dir):
        d.mkdir(parents=True, exist_ok=True)
    return ws


async def _capture_options(tmp_path, engine, monkeypatch, *, workspace):
    """Run one review through a fake `query` and hand back the SDK options."""
    await make_env(tmp_path, engine)
    captured = {}

    async def fake_query(*, prompt, options):
        captured["options"] = options
        yield SimpleNamespace(session_id="fork-1", result="ok")

    monkeypatch.setattr(fork_mod, "query", fake_query)
    sidecar = UsageSidecar((workspace or Workspace.owner(tmp_path)).skills_dir)
    svc = review_service(Config(), tmp_path, engine, sidecar, workspace=workspace)
    assert await svc.run("tg:private:1", {"wiki"}) == "ok"
    return captured["options"]


async def test_the_fork_runs_in_the_users_workspace(tmp_path, engine, monkeypatch):
    """`cwd` is THEIR `agent_home` and `CLAUDE_CONFIG_DIR` is THEIR transcript
    store — without the second, `resume` would replay the owner's conversation
    into a stranger's review."""
    ws = _user_tree(tmp_path, 7)
    opts = await _capture_options(tmp_path, engine, monkeypatch, workspace=ws)
    assert opts.cwd == str(ws.agent_home)
    assert opts.env["CLAUDE_CONFIG_DIR"] == str(ws.cli_config_dir)
    assert opts.env["TMPDIR"] == str(ws.tmp_dir)
    assert opts.env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"   # merged, not replaced
    assert opts.resume == "sdk-abc" and opts.fork_session is True


async def test_no_workspace_keeps_the_owners_tree(tmp_path, engine, monkeypatch):
    """The legacy head and every pre-4b caller: byte-identical to 4a."""
    opts = await _capture_options(tmp_path, engine, monkeypatch, workspace=None)
    assert opts.cwd == str(tmp_path / "agent_home")
    assert opts.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".claude-cli")


async def test_the_fork_cannot_read_the_owners_tree(tmp_path, engine, monkeypatch):
    """The SAME path guard a foreground turn gets: a review fork for user 7 that
    could `Read` `/data/agent_home/CLAUDE.md` would be the 4a leak with a
    background process as the reader."""
    ws = _user_tree(tmp_path, 7)
    opts = await _capture_options(tmp_path, engine, monkeypatch, workspace=ws)
    guard = _matcher(opts, fork_mod.PATH_TOOLS).hooks[0]
    owner_file = tmp_path / "agent_home" / "CLAUDE.md"
    assert _denied(await guard({"tool_name": "Read",
                                "tool_input": {"file_path": str(owner_file)}},
                               "t", None))
    assert await guard({"tool_name": "Read",
                        "tool_input": {"file_path": str(ws.agent_home / "CLAUDE.md")}},
                       "t", None) == {}
    assert _denied(await guard({"tool_name": "Glob",
                                "tool_input": {"pattern": str(tmp_path / "users" / "*")}},
                               "t", None))


async def test_a_relative_skill_write_passes_both_hooks_and_a_logs_write_fails(
        tmp_path, engine, monkeypatch):
    """Advisor B-8: the path guard and the write guard are stacked on one
    matcher and used to resolve a relative path against DIFFERENT directories
    (the lane's cwd vs this process's cwd). Now they agree: a relative
    `.claude/skills/x/SKILL.md` is inside for both, a relative `logs/x` is
    inside the workspace but outside the write roots, so the write is refused.
    """
    ws = _user_tree(tmp_path, 7)
    opts = await _capture_options(tmp_path, engine, monkeypatch, workspace=ws)
    assert await _all_hooks_allow(opts, _write(".claude/skills/x/SKILL.md"))
    assert await _all_hooks_allow(opts, _write("wiki/topics/x.md"))
    assert not await _all_hooks_allow(opts, _write("logs/x"))
    assert not await _all_hooks_allow(opts, _write("../../../agent_home/CLAUDE.md"))


async def test_the_fork_has_bash_agent_and_task_removed(tmp_path, engine, monkeypatch):
    """`tools=` already omits them; `disallowed_tools` is the control that means
    "cannot" under `bypassPermissions` (4a's live smoke)."""
    ws = _user_tree(tmp_path, 7)
    opts = await _capture_options(tmp_path, engine, monkeypatch, workspace=ws)
    assert opts.disallowed_tools == ["Bash", "Agent", "Task"]
    assert "Bash" not in opts.tools


# ------------------------------------------------------- the review slot (4b) --

class FakeGate:
    def __init__(self):
        self.claimed = False
        self.events = []

    async def running(self):
        return self.claimed

    async def claim(self):
        self.claimed = True
        self.events.append("claim")

    async def release(self):
        self.claimed = False
        self.events.append("release")


class FakeSlot:
    """`ReviewSlot`: held by somebody else, or free and recording."""

    def __init__(self, free=True):
        self.free = free
        self.events = []

    async def acquire(self):
        self.events.append("acquire")
        return self.free

    async def release(self):
        self.events.append("release")


async def test_a_held_slot_releases_the_gate_and_drops_the_review(tmp_path, engine, monkeypatch):
    """One review at a time deploy-wide (B-1). The per-user gate was claimed
    first; a held slot must hand it straight back, or the curator would sit
    behind a lease for a pass that never ran."""
    sidecar, _ = await make_env(tmp_path, engine)
    forked = []

    async def fake_query(*, prompt, options):
        forked.append(prompt)
        yield SimpleNamespace(session_id="fork-1", result="ok")

    monkeypatch.setattr(fork_mod, "query", fake_query)
    gate, slot = FakeGate(), FakeSlot(free=False)
    svc = review_service(Config(), tmp_path, engine, sidecar, gate=gate, slot=slot)
    assert await svc.run("tg:private:1", {"wiki"}) is None
    assert forked == []
    assert gate.events == ["claim", "release"] and not gate.claimed
    assert slot.events == ["acquire"]                       # never held, never released


async def test_a_free_slot_is_held_for_the_pass_and_released_after(tmp_path, engine, monkeypatch):
    sidecar, _ = await make_env(tmp_path, engine)
    order = []

    async def fake_query(*, prompt, options):
        order.append("fork")
        yield SimpleNamespace(session_id="fork-1", result="ok")

    monkeypatch.setattr(fork_mod, "query", fake_query)
    gate, slot = FakeGate(), FakeSlot(free=True)
    svc = review_service(Config(), tmp_path, engine, sidecar, gate=gate, slot=slot)
    assert await svc.run("tg:private:1", {"wiki"}) == "ok"
    assert slot.events == ["acquire", "release"]
    assert gate.events == ["claim", "release"]


class RaisingSlot(FakeSlot):
    async def acquire(self):
        self.events.append("acquire")
        raise RuntimeError("redis went away")


async def test_a_raising_slot_acquire_hands_the_gate_back(tmp_path, engine, monkeypatch):
    """Advisor risk 3 (code pass): the gate was claimed BEFORE the slot; a Redis
    blip inside `acquire` must not leave `learning_review_until[:<id>]` set for
    the full lease — the user's nudges would be dropped and their curator
    blocked, for a review that never started."""
    sidecar, _ = await make_env(tmp_path, engine)
    gate, slot = FakeGate(), RaisingSlot()
    svc = review_service(Config(), tmp_path, engine, sidecar, gate=gate, slot=slot)
    with pytest.raises(RuntimeError):
        await svc.run("tg:private:1", {"wiki"})
    assert gate.events == ["claim", "release"] and not gate.claimed


async def test_the_redis_slot_never_reacquires_while_it_believes_it_holds(monkeypatch):
    """Advisor risk 2 (code pass): one `RedisReviewSlot` is shared by every scope
    in the process. If pass A's key expired and B acquired through the same
    object, A's `release()` would delete B's key and let a third review start.
    A slot that already holds must answer False without touching Redis."""
    from claude_hermes.modules.learning.infrastructure import review_slot as slot_mod

    built = []

    class FakeLock:
        def __init__(self, client, key, *, ttl_ms):
            built.append(key)
            self.released = False

        async def acquire(self):
            return True

        async def release(self):
            self.released = True

    monkeypatch.setattr(slot_mod, "Lock", FakeLock)
    slot = slot_mod.RedisReviewSlot(object(), ttl_ms=1000)
    assert await slot.acquire() is True
    assert await slot.acquire() is False          # held here already: no second Lock
    assert built == ["lock:review"]
    await slot.release()
    assert await slot.acquire() is True           # free again after the release
    assert built == ["lock:review", "lock:review"]

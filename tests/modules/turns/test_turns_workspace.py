"""What the executor builds for ONE lane, and how it differs between the deploy
owner and everybody else (Wave 4a, D6).

`SdkTurnExecutor.execute` is driven with the SDK's `query` patched out, so what
is under test is the `ClaudeAgentOptions` it hands over — `cwd`, `env`,
`allowed_tools`, the hook table — plus which hermes tools were registered. Those
four are the whole of the confinement: the CLI enforces `disallowed_tools`, the
hook enforces the paths, and `cwd`/`env` decide which tree and which transcript
store the process even sees.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.guards import WRITE_TOOLS
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.domain.policies import _BUILTIN_TOOLS, builtin_tools
from claude_hermes.modules.turns.domain.value_objects import TurnContext
from claude_hermes.modules.turns.infrastructure import sdk_runner


class Result:
    """The one message a fake `query` yields — duck-typed `ResultMessage`."""
    session_id = "sdk-1"
    result = "done"
    total_cost_usd = None
    model_usage: dict = {}


@pytest.fixture
def captured(monkeypatch):
    """Run one turn against a fake SDK and keep the options it was given."""
    box: dict = {}

    async def fake_query(*, prompt, options):
        box["options"] = options
        yield Result()

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    return box


class Costs:
    async def record(self, *a, **kw):
        return None


class Hooks:
    """A `TurnHooksProvider` that records which workspace it was asked about —
    the wiki hooks must be built over the LANE owner's tree, not the deploy
    owner's (advisor blocker B1) — and under which owner id, which is what the
    Stop-hook index flush stamps the rows it writes with."""

    def __init__(self):
        self.workspaces: list = []
        self.owners: list[int] = []

    def build(self, session_key, index_seen, workspace=None, *, owner_id=0):
        self.workspaces.append(workspace)
        self.owners.append(owner_id)
        return {}


class WikiSearchPort:
    async def search(self, query, *, owner_id, limit=8, depth=1):
        return []


class Skills:
    async def handle(self, args, *, ctx=None):
        return "ok"


def executor(tmp_path, *, hooks=None, skills=None, settings=None, catalog=None,
             wiki_search=None):
    return sdk_runner.SdkTurnExecutor(
        settings=settings or TurnSettings(),
        home=tmp_path,
        search=object(),
        costs=Costs(),
        hooks=hooks,
        catalog_root=catalog,
        skills=skills,
        wiki_search=wiki_search,
    )


async def execute(ex, ctx):
    return await ex.execute(
        user_text="hi", resume_id=None, append="", session_key=ctx.session_key,
        ctx=ctx, attachments=None, index_seen={}, on_slash_commands=lambda n: None)


def ctx_for(workspace, *, key="web:1:x", owner_lane=True, chat_id=0):
    return TurnContext(session_key=key, chat_id=chat_id, workspace=workspace,
                       is_owner_lane=owner_lane)


def hook_matchers(options, event="PreToolUse"):
    return list(options.hooks.get(event) or [])


# --- the owner lane: byte-identical to Wave 3b -------------------------------


async def test_the_owner_lane_runs_where_it_always_did(tmp_path, captured):
    """`Workspace.owner(home).root is home`, so `cwd` is the same
    `<CH_HOME>/agent_home` it was before this wave — a live volume needs no
    migration and every SDK session keeps resuming."""
    ws = Workspace.owner(tmp_path)
    await execute(executor(tmp_path), ctx_for(ws))
    assert captured["options"].cwd == str(tmp_path / "agent_home")


async def test_the_owner_keeps_the_whole_builtin_toolset(tmp_path, captured):
    await execute(executor(tmp_path), ctx_for(Workspace.owner(tmp_path)))
    allowed = captured["options"].allowed_tools
    assert allowed[:len(_BUILTIN_TOOLS)] == _BUILTIN_TOOLS


async def test_a_turn_with_no_workspace_falls_back_to_the_owners(tmp_path, captured):
    """The legacy `run_turn` head: the five runner test modules and
    `gateway.telegram` resolve nothing, and must keep running where they did."""
    await execute(executor(tmp_path), TurnContext(session_key="web:1:x", chat_id=0))
    assert captured["options"].cwd == str(tmp_path / "agent_home")


# --- the env: a CLI process per user -----------------------------------------


async def test_the_cli_config_dir_and_tmpdir_are_this_users(tmp_path, captured):
    """`CLAUDE_CONFIG_DIR` is what `resume` reads, so sharing it would replay one
    user's conversation into another's; `/tmp` is shared by every lane in the
    container, so a scratch file written there is readable by every other turn."""
    ws = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path), ctx_for(ws, owner_lane=False))
    env = captured["options"].env
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "users" / "7" / ".claude-cli")
    assert env["TMPDIR"] == str(tmp_path / "users" / "7" / "tmp")


async def test_the_owners_env_names_the_owners_dirs(tmp_path, captured):
    await execute(executor(tmp_path), ctx_for(Workspace.owner(tmp_path)))
    env = captured["options"].env
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".claude-cli")
    assert env["TMPDIR"] == str(tmp_path / "tmp")


# --- the user lane: cwd, toolset, tools --------------------------------------


async def test_a_user_lane_runs_in_its_own_tree(tmp_path, captured):
    ws = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path), ctx_for(ws, owner_lane=False))
    assert captured["options"].cwd == str(
        tmp_path / "users" / "7" / "agent_home")


async def test_a_user_lane_gets_no_shell_and_no_subagents(tmp_path, captured):
    """One container is one uid, so `Bash` reaches every other user's tree
    whatever a hook says; `Agent`/`Task` go with it because a subagent inherits
    the shell (the read guard itself does hold inside a subagent — verified)."""
    ws = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path), ctx_for(ws, owner_lane=False))
    allowed = captured["options"].allowed_tools
    assert "Bash" not in allowed and "Agent" not in allowed and "Task" not in allowed
    assert "Read" in allowed and "WebSearch" in allowed and "TodoWrite" in allowed
    # `allowed_tools` is only the auto-approve list under bypassPermissions; what
    # actually takes the shell away is `disallowed_tools` (the live smoke proved
    # the difference: a non-owner lane ran `ls` with Bash absent from allowed).
    assert captured["options"].disallowed_tools == ["Bash", "Agent", "Task"]


async def test_a_shell_grant_on_the_context_hands_the_shell_back(tmp_path, captured):
    """Since 0009 the shell is the lane owner's GRANT, resolved per turn by the
    runner and carried on the context — the executor reads nothing else."""
    ws = Workspace.for_user(tmp_path, "users/7")
    ctx = replace(ctx_for(ws, owner_lane=False), shell_enabled=True)
    assert ctx.has_shell
    await execute(executor(tmp_path), ctx)
    allowed = captured["options"].allowed_tools
    assert allowed[:len(_BUILTIN_TOOLS)] == _BUILTIN_TOOLS       # same order, all of it
    assert captured["options"].disallowed_tools == []


async def test_the_settings_no_longer_carry_a_deploy_wide_shell(tmp_path, captured):
    """`users_shell_enabled` in config is only the DEFAULT identity resolves a grant
    from; a copy frozen into `TurnSettings` at boot is what made a change need a
    restart, and it must not creep back in as a second way to hand out a shell."""
    from claude_hermes.core.config import Config

    settings = TurnSettings.from_config(Config(users_shell_enabled=True,
                                               users_learning_enabled=True))
    assert not hasattr(settings, "users_shell_enabled")
    assert not hasattr(settings, "users_learning_enabled")
    ws = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path, settings=settings), ctx_for(ws, owner_lane=False))
    assert captured["options"].disallowed_tools == ["Bash", "Agent", "Task"]


async def test_the_owner_lane_needs_no_grant(tmp_path, captured):
    """An owner (or group) lane gets the whole toolset through `is_owner_lane`; its
    grant fields stay at their False defaults and change nothing."""
    ctx = ctx_for(Workspace.owner(tmp_path))
    assert (ctx.shell_enabled, ctx.learning_enabled, ctx.has_shell) == (False, False, True)
    await execute(executor(tmp_path), ctx)
    assert captured["options"].disallowed_tools == []
    assert "Bash" in captured["options"].allowed_tools


def test_the_filtered_toolset_keeps_the_builtin_order():
    """`allowed_tools` is part of the cached prompt prefix, so the list is
    filtered, never rebuilt: a reorder is a cache miss on every lane."""
    trimmed = builtin_tools(is_owner_lane=False, shell_enabled=False)
    assert trimmed == [t for t in _BUILTIN_TOOLS if t in trimmed]
    assert builtin_tools() == _BUILTIN_TOOLS
    assert builtin_tools(is_owner_lane=False, shell_enabled=True) == _BUILTIN_TOOLS


async def test_skill_admin_exists_on_a_users_private_lane_and_not_in_a_group(
        tmp_path, captured):
    """Since Wave 4b the library is per user, so `skill_admin` exists on every
    private/web lane and administers the LANE OWNER's tree (the port gets the
    ctx). The one place it does not exist is a group lane, where the library
    is the deploy owner's and the room is not the owner (D6)."""
    owner = Workspace.owner(tmp_path)
    await execute(executor(tmp_path, skills=Skills()), ctx_for(owner))
    assert "mcp__hermes__skill_admin" in captured["options"].allowed_tools

    user = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path, skills=Skills()),
                  ctx_for(user, owner_lane=False, key="web:7:x"))
    assert "mcp__hermes__skill_admin" in captured["options"].allowed_tools

    await execute(executor(tmp_path, skills=Skills()),
                  ctx_for(owner, key="tg:group:-5:1", chat_id=-5))
    assert "mcp__hermes__skill_admin" not in captured["options"].allowed_tools


# --- the hooks ----------------------------------------------------------------


async def test_the_path_guard_is_installed_for_every_lane(tmp_path, captured):
    """The owner included: the point is not that the owner is untrusted, it is
    that one composition builds every lane and a guard that is sometimes absent
    is a guard nobody can reason about."""
    for ws, owner in ((Workspace.owner(tmp_path), True),
                      (Workspace.for_user(tmp_path, "users/7"), False)):
        await execute(executor(tmp_path), ctx_for(ws, owner_lane=owner))
        matchers = [m.matcher for m in hook_matchers(captured["options"])]
        assert "Bash" in matchers
        assert any("NotebookEdit" in (m or "") for m in matchers)


async def test_the_guard_admits_this_user_and_refuses_the_other(tmp_path,
                                                                captured):
    """Driven through the hook the executor actually installed, not through a
    guard the test built — the wiring is the thing that can be wrong."""
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    ws = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path, catalog=catalog),
                  ctx_for(ws, owner_lane=False))
    guard = [m for m in hook_matchers(captured["options"])
             if m.matcher and "NotebookEdit" in m.matcher][0].hooks[0]

    async def decide(path):
        return await guard({"tool_name": "Read", "tool_input": {"file_path": path}},
                           "t", None)

    assert await decide(str(ws.agent_home / "notes.md")) == {}
    assert await decide(str(ws.tmp_dir / "scratch")) == {}
    assert await decide(str(catalog / "mcp" / "x.yaml")) == {}
    assert await decide(str(tmp_path / "plugins" / "p")) == {}
    # the deploy owner's tree, and a sibling user's
    for outside in (tmp_path / "agent_home" / "secret.md",
                    tmp_path / "users" / "8" / "agent_home" / "secret.md"):
        out = await decide(str(outside))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_the_wiki_hooks_are_built_over_the_lane_owners_workspace(tmp_path,
                                                                       captured):
    """Without this, B's Stop gate nags about A's pages and a `Write` into A's
    wiki is waved through by the one hook meant to see it (blocker B1)."""
    hooks = Hooks()
    ws = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path, hooks=hooks), ctx_for(ws, owner_lane=False))
    assert hooks.workspaces == [ws]


async def test_the_hooks_are_built_under_the_lane_owners_id(tmp_path, captured):
    """The Stop hook that flushes touched pages into the wiki index writes rows
    keyed `(owner_id, path)`. The id has to be the LANE's owner — the one the
    runner already decided — or one user's pages become searchable by another.
    A context with no owner passes `0`, which memory reads as "do not index"."""
    hooks = Hooks()
    ws = Workspace.for_user(tmp_path, "users/7")
    ctx = TurnContext(session_key="web:7:x", chat_id=0, workspace=ws,
                      is_owner_lane=False, owner_id=7)
    await execute(executor(tmp_path, hooks=hooks), ctx)
    assert hooks.owners == [7]

    await execute(executor(tmp_path, hooks=hooks), ctx_for(ws, owner_lane=False))
    assert hooks.owners == [7, 0]


# --- the shared wiki namespace -----------------------------------------------


async def test_a_non_owner_reads_the_shared_wiki_and_the_owner_needs_no_extra_root(
        tmp_path):
    """`shared/` lives inside the OWNER's wiki, so for them it is already under
    `agent_home` and a second root would say nothing. For everybody else it is
    the one path outside their tree they may read."""
    owner = Workspace.owner(tmp_path)
    user = Workspace.for_user(tmp_path, "users/7")
    ex = executor(tmp_path)
    assert user.shared_wiki_root in ex._allowed_roots(user)
    assert owner.shared_wiki_root not in ex._allowed_roots(owner)
    assert owner.shared_wiki_root.is_relative_to(owner.agent_home)


def _fence(options):
    """The `WRITE_TOOLS` matcher, which exists only for a non-owner lane."""
    return [m for m in hook_matchers(options) if m.matcher == WRITE_TOOLS]


async def test_a_non_owner_may_not_write_the_shared_wiki_under_either_spelling(
        tmp_path, captured):
    """A model told "there is a read-only shared wiki" will try
    `Write("wiki/shared/x.md")`, which resolves into its OWN tree and would land
    as a private page nobody else ever sees. Both paths are fenced, so the deny
    message teaches it the truth instead."""
    ws = Workspace.for_user(tmp_path, "users/7")
    await execute(executor(tmp_path), ctx_for(ws, owner_lane=False))
    matchers = _fence(captured["options"])
    assert len(matchers) == 1
    guard = matchers[0].hooks[0]

    async def decide(path):
        return await guard({"tool_name": "Write",
                            "tool_input": {"file_path": path}}, "t", None)

    for denied in (ws.shared_wiki_root / "x.md",
                   ws.wiki_root / "shared" / "x.md"):
        out = await decide(str(denied))
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    # a relative path resolves against the turn's cwd, the same way the read
    # guard resolves it — the two hooks on one tool must not disagree
    out = await decide("wiki/shared/x.md")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert await decide(str(ws.wiki_root / "user" / "prefs.md")) == {}


async def test_the_owner_gets_no_write_fence_at_all(tmp_path, captured):
    """For the owner the two shared paths are one path and it is theirs."""
    await execute(executor(tmp_path), ctx_for(Workspace.owner(tmp_path)))
    assert _fence(captured["options"]) == []


# --- memory_search -------------------------------------------------------------


async def test_memory_search_exists_on_a_private_lane_and_nowhere_else(
        tmp_path, captured):
    """Gated like `session_search`: a group lane's wiki is the deploy owner's,
    and a room full of people must not read it. The flag is the other gate, and
    flipping it costs every lane one session reset at boot."""
    ws = Workspace.for_user(tmp_path, "users/7")
    ex = executor(tmp_path, wiki_search=WikiSearchPort())
    await execute(ex, ctx_for(ws, owner_lane=False, key="web:7:x"))
    assert "mcp__hermes__memory_search" in captured["options"].allowed_tools

    await execute(ex, ctx_for(Workspace.owner(tmp_path), key="tg:group:-5:1",
                              chat_id=-5))
    assert "mcp__hermes__memory_search" not in captured["options"].allowed_tools

    off = executor(tmp_path, wiki_search=WikiSearchPort(),
                   settings=TurnSettings(memory_search_enabled=False))
    await execute(off, ctx_for(ws, owner_lane=False, key="web:7:x"))
    assert "mcp__hermes__memory_search" not in captured["options"].allowed_tools


async def test_without_the_port_there_is_no_tool(tmp_path, captured):
    await execute(executor(tmp_path), ctx_for(Workspace.owner(tmp_path)))
    assert "mcp__hermes__memory_search" not in captured["options"].allowed_tools


# --- send_document ------------------------------------------------------------


async def test_send_document_is_contained_by_the_lane_owners_root(tmp_path):
    """Not by `CH_HOME`: otherwise a non-owner could deliver a file out of a tree
    their own `Read` is already denied."""
    from claude_hermes.modules.turns.infrastructure.hermes_server import make_handlers

    ws = Workspace.for_user(tmp_path, "users/7")
    ws.agent_home.mkdir(parents=True)
    outside = tmp_path / "agent_home"
    outside.mkdir()
    (outside / "secret.md").write_text("x", encoding="utf-8")
    inside = ws.agent_home / "ok.md"
    inside.write_text("x", encoding="utf-8")

    async def deliver(chat_id, thread_id, path, caption=None):
        return None

    handlers = make_handlers(
        search=object(), costs=Costs(), ctx=ctx_for(ws, key="tg:private:7", chat_id=7,
                                                    owner_lane=False),
        home=tmp_path, workspace=ws, deliver_document=deliver)
    send = handlers["send_document"]
    refused = await send({"path": str(outside / "secret.md")})
    assert "Refused" in refused["content"][0]["text"]
    assert str(ws.root) in refused["content"][0]["text"]
    sent = await send({"path": str(inside)})
    assert "Sent ok.md" in sent["content"][0]["text"]


def test_the_allowed_roots_are_the_ones_this_lane_owns(tmp_path):
    """Stated once, here, because a root added by accident is a boundary moved by
    accident. `logs_dir` is per user like the rest and is in the list so the
    owner does not lose the `Read` of their own curator reports; the shared wiki
    is the ONE path outside a non-owner's tree, and it comes with a write fence
    (see the two tests above)."""
    catalog = tmp_path / "catalog"
    ws = Workspace.for_user(tmp_path, "users/7")
    roots = executor(tmp_path, catalog=catalog)._allowed_roots(ws)
    assert roots == [ws.agent_home, ws.tmp_dir, ws.logs_dir, ws.plugins_dir,
                     ws.shared_wiki_root, catalog]
    owner = Workspace.owner(tmp_path)
    assert executor(tmp_path, catalog=catalog)._allowed_roots(owner) == [
        owner.agent_home, owner.tmp_dir, owner.logs_dir, owner.plugins_dir,
        catalog]
    # the plugin clones are DEPLOY-wide and therefore shared, by design (D8)
    assert ws.plugins_dir == tmp_path / "plugins"
    assert Path(ws.agent_home).is_relative_to(tmp_path / "users" / "7")


async def test_a_group_lane_has_no_recall_cost_report_or_skill_admin_for_anybody(
        tmp_path, captured):
    """A group lane belongs to the deploy owner, so `session_search`,
    `cost_report` and `skill_admin` would scope to the OWNER's history, spend and
    library — for whichever group member is talking. Tool presence is a
    property of the LANE (4b, D6): 4a withheld the first two per SENDER, and a
    per-sender flip rewrites the MCP schema list between two turns of one
    session, a cached-prefix miss. So the owner in the same room has none of
    the three either."""
    owner = Workspace.owner(tmp_path)
    for uid in (9, 1):
        member = TurnContext(session_key=f"tg:supergroup:-5:{uid}", chat_id=-5,
                             user_id=uid, owner_id=1, workspace=owner)
        await execute(executor(tmp_path, skills=Skills()), member)
        tools = captured["options"].allowed_tools
        assert "mcp__hermes__session_search" not in tools
        assert "mcp__hermes__cost_report" not in tools
        assert "mcp__hermes__skill_admin" not in tools

    # The same owner on a private lane has all three: it is the lane, not them.
    alone = TurnContext(session_key="tg:private:1", chat_id=1, user_id=1,
                        owner_id=1, workspace=owner)
    await execute(executor(tmp_path, skills=Skills()), alone)
    tools = captured["options"].allowed_tools
    assert {"mcp__hermes__session_search", "mcp__hermes__cost_report",
            "mcp__hermes__skill_admin"} <= set(tools)


# --- the toolset fingerprint the composition root takes at boot ---------------


def test_tool_names_is_the_owner_private_lane_list_in_registration_order(tmp_path):
    """What `main.sync_hermes_toolset` fingerprints: the widest list this
    process can publish, in the order `build_hermes_server` registers it."""
    names = executor(tmp_path).tool_names()
    assert names[:2] == ["mcp__hermes__session_search", "mcp__hermes__cost_report"]
    assert "mcp__hermes__memory_search" not in names          # no wiki_search port

    with_search = executor(tmp_path, wiki_search=WikiSearchPort()).tool_names()
    assert with_search[-1] == "mcp__hermes__memory_search"
    assert with_search[:-1] == names
    off = executor(tmp_path, wiki_search=WikiSearchPort(),
                   settings=TurnSettings(memory_search_enabled=False)).tool_names()
    assert off == names


async def test_tool_names_equals_what_an_owner_private_lane_actually_gets(tmp_path, captured):
    """The two lane-gated tools (`send_document` needs a turn to deliver into,
    `ask_user` a private chat) must be in the fingerprint too, or wiring either
    for the first time would swap the toolset under every session unnoticed."""
    async def deliver(chat_id, thread_id, path, caption=None):
        return None

    class Ask:
        async def ask(self, *a, **k):
            return None

    ex = sdk_runner.SdkTurnExecutor(
        settings=TurnSettings(), home=tmp_path, search=object(), costs=Costs(),
        hooks=None, skills=Skills(), deliver_document=deliver, ask=Ask(),
        wiki_search=WikiSearchPort())
    await execute(ex, ctx_for(Workspace.owner(tmp_path), key="tg:private:1", chat_id=1))
    live = [t for t in captured["options"].allowed_tools if t.startswith("mcp__hermes__")]
    assert ex.tool_names() == live
    assert {"mcp__hermes__send_document", "mcp__hermes__ask_user",
            "mcp__hermes__memory_search"} <= set(live)

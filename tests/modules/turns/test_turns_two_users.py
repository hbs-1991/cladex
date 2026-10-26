"""Two users, one deployment, one process — and nothing of A's reaching B.

This is the acceptance line of Wave 4a ("two different users cannot see each
other's workspace files, via API or via the agent's tools") driven end to end
through the real `TurnService` + `SdkTurnExecutor` with only the SDK faked. Three
things are asserted about the SAME pair of turns, because in the bug they all
went wrong together:

* the two `cwd`s are different trees;
* **B's `system_append` contains none of A's wiki page titles** — the system
  prompt is the cached prefix, so a leak there is a leak on every later turn of
  that lane and is invisible in any per-call check (advisor blocker B1);
* neither turn's path guard admits the other's root.

Per-user connectors add the fourth: **B's turn composes none of A's MCP
servers.** Every layer below asserts that with fakes; this file asserts it over
the real `ComposeService` on the real tables, because the whole point of a
required `owner_id` is that some seam between the lane and `ClaudeAgentOptions`
might still reach for a deploy-wide row.
"""
from __future__ import annotations

import pytest

from claude_hermes.core.app_context import ExtensionCompose
from claude_hermes.core.events import TurnStats
from claude_hermes.core.session_keys import chat_id_from_key, lane_owner
from claude_hermes.core.workspace import Workspace, WorkspaceResolver
from claude_hermes.modules.extensions.application.services.compose_service import (
    ComposeService,
)
from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (  # noqa: E501
    SqlExtensionRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (  # noqa: E501
    SqlOauthTokenRepository,
)
from claude_hermes.modules.memory.infrastructure.wiki_store import WikiStore
from claude_hermes.modules.turns.application.execution import TurnRunService
from claude_hermes.modules.turns.application.control import CancelRegistry
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.application.turn_service import TurnService
from claude_hermes.modules.turns.domain.value_objects import TurnContext
from claude_hermes.modules.turns.infrastructure import sdk_runner

from tests.conftest import ensure_users
from tests.ext_helpers import secrets_for

OWNER = 1
USER = 2
DIRS = {OWNER: "", USER: f"users/{USER}"}


# --- the smallest collaborators a turn needs ---------------------------------


class Sessions:
    async def route(self, session_key, now):
        return None

    async def bind(self, *a, **kw):
        return None

    async def touch(self, *a, **kw):
        return None

    async def reset(self, session_key):
        return None


class Messages:
    async def record(self, *a, **kw):
        return None

    async def last(self, *a, **kw):
        return None


class Threads:
    async def touch(self, *a, **kw):
        return None


class Bus:
    def __init__(self):
        self.events = []

    async def emit(self, session_key, turn_id, type, payload):
        self.events.append((type, payload))


class Costs:
    async def record(self, *a, **kw):
        return None


class Executor:
    """Wraps the real `SdkTurnExecutor` and keeps what each turn was given."""

    def __init__(self, inner):
        self._inner = inner
        self.calls: list[dict] = []

    def is_cli_death(self, error):
        return self._inner.is_cli_death(error)

    async def execute(self, **kw):
        self.calls.append(kw)
        return await self._inner.execute(**kw)


class Result:
    session_id = "sdk-1"
    result = "done"
    total_cost_usd = None
    model_usage: dict = {}


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """One CH_HOME, two seeded workspaces, a distinct wiki page in each, and the
    SDK replaced by a recorder — `captured` is the options of every turn run."""
    captured: list = []

    async def query(*, prompt, options):
        captured.append(options)
        yield Result()

    monkeypatch.setattr(sdk_runner, "query", query)

    async def workspace_dir_of(user_id: int) -> str:
        return DIRS[user_id]

    async def deploy_owner() -> int:
        return OWNER

    resolver = WorkspaceResolver(tmp_path, workspace_dir_of=workspace_dir_of,
                                 deploy_owner=deploy_owner)
    for uid in (OWNER, USER):
        ws = Workspace.for_user(tmp_path, DIRS[uid]).ensure()
        (ws.wiki_root / "index.md").write_text(
            f"# Wiki index\n- topics/{'alpha' if uid == OWNER else 'beta'}.md\n",
            encoding="utf-8")
    return tmp_path, resolver, captured


def build(tmp_path, resolver, *, executor):
    def wiki_for(workspace):
        return WikiStore.for_workspace(workspace or Workspace.owner(tmp_path))

    return TurnService(settings=TurnSettings(), sessions=Sessions(),
                       messages=Messages(), threads=Threads(),
                       wiki_for=wiki_for, bus=Bus(), executor=executor)


def guard_of(options):
    matcher = [m for m in options.hooks["PreToolUse"]
               if m.matcher and "NotebookEdit" in m.matcher][0]
    return matcher.hooks[0]


async def denies(guard, path) -> bool:
    out = await guard({"tool_name": "Read", "tool_input": {"file_path": str(path)}},
                      "t", None)
    return (out or {}).get("hookSpecificOutput", {}).get(
        "permissionDecision") == "deny"


async def run_both(tmp_path, resolver, captured):
    inner = sdk_runner.SdkTurnExecutor(
        settings=TurnSettings(), home=tmp_path, search=object(),
        costs=Costs())
    executor = Executor(inner)
    service = build(tmp_path, resolver, executor=executor)
    for uid, key in ((OWNER, f"web:{OWNER}:a"), (USER, f"web:{USER}:b")):
        ws = await resolver.for_lane(key, "web", uid)
        await service.run_turn(key, "hello", user_id=uid, channel="web",
                               workspace=ws)
    return executor, captured


async def test_the_two_turns_run_in_different_trees(deployment):
    tmp_path, resolver, captured = deployment
    _, options = await run_both(tmp_path, resolver, captured)
    owner_opts, user_opts = options
    assert owner_opts.cwd == str(tmp_path / "agent_home")
    assert user_opts.cwd == str(tmp_path / "users" / "2" / "agent_home")
    assert owner_opts.cwd != user_opts.cwd


async def test_the_second_users_prompt_carries_none_of_the_first_users_wiki(
        deployment):
    """The system append IS the cached prefix. A wiki index from the wrong tree
    there is not one leaked read — it is a leak on every turn of that lane until
    the session resets, and no per-call check can see it."""
    tmp_path, resolver, captured = deployment
    executor, _ = await run_both(tmp_path, resolver, captured)
    owner_append = executor.calls[0]["append"]
    user_append = executor.calls[1]["append"]
    assert "topics/alpha.md" in owner_append
    assert "topics/alpha.md" not in user_append
    assert "topics/beta.md" in user_append


async def test_neither_guard_admits_the_others_root(deployment):
    tmp_path, resolver, captured = deployment
    _, options = await run_both(tmp_path, resolver, captured)
    owner_guard, user_guard = (guard_of(o) for o in options)
    owner_home = tmp_path / "agent_home"
    user_home = tmp_path / "users" / "2" / "agent_home"

    assert await denies(owner_guard, user_home / "notes.md")
    assert await denies(user_guard, owner_home / "notes.md")
    assert not await denies(owner_guard, owner_home / "notes.md")
    assert not await denies(user_guard, user_home / "notes.md")


class Observer:
    """A `TurnObserver` that records WHOSE turn it was told about — the owner id
    is what picks the sidecar and the counters on the other side (4b, D3)."""

    def __init__(self):
        self.seen: list[tuple[str, int]] = []

    async def after_turn(self, session_key, stats, *, owner_id=0):
        self.seen.append((session_key, owner_id))


class FakeExecutor:
    def is_cli_death(self, e):
        return False

    async def execute(self, **kw):
        return "hi", "sdk-1", TurnStats(), None, None, kw["ctx"].model


class Grant:
    """What identity's `Grants` looks like from here: two attributes."""

    def __init__(self, shell=False, learning=False):
        self.shell, self.learning = shell, learning


class Grants:
    """A `LaneGrants` answering per user id, and recording who was asked — the
    deploy owner's lanes must never be."""

    def __init__(self, by_user=None, default=None):
        self.by_user = dict(by_user or {})
        self.default = default or Grant()
        self.asked: list[int] = []

    async def grants(self, user_id):
        self.asked.append(user_id)
        return self.by_user.get(user_id, self.default)

    async def grant_defaults(self):
        return self.default


async def observe_both(tmp_path, resolver, *, grants=None):
    observer = Observer()
    service = TurnService(settings=TurnSettings(), sessions=Sessions(),
                          messages=Messages(), threads=Threads(),
                          wiki_for=lambda ws: WikiStore.for_workspace(
                              ws or Workspace.owner(tmp_path)),
                          bus=Bus(), executor=FakeExecutor(), observer=observer,
                          grants=grants)
    for uid, key in ((OWNER, f"web:{OWNER}:a"), (USER, f"web:{USER}:b")):
        await service.run_turn(key, "hi", user_id=uid, channel="web",
                               workspace=await resolver.for_lane(key, "web", uid))
    return observer


async def test_the_user_lane_is_not_observed_without_a_learning_grant(deployment):
    """No grant (the default): a non-owner's review forks and curator passes
    would be spend on the OWNER's account for somebody else's traffic, and
    nothing meters that yet (D12). Without it, the lane is exactly what 4a made
    it — and nothing in their reply may claim a lesson was learned. A service
    built without the port fails the same, closed way."""
    tmp_path, resolver, _ = deployment
    grants = Grants({USER: Grant(learning=False)})
    observer = await observe_both(tmp_path, resolver, grants=grants)
    assert observer.seen == [(f"web:{OWNER}:a", OWNER)]
    assert grants.asked == [USER]                  # the owner's lane never asks
    observer = await observe_both(tmp_path, resolver)
    assert observer.seen == [(f"web:{OWNER}:a", OWNER)]


async def test_the_user_lane_is_observed_with_its_owner_when_granted(deployment):
    """With the grant, B's turn reaches the observer stamped with B's id, so
    the sidecar it bumps and the nudge counters it reads are B's — a turn
    observed under the owner's id would grow the OWNER's library from B's
    conversation, which is the leak 4b exists to close."""
    tmp_path, resolver, _ = deployment
    observer = await observe_both(tmp_path, resolver,
                                  grants=Grants({USER: Grant(learning=True)}))
    assert observer.seen == [(f"web:{OWNER}:a", OWNER), (f"web:{USER}:b", USER)]


async def test_a_shell_grant_reaches_the_real_executor_per_turn(deployment):
    """The grant is resolved by the runner, carried on the context and read by
    the real `SdkTurnExecutor` — and asked again on the next turn, so a flip
    applies with no rebuild. A group lane is the owner's and never asks."""
    tmp_path, resolver, captured = deployment
    grants = Grants({USER: Grant(shell=True)})
    inner = sdk_runner.SdkTurnExecutor(settings=TurnSettings(), home=tmp_path,
                                       search=object(), costs=Costs())
    service = TurnService(settings=TurnSettings(), sessions=Sessions(),
                          messages=Messages(), threads=Threads(),
                          wiki_for=lambda ws: WikiStore.for_workspace(
                              ws or Workspace.owner(tmp_path)),
                          bus=Bus(), executor=Executor(inner), grants=grants)

    async def turn(key, uid, channel="web"):
        await service.run_turn(key, "hi", user_id=uid, channel=channel,
                               workspace=await resolver.for_lane(key, channel, uid))
        return captured[-1]

    options = await turn(f"web:{USER}:b", USER)
    assert {"Bash", "Agent", "Task"} <= set(options.allowed_tools)
    assert options.disallowed_tools == []

    grants.by_user[USER] = Grant(shell=False)                  # the owner flips it
    options = await turn(f"web:{USER}:b", USER)
    assert not {"Bash", "Agent", "Task"} & set(options.allowed_tools)
    assert options.disallowed_tools == ["Bash", "Agent", "Task"]
    assert grants.asked == [USER, USER]

    # A group lane belongs to the deploy owner whoever speaks in it: the whole
    # toolset, and nobody's grant is consulted.
    options = await turn("tg:supergroup:-5:9", 9, channel="telegram")
    assert options.disallowed_tools == [] and "Bash" in options.allowed_tools
    options = await turn(f"web:{OWNER}:a", OWNER)
    assert options.disallowed_tools == [] and "Bash" in options.allowed_tools
    assert grants.asked == [USER, USER]


# --- lane-shaped tools (4b, D6) ----------------------------------------------


class Skills:
    async def handle(self, args, *, ctx=None):
        return "ok"


async def lane_tools(tmp_path, resolver, key, channel, uid, *, shell=False):
    """`shell` is the lane owner's grant as the runner would have put it on the
    context (0009) — there is no deploy-wide setting to flip any more."""
    ex = sdk_runner.SdkTurnExecutor(settings=TurnSettings(), home=tmp_path,
                                    search=object(), costs=Costs(), skills=Skills())
    ws = await resolver.for_lane(key, channel, uid)
    ctx = TurnContext(session_key=key, chat_id=chat_id_from_key(key) or 0,
                      user_id=uid, owner_id=lane_owner(key, channel, uid, OWNER),
                      workspace=ws, is_owner_lane=ws.root == ws.home,
                      shell_enabled=shell)
    await ex.execute(user_text="hi", resume_id=None, append="", session_key=key,
                     ctx=ctx, attachments=None, index_seen={},
                     on_slash_commands=lambda n: None)


THREE = {"mcp__hermes__skill_admin", "mcp__hermes__session_search",
         "mcp__hermes__cost_report"}


async def test_a_group_lane_offers_none_of_the_three_tools_to_anybody(deployment):
    """The room's library, recall and spend are the DEPLOY OWNER's. Offering the
    tools per sender (4a) flipped the MCP schema list between two turns of one
    session — a cached-prefix miss — so presence is a property of the lane: not
    for the stranger, and not for the owner talking in the same room either."""
    tmp_path, resolver, captured = deployment
    for uid in (9, OWNER):
        await lane_tools(tmp_path, resolver, f"tg:supergroup:-5:{uid}",
                         "telegram", uid)
    for options in captured:
        assert not THREE & set(options.allowed_tools)
        assert "mcp__hermes__cron_manage" not in options.allowed_tools


async def test_a_private_lane_offers_all_three_scoped_to_its_owner(deployment):
    """On a `web:`/`tg:private:` lane the sender IS the owner structurally, so a
    non-owner gets `skill_admin` over THEIR library (4a withheld it because the
    library was one tree) alongside their recall and their spend."""
    tmp_path, resolver, captured = deployment
    await lane_tools(tmp_path, resolver, f"web:{USER}:b", "web", USER)
    await lane_tools(tmp_path, resolver, f"tg:private:{USER}", "telegram", USER)
    await lane_tools(tmp_path, resolver, f"web:{OWNER}:a", "web", OWNER)
    for options in captured:
        assert THREE <= set(options.allowed_tools)
    # …and the shell stays withheld from the non-owner: the lane rule adds the
    # three hermes tools, it does not hand back `Bash`.
    assert "Bash" not in captured[0].allowed_tools
    assert "Bash" in captured[2].allowed_tools


async def test_a_granted_shell_comes_back_on_the_users_private_lanes(deployment):
    """The grant (0009) is what hands a non-owner `Bash`/`Agent`/`Task` back — on
    their web and private lanes alike, and without changing the lane-shaped
    three. The stranger in a group gets nothing from anybody's grant: that lane
    is the owner's and already has the whole toolset."""
    tmp_path, resolver, captured = deployment
    await lane_tools(tmp_path, resolver, f"web:{USER}:b", "web", USER, shell=True)
    await lane_tools(tmp_path, resolver, f"tg:private:{USER}", "telegram", USER,
                     shell=True)
    for options in captured:
        assert {"Bash", "Agent", "Task"} <= set(options.allowed_tools)
        assert options.disallowed_tools == []
        assert THREE <= set(options.allowed_tools)


async def test_the_run_service_resolves_the_lane_owner_from_the_row(deployment):
    """The one place a workspace is decided: `TurnRunService` holds the row, so
    it is the only thing that knows the key, the channel and the sender at once.
    Everything below is handed the answer."""
    tmp_path, resolver, _ = deployment
    row = {"id": "t1", "session_key": f"web:{USER}:b", "channel": "web",
           "user_id": USER, "text": "hi", "attachment_ids": [],
           "status": "queued"}

    class Store:
        async def claim(self, turn_id, worker_id, now_iso, lease_until):
            return dict(row)

        async def heartbeat(self, *a):
            return {"cancel_requested_at": None}

        async def finish(self, *a, **kw):
            return True

        async def get(self, turn_id):
            return dict(row)

    class Runner:
        def __init__(self):
            self.kwargs = None

        async def run_turn(self, session_key, text, **kw):
            self.kwargs = kw
            return "ok"

    runner = Runner()
    service = TurnRunService(Store(), runner, None, CancelRegistry(),
                             lease_seconds=30.0, workspaces=resolver)
    await service.run("t1")
    assert runner.kwargs["workspace"].root == tmp_path / "users" / "2"


# --- per-user connectors -----------------------------------------------------

FETCH = {
    "manifest_version": 1, "name": "fetch", "label": "Fetch", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.test/mcp",
    "scopes": ["turn", "cron"],
    "auth": [{"name": "FETCH_TOKEN", "prompt": "token", "header": "Authorization",
              "format": "Bearer ${FETCH_TOKEN}"}],
}


class Catalog:
    """extensions' `CatalogReader` over one manifest in memory. The catalog is
    deploy-wide and always has been — what changed is whose row enables an entry
    of it, so the shared half is exactly what this fake keeps shared."""

    def load_all(self, root=None):
        return {"fetch": parse(FETCH, "mcp")}

    def get(self, name, root=None):
        return self.load_all().get(name)

    def is_repo_entry(self, name):
        return False


def composer(tmp_path, engine):
    """The real `ComposeService` over the real three tables, behind the same
    `ExtensionCompose` adapter `main.build_app` hands the runner."""
    return ExtensionCompose(ComposeService(
        tmp_path, catalog=Catalog(), secrets=secrets_for(engine),
        store=SqlExtensionRepository(engine),
        tokens=SqlOauthTokenRepository(engine)))


async def connector_turn(tmp_path, resolver, engine, key, uid):
    ex = sdk_runner.SdkTurnExecutor(settings=TurnSettings(), home=tmp_path,
                                    search=object(), costs=Costs(),
                                    compose=composer(tmp_path, engine))
    ws = await resolver.for_lane(key, "web", uid)
    ctx = TurnContext(session_key=key, chat_id=chat_id_from_key(key) or 0,
                      user_id=uid, owner_id=lane_owner(key, "web", uid, OWNER),
                      workspace=ws, is_owner_lane=ws.root == ws.home)
    await ex.execute(user_text="hi", resume_id=None, append="", session_key=key,
                     ctx=ctx, attachments=None, index_seen={},
                     on_slash_commands=lambda n: None)


async def test_b_does_not_get_as_mcp_servers(deployment, engine):
    """The acceptance line: two users' turns do not share a connector.

    A enables `fetch` and stores the token it needs. B has neither row, so B's
    turn is composed with no `fetch` server and no `mcp__fetch` in its allowed
    tools — which is also why B cannot reach A's credential through the model:
    the header carrying it is never built for B's options at all.
    """
    tmp_path, resolver, captured = deployment
    await ensure_users(engine, USER, owner=OWNER)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=OWNER,
                    now="2026-09-22T10:00:00+00:00", owner_id=OWNER)
    await secrets_for(engine).put("FETCH_TOKEN", "owners", owner_id=OWNER)

    await connector_turn(tmp_path, resolver, engine, f"web:{OWNER}:a", OWNER)
    await connector_turn(tmp_path, resolver, engine, f"web:{USER}:b", USER)
    a, b = captured

    assert "fetch" in a.mcp_servers
    assert "mcp__fetch" in a.allowed_tools
    assert "fetch" not in b.mcp_servers
    assert "mcp__fetch" not in b.allowed_tools


async def test_the_second_user_enabling_it_gets_their_own_credential(deployment,
                                                                     engine):
    """And the other half of the same rule: B adopting the catalog entry composes
    the server from B's OWN secret. One shared manifest, two headers."""
    tmp_path, resolver, captured = deployment
    await ensure_users(engine, USER, owner=OWNER)
    store = SqlExtensionRepository(engine)
    secrets = secrets_for(engine)
    for uid, token in ((OWNER, "owners"), (USER, "hers")):
        await store.add("fetch", "mcp", "1.0.0", installed_by=uid,
                        now="2026-09-22T10:00:00+00:00", owner_id=uid)
        await secrets.put("FETCH_TOKEN", token, owner_id=uid)

    await connector_turn(tmp_path, resolver, engine, f"web:{OWNER}:a", OWNER)
    await connector_turn(tmp_path, resolver, engine, f"web:{USER}:b", USER)
    a, b = captured

    assert a.mcp_servers["fetch"]["headers"] == {"Authorization": "Bearer owners"}
    assert b.mcp_servers["fetch"]["headers"] == {"Authorization": "Bearer hers"}

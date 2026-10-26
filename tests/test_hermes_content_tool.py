"""`content_plan` in chat: on which lanes it exists, where it registers, what it sends.

The tool's body and declaration belong to the content module; `turns` owns only
where it appears. That is the part pinned here, against a FAKE port — modules are
independent, so this file never imports `content`:

* it exists on the deploy owner's non-group lanes and nowhere else, decided from
  the LANE (never the sender), because a per-sender flip rewrites the MCP schema
  list between two turns of one session — a cached-prefix miss (P1);
* it registers LAST, after `memory_search`, and only from the port's declaration,
  exactly like `ext_manage`: the routine runs build the same tool from the same
  declaration, and a second spelling here would be the one that drifts;
* `tool_names()` — the boot fingerprint — carries it, so flipping
  `content_enabled` resets every lane once instead of swapping a toolset under a
  warm session.

`execute` is driven with the SDK's `query` patched out, so what is under test is
the `allowed_tools` it hands over, as in `tests/modules/turns/test_turns_workspace.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from claude_hermes.core.workspace import Workspace
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.domain.value_objects import TurnContext
from claude_hermes.modules.turns.infrastructure import hermes_server, sdk_runner
from claude_hermes.modules.turns.infrastructure.hermes_server import (
    build_hermes_server,
    make_handlers,
)

TOOL = "mcp__hermes__content_plan"


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


class WikiSearchPort:
    async def search(self, query, *, owner_id, limit=8, depth=1):
        return []


class ContentPort:
    """What the content module hands in: a declaration plus `handle`, duck-typed.

    Records every call, so a test can read back exactly what the chat envelope
    passed — the lane owner, the origin and the thread — and answers the MCP result
    dict itself, which the envelope must hand through untouched.
    """

    name = "content_plan"
    description = "Read and edit the content plan."
    schema = {"action": str, "id": int}

    def __init__(self, answer: dict | None = None):
        self.calls: list[tuple[dict, dict]] = []
        self.answer = answer or {"content": [{"type": "text", "text": "ok"}]}

    async def handle(self, args, *, owner_id, origin, thread_id):
        self.calls.append((dict(args), {"owner_id": owner_id, "origin": origin,
                                        "thread_id": thread_id}))
        return self.answer


class Undeclared:
    """A port that forgot its declaration — a wiring bug, not a tool."""

    async def handle(self, args, *, owner_id, origin, thread_id):
        return {"content": [{"type": "text", "text": "ok"}]}


def executor(tmp_path, *, content=None, wiki_search=None, settings=None):
    return sdk_runner.SdkTurnExecutor(
        settings=settings or TurnSettings(), home=tmp_path, search=object(),
        costs=Costs(), hooks=None, wiki_search=wiki_search, content=content)


async def execute(ex, ctx):
    return await ex.execute(
        user_text="hi", resume_id=None, append="", session_key=ctx.session_key,
        ctx=ctx, attachments=None, index_seen={}, on_slash_commands=lambda n: None)


def hermes_tools(options) -> list[str]:
    return [t for t in options.allowed_tools if t.startswith("mcp__hermes__")]


def owner_ctx(tmp_path, *, key="tg:private:1", chat_id=1, owner_id=1, **kw):
    return TurnContext(session_key=key, chat_id=chat_id, user_id=owner_id,
                       owner_id=owner_id, workspace=Workspace.owner(tmp_path), **kw)


# --- which lanes have it ------------------------------------------------------


@pytest.mark.parametrize("key,chat_id", [("tg:private:1", 1), ("web:1:abc", 0)])
async def test_the_deploy_owners_private_and_web_lanes_have_it_last(
        tmp_path, captured, key, chat_id):
    ex = executor(tmp_path, content=ContentPort(), wiki_search=WikiSearchPort())
    await execute(ex, owner_ctx(tmp_path, key=key, chat_id=chat_id))
    names = hermes_tools(captured["options"])
    assert names[-2:] == ["mcp__hermes__memory_search", TOOL]


async def test_a_group_lane_has_it_for_nobody_the_owner_included(tmp_path, captured):
    """A group lane belongs to the deploy owner, so it IS an owner lane — and a
    room must still not edit the owner's publishing queue. Decided by the lane,
    so the owner speaking in the room gets exactly what a stranger gets."""
    ex = executor(tmp_path, content=ContentPort())
    for sender in (9, 1):
        member = TurnContext(session_key=f"tg:supergroup:-5:{sender}", chat_id=-5,
                             user_id=sender, owner_id=1, is_owner_lane=True,
                             workspace=Workspace.owner(tmp_path))
        await execute(ex, member)
        assert TOOL not in captured["options"].allowed_tools


async def test_a_non_owner_lane_does_not_have_it(tmp_path, captured):
    """v1's plan is the deploy owner's alone: another user's private or web lane
    has no content plan of its own to edit."""
    ws = Workspace.for_user(tmp_path, "users/7")
    ex = executor(tmp_path, content=ContentPort())
    for key, chat_id in (("tg:private:7", 7), ("web:7:x", 0)):
        ctx = TurnContext(session_key=key, chat_id=chat_id, user_id=7, owner_id=7,
                          workspace=ws, is_owner_lane=False)
        await execute(ex, ctx)
        assert TOOL not in captured["options"].allowed_tools


async def test_a_lane_with_no_owner_id_does_not_have_it(tmp_path, captured):
    """`owner_id=0` is a fork with no lane — the port would refuse every call, so
    the tool is not offered at all."""
    ex = executor(tmp_path, content=ContentPort())
    await execute(ex, owner_ctx(tmp_path, owner_id=0))
    assert TOOL not in captured["options"].allowed_tools


async def test_without_the_port_there_is_no_tool(tmp_path, captured):
    """`content_enabled: false` is `content=None`, and nothing else moves."""
    await execute(executor(tmp_path), owner_ctx(tmp_path))
    assert TOOL not in captured["options"].allowed_tools


def test_the_owner_lane_must_be_stated_not_defaulted(tmp_path):
    """The builtin toolset reads `is_owner_lane` with a True default so a bare fork
    keeps its reach; the content plan asks for an explicit True instead. A context
    that does not say it is the owner's does not get the owner's queue."""
    ex = executor(tmp_path, content=ContentPort())
    unsaid = SimpleNamespace(owner_id=1)                      # no is_owner_lane at all
    truthy = SimpleNamespace(owner_id=1, is_owner_lane=1)     # truthy but not True
    said = SimpleNamespace(owner_id=1, is_owner_lane=True)
    assert ex._content_for(unsaid, "tg:private:1") is None
    assert ex._content_for(truthy, "tg:private:1") is None
    assert ex._content_for(said, "tg:private:1") is not None


# --- the fingerprint ------------------------------------------------------------


def test_tool_names_carries_it_last_so_the_flag_resets_every_lane(tmp_path):
    """`main.sync_hermes_toolset` fingerprints `tool_names()`: enabling or
    disabling the content plan must change that list, and appending is the only
    change that keeps every earlier name where the warm prefix has it."""
    off = executor(tmp_path, wiki_search=WikiSearchPort()).tool_names()
    on = executor(tmp_path, content=ContentPort(),
                  wiki_search=WikiSearchPort()).tool_names()
    assert TOOL not in off
    assert on == off + [TOOL]


async def test_tool_names_equals_what_an_owner_private_lane_actually_gets(
        tmp_path, captured):
    ex = executor(tmp_path, content=ContentPort(), wiki_search=WikiSearchPort())
    await execute(ex, owner_ctx(tmp_path))
    assert ex.tool_names() == hermes_tools(captured["options"])


# --- registration and the envelope ---------------------------------------------


def test_it_registers_from_the_declaration_and_only_from_it(monkeypatch):
    """The name, description and schema ride on the port; nothing is spelled here.
    A port that declares nothing raises instead of registering a guess."""
    seen: list[tuple] = []
    real_tool = hermes_server.tool

    def recording_tool(name, description, schema):
        seen.append((name, description, schema))
        return real_tool(name, description, schema)

    monkeypatch.setattr(hermes_server, "tool", recording_tool)
    port = ContentPort()
    _, names = build_hermes_server(make_handlers(search=None, costs=None, content=port))
    assert names == [TOOL]
    assert seen == [(port.name, port.description, port.schema)]

    with pytest.raises(ValueError, match="without a declaration"):
        build_hermes_server(make_handlers(search=None, costs=None,
                                          content=Undeclared()))


async def test_the_handler_passes_the_lane_owner_and_chat_origin(tmp_path):
    """`origin` is always "chat" here — only the publisher routine's own run says
    `cron:<job_id>`, which is what keeps a chat turn from claiming an item — and
    the port's MCP answer, refusal flag included, goes back untouched."""
    refusal = {"content": [{"type": "text", "text": "Refused."}], "is_error": True}
    port = ContentPort(answer=refusal)
    ctx = owner_ctx(tmp_path, key="web:1:abc", chat_id=0)
    handlers = make_handlers(search=None, costs=None, ctx=ctx, content=port)
    out = await handlers["content_plan"]({"action": "claim", "id": 3})
    assert out is refusal
    assert port.calls == [({"action": "claim", "id": 3},
                           {"owner_id": 1, "origin": "chat", "thread_id": "web:1:abc"})]

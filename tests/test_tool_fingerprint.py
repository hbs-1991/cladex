"""The boot fingerprint of the `hermes` toolset: names, descriptions AND schemas.

`main.sync_hermes_toolset` resets every lane when the stored fingerprint differs
from this boot's (P1). It used to hash the tool NAMES only, so a reworded
description — prompt text in the cached prefix like any schema — moved every
cached prefix with no reset. `SdkTurnExecutor.tool_fingerprint()` now hashes each
tool's name, description and input schema, read from `hermes_server.hermes_tools`,
the one list `build_hermes_server` wraps. Pinned here:

* it is deterministic — same toolset, same value, in a fresh executor too, and a
  schema's key order does not matter;
* rewording a BUILT-IN tool (whose description is a literal inside `hermes_tools`)
  moves it while the name list stays put — the case a name-only hash missed;
* a declared tool's description or schema moving moves it, and so does adding the
  content port;
* `hermes_tools` and `build_hermes_server` register the same tools in the same order.
"""
from __future__ import annotations

from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.infrastructure import hermes_server, sdk_runner
from claude_hermes.modules.turns.infrastructure.hermes_server import (
    build_hermes_server,
    hermes_tools,
    make_handlers,
)


class Costs:
    async def record(self, *a, **kw):
        return None


class WikiSearchPort:
    async def search(self, query, *, owner_id, limit=8, depth=1):
        return []


class ContentPort:
    """A content declaration, duck-typed — this file never imports `content`."""

    def __init__(self, *, description="Read and edit the content plan.",
                 schema=None):
        self.name = "content_plan"
        self.description = description
        self.schema = schema if schema is not None else {"action": str, "id": int}

    async def handle(self, args, *, owner_id, origin, thread_id):
        return {"content": [{"type": "text", "text": "ok"}]}


def executor(tmp_path, *, content=None, settings=None):
    return sdk_runner.SdkTurnExecutor(
        settings=settings or TurnSettings(), home=tmp_path, search=object(),
        costs=Costs(), hooks=None, wiki_search=WikiSearchPort(), content=content)


def test_the_same_toolset_gives_the_same_value(tmp_path):
    ex = executor(tmp_path, content=ContentPort())
    first = ex.tool_fingerprint()
    assert len(first) == 64 and int(first, 16) >= 0          # a sha256 hex digest
    assert ex.tool_fingerprint() == first
    assert executor(tmp_path, content=ContentPort()).tool_fingerprint() == first


def test_a_schemas_key_order_does_not_move_it(tmp_path):
    """Deterministic JSON with sorted keys: a dict rebuilt in another order is the
    same declaration, and must not cost every lane a reset."""
    a = executor(tmp_path, content=ContentPort(
        schema={"action": str, "id": int})).tool_fingerprint()
    b = executor(tmp_path, content=ContentPort(
        schema={"id": int, "action": str})).tool_fingerprint()
    assert a == b


def test_rewording_a_built_in_tool_moves_it(tmp_path, monkeypatch):
    """`session_search`'s description is a literal inside `hermes_tools`, not a
    port declaration — the case a names-only hash could never see."""
    ex = executor(tmp_path)
    before_names = ex.tool_names()
    before = ex.tool_fingerprint()

    real_tool = hermes_server.tool

    def reworded(name, description, schema):
        if name == "session_search":
            description += " Reworded."
        return real_tool(name, description, schema)

    monkeypatch.setattr(hermes_server, "tool", reworded)
    assert ex.tool_names() == before_names
    assert ex.tool_fingerprint() != before


def test_a_declared_description_or_schema_change_moves_it(tmp_path):
    """The `content_plan` description growing a sentence is exactly the deploy
    this fingerprint exists for: one clean reset instead of a silent miss."""
    base = executor(tmp_path, content=ContentPort())
    longer = executor(tmp_path, content=ContentPort(
        description="Read and edit the content plan. One more sentence."))
    wider = executor(tmp_path, content=ContentPort(
        schema={"action": str, "id": int, "body": str}))
    assert base.tool_names() == longer.tool_names() == wider.tool_names()
    assert len({base.tool_fingerprint(), longer.tool_fingerprint(),
                wider.tool_fingerprint()}) == 3


def test_adding_the_content_port_moves_it(tmp_path):
    assert (executor(tmp_path).tool_fingerprint()
            != executor(tmp_path, content=ContentPort()).tool_fingerprint())


def test_the_helper_is_the_list_the_server_registers():
    """One list, two readers: the fingerprint hashes what the server wraps, in the
    order it registers it."""
    handlers = make_handlers(search=object(), costs=Costs(),
                             wiki_search=WikiSearchPort(), content=ContentPort())
    tools = hermes_tools(handlers)
    _, names = build_hermes_server(handlers)
    assert names == ["mcp__hermes__" + t.name for t in tools]
    assert names[0] == "mcp__hermes__session_search"
    assert names[-1] == "mcp__hermes__content_plan"

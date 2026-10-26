"""The Stop-hook flush: what a turn wrote reaches the index, and nothing the
index does reaches the turn.

The rule these pin is asymmetric on purpose. A page written this turn must be
searchable this turn — otherwise `memory_search` quotes a note the model has
already replaced. But a database that is down, slow or wrong must be invisible to
the SDK: the hook swallows, and a hung connection is abandoned after
`FLUSH_TIMEOUT_SECONDS` rather than holding the reply.
"""
import asyncio

import pytest

from claude_hermes.modules.memory.infrastructure import turn_hooks
from claude_hermes.modules.memory.infrastructure.turn_hooks import (
    WikiTurnState,
    index_flush_factory,
    wiki_hooks,
)


class WS:
    """The attributes the indexer reads off a workspace."""

    def __init__(self, root):
        self.root = root
        self.wiki_root = root / "agent_home" / "wiki"
        self.is_owner = True


class FakeIndexer:
    def __init__(self, boom=None):
        self.calls: list[tuple[int, object, set[str]]] = []
        self._boom = boom

    async def touch(self, owner_id, workspace, rel_paths):
        self.calls.append((owner_id, workspace, set(rel_paths)))
        if self._boom is not None:
            await self._boom()

    async def reindex(self, owner_id, workspace):
        pass


def _stop(hooks):
    """The Stop matcher's hook list — one matcher, memory installs no other."""
    assert len(hooks["Stop"]) == 1
    return hooks["Stop"][0].hooks


async def _touch(hooks, state, tmp_path, rel):
    """Drive the real PostToolUse recorder so `state.pages` is filled the way a
    turn fills it, not by hand."""
    hook = hooks["PostToolUse"][0].hooks[0]
    await hook({"tool_name": "Write",
                "tool_input": {"file_path": str(tmp_path / rel)}}, "t", None)
    assert rel in state.pages


async def test_the_flush_indexes_what_the_turn_touched(tmp_path):
    state, indexer, ws = WikiTurnState(), FakeIndexer(), WS(tmp_path)
    hooks = wiki_hooks(tmp_path, state, indexer=indexer, owner_id=7, workspace=ws)
    await _touch(hooks, state, tmp_path, "topics/x.md")
    flush = _stop(hooks)[1]
    assert await flush({}, "t", None) == {}
    assert indexer.calls == [(7, ws, {"topics/x.md"})]


async def test_a_relative_write_is_recorded_against_the_lanes_cwd(tmp_path):
    """The wiki rules say "write pages under ./wiki/"; the CLI's cwd is the
    lane's `agent_home`, so `wiki/topics/x.md` is that page — not a path under
    whatever directory this worker process runs in."""
    wiki_root = tmp_path / "agent_home" / "wiki"
    state = WikiTurnState()
    hooks = wiki_hooks(wiki_root, state, indexer=FakeIndexer(), owner_id=7,
                       workspace=WS(wiki_root))
    hook = hooks["PostToolUse"][0].hooks[0]
    await hook({"tool_name": "Write",
                "tool_input": {"file_path": "wiki/topics/x.md"}}, "t", None)
    assert state.pages == {"topics/x.md"}
    # and the raw guard judges a relative path the same way
    raw = hooks["PreToolUse"][0].hooks[0]
    denied = await raw({"tool_name": "Edit",
                        "tool_input": {"file_path": "wiki/raw/src.md"}}, "t", None)
    assert denied.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


async def test_a_bare_context_indexes_nothing(tmp_path):
    """`owner_id == 0` is a fork with no lane owner. Indexing under a made-up id
    would make those pages searchable by whoever owns it."""
    state, indexer = WikiTurnState(), FakeIndexer()
    hooks = wiki_hooks(tmp_path, state, indexer=indexer, owner_id=0, workspace=WS(tmp_path))
    await _touch(hooks, state, tmp_path, "topics/x.md")
    assert await _stop(hooks)[1]({}, "t", None) == {}
    assert indexer.calls == []


async def test_a_turn_that_wrote_no_page_does_not_touch_the_database(tmp_path):
    indexer = FakeIndexer()
    hooks = wiki_hooks(tmp_path, WikiTurnState(), indexer=indexer, owner_id=7,
                       workspace=WS(tmp_path))
    assert await _stop(hooks)[1]({}, "t", None) == {}
    assert indexer.calls == []


def test_without_an_indexer_the_stop_list_is_what_it_always_was(tmp_path):
    hooks = wiki_hooks(tmp_path, WikiTurnState())
    assert len(_stop(hooks)) == 1


def test_the_flush_is_appended_after_the_gate(tmp_path):
    """Order matters only in one direction: the gate gets its chance to block
    before the flush runs. Flushing twice is idempotent, so whether the SDK runs
    later hooks after a block is not something this code may assume."""
    state = WikiTurnState()
    hooks = wiki_hooks(tmp_path, state, indexer=FakeIndexer(), owner_id=7,
                       workspace=WS(tmp_path))
    gate, flush = _stop(hooks)
    assert gate is not flush
    assert gate.__qualname__.startswith("stop_gate_factory")
    assert flush.__qualname__.startswith("index_flush_factory")


async def test_a_broken_index_never_reaches_the_turn(tmp_path):
    async def boom():
        raise RuntimeError("db down")

    state = WikiTurnState()
    state.pages.add("topics/x.md")
    flush = index_flush_factory(state, FakeIndexer(boom=boom), 7, WS(tmp_path))
    assert await flush({}, "t", None) == {}


async def test_a_hung_index_is_abandoned(tmp_path, monkeypatch):
    """The failure to fear is not an exception; it is a connection that never
    answers while the SDK waits to return the reply."""
    async def never():
        await asyncio.Event().wait()

    monkeypatch.setattr(turn_hooks, "FLUSH_TIMEOUT_SECONDS", 0.05)
    state = WikiTurnState()
    state.pages.add("topics/x.md")
    flush = index_flush_factory(state, FakeIndexer(boom=never), 7, WS(tmp_path))
    assert await asyncio.wait_for(flush({}, "t", None), 5) == {}


@pytest.mark.parametrize("kwargs", [{"indexer": None, "owner_id": 7}, {"owner_id": 7}])
def test_no_indexer_means_no_flush_whatever_the_owner(tmp_path, kwargs):
    hooks = wiki_hooks(tmp_path, WikiTurnState(), workspace=WS(tmp_path), **kwargs)
    assert len(_stop(hooks)) == 1

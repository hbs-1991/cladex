"""`wiki_hooks()`: the provider the composition root installs.

The shape is what matters — which SDK events memory contributes to, and that the
two optional halves (the WebFetch ingest hint, the index refresh) are opt-in, so a
background fork does not accidentally get a foreground turn's hooks.
"""
from claude_hermes.modules.memory.infrastructure.turn_hooks import WikiTurnState, wiki_hooks


class FakeIndex:
    """Satisfies WikiIndexPort structurally (see test_memory_ports.py)."""

    def __init__(self, root):
        self.root = root

    def index(self) -> str:
        return ""

    def index_mtime(self) -> float:
        return 0.0


def test_a_background_fork_gets_the_guard_the_touch_and_the_gate(tmp_path):
    hooks = wiki_hooks(tmp_path, WikiTurnState())
    assert set(hooks) == {"PreToolUse", "PostToolUse", "Stop"}
    assert [m.matcher for m in hooks["PreToolUse"]] == ["Write|Edit"]
    assert [m.matcher for m in hooks["PostToolUse"]] == ["Write|Edit"]


def test_a_foreground_turn_adds_the_fetch_hint_and_the_index_refresh(tmp_path):
    hooks = wiki_hooks(
        tmp_path, WikiTurnState(), index=FakeIndex(tmp_path), seen={},
        session_key="web:1", webfetch_hint=True,
    )
    assert [m.matcher for m in hooks["PostToolUse"]] == ["Write|Edit", "WebFetch"]
    assert len(hooks["UserPromptSubmit"]) == 1


async def test_the_raw_guard_in_the_provider_is_the_real_one(tmp_path):
    """One installed guard, end to end: wiki/raw/ stays immutable."""
    (tmp_path / "raw").mkdir()
    src = tmp_path / "raw" / "article.md"
    src.write_text("original", encoding="utf-8")
    guard = wiki_hooks(tmp_path, WikiTurnState())["PreToolUse"][0].hooks[0]
    out = await guard({"tool_name": "Edit", "tool_input": {"file_path": str(src)}}, None, None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- the composition root's two adapters -------------------------------------
# `WikiHooksProvider` and `WikiForkGuards` live in `core.app_context` because core
# may not import a module; what they must not do is drop an argument on the way
# through, which is exactly the kind of wiring bug no other test would see.


class FakeIndexer:
    async def touch(self, owner_id, workspace, rel_paths):
        pass

    async def reindex(self, owner_id, workspace):
        pass


def test_the_hooks_provider_passes_the_indexer_the_owner_and_the_workspace(tmp_path):
    from claude_hermes.core.app_context import WikiHooksProvider

    captured = {}

    def fake_wiki_hooks(root, state, **kwargs):
        captured.update(kwargs, root=root)
        return {"Stop": []}

    indexer = FakeIndexer()
    provider = WikiHooksProvider(FakeIndex(tmp_path), wiki_hooks=fake_wiki_hooks,
                                 new_state=WikiTurnState, indexer=indexer)
    ws = object()
    provider.build("tg:private:7", {}, ws, owner_id=7)
    assert captured["indexer"] is indexer
    assert captured["owner_id"] == 7 and captured["workspace"] is ws


def test_the_hooks_provider_without_an_indexer_still_builds(tmp_path):
    """Every existing call site omits the kwarg; the default must not be a
    half-wired provider that indexes under owner 0."""
    from claude_hermes.core.app_context import WikiHooksProvider

    hooks = WikiHooksProvider(FakeIndex(tmp_path), wiki_hooks=wiki_hooks,
                              new_state=WikiTurnState).build("tg:private:7", {})
    assert len(hooks["Stop"][0].hooks) == 1


def test_the_fork_guards_hand_out_a_flush_only_when_an_indexer_is_wired(tmp_path):
    from claude_hermes.core.app_context import WikiForkGuards
    from claude_hermes.modules.memory.infrastructure.turn_hooks import (
        index_flush_factory,
        page_touch_factory,
        raw_guard_factory,
        stop_gate_factory,
    )

    state = WikiTurnState()
    kwargs = dict(new_state=WikiTurnState, raw_guard=raw_guard_factory,
                  page_touch=page_touch_factory, stop_gate=stop_gate_factory)
    wired = WikiForkGuards(**kwargs, index_flush=index_flush_factory,
                           indexer=FakeIndexer())
    assert wired.index_flush(state, 7, object()) is not None
    assert WikiForkGuards(**kwargs).index_flush(state, 7, object()) is None

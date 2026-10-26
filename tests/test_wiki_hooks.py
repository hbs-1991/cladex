import pytest

from claude_hermes.modules.memory.infrastructure.turn_hooks import (
    WikiTurnState,
    index_refresh_factory,
    page_touch_factory,
    raw_guard_factory,
    stop_gate_factory,
    webfetch_hint_factory,
)
from claude_hermes.modules.memory.infrastructure.wiki_store import WikiStore


@pytest.fixture
def wiki(tmp_path):
    store = WikiStore(tmp_path)
    store.seed()
    return store


def _write(path, tool="Write"):
    return {"tool_name": tool, "tool_input": {"file_path": str(path)}}


def _denied(out) -> bool:
    return out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


async def test_raw_guard_denies_editing_a_source(wiki):
    src = wiki.root / "raw" / "article.md"
    src.write_text("original", encoding="utf-8")
    guard = raw_guard_factory(wiki.root)
    assert _denied(await guard(_write(src, tool="Edit"), None, None))


async def test_raw_guard_denies_overwriting_a_source(wiki):
    src = wiki.root / "raw" / "article.md"
    src.write_text("original", encoding="utf-8")
    guard = raw_guard_factory(wiki.root)
    assert _denied(await guard(_write(src, tool="Write"), None, None))


async def test_raw_guard_allows_a_new_source(wiki):
    guard = raw_guard_factory(wiki.root)
    out = await guard(_write(wiki.root / "raw" / "fresh.md"), None, None)
    assert out == {}


async def test_raw_guard_ignores_everything_outside_raw(wiki):
    guard = raw_guard_factory(wiki.root)
    page = wiki.root / "topics" / "llm-wiki.md"
    page.write_text("x", encoding="utf-8")
    assert await guard(_write(page, tool="Edit"), None, None) == {}


async def test_page_touch_records_pages_index_and_log_separately(wiki):
    state = WikiTurnState()
    hook = page_touch_factory(state, wiki.root)
    await hook(_write(wiki.root / "user" / "profile.md"), None, None)
    await hook(_write(wiki.root / "index.md"), None, None)
    await hook(_write(wiki.root / "log.md"), None, None)
    await hook(_write(wiki.root / "raw" / "src.md"), None, None)
    assert state.pages == {"user/profile.md"}      # raw/ is a source, not a page
    assert state.index_written and state.log_written


async def test_page_touch_ignores_writes_outside_the_wiki(wiki, tmp_path):
    state = WikiTurnState()
    hook = page_touch_factory(state, wiki.root)
    await hook(_write(tmp_path / "somewhere" / "else.md"), None, None)
    assert state.pages == set()


async def test_stop_gate_blocks_a_page_written_without_the_index(wiki):
    state = WikiTurnState(pages={"user/profile.md"})
    out = await stop_gate_factory(state)({"stop_hook_active": False}, None, None)
    assert out["decision"] == "block"
    assert "user/profile.md" in out["reason"]
    assert "index.md" in out["reason"] and "log.md" in out["reason"]


async def test_stop_gate_passes_when_index_and_log_were_written(wiki):
    state = WikiTurnState(pages={"user/profile.md"}, index_written=True, log_written=True)
    assert await stop_gate_factory(state)({"stop_hook_active": False}, None, None) == {}


async def test_stop_gate_passes_when_no_page_was_touched(wiki):
    assert await stop_gate_factory(WikiTurnState())({"stop_hook_active": False}, None, None) == {}


async def test_stop_gate_never_blocks_twice(wiki):
    """stop_hook_active is the loop breaker: one nudge, then let the turn finish."""
    state = WikiTurnState(pages={"user/profile.md"})
    assert await stop_gate_factory(state)({"stop_hook_active": True}, None, None) == {}


async def test_webfetch_hint_fires_once_per_turn(wiki):
    state = WikiTurnState()
    hook = webfetch_hint_factory(state)
    first = await hook({"tool_name": "WebFetch"}, None, None)
    second = await hook({"tool_name": "WebFetch"}, None, None)
    assert "wiki-ingest" in first["hookSpecificOutput"]["additionalContext"]
    assert second == {}


async def test_index_refresh_injects_only_when_the_index_moved(wiki):
    seen: dict[str, float] = {"k": wiki.index_mtime()}
    hook = index_refresh_factory(wiki, seen, "k")
    assert await hook({"prompt": "hi"}, None, None) == {}      # unchanged → silence

    index = wiki.root / "index.md"
    index.write_text("# Wiki index\n- topics/new.md — fresh\n", encoding="utf-8")
    import os
    later = wiki.index_mtime() + 10
    os.utime(index, (later, later))

    out = await hook({"prompt": "hi"}, None, None)
    assert "topics/new.md" in out["hookSpecificOutput"]["additionalContext"]
    assert await hook({"prompt": "hi"}, None, None) == {}      # and only once

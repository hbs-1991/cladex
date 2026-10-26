"""What a background fork may reach, and what it writes back into the index.

The review fork is the wiki's main rewriter — curation runs out of band (P3), so
most pages are written by a fork and not by the turn the user watched. Two
consequences are pinned here: it reads the shared namespace under the same
read-only fence a foreground turn gets, and it flushes the pages it wrote into
the wiki index, or `memory_search` would quote a stale snippet as memory until
the next lint or boot.

`WikiGuardsPort` is faked: `learning` declares it and `memory` satisfies it, and
this module may not import the provider to test the consumer.
"""
from __future__ import annotations

import pytest

import claude_hermes.modules.learning.infrastructure.fork_runner as fork_mod
from claude_hermes.core.config import Config
from claude_hermes.core.workspace import Workspace
from claude_hermes.integrations.claude_sdk.guards import WRITE_TOOLS
from claude_hermes.modules.learning.infrastructure.fork_runner import (
    SdkForkRunner,
    fork_readonly_fence,
    fork_roots,
)


class FakeGuards:
    """memory's hooks as learning sees them: opaque callables and a state."""

    def __init__(self, *, flush=True):
        self.flush_calls: list[tuple] = []
        self._flush = flush
        self.state = object()

    def new_turn_state(self):
        return self.state

    def raw_guard(self, wiki_root):
        return "raw-guard"

    def page_touch(self, state, wiki_root):
        return "page-touch"

    def stop_gate(self, state):
        return "stop-gate"

    def index_flush(self, state, owner_id, workspace):
        self.flush_calls.append((state, owner_id, workspace))
        return "index-flush" if self._flush else None


class Result:
    session_id = "fork-1"
    result = "done"


@pytest.fixture
def captured(monkeypatch):
    box: dict = {}

    async def fake_query(*, prompt, options):
        box["options"] = options
        yield Result()

    monkeypatch.setattr(fork_mod, "query", fake_query)
    return box


async def run_review(tmp_path, guards, ws, *, owner_id=0):
    runner = SdkForkRunner(Config(), tmp_path, guards)
    return await runner.run_review(
        prompt="p", system_append="", resume="sdk-1", skills_dir=ws.skills_dir,
        wiki_root=ws.wiki_root, touched=set(), workspace=ws, owner_id=owner_id)


def matchers(options, event):
    return list(options.hooks.get(event) or [])


# --- the shared namespace -----------------------------------------------------


def test_a_non_owners_fork_reads_the_shared_wiki(tmp_path):
    user = Workspace.for_user(tmp_path, "users/7")
    owner = Workspace.owner(tmp_path)
    assert fork_roots(user)[-1] == user.shared_wiki_root
    assert owner.shared_wiki_root not in fork_roots(owner)


async def test_the_fence_denies_both_shared_spellings_for_a_non_owner(tmp_path):
    """The review fork's own write guard already excludes the OWNER's shared
    tree (it is not under the user's `wiki_root`) — but not the user's own
    `wiki/shared/`, which a relative `wiki/shared/x.md` lands in. The fence is
    what closes that, so the deny teaches the fork the page is not its to fix."""
    ws = Workspace.for_user(tmp_path, "users/7")
    fence = fork_readonly_fence(ws)
    assert fence is not None and fence.matcher == WRITE_TOOLS
    guard = fence.hooks[0]

    async def decide(path):
        return await guard({"tool_name": "Write",
                            "tool_input": {"file_path": str(path)}}, "t", None)

    for denied in (ws.shared_wiki_root / "home.md",
                   ws.wiki_root / "shared" / "home.md", "wiki/shared/home.md"):
        out = await decide(denied)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert await decide(ws.wiki_root / "user" / "prefs.md") == {}


def test_the_owner_has_no_fence(tmp_path):
    assert fork_readonly_fence(Workspace.owner(tmp_path)) is None


async def test_both_forks_install_the_fence_for_a_non_owner(tmp_path, captured):
    ws = Workspace.for_user(tmp_path, "users/7")
    guards = FakeGuards()
    await run_review(tmp_path, guards, ws, owner_id=7)
    assert any(m.matcher == WRITE_TOOLS for m in matchers(captured["options"],
                                                          "PreToolUse"))

    await SdkForkRunner(Config(), tmp_path, guards).run_consolidation(
        prompt="p", skills_dir=ws.skills_dir, workspace=ws)
    assert any(m.matcher == WRITE_TOOLS for m in matchers(captured["options"],
                                                          "PreToolUse"))


async def test_the_owners_forks_carry_no_fence(tmp_path, captured):
    ws = Workspace.owner(tmp_path)
    await run_review(tmp_path, FakeGuards(), ws)
    assert not [m for m in matchers(captured["options"], "PreToolUse")
                if m.matcher == WRITE_TOOLS]


# --- the index flush ----------------------------------------------------------


async def test_the_review_fork_flushes_what_it_wrote_after_the_gate(tmp_path,
                                                                    captured):
    """After the gate, not instead of it: the gate is what makes the fork write
    `index.md` before it stops, and the flush is what makes the rows match the
    tree it just changed. Flushing twice is idempotent, so whether the SDK runs
    a later hook after a `block` does not matter here."""
    ws = Workspace.for_user(tmp_path, "users/7")
    guards = FakeGuards()
    await run_review(tmp_path, guards, ws, owner_id=7)
    stop = matchers(captured["options"], "Stop")
    assert [h for m in stop for h in m.hooks] == ["stop-gate", "index-flush"]
    assert guards.flush_calls == [(guards.state, 7, ws)]


async def test_no_indexer_leaves_the_stop_list_exactly_as_it_was(tmp_path,
                                                                 captured):
    """`index_flush` answering `None` is the composition with no index wired —
    every pre-index deployment, and every test that builds the guards by hand."""
    ws = Workspace.owner(tmp_path)
    guards = FakeGuards(flush=False)
    await run_review(tmp_path, guards, ws, owner_id=1)
    stop = matchers(captured["options"], "Stop")
    assert [h for m in stop for h in m.hooks] == ["stop-gate"]


async def test_a_guards_object_without_the_method_is_still_a_valid_fork(
        tmp_path, captured):
    """The port grew a method; an older provider is not a crashed review."""
    class Old:
        def new_turn_state(self):
            return object()

        def raw_guard(self, wiki_root):
            return "raw-guard"

        def page_touch(self, state, wiki_root):
            return "page-touch"

        def stop_gate(self, state):
            return "stop-gate"

    assert not hasattr(Old(), "index_flush")
    ws = Workspace.owner(tmp_path)
    await run_review(tmp_path, Old(), ws, owner_id=1)
    assert [h for m in matchers(captured["options"], "Stop")
            for h in m.hooks] == ["stop-gate"]


async def test_the_consolidation_fork_gets_no_flush(tmp_path, captured):
    """It writes skills, never wiki pages: there is nothing to reindex."""
    ws = Workspace.owner(tmp_path)
    guards = FakeGuards()
    await SdkForkRunner(Config(), tmp_path, guards).run_consolidation(
        prompt="p", skills_dir=ws.skills_dir, workspace=ws)
    assert guards.flush_calls == []
    assert matchers(captured["options"], "Stop") == []

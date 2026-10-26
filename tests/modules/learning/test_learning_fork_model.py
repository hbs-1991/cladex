"""Which model a background fork runs on, and what it says afterwards.

A fork resumes the LANE's history on the deploy's fork model, whatever the lane
itself is on (the story's Locked Decisions): a review pass is not the user's
conversation and must not cost what the user's model costs. Two things follow,
and both are pinned here — the model goes through `cli_id` like every other model
string in the codebase, and the fork logs which model actually answered it,
because nothing else in the system would ever show a substitution.
"""
from __future__ import annotations

import pytest

import claude_hermes.modules.learning.infrastructure.fork_runner as fork_mod
from claude_hermes.core.config import Config
from claude_hermes.core.workspace import Workspace
from claude_hermes.modules.learning.infrastructure.fork_runner import SdkForkRunner
from claude_hermes.modules.learning.infrastructure.fork_usage import drain_fork


class FakeGuards:
    """memory's hooks as learning sees them: opaque callables and a state."""

    def new_turn_state(self):
        return object()

    def raw_guard(self, wiki_root):
        return "raw-guard"

    def page_touch(self, state, wiki_root):
        return "page-touch"

    def stop_gate(self, state):
        return "stop-gate"


class Answer:
    """A main-loop `AssistantMessage` as `drain_fork` duck-types it."""

    def __init__(self, model, usage=None, parent_tool_use_id=None):
        self.model = model
        self.usage = usage
        self.parent_tool_use_id = parent_tool_use_id
        self.result = None


class Result:
    session_id = "fork-1"
    result = "done"


class FakeLog:
    def __init__(self):
        self.lines: list[tuple[str, dict]] = []

    def info(self, event, **kw):
        self.lines.append((event, kw))


@pytest.fixture
def captured(monkeypatch):
    box: dict = {}

    async def fake_query(*, prompt, options):
        box["options"] = options
        yield Answer("claude-sonnet-5")
        yield Result()

    monkeypatch.setattr(fork_mod, "query", fake_query)
    return box


async def run_review(tmp_path, cfg):
    ws = Workspace.owner(tmp_path)
    return await SdkForkRunner(cfg, tmp_path, FakeGuards()).run_review(
        prompt="p", system_append="a", resume="s",
        skills_dir=tmp_path / "skills", wiki_root=tmp_path / "wiki",
        touched=set(), workspace=ws)


# --- the options ------------------------------------------------------------


async def test_the_review_fork_runs_the_fork_model_through_cli_id(tmp_path, captured):
    await run_review(tmp_path, Config(model="claude-sonnet-5",
                                      review_model="claude-opus-5"))
    options = captured["options"]
    assert options.model == "claude-opus-5"
    # No effort: a fork is background work nobody is waiting on, and an effort
    # flag would be spend with no reader.
    assert options.effort is None


async def test_the_review_fork_falls_back_to_the_deploy_model(tmp_path, captured):
    await run_review(tmp_path, Config(model="claude-opus-5-5", review_model=""))
    assert captured["options"].model == "claude-opus-5-5"


async def test_the_fork_env_keeps_the_model_we_asked_for(tmp_path, captured):
    """`CLAUDE_CODE_NO_MODEL_FALLBACK=1` reaches the fork through
    `workspace_env`, MERGED over `NO_ADVISOR_ENV` — a replace would drop one of
    the two and neither failure is visible from the outside."""
    await run_review(tmp_path, Config())
    env = captured["options"].env
    assert env["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"
    assert env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"


async def test_the_curator_fork_prefers_its_own_model(tmp_path, captured):
    cfg = Config(model="claude-sonnet-5", review_model="claude-opus-5",
                 curator_model="claude-opus-5-5")
    await SdkForkRunner(cfg, tmp_path, FakeGuards()).run_consolidation(
        prompt="p", skills_dir=tmp_path / "skills",
        workspace=Workspace.owner(tmp_path))
    assert captured["options"].model == "claude-opus-5-5"
    assert captured["options"].effort is None
    assert captured["options"].env["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"


# --- what the fork reports --------------------------------------------------


async def messages(*msgs):
    for m in msgs:
        yield m


async def test_the_fork_logs_the_model_that_ran_and_its_cache_share():
    log = FakeLog()
    final = await drain_fork(log, "review", messages(
        Answer("claude-opus-5", usage={"input_tokens": 100,
                                       "cache_read_input_tokens": 700,
                                       "cache_creation_input_tokens": 200}),
        Result()))

    assert final == "done"
    [(event, kw)] = log.lines
    assert event == "fork_ran" and kw["fork"] == "review"
    assert kw["model"] == "claude-opus-5"
    assert kw["cache_read_share"] == 0.7


async def test_a_subagents_call_is_not_the_fork_and_the_result_is_not_counted():
    """A `ResultMessage`'s usage is the SUM over every call of the fork; counting
    it beside the per-step figures would double everything. A subagent's call is
    not the main loop either."""
    log = FakeLog()

    class SummedResult:
        result = "done"
        usage = {"input_tokens": 9999, "cache_read_input_tokens": 0,
                 "cache_creation_input_tokens": 0}

    await drain_fork(log, "curator", messages(
        Answer("claude-opus-5", usage={"input_tokens": 100,
                                       "cache_read_input_tokens": 100,
                                       "cache_creation_input_tokens": 0}),
        Answer("claude-haiku-4-5-20251001",
               usage={"input_tokens": 500, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0},
               parent_tool_use_id="c1"),
        SummedResult()))

    [(_, kw)] = log.lines
    assert kw["model"] == "claude-opus-5" and kw["cache_read_share"] == 0.5


async def test_the_synthetic_model_is_never_the_model_that_ran():
    log = FakeLog()
    await drain_fork(log, "review", messages(Answer("<synthetic>"), Result()))
    [(_, kw)] = log.lines
    assert kw["model"] is None and kw["cache_read_share"] is None

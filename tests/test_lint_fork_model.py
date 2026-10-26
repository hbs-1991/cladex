"""Which model the wiki-lint fork runs on, and what it reports afterwards.

The lint pass is the third background fork (beside review and curation) and
follows the same rule: the deploy's fork model, through `cli_id`, with no effort
and the CLI's own availability fallback turned off. It lives in `memory`, so it
cannot share `learning`'s helper — which is why the same two facts are pinned
again here rather than assumed.
"""
from __future__ import annotations

import pytest

import claude_hermes.modules.memory.infrastructure.lint_fork as lint_mod
from claude_hermes.core.config import Config
from claude_hermes.core.workspace import Workspace
from claude_hermes.modules.memory.infrastructure.lint_fork import (
    SdkForkRunner,
    WikiLintOptions,
)


class Answer:
    """A main-loop `AssistantMessage` as the fork loop duck-types it."""

    def __init__(self, model, usage=None, parent_tool_use_id=None):
        self.model = model
        self.usage = usage
        self.parent_tool_use_id = parent_tool_use_id
        self.result = None


class Result:
    result = "linted"


def options_for(tmp_path, cfg: Config):
    ws = Workspace.owner(tmp_path)
    return WikiLintOptions(cfg, tmp_path, tmp_path / "wiki", ws).build(dry_run=False)


def test_the_lint_fork_runs_the_fork_model_through_cli_id(tmp_path):
    cfg = Config(model="claude-sonnet-5", review_model="claude-opus-5")
    options = options_for(tmp_path, cfg)
    assert options.model == "claude-opus-5"
    # No effort: nobody waits on a lint pass.
    assert options.effort is None


def test_the_lint_fork_prefers_the_curator_model(tmp_path):
    cfg = Config(model="claude-sonnet-5", review_model="claude-opus-5",
                 curator_model="claude-opus-5-5")
    assert options_for(tmp_path, cfg).model == "claude-opus-5-5"


def test_the_lint_fork_keeps_the_model_we_asked_for(tmp_path):
    """MERGED over `NO_ADVISOR_ENV`, never replacing it: `env` is one dict the SDK
    lays over `os.environ`, so a replace would silently drop one of the two."""
    env = options_for(tmp_path, Config()).env
    assert env["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"
    assert env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"


@pytest.fixture
def messages(monkeypatch):
    """Patch the fork's `query` with a fixed message stream."""

    def use(*msgs):
        async def fake_query(*, prompt, options):
            for m in msgs:
                yield m

        monkeypatch.setattr(lint_mod, "query", fake_query)

    return use


async def test_the_lint_fork_logs_the_model_that_ran_and_its_cache_share(
        messages, caplog):
    messages(
        Answer("claude-opus-5", usage={"input_tokens": 100,
                                       "cache_read_input_tokens": 300,
                                       "cache_creation_input_tokens": 0}),
        # The CLI's own side call is not the main loop and must not stand in for
        # the model that ran.
        Answer("claude-haiku-4-5-20251001",
               usage={"input_tokens": 900, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0},
               parent_tool_use_id="c1"),
        Result())

    with caplog.at_level("INFO"):
        assert await SdkForkRunner().run("lint", object()) == "linted"

    line = next(r for r in caplog.records if "wiki_lint_fork_ran" in r.getMessage())
    assert "claude-opus-5" in line.getMessage()
    assert "0.75" in line.getMessage()

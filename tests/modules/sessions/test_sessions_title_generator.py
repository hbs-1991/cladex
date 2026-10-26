"""The one-shot namer: what it asks the model for, and what it refuses to keep."""
import claude_hermes.modules.sessions.infrastructure.title_generator as title_generator
from claude_hermes.modules.sessions.domain.constants import TITLE_MAX_CHARS
from claude_hermes.modules.sessions.infrastructure.title_generator import (
    SdkTitleGenerator,
    _clean,
)


class FakeResult:
    def __init__(self, result):
        self.result = result


def fake_query(produced, seen=None):
    def _query(prompt, options):
        if seen is not None:
            seen.append(options)

        async def _gen():
            yield FakeResult(produced)
        return _gen()
    return _query


def test_an_answer_instead_of_a_title_is_dropped_not_truncated():
    """No name beats a garbage name: the topic keeps its default, which reads far
    better than the first 128 characters of an answer."""
    answer = ("I can help you deploy with Ansible! To give you the most relevant "
              "guidance, could you clarify:\n\n1. What are you deploying?")
    assert _clean(answer) == ""
    assert _clean("") == "" and _clean('  "Ansible deploy"  ') == "Ansible deploy"
    assert len(_clean("word " * 200)) == 0          # too many words -> an answer
    assert _clean("x" * 500) == "x" * TITLE_MAX_CHARS


async def test_the_titler_replaces_the_agent_prompt_rather_than_appending_to_it(
    monkeypatch
):
    """With the claude_code preset loaded, the model answers the message instead of
    naming it — the six-word append never outweighs the whole agent persona."""
    seen = []
    monkeypatch.setattr(title_generator, "query", fake_query("Ansible deploy flow", seen))
    out = await SdkTitleGenerator("claude-haiku-4-5-20251001").generate("how do I deploy?")
    assert out == "Ansible deploy flow"
    assert isinstance(seen[0].system_prompt, str)   # a preset dict would 'append'
    assert seen[0].setting_sources == []            # "project" loads the CLAUDE.md persona
    assert seen[0].model == "claude-haiku-4-5-20251001"


async def test_the_titler_refuses_a_silent_model_swap_like_every_other_call(monkeypatch):
    """`workspace_env()` carries `CLAUDE_CODE_NO_MODEL_FALLBACK` for the turn and
    every fork — but this call is hand-built and never goes near that builder, so
    without its own copy the titler would be the one process where the CLI could
    still answer on a model of its own choosing, on `title_model`'s budget."""
    seen = []
    monkeypatch.setattr(title_generator, "query", fake_query("Deploy flow", seen))
    await SdkTitleGenerator("claude-haiku-4-5-20251001").generate("hi")
    assert seen[0].env["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"
    # …and it still turns the advisor off, which is what the env used to be for.
    assert seen[0].env["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"


async def test_the_titler_hands_the_model_no_tools(monkeypatch):
    """allowed_tools=[] does not take the tools away — the model still reaches for one,
    spends its single turn on it and the call dies on 'maximum number of turns'.
    tools=[] is what actually leaves it nothing to reach for."""
    seen = []
    monkeypatch.setattr(title_generator, "query", fake_query("Deploy flow", seen))
    await SdkTitleGenerator("m").generate("refactor the runner")
    assert seen[0].tools == [] and seen[0].max_turns == 1

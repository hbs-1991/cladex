"""The one-shot model call that names a thread. Satisfies `TitleGenerator`.

`query` is imported at module scope on purpose: it is the seam tests replace
(`monkeypatch.setattr(title_generator, "query", ...)`), and a call through the
module global is what makes that work.
"""
from __future__ import annotations

import structlog
from claude_agent_sdk import ClaudeAgentOptions, query

from claude_hermes.core.config import NO_ADVISOR_ENV

from ..domain.constants import TITLE_MAX_CHARS, TITLE_MAX_WORDS, TITLE_PROMPT

log = structlog.get_logger(__name__)

# The titler's own env. `workspace_env()` carries `CLAUDE_CODE_NO_MODEL_FALLBACK`
# for every turn and every fork, but this call is hand-built (see `generate`) and
# never goes near that builder, so it would be the one place where the CLI could
# still substitute a model of its own choosing. The titler runs on `title_model` —
# a cheap one-shot — and a silent upgrade to a bigger model is a bill nobody asked
# for, on a call nobody reads.
TITLE_ENV = {**NO_ADVISOR_ENV, "CLAUDE_CODE_NO_MODEL_FALLBACK": "1"}


def _clean(raw: str) -> str:
    """The title, or "" if the model answered the message instead of naming it.

    Dropping such a reply beats truncating it: the topic keeps its default name,
    which reads far better than the first 128 characters of an answer.
    """
    title = raw.strip().strip('"').strip()
    if not title:
        return ""
    if "\n" in title or len(title.split()) > TITLE_MAX_WORDS:
        log.warning("model answered instead of titling; keeping the default name: %r",
                    title[:80])
        return ""
    return title[:TITLE_MAX_CHARS]


class SdkTitleGenerator:
    """A cheap, tool-less, persona-less model call: the only thing it can produce
    is a few words."""

    def __init__(self, model: str):
        self._model = model

    async def generate(self, text: str) -> str:
        # Hand-built on purpose — NOT via options.base_options(). The builder's three
        # invariants (cwd=agent_home, setting_sources=["project"], bypassPermissions)
        # are all wrong here: this is a one-shot namer, not an agent. Routing it through
        # the builder would mean overriding every default, which is not reuse.
        options = ClaudeAgentOptions(
            model=self._model,
            max_turns=1,
            # tools=[], not just allowed_tools=[]: an empty allow-list still leaves the
            # tools on the table, and the model spends its one turn reaching for one,
            # so the call dies on "maximum number of turns" and the topic goes unnamed.
            tools=[],
            allowed_tools=[],
            # Empty setting_sources on purpose: with "project" the agent_home
            # CLAUDE.md persona loads and the model ANSWERS the message instead
            # of titling it.
            setting_sources=[],
            # A bare string REPLACES the system prompt; a {"preset": "claude_code"}
            # dict only appends to it, and the coding-agent persona underneath then
            # answers the message rather than naming it.
            system_prompt=TITLE_PROMPT,
            env=TITLE_ENV,
        )
        out = ""
        async for msg in query(prompt=text, options=options):
            if getattr(msg, "result", None):
                out = msg.result
        return _clean(out)

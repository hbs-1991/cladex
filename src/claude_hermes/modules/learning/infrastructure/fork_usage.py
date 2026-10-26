"""Driving a background fork's message stream, and saying what it cost.

Both forks in this module — the review pass and the curator — are a loop that
keeps the last `result` and throws everything else away. Two things must not be
thrown away with it: WHICH model answered, because a fork resumes the lane's
history on the deploy's fork model and a silent substitution would show up
nowhere else, and how much of its input came out of cache, because a fork that
re-reads a whole conversation cold is the NFR-1 regression the prompt-cache
discipline exists to catch.

Duck-typed on purpose (`model`, `usage`, `result`): this file may not import the
SDK's message classes to name what it is looking at — `fork_runner.py` is the one
place in `learning` that reaches `claude_agent_sdk`, and a test drives these
loops with plain objects.
"""
from __future__ import annotations

from claude_hermes.core.model_catalog import lookup

# The CLI's stand-in when nothing answered, and its own Haiku side call: neither
# is ever "the model that ran" (recorded live, story Dev Notes).
_SYNTHETIC_MODEL = "<synthetic>"

_PROMPT_KEYS = ("input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")


async def drain_fork(log, kind: str, messages) -> str | None:
    """Run one fork to the end, log what answered it, return its last `result`.

    Only a main-loop `AssistantMessage` carries both a model and a per-step
    usage; a `ResultMessage`'s usage is the SUM over every call of the fork and
    would double-count everything here, which is the same trap `_usage_wire` in
    `turns` documents.
    """
    final: str | None = None
    ran = ""
    totals = dict.fromkeys(_PROMPT_KEYS, 0)
    async for msg in messages:
        model = getattr(msg, "model", None)
        if model and getattr(msg, "parent_tool_use_id", None) is None:
            if model != _SYNTHETIC_MODEL:
                ran = lookup(model).id
            usage = getattr(msg, "usage", None)
            if isinstance(usage, dict):
                for key in _PROMPT_KEYS:
                    totals[key] += int(usage.get(key) or 0)
        if getattr(msg, "result", None):
            final = msg.result
    prompt_tokens = sum(totals.values())
    log.info("fork_ran", fork=kind, model=ran or None,
             cache_read_share=(round(totals["cache_read_input_tokens"] / prompt_tokens, 3)
                               if prompt_tokens else None))
    return final

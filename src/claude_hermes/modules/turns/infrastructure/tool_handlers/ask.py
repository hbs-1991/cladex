"""`ask_user`: put 2-5 options in front of the human and block the turn on the tap.

The registration gate (private chat only, and only where a presenter is wired) is
in `hermes_server.py`; the caps here are the ones that bound a live turn's wall
clock, which nothing else does.
"""
from __future__ import annotations

import structlog

from claude_hermes.modules.turns.application.ports import AskPresenterPort
from claude_hermes.modules.turns.domain.value_objects import CANCELLED

log = structlog.get_logger(__name__)


def make_ask_user(*, ctx, ask: AskPresenterPort, settings):
    async def ask_user_handler(args: dict) -> dict:
        def reply(text: str) -> dict:
            return {"content": [{"type": "text", "text": text}]}

        limit = getattr(settings, "ask_user_max_per_turn", 2)
        if ctx.flags.asks_used >= limit:
            # Nothing bounds a live turn's wall clock — unlike cron, which wraps
            # its run in asyncio.wait_for. Without this cap a model that keeps
            # re-asking holds the lane lock for max_turns * timeout.
            return reply(
                "You have already asked the user this turn and must not ask "
                "again; proceed with your best judgement and say what you assumed."
            )

        question = (args.get("question") or "").strip()
        if not question:
            return reply("ask_user needs a 'question'.")
        if len(question) > 500:
            return reply("Refused: keep the question under 500 characters.")

        options = [o.strip() for o in (args.get("options") or "").splitlines()
                   if o.strip()]
        if not 2 <= len(options) <= 5:
            return reply("Refused: give between 2 and 5 options, one per line.")
        if any(len(o) > 64 for o in options):
            return reply("Refused: each option must be 64 characters or fewer.")

        ctx.flags.asks_used += 1
        timeout = float(getattr(settings, "ask_user_timeout_seconds", 120.0))
        try:
            answer = await ask(ctx.chat_id, ctx.thread_id, question, options,
                               ctx.user_id, timeout, emit=ctx.emit)
        except Exception as e:
            log.exception("ask_user failed")
            return reply(f"Could not ask the user: {e}")

        if answer is CANCELLED or answer == CANCELLED:
            return reply("The user declined to choose. Proceed with your best "
                         "judgement and state which default you assumed.")
        if answer is None:
            return reply(f"The user did not answer within {timeout:.0f}s. Proceed "
                         "with your best judgement and state which default you "
                         "assumed.")
        return reply(f'The user chose: "{answer}"')

    return ask_user_handler

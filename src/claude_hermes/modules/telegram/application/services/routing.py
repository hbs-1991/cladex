"""Routing one Telegram update: which of the intercepted commands is this, and
does it reach the model at all?

Moved verbatim from `gateway/telegram.py::handle_event` in Wave 2. Every
comment in `handle` is a security invariant and travels with the code.
"""
from __future__ import annotations

from claude_hermes.core.config import Config
from claude_hermes.core.session_keys import build_session_key

from ...domain.constants import SCOPE_DEFAULT, SCOPE_THREAD
from ...domain.messages import _EFFORT_POINTER, _MODEL_OFF, _REFUSAL, _RESET_ACK
from ...domain.models import MessageEvent
from ..ports import AclPort, LearnPromptPort, ModelMenuPort, TurnRunnerPort
from .commands import CommandService
from .gating import verdict as _verdict


class MessageRouter:
    """The one place that decides between "silent", "refuse", "intercept" and
    "run the turn"."""

    def __init__(self, cfg: Config, *, runner: TurnRunnerPort, acl: AclPort,
                 commands: CommandService, learn_prompt: LearnPromptPort | None = None,
                 model_menu: ModelMenuPort | None = None):
        self._cfg = cfg
        self._runner = runner
        self._acl = acl
        self._commands = commands
        self._learn_prompt = learn_prompt
        self._model_menu = model_menu

    async def handle(self, event: MessageEvent, *, delete_message=None) -> str | None:
        """Route one Telegram update. Returns the final reply text, or None to stay
        silent."""
        text = event.text
        command, _, rest = text.partition(" ")
        command = command.split("@", 1)[0]          # tolerate /cmd@botname

        verdict = await _verdict(self._cfg, event, self._acl)

        if command == "/secret":
            # Handled here rather than passed to the runner: a secret that reaches run_turn
            # ends up in the LLM context, the SDK transcript and messages_fts, and no amount
            # of deleting the chat message gets it back out of those. The deletion runs even
            # for a denied sender — they may have pasted a real key, and it must be wiped
            # from the chat regardless of who they are. But an *ignored* chat stays ignored:
            # answering there would break the bot's silence on a stranger's command.
            # Authorization to *store* is the stricter is_owner check inside handle_secret.
            answer = await self._commands.handle_secret(
                rest, event, delete_message=delete_message)
            return None if verdict == "ignore" else answer

        if command == "/approve":
            # Handled here, before the runner, for the same reason as /secret: approving a
            # catalog entry makes something runnable in this container, and a prompt-injected
            # turn must not be able to enact its own draft. Only a literal owner message does.
            # In a group where the bot is not addressed the promotion (and the lane reset on
            # success) still runs while the reply is suppressed by the mention gate.
            key = build_session_key(event.chat_type, event.chat_id,
                                    user_id=event.user_id, thread_id=event.thread_id)
            answer = await self._commands.handle_approve(rest, event, session_key=key)
            return None if verdict == "ignore" else answer

        if verdict == "ignore":
            return None
        if verdict == "deny":
            return _REFUSAL if event.chat_type == "private" else None

        key = build_session_key(
            event.chat_type, event.chat_id, user_id=event.user_id,
            thread_id=event.thread_id
        )
        if command == "/new":
            await self._runner.reset(key)
            return _RESET_ACK
        if command in ("/model", "/effort"):
            # Intercepted here for the same reason /secret and /approve are, one
            # layer up: the SDK CLI runs BOTH of these as its own local commands in
            # non-interactive mode ("Set model to x"), so a turn carrying one would
            # switch the session's model with nothing recorded in any table and no
            # price shown to anybody. `/models` is a different word and still
            # reaches the model — `command` is an exact match, not a prefix.
            if command == "/effort":
                # One picker, not two: effort is part of the pair, and a second
                # keyboard that could set an effort against a model the lane no
                # longer runs is a way to lie about what will answer.
                return _EFFORT_POINTER
            if self._model_menu is None:
                return _MODEL_OFF
            scope = SCOPE_DEFAULT if rest.strip().lower().startswith("default") \
                else SCOPE_THREAD
            return await self._model_menu.open(event, scope)
        if command == "/learn" and self._learn_prompt is not None:
            text = self._learn_prompt.build(rest.strip())

        return await self._runner.run_turn(key, text, user_id=event.user_id,
                                           attachments=event.attachments)

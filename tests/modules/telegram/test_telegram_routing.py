"""`MessageRouter` and the gate, wired only through ports.

`tests/test_gateway.py` covers the same branches through the legacy shim; these
cover the two things the shim cannot express — an unwired `LearnPromptPort`, and
the callback service on its own.
"""
from __future__ import annotations

import pytest

from claude_hermes.core.config import Config
from claude_hermes.modules.telegram.application.services.callbacks import handle_callback
from claude_hermes.modules.telegram.application.services.gating import (
    should_title,
    verdict,
)
from claude_hermes.modules.telegram.application.services.routing import MessageRouter
from claude_hermes.modules.telegram.domain.models import MessageEvent
from claude_hermes.modules.turns.domain.exceptions import TurnFailed


class Acl:
    def __init__(self, decision="allow"):
        self._decision = decision

    async def check(self, user_id, chat_type, profile=None):
        return self._decision

    async def is_owner(self, user_id):
        return True


class Runner:
    def __init__(self):
        self.turns, self.resets = [], []

    async def run_turn(self, key, text, *, user_id=0, attachments=None,
                       channel="telegram"):
        self.turns.append((key, text))
        return "reply"

    async def reset(self, key):
        self.resets.append(key)


class LearnPrompt:
    def build(self, text):
        return f"<learn>{text}</learn>"


class Commands:
    def __init__(self):
        self.seen = []

    async def handle_secret(self, rest, event, *, delete_message=None):
        self.seen.append(("secret", rest))
        return "secret answer"

    async def handle_approve(self, rest, event, *, session_key):
        self.seen.append(("approve", rest, session_key))
        return "approve answer"


class Broker:
    def __init__(self, accept=True):
        self.calls = []
        self._accept = accept

    async def resolve(self, qid, token, user_id):
        self.calls.append((qid, token, user_id))
        return self._accept


def ev(**kw):
    base = dict(chat_type="private", chat_id=1, user_id=7, text="hi", mentioned=False)
    base.update(kw)
    return MessageEvent(**base)


def router(*, acl=None, runner=None, commands=None, learn_prompt=None, cfg=None):
    return MessageRouter(cfg or Config(), runner=runner or Runner(), acl=acl or Acl(),
                         commands=commands or Commands(), learn_prompt=learn_prompt)


async def test_learn_is_rewritten_when_the_port_is_wired():
    r = Runner()
    await router(runner=r, learn_prompt=LearnPrompt()).handle(ev(text="/learn ansible"))
    assert r.turns == [("tg:private:1", "<learn>ansible</learn>")]


async def test_learn_falls_through_unwrapped_when_no_prompt_builder_is_wired():
    """The command still reaches the model — the SDK CLI dispatches unknown
    slash commands — it simply is not rewritten."""
    r = Runner()
    await router(runner=r).handle(ev(text="/learn ansible"))
    assert r.turns == [("tg:private:1", "/learn ansible")]


async def test_an_ignored_chat_still_runs_the_secret_command_but_answers_nothing():
    c = Commands()
    reply = await router(
        cfg=Config(group_require_mention=True), commands=c,
    ).handle(ev(chat_type="supergroup", chat_id=-5, text="/secret K=v"))
    assert reply is None                  # silence preserved
    assert c.seen == [("secret", "K=v")]  # but the key is still wiped


async def test_approve_carries_the_lane_key_the_reset_will_use():
    c = Commands()
    await router(commands=c).handle(ev(text="/approve x", thread_id=9))
    assert c.seen == [("approve", "x", "tg:private:1:t9")]


async def test_a_denied_group_message_is_silent_but_a_private_one_is_refused():
    assert await router(acl=Acl("deny")).handle(ev()) is not None
    assert await router(acl=Acl("deny")).handle(
        ev(chat_type="supergroup", mentioned=True)) is None


async def test_the_gate_and_the_titler_agree_about_commands():
    assert await verdict(Config(), ev(), Acl()) == "run"
    assert await should_title(Config(), ev(text="/new"), Acl()) is False
    assert await should_title(Config(), ev(text="hi"), Acl()) is True


async def test_every_unparseable_callback_is_the_same_stale_answer():
    b = Broker()
    for data in ("", "garbage", "q:", "q:abc", "x:abc:0", "q:abc:0:extra", None):
        assert "no longer active" in await handle_callback(data, 7, b)
    assert b.calls == []            # none of them even reached the broker


async def test_a_refused_click_reads_as_stale_too():
    assert "no longer active" in await handle_callback("q:ab:0", 7, Broker(accept=False))
    assert "Got it" in await handle_callback("q:ab:0", 7, Broker(accept=True))


# --- QueuedTurnRunner -------------------------------------------------------
# The adapter the composition root gives `MessageRouter` since Wave 3b: a
# Telegram message starts a `turns` row and waits for the end event, and the
# process that runs it may be a worker in another container. What is worth
# pinning is the one behaviour a user would notice if it were dropped —
# double-texting a busy lane still gets answered.

class Launcher:
    """`TurnLauncher` as the adapter sees it: start (which can be busy) and wait."""

    queue_timeout_seconds = 300.0

    def __init__(self, *, busy_times=0, reply="reply", error=None):
        self.started = []
        self.waited = []
        self._busy_times = busy_times
        self._reply = reply
        self._error = error

    async def start(self, session_key, text, *, user_id=0, attachments=None,
                    channel="telegram"):
        if self._busy_times > 0:
            self._busy_times -= 1
            raise Busy("the agent is busy in this thread")
        self.started.append((session_key, text, user_id, channel))
        return f"t{len(self.started)}"

    async def wait(self, turn_id, session_key):
        self.waited.append((turn_id, session_key))
        if self._error is not None:
            raise self._error
        return self._reply


class Busy(Exception):
    """Stands in for turns' `LaneBusy`, which `core` may not import — the real
    class arrives through the `busy=` keyword, from `main.py`."""


class Resetter:
    def __init__(self):
        self.resets = []

    async def reset(self, key):
        self.resets.append(key)


def queued(launcher, **kw):
    from claude_hermes.core.app_context import QueuedTurnRunner

    kw.setdefault("busy_retry_seconds", 0.01)
    return QueuedTurnRunner(launcher, Resetter(), busy=Busy, **kw)


async def test_a_telegram_turn_starts_a_row_and_waits_for_it():
    launcher = Launcher()
    reply = await queued(launcher).run_turn("tg:private:1", "hi", user_id=7)
    assert reply == "reply"
    assert launcher.started == [("tg:private:1", "hi", 7, "telegram")]
    assert launcher.waited == [("t1", "tg:private:1")]


async def test_a_second_message_on_a_busy_lane_waits_its_turn_and_is_answered():
    """Today's behaviour, and the reason it is retried rather than refused:
    double-texting is the most common Telegram habit there is, and answering it
    with "Something went wrong on my side" would be a break, not a wording
    change."""
    launcher = Launcher(busy_times=3)
    assert await queued(launcher).run_turn("tg:private:1", "and another thing") \
        == "reply"
    assert len(launcher.started) == 1


async def test_a_lane_that_never_frees_gives_up_after_the_queue_timeout():
    launcher = Launcher(busy_times=10_000)
    with pytest.raises(Busy):
        await queued(launcher, timeout_seconds=0.03).run_turn("tg:private:1", "hi")


async def test_a_cancelled_turn_comes_back_as_none_so_no_final_message_is_sent():
    assert await queued(Launcher(reply=None)).run_turn("tg:private:1", "hi") is None


async def test_a_failed_turn_propagates_to_the_dispatchers_except():
    launcher = Launcher(error=TurnFailed("worker lost: lease expired"))
    with pytest.raises(TurnFailed):
        await queued(launcher).run_turn("tg:private:1", "hi")


async def test_reset_goes_straight_to_the_runner_because_it_starts_nothing():
    from claude_hermes.core.app_context import QueuedTurnRunner

    resetter = Resetter()
    await QueuedTurnRunner(Launcher(), resetter, busy=Busy).reset("tg:private:1")
    assert resetter.resets == ["tg:private:1"]

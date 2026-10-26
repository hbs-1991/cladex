"""`ask_user`'s body: what the model is told, and what never reaches the human.

The registration gate (a presenter, a turn, a PRIVATE lane) is pinned in
`tests/modules/turns/test_turns_tool_gates.py::test_ask_user_never_exists_outside_
a_private_lane`; here the tool exists, and the question is what it does with the
options, the per-turn cap and the timeout. Two tests run the whole chain —
handler → `AskService` → `AskBroker` — with a presenter that taps like a user; the
rest record the presenter call through a fake port, because what they assert is
exactly what reaches it.
"""
from claude_hermes.modules.turns.application.ask_service import AskService
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.domain.value_objects import CANCELLED, TurnContext
from claude_hermes.modules.turns.infrastructure.ask_broker import AskBroker
from claude_hermes.modules.turns.infrastructure.hermes_server import make_handlers


def _ctx(key="tg:private:1"):
    return TurnContext(session_key=key, chat_id=1, user_id=7)


class FakeAsk:
    """Records what the tool asked and replays a canned answer."""

    def __init__(self, answer="Redis"):
        self.calls = []
        self._answer = answer

    async def __call__(self, chat_id, thread_id, question, options, user_id, timeout,
                       emit=None):
        self.calls.append({"chat_id": chat_id, "question": question,
                           "options": options, "timeout": timeout, "emit": emit})
        return self._answer


class TappingPresenter:
    """Shows the keyboard and, like the user, taps `token` at once (or never)."""

    def __init__(self, broker, token=None):
        self._broker, self._token = broker, token
        self.presented = []

    async def present(self, chat_id, thread_id, qid, question, options):
        self.presented.append({"chat_id": chat_id, "question": question,
                               "options": list(options)})
        if self._token is not None:
            assert await self._broker.resolve(qid, self._token, user_id=7) is True
        return None

    async def dismiss(self, handle):
        pass


def _real_ask(token=None):
    broker = AskBroker()
    presenter = TappingPresenter(broker, token)
    return AskService(broker, presenter), presenter


def _handlers(ask, ctx=None, settings=None):
    return make_handlers(search=None, costs=None,
                         ctx=ctx if ctx is not None else _ctx(),
                         settings=settings if settings is not None else TurnSettings(),
                         ask=ask)


async def test_a_chosen_option_is_reported_to_the_model():
    svc, presenter = _real_ask("0")
    h = _handlers(svc)
    out = await h["ask_user"]({"question": "Which backend?",
                               "options": "Redis\nin-memory"})
    assert 'The user chose: "Redis"' in out["content"][0]["text"]
    assert presenter.presented[0]["options"] == ["Redis", "in-memory"]


async def test_blank_option_lines_are_dropped():
    fake = FakeAsk()
    h = _handlers(fake)
    await h["ask_user"]({"question": "q", "options": "Redis\n\n  \nin-memory\n"})
    assert fake.calls[0]["options"] == ["Redis", "in-memory"]


async def test_too_few_or_too_many_options_are_refused_without_asking():
    fake = FakeAsk()
    h = _handlers(fake)
    one = await h["ask_user"]({"question": "q", "options": "only"})
    six = await h["ask_user"]({"question": "q", "options": "\n".join("abcdef")})
    assert "between 2 and 5" in one["content"][0]["text"]
    assert "between 2 and 5" in six["content"][0]["text"]
    assert fake.calls == []          # nothing reached Telegram


async def test_cancel_and_timeout_have_distinct_replies():
    cancel, _ = _real_ask("c")
    h_cancel = _handlers(cancel)
    timeout, _ = _real_ask(None)                       # nobody ever taps
    h_timeout = _handlers(timeout, settings=TurnSettings(ask_user_timeout_seconds=0.01))
    args = {"question": "q", "options": "a\nb"}
    assert "declined" in (await h_cancel["ask_user"](args))["content"][0]["text"]
    assert "did not answer" in (await h_timeout["ask_user"](args))["content"][0]["text"]


async def test_the_cancel_sentinel_from_a_port_reads_as_declined():
    """The handler compares against `CANCELLED` by identity and by value, so a
    broker that hands the sentinel back over the wire (the Redis one) is still a
    decline and never a chosen option."""
    h = _handlers(FakeAsk(CANCELLED))
    out = await h["ask_user"]({"question": "q", "options": "a\nb"})
    assert "declined" in out["content"][0]["text"]


async def test_the_per_turn_cap_refuses_without_reaching_telegram():
    ctx = _ctx()
    fake = FakeAsk()
    h = _handlers(fake, ctx=ctx, settings=TurnSettings(ask_user_max_per_turn=2))
    args = {"question": "q", "options": "a\nb"}
    await h["ask_user"](args)
    await h["ask_user"](args)
    third = await h["ask_user"](args)
    assert "already asked" in third["content"][0]["text"]
    assert len(fake.calls) == 2          # the third never left the process
    assert ctx.flags.asks_used == 2


async def test_the_configured_timeout_is_passed_down():
    fake = FakeAsk()
    h = _handlers(fake, settings=TurnSettings(ask_user_timeout_seconds=7.5))
    await h["ask_user"]({"question": "q", "options": "a\nb"})
    assert fake.calls[0]["timeout"] == 7.5


async def test_the_tool_exists_on_a_web_lane_and_passes_emit():
    async def emit(type, payload):
        pass

    ctx = TurnContext(session_key="web:1:abc", chat_id=0, thread_id=None, user_id=1,
                      turn_id="t1", emit=emit)
    ask = FakeAsk()
    h = _handlers(ask, ctx=ctx)
    assert "ask_user" in h
    await h["ask_user"]({"question": "Which?", "options": "A\nB"})
    assert ask.calls[0]["emit"] is emit and ask.calls[0]["chat_id"] == 0

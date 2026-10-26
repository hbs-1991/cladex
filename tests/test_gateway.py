"""The Telegram gate and router, driven through `MessageRouter` — the object the
legacy `gateway.telegram.handle_event` built — plus the two pure helpers that
had no other pin: `chunk_text` and `thread_lane`.

`tests/modules/telegram/test_telegram_routing.py` covers the ports-only shape
(an unwired `LearnPromptPort`, the callback service); what stays here is the
behaviour a Telegram user would notice: which lane a message lands in, what
`/new` and `/learn` do, and what a denied or unaddressed sender gets back.
"""
from types import SimpleNamespace

from claude_hermes.core.config import Config
from claude_hermes.modules.learning.domain.prompts import build_learn_prompt
from claude_hermes.modules.telegram.application.services.gating import should_title
from claude_hermes.modules.telegram.application.services.routing import MessageRouter
from claude_hermes.modules.telegram.domain.formatting import chunk_text
from claude_hermes.modules.telegram.domain.models import MessageEvent
from claude_hermes.modules.telegram.infrastructure.aiogram_mapping import thread_lane


class FakeRunner:
    def __init__(self, reply="ok"):
        self.calls = []
        self.resets = []
        self.reply = reply

    async def run_turn(self, session_key, text, *, user_id=0, attachments=None,
                       channel="telegram"):
        self.calls.append((session_key, text))
        return self.reply

    async def reset(self, session_key):
        self.resets.append(session_key)


def ev(**kw):
    base = dict(chat_type="private", chat_id=1, user_id=7, text="hi", mentioned=False)
    base.update(kw)
    return MessageEvent(**base)


class AllowAcl:
    async def check(self, user_id, chat_type, profile=None):
        return "allow"


class DenyAcl:
    async def check(self, user_id, chat_type, profile=None):
        return "deny"


class NoCommands:
    """`/secret` and `/approve` are `tests/modules/telegram/test_telegram_commands.py`'s;
    nothing here sends either."""

    async def handle_secret(self, rest, event, *, delete_message=None):
        raise AssertionError("no test here sends /secret")

    async def handle_approve(self, rest, event, *, session_key):
        raise AssertionError("no test here sends /approve")


class LearnPrompt:
    """telegram's `LearnPromptPort` over learning's `build_learn_prompt` — what
    `main.run` wires (`LearnPromptBuilder`)."""

    def build(self, text):
        return build_learn_prompt(text)


async def handle_event(runner, cfg, event, *, acl):
    router = MessageRouter(cfg, runner=runner, acl=acl, commands=NoCommands(),
                           learn_prompt=LearnPrompt())
    return await router.handle(event)


async def test_new_command_resets_only_this_thread():
    runner = FakeRunner()
    reply = await handle_event(runner, Config(), ev(text="/new", thread_id=9), acl=AllowAcl())
    assert runner.resets == ["tg:private:1:t9"]
    assert runner.calls == []                 # no turn is run
    assert reply is not None and "fresh" in reply.lower()


async def test_new_command_tolerates_botname_suffix():
    runner = FakeRunner()
    await handle_event(runner, Config(), ev(text="/new@hermes_bot"), acl=AllowAcl())
    assert runner.resets == ["tg:private:1"]


async def test_group_without_mention_is_ignored():
    runner = FakeRunner()
    reply = await handle_event(
        runner, Config(group_require_mention=True),
        ev(chat_type="supergroup", chat_id=-5, mentioned=False),
        acl=AllowAcl(),
    )
    assert reply is None and runner.calls == []


async def test_group_with_mention_uses_per_user_key():
    runner = FakeRunner()
    await handle_event(
        runner, Config(),
        ev(chat_type="supergroup", chat_id=-5, user_id=7, mentioned=True),
        acl=AllowAcl(),
    )
    assert runner.calls[0][0] == "tg:supergroup:-5:7"


async def test_denied_private_user_gets_refusal():
    runner = FakeRunner()
    reply = await handle_event(runner, Config(), ev(), acl=DenyAcl())
    assert runner.calls == []
    assert "not" in reply.lower()


async def test_denied_group_user_gets_silence():
    runner = FakeRunner()
    reply = await handle_event(
        runner, Config(), ev(chat_type="supergroup", mentioned=True), acl=DenyAcl(),
    )
    assert reply is None and runner.calls == []


async def test_learn_command_wraps_prompt():
    runner = FakeRunner()
    await handle_event(runner, Config(), ev(text="/learn деплой через ansible"),
                       acl=AllowAcl())
    sent = runner.calls[0][1]
    assert "деплой через ansible" in sent
    assert "SKILL.md" in sent           # learn wrapper applied


async def test_learn_requires_exact_command_token():
    runner = FakeRunner()
    await handle_event(runner, Config(), ev(text="/learning about ansible"),
                       acl=AllowAcl())
    assert runner.calls[0][1] == "/learning about ansible"   # not wrapped


async def test_learn_with_botname_suffix_wraps_prompt():
    runner = FakeRunner()
    await handle_event(runner, Config(), ev(text="/learn@hermes_bot deploy flow"),
                       acl=AllowAcl())
    sent = runner.calls[0][1]
    assert "deploy flow" in sent and "@hermes_bot" not in sent
    assert "SKILL.md" in sent


async def test_an_ordinary_message_reaches_the_runner_and_its_reply_comes_back():
    runner = FakeRunner()
    reply = await handle_event(runner, Config(), ev(text="привет"), acl=AllowAcl())
    assert runner.calls == [("tg:private:1", "привет")] and reply == "ok"


def test_chunk_text_splits_on_paragraphs():
    text = "para1\n\n" + "x" * 3990 + "\n\npara3"
    chunks = chunk_text(text, limit=4000)
    assert all(len(c) <= 4000 for c in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")
    assert len(chunks) >= 2


def test_chunk_text_hard_splits_monolith():
    chunks = chunk_text("y" * 9000, limit=4000)
    assert [len(c) for c in chunks] == [4000, 4000, 1000]


def msg(chat_type="private", thread_id=None, is_topic=None, is_forum=False):
    return SimpleNamespace(
        message_thread_id=thread_id,
        is_topic_message=is_topic,
        chat=SimpleNamespace(type=chat_type, is_forum=is_forum),
    )


def test_thread_lane_accepts_private_topics():
    assert thread_lane(msg(thread_id=9)) == 9
    assert thread_lane(msg(thread_id=None)) is None


def test_thread_lane_rejects_a_supergroup_reply_chain():
    # Telegram stamps message_thread_id on plain reply chains too — that must not
    # fork a fresh session.
    assert thread_lane(msg("supergroup", thread_id=55, is_topic=False)) is None
    assert thread_lane(msg("supergroup", thread_id=55, is_topic=True)) == 55


async def test_denied_user_is_never_titled():
    assert await should_title(Config(), ev(thread_id=9), DenyAcl()) is False


async def test_unmentioned_group_message_is_never_titled():
    assert await should_title(
        Config(), ev(chat_type="supergroup", thread_id=9), AllowAcl()
    ) is False

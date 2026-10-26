"""Content approval cards on the gateway side: the `cnt:` buttons, a reply to a
card, and the cards' topic the titler must not rename.

The application functions are tested directly with a fake `ContentDecisionsPort`
(the content module is never imported). The last group drives the aiogram
handlers `create_dispatcher` registers, with fake messages, to pin the ORDER the
story locks: a card reply is intercepted before intake, the titler and the
router ever see it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from claude_hermes.core.config import Config
from claude_hermes.modules.telegram.application import ports
from claude_hermes.modules.telegram.application.services.content_cards import (
    handle_content_callback,
    intercept_card_reply,
    is_content_callback,
    skip_title,
)
from claude_hermes.modules.telegram.domain.cards import card_callback, parse_card_callback
from claude_hermes.modules.telegram.domain.messages import (
    _CB_STALE,
    _CONTENT_FAILED,
    _CONTENT_REDO_HINT,
    _CONTENT_STALE,
)
from claude_hermes.modules.telegram.domain.models import MessageEvent
from claude_hermes.modules.telegram.infrastructure.aiogram_mapping import reply_target
from claude_hermes.modules.telegram.infrastructure.dispatcher import create_dispatcher

OWNER = 7
STRANGER = 8
REV = "0123456789ab"
CARD_ID = 101


class Decisions:
    """A fake content plan: one live card, id `CARD_ID`, in chat 555; topic 9 is
    the cards' topic."""

    def __init__(self, *, approve="✅ Одобрено — выйдет ≈ 30.09 10:00",
                 redraft="🔁 Переделываю", error=None):
        self.calls = []
        self._approve = approve
        self._redraft = redraft
        self._error = error

    async def approve_from_card(self, item_id, revision, user_id):
        self.calls.append(("approve", item_id, revision, user_id))
        if self._error:
            raise self._error
        return self._approve

    async def redraft_from_reply(self, chat_id, reply_to_message_id, user_id, comment):
        self.calls.append(("redraft", chat_id, reply_to_message_id, user_id, comment))
        if self._error:
            raise self._error
        if chat_id == 555 and reply_to_message_id == CARD_ID:
            return self._redraft
        return None

    async def is_card_topic(self, chat_id, thread_id):
        self.calls.append(("topic", chat_id, thread_id))
        if self._error:
            raise self._error
        return chat_id == 555 and thread_id == 9


async def is_owner(user_id):
    return user_id == OWNER


def ev(**kw):
    base = dict(chat_type="private", chat_id=555, user_id=OWNER, text="shorter, please",
                mentioned=True, thread_id=9, message_id=200,
                reply_to_message_id=CARD_ID)
    base.update(kw)
    return MessageEvent(**base)


def test_the_fake_satisfies_the_port():
    assert isinstance(Decisions(), ports.ContentDecisionsPort)


def test_a_message_event_carries_no_reply_target_by_default():
    assert MessageEvent(chat_type="private", chat_id=1, user_id=1, text="",
                        mentioned=False).reply_to_message_id is None


# --- callback_data --------------------------------------------------------


def test_the_callback_shape_round_trips_and_fits_64_bytes():
    data = card_callback("a", 123456789, REV)
    assert data == f"cnt:a:123456789:{REV}"
    assert len(data.encode()) <= 64
    tap = parse_card_callback(data)
    assert (tap.action, tap.item_id, tap.revision) == ("a", 123456789, REV)


@pytest.mark.parametrize("data", [
    None, "", "cnt", "cnt:a:42", f"cnt:a:42:{REV}:x", f"mdl:a:42:{REV}",
    f"cnt:x:42:{REV}", f"cnt:a:abc:{REV}", f"cnt:a:0:{REV}", f"cnt:a:-1:{REV}",
    f"cnt:a:²:{REV}", "cnt:a:42:0123456789a", "cnt:a:42:0123456789abc",
    "cnt:a:42:0123456789xz", "cnt:a:42:0123456789AB",
])
def test_anything_but_an_exact_card_button_does_not_parse(data):
    assert parse_card_callback(data) is None


def test_is_content_callback_is_the_prefix_only():
    assert is_content_callback(f"cnt:a:42:{REV}")
    assert is_content_callback("cnt:garbage")      # routed here, answered stale
    assert not is_content_callback("mdl:t:m:x")
    assert not is_content_callback("q:abc:1")
    assert not is_content_callback(None)


# --- a button press -------------------------------------------------------


async def test_approve_asks_the_content_plan_with_the_parsed_button():
    decisions = Decisions()
    answer = await handle_content_callback(f"cnt:a:42:{REV}", OWNER, decisions,
                                           is_owner)
    assert answer == "✅ Одобрено — выйдет ≈ 30.09 10:00"
    assert decisions.calls == [("approve", 42, REV, OWNER)]


async def test_redo_only_explains_how_and_changes_nothing():
    decisions = Decisions()
    answer = await handle_content_callback(f"cnt:r:42:{REV}", OWNER, decisions,
                                           is_owner)
    assert answer == _CONTENT_REDO_HINT
    assert decisions.calls == []


@pytest.mark.parametrize("action", ["a", "r"])
async def test_a_strangers_tap_is_stale_and_never_reaches_the_plan(action):
    decisions = Decisions()
    answer = await handle_content_callback(f"cnt:{action}:42:{REV}", STRANGER,
                                           decisions, is_owner)
    assert answer == _CONTENT_STALE
    assert decisions.calls == []


async def test_a_malformed_button_or_no_plan_wired_is_stale():
    decisions = Decisions()
    assert await handle_content_callback("cnt:a:42:short", OWNER, decisions,
                                         is_owner) == _CONTENT_STALE
    assert await handle_content_callback(f"cnt:a:42:{REV}", OWNER, None,
                                         is_owner) == _CONTENT_STALE
    assert decisions.calls == []


async def test_a_failing_plan_still_answers_the_button():
    answer = await handle_content_callback(
        f"cnt:a:42:{REV}", OWNER, Decisions(error=RuntimeError("db down")), is_owner)
    assert answer == _CONTENT_FAILED


async def test_an_empty_or_overlong_answer_is_still_a_valid_popup():
    assert await handle_content_callback(
        f"cnt:a:42:{REV}", OWNER, Decisions(approve=""), is_owner) == _CONTENT_STALE
    long = await handle_content_callback(
        f"cnt:a:42:{REV}", OWNER, Decisions(approve="x" * 500), is_owner)
    assert len(long) == 200


# --- a reply to a card ----------------------------------------------------


async def test_the_owners_reply_to_a_live_card_is_a_redraft():
    decisions = Decisions()
    answer = await intercept_card_reply(ev(text="  shorter, please  "), decisions,
                                        is_owner)
    assert answer == "🔁 Переделываю"
    assert decisions.calls == [("redraft", 555, CARD_ID, OWNER, "shorter, please")]


async def test_a_reply_to_anything_else_proceeds():
    decisions = Decisions()
    assert await intercept_card_reply(ev(reply_to_message_id=50), decisions,
                                      is_owner) is None


async def test_a_message_that_replies_to_nothing_never_asks_the_plan():
    decisions = Decisions()
    assert await intercept_card_reply(ev(reply_to_message_id=None), decisions,
                                      is_owner) is None
    assert decisions.calls == []


async def test_the_owner_is_checked_before_the_plan_is_asked():
    decisions = Decisions()
    assert await intercept_card_reply(ev(user_id=STRANGER), decisions,
                                      is_owner) is None
    assert decisions.calls == []


async def test_no_plan_wired_intercepts_nothing():
    assert await intercept_card_reply(ev(), None, is_owner) is None


async def test_a_failing_plan_fails_closed():
    """The gateway cannot tell which replies are card replies during an outage,
    and a card reply must never become a turn."""
    assert await intercept_card_reply(ev(), Decisions(error=RuntimeError("db")),
                                      is_owner) == _CONTENT_FAILED


# --- the titler -----------------------------------------------------------


async def test_the_cards_topic_is_never_titled():
    decisions = Decisions()
    assert await skip_title(ev(thread_id=9), decisions) is True
    assert await skip_title(ev(thread_id=10), decisions) is False


async def test_a_flat_chat_or_no_plan_never_asks():
    decisions = Decisions()
    assert await skip_title(ev(thread_id=None), decisions) is False
    assert await skip_title(ev(), None) is False
    assert decisions.calls == []


async def test_a_failed_topic_lookup_skips_the_title():
    assert await skip_title(ev(), Decisions(error=RuntimeError("db"))) is True


def test_reply_target_reads_the_replied_to_message_id():
    assert reply_target(SimpleNamespace(reply_to_message=None)) is None
    assert reply_target(SimpleNamespace()) is None
    assert reply_target(SimpleNamespace(
        reply_to_message=SimpleNamespace(message_id=CARD_ID))) == CARD_ID


# --- the dispatcher's order ----------------------------------------------


class Acl:
    async def check(self, user_id, chat_type, profile=None):
        return "allow"

    async def is_owner(self, user_id):
        return user_id == OWNER


class Sender:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, thread_id, text):
        self.sent.append((chat_id, thread_id, text))

    async def send_draft(self, chat_id, thread_id, status, draft_id=1):
        return None


class Titler:
    def __init__(self):
        self.spawned = []

    async def is_new_thread(self, session_key, thread_id):
        return True

    def spawn(self, chat_id, thread_id, text, session_key=None):
        self.spawned.append((chat_id, thread_id, text))


class Intake:
    def __init__(self):
        self.intakes = []

    def specs(self, message):
        return [object()] if message.photo else []

    async def intake(self, message, *, gate, chat_action=None):
        self.intakes.append(gate)
        raise AssertionError("a card reply must not reach intake")


class Router:
    def __init__(self):
        self.events = []

    async def handle(self, event, *, delete_message=None):
        self.events.append(event)
        return "turn reply"


class Bus:
    def subscribe(self, key, listener):
        return lambda: None

    async def ready(self, key):
        return None


class Presenter:
    def __init__(self, sender, chat_id, thread_id, chat_action=None):
        self.finalized = []

    async def on_turn_event(self, event):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def finalize(self, text):
        self.finalized.append(text)


class World:
    def __init__(self, decisions):
        self.sender, self.titler = Sender(), Titler()
        self.intake, self.router = Intake(), Router()
        self.dp = create_dispatcher(
            Config(), bot_username="hermes_bot", router=self.router, acl=Acl(),
            sender=self.sender, titler=self.titler, intake=self.intake, bus=Bus(),
            presenter_factory=Presenter, content_decisions=decisions)

    async def message(self, **kw):
        await self.dp.message.handlers[0].callback(_message(**kw))

    async def tap(self, data, user_id=OWNER):
        answers = []

        async def answer(text):
            answers.append(text)

        cb = SimpleNamespace(data=data, from_user=SimpleNamespace(id=user_id),
                             answer=answer, message=None)
        await self.dp.callback_query.handlers[0].callback(cb)
        return answers


def _message(*, text="shorter, please", reply_to=CARD_ID, user_id=OWNER,
             thread_id=9, photo=None, caption=None):
    replied = (SimpleNamespace(message_id=reply_to,
                               from_user=SimpleNamespace(username="hermes_bot"))
               if reply_to else None)

    async def send_chat_action(*a, **kw):
        return None

    return SimpleNamespace(
        voice=None, audio=None, photo=photo, text=text, caption=caption,
        chat=SimpleNamespace(type="private", id=555),
        from_user=SimpleNamespace(id=user_id, first_name="O", last_name="",
                                  username="o"),
        reply_to_message=replied, message_id=200, message_thread_id=thread_id,
        is_topic_message=True, bot=SimpleNamespace(send_chat_action=send_chat_action))


async def test_a_card_reply_is_answered_and_never_titled_routed_or_downloaded():
    world = World(Decisions())
    await world.message()
    assert world.sender.sent == [(555, 9, "🔁 Переделываю")]
    assert world.router.events == [] and world.titler.spawned == []


async def test_a_captioned_photo_reply_is_intercepted_before_intake():
    world = World(Decisions())
    await world.message(text=None, photo=[object()], caption="new cover")
    assert world.sender.sent == [(555, 9, "🔁 Переделываю")]
    assert world.intake.intakes == [] and world.router.events == []


async def test_an_ordinary_message_in_the_cards_topic_is_a_turn_but_no_title():
    world = World(Decisions())
    await world.message(text="what's planned this week?", reply_to=1)
    assert [e.text for e in world.router.events] == ["what's planned this week?"]
    assert world.router.events[0].reply_to_message_id == 1
    assert world.titler.spawned == [] and world.sender.sent == []


async def test_a_new_topic_elsewhere_is_still_titled():
    world = World(Decisions())
    await world.message(text="plan a trip", reply_to=None, thread_id=12)
    assert world.titler.spawned == [(555, 12, "plan a trip")]
    assert len(world.router.events) == 1


async def test_without_a_plan_wired_a_reply_is_an_ordinary_turn():
    world = World(None)
    await world.message()
    assert len(world.router.events) == 1 and world.sender.sent == []


async def test_a_card_button_is_answered_by_the_gateway_without_a_broker():
    decisions = Decisions()
    world = World(decisions)
    assert await world.tap(f"cnt:a:42:{REV}") == ["✅ Одобрено — выйдет ≈ 30.09 10:00"]
    assert await world.tap(f"cnt:a:42:{REV}", user_id=STRANGER) == [_CONTENT_STALE]
    assert decisions.calls == [("approve", 42, REV, OWNER)]
    # Not a content button, no broker: the ask path's answer, unchanged.
    assert await world.tap("q:abc:1") == [_CB_STALE]

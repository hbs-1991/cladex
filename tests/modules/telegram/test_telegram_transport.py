"""The transport seam. `run_polling` and `handle_update` must reach the same
dispatcher, because that is the whole claim behind the webhook option: the way an
update arrives is a composition choice, not a behaviour change.
"""
from __future__ import annotations

import pytest

from claude_hermes.modules.telegram.infrastructure.gateway import TelegramGateway


class FakeDispatcher:
    def __init__(self):
        self.polled = []
        self.fed = []

    async def start_polling(self, bot):
        self.polled.append(bot)

    async def feed_update(self, bot, update):
        self.fed.append((bot, update))


UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 7,
        "date": 1757000000,
        "chat": {"id": 42, "type": "private"},
        "from": {"id": 42, "is_bot": False, "first_name": "Ann"},
        "text": "hi",
    },
}


async def test_run_polling_hands_the_bot_to_the_dispatcher():
    dp, bot = FakeDispatcher(), object()
    await TelegramGateway(bot, dp, username="hermes_bot").run_polling()
    assert dp.polled == [bot]


async def test_handle_update_feeds_a_raw_webhook_body_to_the_same_dispatcher():
    dp, bot = FakeDispatcher(), object()
    await TelegramGateway(bot, dp).handle_update(UPDATE)
    [(fed_bot, update)] = dp.fed
    assert fed_bot is bot
    assert update.update_id == 1
    assert update.message.text == "hi"
    assert update.message.chat.id == 42


async def test_a_malformed_body_raises_rather_than_being_silently_dropped():
    # The webhook route turns this into a 400; swallowing it here would make a
    # broken integration look healthy.
    with pytest.raises(Exception):
        await TelegramGateway(object(), FakeDispatcher()).handle_update({"nope": 1})


def test_the_gateway_exposes_what_the_composition_root_wires():
    dp, bot = FakeDispatcher(), object()
    gw = TelegramGateway(bot, dp, username="hermes_bot")
    assert gw.bot is bot and gw.dispatcher is dp and gw.username == "hermes_bot"

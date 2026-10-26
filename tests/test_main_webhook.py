"""The transport decision `main.run()` ends in, driven with a fake bot.

`start_transport` is the whole of D9's branch: which of `set_webhook` /
`delete_webhook` is called, with what, and what this process then blocks on. It is
a function rather than ten lines inside `run()` precisely so this file can exist —
booting a real process to find out whether `allowed_updates` came from the
dispatcher is not a test anyone would run twice.
"""
from __future__ import annotations

import asyncio

import pytest

from claude_hermes.core.config import Config
from claude_hermes.modules.telegram.infrastructure.transport import (
    start_transport,
    webhook_url,
)

SECRET = "s3cret"
ORIGIN = "https://cladex.example.com"


class FakeBot:
    """Records the two Bot API calls the transport is allowed to make."""

    def __init__(self):
        self.set_webhook_calls = []
        self.delete_webhook_calls = []

    async def set_webhook(self, **kwargs):
        self.set_webhook_calls.append(kwargs)
        return True

    async def delete_webhook(self, **kwargs):
        self.delete_webhook_calls.append(kwargs)
        return True


class FakeDispatcher:
    def __init__(self, used=("message", "callback_query")):
        self._used = list(used)

    def resolve_used_update_types(self):
        return list(self._used)


class FakeGateway:
    def __init__(self, dispatcher=None):
        self.dispatcher = dispatcher or FakeDispatcher()
        self.polled = False

    async def run_polling(self):
        self.polled = True


def cfg(**kw) -> Config:
    base = dict(telegram_mode="webhook", telegram_webhook_secret=SECRET,
                web_enabled=True, web_public_origin=ORIGIN)
    base.update(kw)
    return Config(**base)


async def finished_task():
    """A `web_task` that is already done — the transport awaits it and returns."""
    return await asyncio.sleep(0)


# --- webhook mode -------------------------------------------------------------


async def test_webhook_mode_registers_the_url_and_blocks_on_the_web_server():
    bot, gateway = FakeBot(), FakeGateway()
    task = asyncio.create_task(finished_task())
    await start_transport(cfg(), bot=bot, gateway=gateway, web_task=task)

    assert bot.delete_webhook_calls == []
    [call] = bot.set_webhook_calls
    assert call["url"] == f"{ORIGIN}/telegram/webhook/{SECRET}"
    assert call["secret_token"] == SECRET
    # …and it awaited the web task rather than starting a poll: in webhook mode
    # the HTTP server is this process's main loop.
    assert task.done() and gateway.polled is False


async def test_allowed_updates_comes_from_the_dispatcher_not_a_literal():
    """aiogram's `start_polling` defaults to `resolve_used_update_types()`. A
    hardcoded list here would make the two transports diverge the day a handler
    for a new update type is added — in exactly one of them, silently."""
    bot = FakeBot()
    gateway = FakeGateway(FakeDispatcher(("message", "my_chat_member")))
    await start_transport(cfg(), bot=bot, gateway=gateway,
                          web_task=asyncio.create_task(finished_task()))
    assert bot.set_webhook_calls[0]["allowed_updates"] == ["message", "my_chat_member"]


async def test_pending_updates_are_never_dropped():
    """An update that arrived while this process was restarting is a message a
    person sent; dropping it is a reply that never comes."""
    bot = FakeBot()
    await start_transport(cfg(), bot=bot, gateway=FakeGateway(),
                          web_task=asyncio.create_task(finished_task()))
    assert bot.set_webhook_calls[0]["drop_pending_updates"] is False


async def test_a_trailing_slash_on_the_origin_does_not_double_the_path():
    assert webhook_url("https://x.test/", "abc") == "https://x.test/telegram/webhook/abc"


@pytest.mark.parametrize("overrides, reason", [
    ({"telegram_webhook_secret": ""}, "CH_TELEGRAM_WEBHOOK_SECRET"),
    ({"web_enabled": False}, "web_enabled"),
    ({"web_public_origin": ""}, "web_public_origin"),
])
async def test_webhook_mode_refuses_to_start_without_what_it_needs(overrides, reason):
    """The route that receives updates is one of the web app's routes. Without the
    server, or without a public URL to point Telegram at, a webhook deployment
    would come up looking healthy and never hear a single message — so it is a
    `SystemExit` naming the key, at boot."""
    bot, gateway = FakeBot(), FakeGateway()
    with pytest.raises(SystemExit) as exc:
        await start_transport(cfg(**overrides), bot=bot, gateway=gateway,
                              web_task=asyncio.create_task(finished_task()))
    assert reason in str(exc.value)
    assert bot.set_webhook_calls == [] and gateway.polled is False


async def test_webhook_mode_without_a_web_task_is_the_same_refusal():
    with pytest.raises(SystemExit):
        await start_transport(cfg(), bot=FakeBot(), gateway=FakeGateway(),
                              web_task=None)


# --- polling mode -------------------------------------------------------------


async def test_polling_mode_deletes_the_webhook_before_it_polls():
    """Load-bearing, not documentation: verified against aiogram 3.29.1,
    `start_polling` does NOT remove a webhook that is still registered, and
    `getUpdates` behind one answers 409 forever. A deploy that switched back from
    webhook mode would simply stop hearing anything."""
    bot, gateway = FakeBot(), FakeGateway()
    await start_transport(Config(telegram_mode="polling"), bot=bot, gateway=gateway)

    assert bot.delete_webhook_calls == [{"drop_pending_updates": False}]
    assert bot.set_webhook_calls == []
    assert gateway.polled is True


async def test_polling_mode_ignores_a_configured_webhook_secret():
    """`telegram_mode` is the switch, and nothing else is. A secret left in `.env`
    from an experiment must not put the process in webhook mode."""
    bot, gateway = FakeBot(), FakeGateway()
    await start_transport(
        Config(telegram_mode="polling", telegram_webhook_secret=SECRET,
               web_enabled=True, web_public_origin=ORIGIN),
        bot=bot, gateway=gateway, web_task=asyncio.create_task(finished_task()))
    assert bot.set_webhook_calls == [] and gateway.polled is True

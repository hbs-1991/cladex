"""The Bot API adapters lifted out of `main.py`: the raw client, the question
keyboard and the menu button. None of these had a test before — they were
closures inside `run()`, which no unit test can reach.
"""
from __future__ import annotations

import pytest

from claude_hermes.modules.telegram.infrastructure.bot_api import BotApiClient
from claude_hermes.modules.telegram.infrastructure.menu import ensure_menu_button
from claude_hermes.modules.telegram.infrastructure.questions import TelegramQuestions


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise RuntimeError(f"HTTP {self._status}")

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, payload=None, status=200):
        self.posts = []
        self._payload = payload if payload is not None else {"ok": True, "result": {}}
        self._status = status

    async def post(self, url, json):
        self.posts.append((url, json))
        return FakeResponse(self._payload, self._status)


class FakeCall:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result if result is not None else {}
        self._error = error

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        if self._error:
            raise self._error
        return self._result


async def test_the_client_builds_the_token_path_and_unwraps_result():
    http = FakeHttp({"ok": True, "result": {"message_id": 5}})
    got = await BotApiClient("123:ABC", http).call("sendMessage", {"chat_id": 1})
    assert http.posts == [("https://api.telegram.org/bot123:ABC/sendMessage",
                           {"chat_id": 1})]
    assert got == {"message_id": 5}


async def test_an_api_level_failure_raises_even_on_http_200():
    http = FakeHttp({"ok": False, "description": "chat not found"})
    with pytest.raises(RuntimeError):
        await BotApiClient("t", http).call("sendMessage", {})


async def test_a_result_less_ok_response_is_an_empty_dict():
    got = await BotApiClient("t", FakeHttp({"ok": True})).call("deleteMessage", {})
    assert got == {}


async def test_the_client_is_usable_as_the_bare_call_callable():
    http = FakeHttp()
    client = BotApiClient("t", http)
    await client("getMe", {})
    assert http.posts[0][0].endswith("/getMe")


async def test_a_question_renders_one_button_per_option_plus_cancel():
    call = FakeCall({"message_id": 9})
    sent = await TelegramQuestions(call).present(42, 7, "ab12", "Which store?",
                                                 ["Redis", "in-memory"])
    method, payload = call.calls[0]
    assert method == "sendMessage" and sent == {"message_id": 9}
    assert payload["chat_id"] == 42 and payload["message_thread_id"] == 7
    assert "Which store?" in payload["text"]
    assert payload["reply_markup"]["inline_keyboard"] == [
        [{"text": "Redis", "callback_data": "q:ab12:0"}],
        [{"text": "in-memory", "callback_data": "q:ab12:1"}],
        [{"text": "✖ Cancel", "callback_data": "q:ab12:c"}],
    ]


async def test_a_threadless_chat_omits_the_thread_id():
    call = FakeCall()
    await TelegramQuestions(call).present(42, None, "q1", "?", ["a"])
    assert "message_thread_id" not in call.calls[0][1]


async def test_a_lane_with_no_chat_asks_telegram_nothing():
    call = FakeCall()
    assert await TelegramQuestions(call).present(0, None, "q1", "?", ["a"]) is None
    assert call.calls == []


async def test_dismiss_clears_the_keyboard_and_never_raises():
    call = FakeCall(error=RuntimeError("message is gone"))
    await TelegramQuestions(call).dismiss(42, {"message_id": 9})
    assert call.calls[0][0] == "editMessageReplyMarkup"
    assert call.calls[0][1]["reply_markup"] == {"inline_keyboard": []}


async def test_dismiss_on_a_question_that_was_never_sent_is_a_no_op():
    call = FakeCall()
    await TelegramQuestions(call).dismiss(42, None)
    assert call.calls == []


async def test_the_menu_button_points_at_the_public_origin():
    call = FakeCall()
    assert await ensure_menu_button(call, "https://cladex.test") is True
    method, payload = call.calls[0]
    assert method == "setChatMenuButton"
    assert payload["menu_button"]["web_app"]["url"] == "https://cladex.test/"


async def test_a_non_https_origin_is_never_registered():
    # Telegram refuses anything else, so the call is not worth making.
    call = FakeCall()
    assert await ensure_menu_button(call, "http://localhost:8080") is False
    assert call.calls == []


async def test_a_failed_menu_button_does_not_stop_the_boot():
    call = FakeCall(error=RuntimeError("bot is restricted"))
    assert await ensure_menu_button(call, "https://cladex.test") is False

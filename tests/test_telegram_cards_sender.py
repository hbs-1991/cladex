"""`TelegramApprovalCards` — the payloads a content approval card becomes.

A fake `Bot` records every aiogram call instead of sending it, so these pin the
shapes: which method carries which media, that the buttons ride on the text
message after the media, what `callback_data` they hold, and that every failure
comes back as data rather than an exception (the content tick keeps an outbox
row on what this answers).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
)
from aiogram.methods import SendMessage

from claude_hermes.modules.telegram.domain.cards import parse_card_callback
from claude_hermes.modules.telegram.domain.messages import _CONTENT_CUT
from claude_hermes.modules.telegram.infrastructure.cards import TelegramApprovalCards

REV = "0123456789ab"


def _api_error(cls, message):
    return cls(method=SendMessage(chat_id=1, text="x"), message=message)


class FakeBot:
    """Records (method, args, kwargs); `fail` maps a method name to an exception
    raised on its FIRST call only, so a fallback can be observed succeeding."""

    def __init__(self, fail=None):
        self.calls = []
        self._fail = dict(fail or {})
        self._next_id = 100

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self._fail:
            raise self._fail.pop(name)
        self._next_id += 1
        return SimpleNamespace(message_id=self._next_id)

    async def send_media_group(self, chat_id, media, **kw):
        self._record("send_media_group", chat_id, media, **kw)
        return [SimpleNamespace(message_id=1)]

    async def send_photo(self, chat_id, photo, **kw):
        return self._record("send_photo", chat_id, photo, **kw)

    async def send_video(self, chat_id, video, **kw):
        return self._record("send_video", chat_id, video, **kw)

    async def send_document(self, chat_id, document, **kw):
        return self._record("send_document", chat_id, document, **kw)

    async def send_message(self, chat_id, text, **kw):
        return self._record("send_message", chat_id, text, **kw)

    def names(self):
        return [c[0] for c in self.calls]


class FakeApi:
    def __init__(self, error=None):
        self.calls = []
        self._error = error

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        if self._error:
            raise self._error
        return {}


class FakeTopics:
    def __init__(self, result=77):
        self.created = []
        self._result = result

    async def create(self, chat_id, name):
        self.created.append((chat_id, name))
        return self._result


def files(tmp_path, *names):
    out = []
    for name in names:
        p = tmp_path / name
        p.write_bytes(b"x")
        out.append(str(p))
    return out


def card(**kw):
    base = {
        "item_id": 42, "chat_id": 555, "thread_id": 9, "title": "Launch <day>",
        "channel": "instagram", "format": "carousel", "body": "Hello & welcome",
        "publish_at_local": "30.09 10:00", "media": [], "media_kind": "none",
        "revision": REV, "calendar_url": "https://hermes.example/calendar?item=42",
    }
    base.update(kw)
    return base


def cards(bot=None, api=None, topics=None):
    return TelegramApprovalCards(bot or FakeBot(), api or FakeApi(),
                                 topics or FakeTopics())


def keyboard(call):
    return call[2]["reply_markup"].inline_keyboard


async def test_a_carousel_is_one_media_group_then_the_text_with_the_buttons(tmp_path):
    bot = FakeBot()
    paths = files(tmp_path, "1.png", "2.png", "3.png")
    got = await cards(bot).send(card(media=paths, media_kind="images"))

    assert bot.names() == ["send_media_group", "send_message"]
    _, (chat_id, media), kw = bot.calls[0]
    assert chat_id == 555 and kw["message_thread_id"] == 9
    assert [m.type for m in media] == ["photo"] * 3
    assert [m.media.path for m in media] == paths
    # The card's id is the TEXT message's — the one a reply is matched against —
    # not the media group's (id 1 in the fake).
    assert got == {"message_id": 102, "thread_id": 9}


async def test_the_text_message_is_escaped_html_with_three_buttons():
    bot = FakeBot()
    await cards(bot).send(card())
    name, (chat_id, text), kw = bot.calls[-1]
    assert name == "send_message" and kw["parse_mode"] == "HTML"
    assert "<b>Launch &lt;day&gt;</b>" in text
    assert "instagram · carousel" in text and "30.09 10:00" in text
    assert "Hello &amp; welcome" in text
    rows = keyboard(bot.calls[-1])
    approve, redo = rows[0]
    assert (approve.text, redo.text) == ("Одобрить", "Переделать")
    assert approve.callback_data == f"cnt:a:42:{REV}"
    assert redo.callback_data == f"cnt:r:42:{REV}"
    assert parse_card_callback(approve.callback_data).item_id == 42
    assert rows[1][0].text == "Открыть в календаре"
    assert rows[1][0].url == "https://hermes.example/calendar?item=42"
    for button in rows[0]:
        assert len(button.callback_data.encode()) <= 64


@pytest.mark.parametrize("url", [None, "", "http://localhost:8000/calendar?item=42"])
async def test_the_calendar_button_is_https_only(url):
    bot = FakeBot()
    await cards(bot).send(card(calendar_url=url))
    rows = keyboard(bot.calls[-1])
    assert len(rows) == 1 and len(rows[0]) == 2


async def test_one_image_is_a_photo_and_a_video_is_a_video(tmp_path):
    bot = FakeBot()
    await cards(bot).send(card(media=files(tmp_path, "a.png"), media_kind="images"))
    await cards(bot).send(card(media=files(tmp_path, "v.mp4"), media_kind="video"))
    assert bot.names() == ["send_photo", "send_message", "send_video", "send_message"]
    assert bot.calls[2][2]["supports_streaming"] is True


async def test_no_media_is_the_text_alone_and_a_flat_chat_has_no_thread():
    bot = FakeBot()
    got = await cards(bot).send(card(thread_id=None))
    assert bot.names() == ["send_message"]
    assert bot.calls[0][2]["message_thread_id"] is None
    assert got == {"message_id": 101, "thread_id": None}


@pytest.mark.parametrize("error", [
    _api_error(TelegramBadRequest, "Bad Request: PHOTO_INVALID_DIMENSIONS"),
    _api_error(TelegramEntityTooLarge, "Request Entity Too Large"),
])
async def test_a_file_refused_as_media_falls_back_to_a_document(tmp_path, error):
    bot = FakeBot(fail={"send_photo": error})
    path = files(tmp_path, "huge.png")
    got = await cards(bot).send(card(media=path, media_kind="images"))
    assert bot.names() == ["send_photo", "send_document", "send_message"]
    assert bot.calls[1][1][1].path == path[0]
    assert "message_id" in got


async def test_a_refused_media_group_sends_every_file_as_a_document(tmp_path):
    bot = FakeBot(fail={"send_media_group": _api_error(
        TelegramBadRequest, "Bad Request: wrong aspect ratio")})
    await cards(bot).send(card(media=files(tmp_path, "1.png", "2.png"),
                               media_kind="images"))
    assert bot.names() == ["send_media_group", "send_document", "send_document",
                           "send_message"]


@pytest.mark.parametrize("method", ["send_media_group", "send_message"])
async def test_a_missing_topic_is_topic_gone_and_nothing_follows(tmp_path, method):
    bot = FakeBot(fail={method: _api_error(
        TelegramBadRequest, "Bad Request: message thread not found")})
    got = await cards(bot).send(card(media=files(tmp_path, "1.png", "2.png"),
                                     media_kind="images"))
    assert got == {"topic_gone": True}
    # No document fallback for a topic that is gone — it is not the file's fault.
    assert "send_document" not in bot.names()


async def test_any_other_failure_is_an_error_and_never_raises(tmp_path):
    bot = FakeBot(fail={"send_message": _api_error(
        TelegramForbiddenError, "Forbidden: bot was blocked by the user")})
    got = await cards(bot).send(card())
    assert got == {"error": "Forbidden: bot was blocked by the user"}


async def test_an_error_reason_never_carries_the_bot_token():
    class Boom(FakeBot):
        async def send_message(self, chat_id, text, **kw):
            raise RuntimeError("POST https://api.telegram.org/bot123:ABC-def/x failed")

    got = await cards(Boom()).send(card())
    assert "123:ABC" not in got["error"] and "bot<token>" in got["error"]


async def test_a_missing_media_file_sends_nothing(tmp_path):
    bot = FakeBot()
    got = await cards(bot).send(card(media=[str(tmp_path / "gone.png")],
                                     media_kind="images"))
    assert got == {"error": "media file missing: gone.png"}
    assert bot.calls == []


async def test_a_long_body_is_cut_to_fit_with_a_pointer_to_the_calendar():
    bot = FakeBot()
    await cards(bot).send(card(body="<&>" * 3000))
    text = bot.calls[-1][1][1]
    assert len(text) <= 4096
    assert _CONTENT_CUT in text
    # Cut before escaping: no entity is ever split in half.
    body = text.split("\n\n", 1)[1].rsplit("\n\n", 1)[0]
    assert body.replace("&lt;&amp;&gt;", "") == ""


async def test_edit_replaces_the_text_without_a_keyboard():
    api = FakeApi()
    await cards(api=api).edit(555, 101, "✅ Одобрено — выйдет ≈ 30.09 10:00")
    assert api.calls == [("editMessageText", {
        "chat_id": 555, "message_id": 101,
        "text": "✅ Одобрено — выйдет ≈ 30.09 10:00"})]


async def test_a_failed_edit_is_swallowed():
    await cards(api=FakeApi(error=RuntimeError("message to edit not found"))).edit(
        555, 101, "x")


async def test_ensure_topic_opens_one_through_the_topic_manager():
    topics = FakeTopics(result=None)
    assert await cards(topics=topics).ensure_topic(555, "Контент") is None
    assert topics.created == [(555, "Контент")]

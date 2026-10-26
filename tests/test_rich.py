from claude_hermes.modules.telegram.domain.formatting import render_html
from claude_hermes.modules.telegram.infrastructure.rich_sender import RichSender


class FakeCall:
    """Records calls; raises for the methods named in `fail`."""

    def __init__(self, fail=(), fail_markdown=False):
        self.calls = []
        self._fail = set(fail)
        self._fail_markdown = fail_markdown

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method in self._fail:
            raise RuntimeError(f"{method} rejected")
        if self._fail_markdown and "markdown" in payload.get("rich_message", {}):
            raise RuntimeError("malformed markdown")
        return {}


async def test_send_uses_markdown_and_thread():
    call = FakeCall()
    await RichSender(call).send(42, 9, "**bold**")
    assert call.calls == [("sendRichMessage", {
        "chat_id": 42, "message_thread_id": 9,
        "rich_message": {"markdown": "**bold**"},
    })]


async def test_thread_is_omitted_when_absent():
    call = FakeCall()
    await RichSender(call).send(42, None, "hi")
    assert "message_thread_id" not in call.calls[0][1]
    await RichSender(call).send(42, 0, "hi")
    assert "message_thread_id" not in call.calls[1][1]


async def test_markdown_failure_falls_back_to_html():
    call = FakeCall(fail_markdown=True)
    await RichSender(call).send(42, None, "**bold**")
    methods = [m for m, _ in call.calls]
    assert methods == ["sendRichMessage", "sendRichMessage"]
    assert "html" in call.calls[1][1]["rich_message"]


async def test_html_failure_falls_back_to_plain_chunks():
    call = FakeCall(fail=("sendRichMessage",))
    await RichSender(call).send(42, 9, "y" * 9000)
    methods = [m for m, _ in call.calls]
    assert methods[:2] == ["sendRichMessage", "sendRichMessage"]
    plain = [p for m, p in call.calls if m == "sendMessage"]
    assert len(plain) == 3                       # 9000 chars → 4000/4000/1000
    assert all(p["message_thread_id"] == 9 for p in plain)


async def test_disabled_sender_goes_straight_to_plain():
    call = FakeCall()
    await RichSender(call, enabled=False).send(42, None, "hi")
    assert [m for m, _ in call.calls] == ["sendMessage"]


async def test_draft_carries_draft_id_and_never_raises():
    call = FakeCall(fail=("sendRichMessageDraft",))
    await RichSender(call).send_draft(42, 9, "🔎 Searching the web…")   # must not raise
    method, payload = call.calls[0]
    assert method == "sendRichMessageDraft"
    assert payload["draft_id"] == 1 and payload["message_thread_id"] == 9


async def test_disabled_sender_sends_no_drafts():
    call = FakeCall()
    await RichSender(call, enabled=False).send_draft(42, 9, "💭 Thinking…")
    assert call.calls == []


def test_render_html_escapes_and_paragraphs():
    assert render_html("a < b\n\nsecond") == "<p>a &lt; b</p><p>second</p>"


async def test_a_failed_chunk_never_aborts_the_rest_of_the_reply():
    # A raise here would let a half-sent reply be followed by an error message,
    # while the turn is already recorded as answered.
    call = FakeCall(fail=("sendRichMessage", "sendMessage"))
    await RichSender(call).send(42, 9, "y" * 9000)          # must not raise
    assert len([m for m, _ in call.calls if m == "sendMessage"]) == 3

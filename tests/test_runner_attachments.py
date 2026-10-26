import base64

from claude_agent_sdk import AssistantMessage, ProcessError, ResultError, TextBlock

from claude_hermes.core.contracts import Attachment
from claude_hermes.core.model_catalog import Choice, cli_id
from claude_hermes.modules.turns.domain.value_objects import _compose_text
# The SDK seam moved with the loop in Wave 2: `query` is read off the module
# that calls it, so this is where it has to be patched.
from claude_hermes.modules.turns.infrastructure import sdk_runner
from claude_hermes.modules.turns.infrastructure.sdk_runner import _build_prompt

from tests.test_runner import FakeLaneModels, make_stack


class FakeResult:
    """Duck-typed ResultMessage stand-in (runner matches on attributes)."""

    def __init__(self, session_id="sdk-1", result="ok"):
        self.session_id = session_id
        self.result = result
        self.is_error = False


def make_fake_query(captured):
    async def fake_query(*, prompt, options):
        captured.append(prompt)
        yield FakeResult()

    return fake_query


def _png(tmp_path) -> Attachment:
    p = tmp_path / "42_shot.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    return Attachment(path=p, filename="shot.png", media_type="image/png",
                      is_image=True)


def _pdf(tmp_path) -> Attachment:
    p = tmp_path / "42_report.pdf"
    p.write_bytes(b"%PDF-1.4")
    return Attachment(path=p, filename="report.pdf",
                      media_type="application/pdf", is_image=False)


def test_prompt_without_attachments_is_still_a_bare_string():
    """The guard on every existing call site — text, voice, /learn, cron."""
    assert _build_prompt("hi", None) == "hi"
    assert _build_prompt("hi", []) == "hi"


async def test_prompt_with_an_image_carries_a_base64_block(tmp_path):
    att = _png(tmp_path)
    stream = _build_prompt("what is this", [att])
    assert not isinstance(stream, str)

    [msg] = [m async for m in stream]
    blocks = msg["message"]["content"]
    assert msg["type"] == "user" and msg["message"]["role"] == "user"
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == base64.standard_b64encode(
        att.path.read_bytes()).decode()
    # the path rides along too, so the agent can re-open the file, not only look
    assert blocks[-1]["type"] == "text"
    assert "what is this" in blocks[-1]["text"]
    assert str(att.path) in blocks[-1]["text"]


async def test_a_document_is_passed_by_path_not_inlined(tmp_path):
    att = _pdf(tmp_path)
    [msg] = [m async for m in _build_prompt("summarise", [att])]
    blocks = msg["message"]["content"]
    assert [b["type"] for b in blocks] == ["text"]
    assert "report.pdf" in blocks[0]["text"]
    assert str(att.path) in blocks[0]["text"]


def test_compose_text_names_every_attachment(tmp_path):
    text = _compose_text("look", [_png(tmp_path), _pdf(tmp_path)])
    assert "look" in text
    assert "shot.png" in text and "report.pdf" in text


def test_build_prompt_returns_a_fresh_iterator_each_call(tmp_path):
    """_execute runs twice on the resume-retry path and an async generator is
    single-use — a shared one would be exhausted on the second attempt."""
    att = _png(tmp_path)
    assert _build_prompt("x", [att]) is not _build_prompt("x", [att])


async def test_the_resume_retry_gets_a_live_prompt_not_a_drained_one(engine, tmp_path,
                                                                     monkeypatch):
    """The same invariant as above, but end-to-end — and this is the one that
    would actually catch the regression.

    `_build_prompt(x) is not _build_prompt(x)` holds for *any* generator
    function, so it says nothing about where `_execute` calls it. Build the
    prompt once above the retry instead of inside `_execute` and that assertion
    still passes, while the second attempt gets the generator the first one
    already drained — an empty turn, and only for attachments, which no other
    retry test covers.
    """
    stack = await make_stack(engine, tmp_path)
    monkeypatch.setattr(sdk_runner, "query", make_fake_query([]))
    await stack.runner.run_turn("tg:private:1", "first")      # binds a resume id

    drained = []

    async def flaky_query(*, prompt, options):
        drained.append([m async for m in prompt])       # the SDK reads the stream
        if options.resume is not None:
            raise ProcessError("No conversation found with session ID", exit_code=1)
        yield FakeResult("sdk-new", "fresh reply")

    monkeypatch.setattr(sdk_runner, "query", flaky_query)
    await stack.runner.run_turn("tg:private:1", "look", attachments=[_png(tmp_path)])

    assert len(drained) == 2            # resume attempt, then the fresh retry
    for msgs in drained:                # a shared generator would leave [] here
        [msg] = msgs
        assert msg["message"]["content"][0]["type"] == "image"


async def test_attachment_provenance_is_searchable(engine, tmp_path, monkeypatch):
    stack = await make_stack(engine, tmp_path)
    monkeypatch.setattr(sdk_runner, "query", make_fake_query([]))
    await stack.runner.run_turn("tg:private:1", "look", attachments=[_pdf(tmp_path)])
    hits = await stack.messages.search("report")
    assert any(h["role"] == "user" for h in hits)


async def test_a_plain_turn_still_sends_a_string_prompt(engine, tmp_path, monkeypatch):
    stack = await make_stack(engine, tmp_path)
    captured = []
    monkeypatch.setattr(sdk_runner, "query", make_fake_query(captured))
    await stack.runner.run_turn("tg:private:1", "hi")
    assert captured == ["hi"]


async def test_the_fallback_after_a_rejection_gets_a_live_prompt_too(engine, tmp_path,
                                                                    monkeypatch):
    """The same single-use-generator invariant as above, on the OTHER retry path.

    A rejected model re-runs the turn on the deploy default, and `_build_prompt`
    is called inside `_execute` for that attempt as well — build it once above
    the retry and the fallback would send an empty message, with an image
    attached and nobody able to see why.
    """
    stack = await make_stack(
        engine, tmp_path,
        models=FakeLaneModels(Choice("claude-fable-5-1",
                                     cli_id("claude-fable-5-1"), "high")))
    drained = []

    async def rejecting_query(*, prompt, options):
        drained.append([m async for m in prompt])
        if options.model == cli_id("claude-fable-5-1"):
            yield AssistantMessage(
                content=[TextBlock(text="There's an issue with the selected model "
                                        "(claude-fable-5-1).")],
                model="<synthetic>", error="model_not_found")
            raise ResultError("Claude Code returned an error result: success", {
                "subtype": "success", "errors": [],
                "result": "There's an issue with the selected model.",
                "api_error_status": 404, "terminal_reason": "api_error"}, 1)
        yield FakeResult("sdk-1", "ok")

    monkeypatch.setattr(sdk_runner, "query", rejecting_query)
    reply = await stack.runner.run_turn("tg:private:1", "look",
                                        attachments=[_png(tmp_path)])

    assert len(drained) == 2            # the rejected call, then the fallback
    for msgs in drained:                # a shared generator would leave [] here
        [msg] = msgs
        assert msg["message"]["content"][0]["type"] == "image"
    assert reply.startswith("Fable 5.1 is unavailable right now")

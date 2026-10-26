import pytest

from claude_hermes.integrations.openai.voice import OpenAIVoice, VoiceError


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", status_ok=True):
        self._json = json_data
        self.content = content
        self._ok = status_ok

    def json(self):
        return self._json

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("http 400")


class FakeHTTP:
    """Records the last POST and returns a queued response."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _voice(http):
    return OpenAIVoice(
        http, "sk-test", stt_model="gpt-4o-mini-transcribe",
        tts_model="gpt-4o-mini-tts", tts_voice="alloy", tts_max_chars=10,
    )


async def test_transcribe_returns_text_and_sends_multipart():
    http = FakeHTTP(FakeResponse(json_data={"text": "  hello world  "}))
    v = _voice(http)
    assert await v.transcribe(b"OggS...", "note.ogg") == "hello world"
    url, kwargs = http.calls[0]
    assert url.endswith("/audio/transcriptions")
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert kwargs["data"]["model"] == "gpt-4o-mini-transcribe"
    assert kwargs["files"]["file"][0] == "note.ogg"   # filename passed through


async def test_transcribe_empty_audio_raises():
    v = _voice(FakeHTTP(FakeResponse(json_data={"text": "x"})))
    with pytest.raises(VoiceError):
        await v.transcribe(b"", "note.ogg")


async def test_transcribe_blank_result_raises():
    v = _voice(FakeHTTP(FakeResponse(json_data={"text": "   "})))
    with pytest.raises(VoiceError):
        await v.transcribe(b"audio", "note.ogg")


async def test_transcribe_http_error_becomes_voiceerror():
    v = _voice(FakeHTTP(FakeResponse(status_ok=False)))
    with pytest.raises(VoiceError):
        await v.transcribe(b"audio", "note.ogg")


async def test_synthesize_returns_opus_bytes():
    http = FakeHTTP(FakeResponse(content=b"OggS-opus-bytes"))
    v = _voice(http)
    assert await v.synthesize("say this") == b"OggS-opus-bytes"
    url, kwargs = http.calls[0]
    assert url.endswith("/audio/speech")
    assert kwargs["json"]["response_format"] == "opus"       # ready for sendVoice
    assert kwargs["json"]["voice"] == "alloy"
    assert kwargs["json"]["model"] == "gpt-4o-mini-tts"


async def test_synthesize_clips_to_max_chars():
    http = FakeHTTP(FakeResponse(content=b"audio"))
    v = _voice(http)                     # tts_max_chars=10
    await v.synthesize("0123456789ABCDEF")
    assert http.calls[0][1]["json"]["input"] == "0123456789"


async def test_synthesize_empty_text_raises():
    v = _voice(FakeHTTP(FakeResponse(content=b"audio")))
    with pytest.raises(VoiceError):
        await v.synthesize("   ")


async def test_synthesize_empty_audio_raises():
    v = _voice(FakeHTTP(FakeResponse(content=b"")))
    with pytest.raises(VoiceError):
        await v.synthesize("hello")

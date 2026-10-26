"""Voice endpoints: transcribe raw audio, speak text back, 503 when disabled."""
from tests.web_helpers import ORIGIN, json_headers, login, make_client


class FakeVoice:
    async def transcribe(self, audio, filename="audio.ogg"):
        return f"heard {len(audio)} bytes from {filename}"

    async def synthesize(self, text):
        return b"OGG" + text.encode()


async def test_transcribe_and_speak(tmp_path):
    client, deps = await make_client(tmp_path, voice=FakeVoice())
    login(client, 1)
    r = await client.post("/api/voice/transcribe", content=b"abc",
                          headers={"Origin": ORIGIN, "Content-Type": "audio/webm"})
    assert r.status_code == 200 and r.json() == {"text": "heard 3 bytes from audio.webm"}
    r = await client.post("/api/voice/speak", json={"text": "hi"}, headers=json_headers())
    assert r.status_code == 200 and r.content == b"OGGhi"
    assert r.headers["content-type"].startswith("audio/ogg")


async def test_voice_disabled_is_503(tmp_path):
    client, deps = await make_client(tmp_path, voice=None)
    login(client, 1)
    r = await client.post("/api/voice/speak", json={"text": "hi"}, headers=json_headers())
    assert r.status_code == 503 and r.json()["error"]["code"] == "voice_disabled"

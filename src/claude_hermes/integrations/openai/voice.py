from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_API = "https://api.openai.com/v1"

# Telegram voice notes want OGG/OPUS, and OpenAI TTS emits exactly that with
# response_format="opus" — so a synthesized reply can go straight to sendVoice
# with no transcoding (ffmpeg is not in the image).
_TTS_FORMAT = "opus"


class VoiceError(RuntimeError):
    """A transcription or synthesis call failed. Carries a short, user-safe message."""


class OpenAIVoice:
    """Speech-to-text and text-to-speech over the OpenAI audio API.

    Runs in the host process, holding the API key in a closure the agent never
    touches (the key is popped from the environment at startup). The httpx client
    is injected so tests can drive it without a network.
    """

    def __init__(self, http, api_key: str, *, stt_model: str, tts_model: str,
                 tts_voice: str, tts_max_chars: int = 4000):
        self._http = http
        self._key = api_key
        self._stt_model = stt_model
        self._tts_model = tts_model
        self._tts_voice = tts_voice
        self._tts_max_chars = tts_max_chars

    @property
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    async def transcribe(self, audio: bytes, filename: str = "audio.ogg") -> str:
        """Voice note bytes → text. Raises VoiceError on any failure."""
        if not audio:
            raise VoiceError("the voice message was empty")
        files = {"file": (filename, audio, "application/octet-stream")}
        data = {"model": self._stt_model, "response_format": "json"}
        try:
            r = await self._http.post(
                f"{_API}/audio/transcriptions",
                headers=self._auth, data=data, files=files,
            )
            r.raise_for_status()
            text = (r.json() or {}).get("text", "")
        except Exception as e:
            log.warning("transcription failed", exc_info=True)
            raise VoiceError("could not transcribe the voice message") from e
        text = (text or "").strip()
        if not text:
            raise VoiceError("the voice message had no recognizable speech")
        return text

    async def synthesize(self, text: str) -> bytes:
        """Text → OGG/OPUS audio bytes ready for Telegram sendVoice. Raises VoiceError."""
        clipped = (text or "").strip()[: self._tts_max_chars]
        if not clipped:
            raise VoiceError("nothing to say")
        payload = {
            "model": self._tts_model,
            "voice": self._tts_voice,
            "input": clipped,
            "response_format": _TTS_FORMAT,
        }
        try:
            r = await self._http.post(
                f"{_API}/audio/speech", headers=self._auth, json=payload,
            )
            r.raise_for_status()
            audio = r.content
        except Exception as e:
            log.warning("speech synthesis failed", exc_info=True)
            raise VoiceError("could not synthesize the voice reply") from e
        if not audio:
            raise VoiceError("the synthesized reply was empty")
        return audio

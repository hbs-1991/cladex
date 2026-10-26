"""Voice for a turn: a recording in, a spoken reply out.

Thin over `VoicePort` (satisfied by `integrations.openai.voice.OpenAIVoice`) — the
value it adds is the "not configured" answer, so neither the web router nor the
Telegram gateway has to decide what a missing key means.
"""
from __future__ import annotations

from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.modules.turns.application.ports import VoicePort


class VoiceUnavailable(ApplicationException):
    status, code = 503, "voice_disabled"


class VoiceService:
    def __init__(self, voice: VoicePort | None):
        self._voice = voice

    @property
    def enabled(self) -> bool:
        return self._voice is not None

    def _require(self) -> VoicePort:
        if self._voice is None:
            raise VoiceUnavailable(message="voice is not configured")
        return self._voice

    async def transcribe(self, audio: bytes, *, filename: str = "audio.ogg") -> str:
        return await self._require().transcribe(audio, filename=filename)

    async def synthesize(self, text: str) -> bytes:
        return await self._require().synthesize(text)

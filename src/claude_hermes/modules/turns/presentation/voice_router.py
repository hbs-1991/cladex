"""Voice for the browser: a recording in, a spoken reply out.

Thin over the same voice port the Telegram gateway uses — the API key lives in the
host process, never in the page. Transcription takes the raw recording as the
request body (MediaRecorder hands the browser a Blob, not a form), and the
extension is derived from the Content-Type because the OpenAI transcription
endpoint sniffs the format from the filename.

Dependencies: `get_voice`, which hands over a `VoiceService` built on `deps.voice`.
The service, not the provider, owns the "not configured" answer — a deployment with no
key must answer `503 voice_disabled`, which is what `client.ts` hides the microphone
on, and not the provider's generic `unavailable`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.integrations.openai.voice import VoiceError

from ..application.voice_service import VoiceService
from .dependencies import get_voice
from .schemas import SpeakIn

_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
        "audio/wav": "wav", "audio/x-wav": "wav"}

router = APIRouter(tags=["turns"])


@router.post("/api/voice/transcribe", operation_id="turns_transcribe_audio")
async def transcribe(request: Request,
                     voice: VoiceService = Depends(get_voice)) -> JSONResponse:
    """The raw body IS the recording — no model, no form. The transport middleware
    already pinned the content type to `audio/`, and the extension comes from it."""
    audio = await request.body()
    ctype = request.headers.get("content-type", "").split(";")[0].strip()
    ext = _EXT.get(ctype, "ogg")
    try:
        text = await voice.transcribe(audio, filename=f"audio.{ext}")
    except VoiceError as e:
        raise ApplicationException(400, "bad_audio", str(e))
    return JSONResponse({"text": text})


@router.post("/api/voice/speak", operation_id="turns_speak_text")
async def speak(body: SpeakIn, voice: VoiceService = Depends(get_voice)) -> Response:
    """Ogg back, played by the page as it arrives."""
    try:
        audio = await voice.synthesize(str(body.text))
    except VoiceError as e:
        raise ApplicationException(400, "bad_request", str(e))
    return Response(audio, media_type="audio/ogg")

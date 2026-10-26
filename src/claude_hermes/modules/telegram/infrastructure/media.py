"""Voice and audio payloads: naming them, and pulling their bytes.

Moved verbatim from `gateway/telegram.py` in Wave 2.
"""
from __future__ import annotations


def media_filename(media) -> str:
    """A filename with an extension the transcription API can key its decoder off.
    Telegram voice notes are audio/ogg; an uploaded Audio may carry its own name."""
    name = getattr(media, "file_name", None)
    if name:
        return name
    mime = getattr(media, "mime_type", "") or ""
    ext = mime.split("/")[-1] if "/" in mime else "ogg"
    return f"audio.{ext}"


async def download_media(bot, media) -> bytes:
    """Pull the raw bytes of a Telegram voice/audio object into memory."""
    buf = await bot.download(media)      # BytesIO when no destination is given
    return buf.read()

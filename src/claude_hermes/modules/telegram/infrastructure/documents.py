"""Multipart uploads: documents and voice replies.

Moved from `main.py`'s `deliver_document` and `send_voice` closures in Wave 2.
Together the two methods satisfy the `turns` module's `DocumentDeliveryPort` and
`DeliverVoicePort`.
"""
from __future__ import annotations


class TelegramDocuments:
    """sendDocument and sendVoice are multipart, not JSON, so they go through
    aiogram's Bot (which holds the token) rather than the raw Bot API client.
    Both run in-process — the token is never exposed to the agent that asked for
    the send.
    """

    def __init__(self, bot):
        self._bot = bot

    async def send_document(self, chat_id: int, thread_id: int | None, path: str,
                            caption: str | None = None) -> None:
        """`path` must be a real file on THIS host: `FSInputFile` opens it.

        That is why `files.BlobStore.local_path` exists and why the S3 backend
        keeps a local mirror — a caller holding a stored address materializes it
        (`FileService.resolve`) before calling here, and never passes a key.
        """
        from aiogram.types import FSInputFile
        await self._bot.send_document(chat_id, FSInputFile(path),
                                      message_thread_id=thread_id, caption=caption)

    async def send_voice(self, chat_id: int, thread_id: int | None,
                         audio: bytes) -> None:
        from aiogram.types import BufferedInputFile
        await self._bot.send_voice(
            chat_id, BufferedInputFile(audio, filename="reply.ogg"),
            message_thread_id=thread_id)

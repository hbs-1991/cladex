"""What is downloadable on a Telegram message, and how to fetch it: the aiogram-bound
provider of `AttachmentDownloaderPort`.

A `Spec` is resolved from the message before a byte is fetched, so the intake
service can refuse an oversized document on `.size` alone (`MAX_ATTACHMENT_BYTES`
in `domain/constants.py` is the ceiling it applies). The stored name is
`core.naming.safe_name` — the same containment rule the files module resolves
against — prefixed with the message id, so two documents named `report.pdf` in one
chat never collide and nothing a user typed can escape the inbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claude_hermes.core.contracts import IMAGE_TYPES, Attachment
from claude_hermes.core.naming import safe_name

__all__ = ["AiogramDownloader", "Spec", "download_all", "specs"]


@dataclass(frozen=True)
class Spec:
    """One downloadable thing, resolved from a Telegram message but not yet fetched."""

    media: object       # the aiogram object handed to bot.download
    original: str       # the name the user sees
    stored: str         # sanitized + message-id-prefixed; what lands on disk
    media_type: str
    size: int


def specs(message) -> list[Spec]:
    """What is downloadable on this message. A list so the caller has one shape
    regardless of whether it was a photo, a document, or neither."""
    out: list[Spec] = []
    photo = getattr(message, "photo", None)
    if photo:
        out.append(_spec(message, photo[-1], "photo.jpg", "image/jpeg"))
    doc = getattr(message, "document", None)
    if doc is not None:
        out.append(_spec(
            message, doc,
            getattr(doc, "file_name", None) or "document",
            getattr(doc, "mime_type", None) or "application/octet-stream",
        ))
    return out


def _spec(message, media, original: str, media_type: str) -> Spec:
    return Spec(
        media=media,
        original=original,
        stored=safe_name(getattr(message, "message_id", 0), original),
        media_type=media_type,
        size=getattr(media, "file_size", None) or 0,
    )


async def download_all(bot, items, dest: Path) -> list[Attachment]:
    """Fetch every Spec into `dest`. Raises on the first failure — the caller
    decides what the user sees, because only it knows the chat."""
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[Attachment] = []
    for s in items:
        path = dest / s.stored
        await bot.download(s.media, destination=path)
        saved.append(Attachment(
            path=path, filename=s.original, media_type=s.media_type,
            is_image=s.media_type in IMAGE_TYPES,
        ))
    return saved


class AiogramDownloader:
    """`AttachmentDownloaderPort` over the two functions above — the one object the
    intake service holds. Stateless; the composition root builds one."""

    def specs(self, message) -> list[Spec]:
        return specs(message)

    async def download_all(self, bot, items, dest: Path) -> list[Attachment]:
        return await download_all(bot, items, dest)

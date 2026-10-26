"""The approval card: what the owner is shown in Telegram for a drafted post.

A card is an outbox row on the item itself (`card_*` columns, migration 0011):

    none --(drafter submits, before publish_at)--> pending --(tick CAS)--> sending
      --> sent | pending (retry, `card_attempts + 1`) | failed (the third time)
    pending | sending | sent --(any content edit, a redraft, a missed time)--> superseded
    failed --(any owner or agent write: approve, unapprove, edit, re-status)--> none

`sending` older than `SENDING_STALE` goes back to `pending`: a worker that died
mid-send. If it died AFTER Telegram accepted the card, the owner gets the card
twice; the orphan's buttons carry the same revision, and only the row's
`card_message_id` is a live card for a reply, so the worst case is a duplicate.

The **revision** binds a button to the exact content on the card: the first 12 hex
characters of a sha256 over the body and each media file's path and sha256. The
Telegram approval hashes the files FRESH and refuses when the revision moved.
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import timedelta

CARD_NONE = "none"
CARD_PENDING = "pending"
CARD_SENDING = "sending"
CARD_SENT = "sent"
CARD_FAILED = "failed"
CARD_SUPERSEDED = "superseded"
CARD_STATES = (CARD_NONE, CARD_PENDING, CARD_SENDING, CARD_SENT, CARD_FAILED,
               CARD_SUPERSEDED)
# A card that may still reach the owner, or already has: a content edit makes
# every one of them stale. `sending` is here too — a send in flight lands as a
# stale card, and the outbox edits it the moment it learns so.
CARD_LIVE = (CARD_PENDING, CARD_SENDING, CARD_SENT)

# The extra key a content write returns (`ContentStore.update(..., supersede=True)`,
# `supersede_missed`): the card state the row had BEFORE the statement, so the
# caller knows whether a card in Telegram now shows something no longer true.
PREVIOUS_CARD = "previous_card_state"

MAX_CARD_ATTEMPTS = 3
SENDING_STALE = timedelta(minutes=5)
REVISION_CHARS = 12

# The forum topic in the owner's private chat that every card goes into.
TOPIC_NAME = "Контент"

# What a card's text becomes once it can no longer approve anything.
CHANGED_TEXT = "✏️ Изменено — одобри в календаре"
REDRAFTING_TEXT = "🔁 Переделываю"
MISSED_TEXT = "⏰ Время публикации прошло"


def approved_text(publish_at_local: str) -> str:
    return f"✅ Одобрено — выйдет ≈ {publish_at_local}"


def card_revision(body: str | None, media: Iterable[dict]) -> str:
    """The revision of this content: 12 hex chars over the body and each file's
    path and sha256, in order. A file with no hash yet hashes as empty — such a
    revision never matches a freshly hashed one."""
    h = hashlib.sha256()
    h.update(str(body or "").encode("utf-8"))
    for entry in media or []:
        h.update(b"\x00")
        h.update(str(entry.get("path") or "").encode("utf-8"))
        h.update(b"\x00")
        h.update(str(entry.get("sha256") or "").encode("utf-8"))
    return h.hexdigest()[:REVISION_CHARS]

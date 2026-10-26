"""The wire shape of a content approval card's buttons, spelled once.

Two layers need it and may not import each other: `infrastructure/cards.py`
writes the `callback_data` onto the card, and `application/services/content_cards.py`
reads it back when the button is pressed. A shape that disagreed between the two
by one character would be a card whose every tap answers "stale".
"""
from __future__ import annotations

import string
from dataclasses import dataclass

from .constants import (
    CONTENT_APPROVE,
    CONTENT_CALLBACK_PREFIX,
    CONTENT_REDO,
    CONTENT_REVISION_LEN,
)

_HEX = frozenset(string.hexdigits.lower())


@dataclass(frozen=True)
class CardTap:
    """One parsed `cnt:` press: which button, on which item, at which revision."""

    action: str
    item_id: int
    revision: str


def card_callback(action: str, item_id: int, revision: str) -> str:
    """`cnt:<action>:<item id>:<revision>` — at most ~30 bytes of Telegram's 64."""
    return f"{CONTENT_CALLBACK_PREFIX}:{action}:{int(item_id)}:{revision}"


def parse_card_callback(data: str | None) -> CardTap | None:
    """The press, or None for anything that is not exactly a card button.

    Strict on purpose: the revision is what binds an approval to the content the
    owner saw, so a truncated or padded one is not "close enough", it is stale.
    """
    parts = (data or "").split(":")
    if len(parts) != 4 or parts[0] != CONTENT_CALLBACK_PREFIX:
        return None
    _, action, raw_id, revision = parts
    if action not in (CONTENT_APPROVE, CONTENT_REDO):
        return None
    # isascii: `str.isdigit` also admits "²", which `int()` then refuses.
    if not (raw_id.isascii() and raw_id.isdigit()) or int(raw_id) <= 0:
        return None
    if len(revision) != CONTENT_REVISION_LEN or not set(revision) <= _HEX:
        return None
    return CardTap(action=action, item_id=int(raw_id), revision=revision)

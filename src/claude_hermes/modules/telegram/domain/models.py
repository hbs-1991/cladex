"""The two shapes the Telegram intake produces before anything else runs.

Both are plain dataclasses on purpose: `telegram` owns no tables, so its domain
is pure data — what the gateway decided about one update, independent of aiogram,
of the Bot API, and of whatever runs the turn afterwards.

Moved from `gateway/events.py` and `gateway/telegram.py`
in Wave 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SenderProfile:
    """What this update says about the person who sent it.

    Telegram sends `first_name`/`last_name`/`username` on every `from_user`, and
    since Wave 4a identity keeps them on the sender's `users` row — a chat is the
    only place a Telegram-only user's name is ever learned.

    It is telegram's own value object, not identity's `Profile`, because **modules
    are independent of each other**: the ACL port below takes whatever is handed to
    it and identity normalises by attribute name, so the two types never have to
    meet. `display_name` is already joined and trimmed; an empty field means "this
    update did not say", which is not the same as "cleared".
    """

    display_name: str = ""
    username: str = ""

    @classmethod
    def of(cls, from_user) -> "SenderProfile | None":
        """From an aiogram `from_user` (or anything with those three attributes).
        `None` when the update carries no sender — a channel post, mostly."""
        if from_user is None:
            return None
        name = (f"{getattr(from_user, 'first_name', '') or ''} "
                f"{getattr(from_user, 'last_name', '') or ''}").strip()
        return cls(display_name=name,
                   username=str(getattr(from_user, "username", "") or ""))


@dataclass
class MessageEvent:
    chat_type: str
    chat_id: int
    user_id: int
    text: str
    mentioned: bool
    thread_id: int | None = None
    message_id: int = 0
    attachments: list = field(default_factory=list)
    # Carried so the ACL can keep the sender's `users` row current without the
    # gate reaching back into aiogram's Message. None = this update said nothing
    # new about them, and the stored names are then left alone.
    profile: SenderProfile | None = None
    # The id of the message this one replies to, None when it replies to
    # nothing. Read by the content-card interception, which matches it against
    # a stored card id — never "is a reply" alone: in a forum topic EVERY message
    # replies to the topic's root.
    reply_to_message_id: int | None = None


@dataclass
class Intake:
    """What the attachment path decided, before any turn runs."""

    verdict: str
    content: str = ""
    attachments: list = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class MessageView:
    """The message a callback button sits on, reduced to facts.

    The application layer decides what a `/model` tap means, and to do that it
    needs the lane the button belongs to and how old it is — but it may not read
    an aiogram `CallbackQuery`, and above all it may not decide what
    `message_thread_id` means: that is `infrastructure/aiogram_mapping.thread_lane`,
    which knows Telegram stamps the field on ordinary reply chains too. So the
    infrastructure resolves the thread id and hands this over.

    `date` is Telegram's own unix timestamp, and `0` means the update carried
    none — an `InaccessibleMessage`, which is exactly what a message too old or
    deleted comes back as. A view of `None` means the same thing one level up.
    """

    chat_type: str
    chat_id: int
    thread_id: int | None = None
    message_id: int = 0
    date: int = 0


@dataclass(frozen=True)
class MenuButton:
    """One inline-keyboard button: what it says, and the `callback_data` that
    comes back when it is pressed."""

    text: str
    data: str


@dataclass(frozen=True)
class ModelMenu:
    """A rendered `/model` keyboard, still free of any Bot API shape — the
    infrastructure turns `rows` into `inline_keyboard` and sends it."""

    text: str
    rows: tuple[tuple[MenuButton, ...], ...] = ()

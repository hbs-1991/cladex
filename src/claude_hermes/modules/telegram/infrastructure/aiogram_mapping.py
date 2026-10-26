"""Reading an aiogram message object into the facts the application layer uses.

Moved verbatim from `gateway/telegram.py` in Wave 2.
"""
from __future__ import annotations


def thread_lane(message) -> int | None:
    """The thread id only when it is a real topic.

    Telegram also stamps message_thread_id on plain reply chains in a supergroup.
    Treating one of those as a lane would fork a fresh, empty session on every reply —
    so outside a private chat we require the message to belong to a forum topic.
    """
    thread_id = getattr(message, "message_thread_id", None)
    if not thread_id:
        return None
    if message.chat.type == "private":
        return thread_id
    is_topic = getattr(message, "is_topic_message", None)
    if is_topic is None:
        is_topic = getattr(message.chat, "is_forum", False)
    return thread_id if is_topic else None


def reply_target(message) -> int | None:
    """The id of the message this one replies to, or None.

    Plain fact, no judgement: in a forum topic every message "replies" to the
    topic's root, which is why the caller never reads this as "is a redraft"
    on its own — the content side matches it against a stored card id.
    """
    replied = getattr(message, "reply_to_message", None)
    message_id = getattr(replied, "message_id", None) if replied is not None else None
    return int(message_id) if message_id else None

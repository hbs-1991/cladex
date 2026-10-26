"""Who may see which lane. The one place that decides it.

A thread is a session lane — a `web:` browser lane, a private Telegram chat, or a
group topic. Cron lanes never show up; group lanes are the owner's alone.
"""
from __future__ import annotations

from .keys import chat_type_from_key


def visible(thread: dict, user_id: int, is_owner: bool) -> bool:
    """Wave 4a (D3): the row's `owner_user_id` is the answer, with one belt.

    Every lane writer now goes through `core.session_keys.lane_owner`, so
    `threads.owner_user_id` names the lane's owner for a group lane too (the
    deploy owner) where the pre-4a `thread_owner` left it NULL. Reading the column
    instead of re-deriving the owner from the key is what makes this rule agree
    with `sessions.owner_id`, with the `0005` backfill and with the workspace a
    turn actually runs in: there is one rule and it is applied once, at write time.

    The `is_owner` belt survives for GROUP lanes only. A group thread whose
    `owner_user_id` the backfill somehow missed would otherwise disappear from the
    owner's sidebar — silently, and with no way to get it back. It is deliberately
    NOT extended to `web:` lanes or to another person's PRIVATE Telegram chat:
    before this wave the owner could not open an allowlisted user's private lane
    either, and "two users cannot see each other's threads" is the line this wave
    exists to hold (advisor blocker B-2 caught the fall-through).
    """
    key = thread["session_key"]
    if key.startswith("cron:"):
        # A job lane is the agent talking to itself. Nobody lists one, not even
        # the owner — the Routines page reads the delivery lane instead.
        return False
    if thread["owner_user_id"] == user_id:
        return True
    if chat_type_from_key(key) in ("group", "supergroup"):
        return is_owner
    return False

"""The session key — the routing and isolation primitive every module reads.

A session key names one conversation lane: a Telegram chat (optionally one of its
forum topics), a browser thread, or a cron job's private lane. Sessions, messages,
threads, turn events, costs and files are all keyed by it, so its format is a
cross-module contract and lives in `core/` rather than in any one module.

Moved verbatim from `sessions.py` in Wave 2; `SessionRouter` stayed
behind because it needs a store, which these functions deliberately do not.
"""
from __future__ import annotations

import uuid


def chat_id_from_key(session_key: str) -> int | None:
    parts = session_key.split(":")
    if parts[0] != "tg" or len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def thread_id_from_key(session_key: str) -> int | None:
    tail = session_key.rsplit(":", 1)[-1]
    if tail.startswith("t") and tail[1:].isdigit():
        return int(tail[1:])
    return None


def chat_type_from_key(session_key: str) -> str:
    """The chat type baked into a session key, or "" if this is not a tg key.

    Symmetric with chat_id_from_key above. Exists so a caller can ask "is this
    lane a private chat?" without spelling out a startswith("tg:private:") in
    three places — and cron keys answer "" rather than raising.

    A `web:` lane is one browser user talking to the agent alone, so it answers
    "private": the ask_user gate (tools.py) and every other private-only rule
    apply to it unchanged.
    """
    parts = session_key.split(":")
    if parts[0] == "web":
        return "private"
    if parts[0] != "tg" or len(parts) < 3:
        return ""
    return parts[1]


def is_group_lane(session_key: str) -> bool:
    """Is this lane a room — a Telegram group or supergroup — rather than one
    person alone with the agent?

    The one predicate behind "tool presence is a property of the LANE" (Wave 4b,
    D6). A group lane belongs to the deploy owner (`lane_owner`), so its library,
    its recall and its spend are the owner's, and `skill_admin`, `session_search`
    and `cost_report` are not offered there to ANYBODY — the owner included.
    4a decided that per SENDER instead, and a per-sender flip changes the MCP
    schema list between two turns of one session, which is a cached-prefix miss
    the lane never recovers from (P1). Every other grammar (`web:`, `tg:private:`,
    `cron:`, and anything unknown) answers False: there the sender is the owner
    structurally, or the lane is a job that has no sender at all.
    """
    return chat_type_from_key(session_key) in ("group", "supergroup")


def web_session_key(user_id: int) -> str:
    """A fresh browser thread: web:<user_id>:<uuid4 hex>."""
    return f"web:{user_id}:{uuid.uuid4().hex}"


def _as_int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None


def lane_owner(session_key: str, channel: str, user_id: int,
               deploy_owner: int) -> int:
    """Who a lane belongs to — the one rule, applied everywhere (D3).

    `sessions.owner_id`, `threads.owner_user_id`, the `0005` backfill and the
    workspace resolver all go through this (or through its spelling in SQL), which
    is what makes it impossible for them to disagree about a lane. It is **pure**
    given the deploy owner: a brand-new lane whose `sessions` row does not exist
    yet resolves exactly like an old one, so a turn never has to read a table to
    learn which workspace it runs in.

    | lane | owner |
    |---|---|
    | `web:<uid>:…` | `<uid>` — the user who created the thread |
    | `tg:private:<chat>[:t<n>]` | `<chat>` (chat id == user id in a private chat) |
    | `tg:group\\|supergroup:…` | the **deploy owner** |
    | `cron:<job>` | the job's owner, which the caller passes as `user_id` |

    The id comes out of the KEY, not out of `user_id`, wherever the key carries
    one: user B opening user A's `web:A:…` lane must not make B its owner, and a
    group message from a stranger must not make the stranger one. `user_id` is the
    answer only where the key holds nothing — a cron lane, or a grammar this
    function has never seen — and `deploy_owner` is the last resort, never a
    silent default for a lane that names somebody.

    A group lane belonging to the deploy owner is deliberate: one lane, one
    workspace, and the deploy owner is the only person in a group chat the agent
    has a workspace for. The sender is still the sender (`turns.user_id`, the
    ask_user keyboard); they just do not own the room.
    """
    parts = session_key.split(":")
    head = parts[0] if parts else ""
    if head == "web":
        from_key = _as_int(parts[1]) if len(parts) > 1 else None
        return from_key if from_key is not None else user_id
    if head == "tg":
        if chat_type_from_key(session_key) == "private":
            chat = chat_id_from_key(session_key)
            return chat if chat is not None else user_id
        return deploy_owner
    if head == "cron":
        # Nothing in `cron:<job_id>` names a person; the scheduler passes the
        # job's `owner_id` through as `user_id`.
        return user_id or deploy_owner
    # An unknown grammar is not a licence to guess a stranger's workspace.
    return user_id if channel == "web" and user_id else deploy_owner


def build_session_key(
    chat_type: str,
    chat_id: int | str,
    user_id: int | str | None = None,
    thread_id: int | None = None,
) -> str:
    """Deterministic routing/isolation primitive (Hermes pattern).

    A Telegram thread is a session lane. A chat with no threads (thread_id 0/None)
    produces the pre-threads key verbatim, so existing rows keep resolving.
    """
    key = f"tg:{chat_type}:{chat_id}"
    if user_id is not None and chat_type != "private":
        key += f":{user_id}"
    if thread_id:
        key += f":t{thread_id}"
    return key

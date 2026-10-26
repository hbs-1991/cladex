"""Content approval cards, as the gateway sees them: a button press, a reply, a topic.

Every decision the dispatcher makes about a card is here, because the dispatcher
is untested by construction. The content plan's own rules — which revision is
live, how many redrafts are left, what the popup says after an approval — are
the content module's, reached through `ContentDecisionsPort`; this module only
decides who may ask and when a message is a card's business at all.

The security shape is `/approve`'s: an approval schedules a PUBLIC post, so it is
enacted by the gateway on a literal owner action and never by a turn. The owner
check runs first, before the port is asked anything.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from ...domain.cards import parse_card_callback
from ...domain.constants import CONTENT_CALLBACK_PREFIX, CONTENT_REDO
from ...domain.messages import _CONTENT_FAILED, _CONTENT_REDO_HINT, _CONTENT_STALE
from ...domain.models import MessageEvent
from ..ports import ContentDecisionsPort

log = structlog.get_logger(__name__)

# `acl.is_owner`, passed as the bound method: the same check `/approve` makes.
IsOwner = Callable[[int], Awaitable[bool]]

# Telegram's ceiling on a callback answer's popup text.
_POPUP_LIMIT = 200


def is_content_callback(data: str | None) -> bool:
    """Is this press a content card's rather than `ask_user`'s?

    Routed BEFORE the ask broker for the same reason `mdl:` is: a deployment
    with no broker wired would otherwise answer every card button with "that
    question is no longer active" — and an approval would silently not happen.
    """
    return (data or "").startswith(f"{CONTENT_CALLBACK_PREFIX}:")


async def handle_content_callback(data: str | None, user_id: int,
                                  decisions: ContentDecisionsPort | None,
                                  is_owner: IsOwner) -> str:
    """One `cnt:` press -> the popup text. Always answers, never raises.

    Every bad case — unparseable, not the owner, no content plan wired — is the
    same stale answer, for the reason `handle_callback` folds its three: they are
    not worth distinguishing to the person pressing, and one branch cannot drift
    from three. "Redo" changes nothing; it only says how to redo, because the
    comment has to come from somewhere and a reply is where Telegram lets it.
    """
    tap = parse_card_callback(data)
    if tap is None or decisions is None:
        return _CONTENT_STALE
    try:
        if not await is_owner(user_id):
            return _CONTENT_STALE
        if tap.action == CONTENT_REDO:
            return _CONTENT_REDO_HINT
        answer = await decisions.approve_from_card(tap.item_id, tap.revision, user_id)
    except Exception:
        # The database, mostly. The button must still be answered, or it spins.
        log.exception("content card tap failed for item %s", tap.item_id)
        return _CONTENT_FAILED
    return (answer or _CONTENT_STALE)[:_POPUP_LIMIT]


async def intercept_card_reply(event: MessageEvent,
                               decisions: ContentDecisionsPort | None,
                               is_owner: IsOwner) -> str | None:
    """A reply to a live card is a redraft request, never a turn.

    Returns the text to answer with — and then the message stops there: no
    download, no title, no turn — or `None` to let it proceed normally. Only the
    port can say whether the replied-to message IS a live card: in a forum topic
    every message replies to the topic's root, so "is a reply" alone means
    nothing, and matching on (chat, message id) is also what keeps interception
    working when the card went flat into the private chat.

    The owner is checked first and a stranger's reply proceeds untouched, to be
    gated like any other message. A port that fails answers the failure text
    rather than `None`: the constraint is that a reply to a card never becomes a
    turn, and during an outage the gateway cannot tell which replies those are.
    """
    if decisions is None or not event.reply_to_message_id:
        return None
    try:
        if not await is_owner(event.user_id):
            return None
        return await decisions.redraft_from_reply(
            event.chat_id, event.reply_to_message_id, event.user_id,
            event.text.strip())
    except Exception:
        log.exception("card reply interception failed for chat %s", event.chat_id)
        return _CONTENT_FAILED


async def skip_title(event: MessageEvent,
                     decisions: ContentDecisionsPort | None) -> bool:
    """Should the titler leave this thread alone? True in the cards' topic.

    The topic is named "Контент" on purpose and the first ordinary message in it
    would otherwise rename it after itself. A failed lookup skips the title too:
    a missed name is cosmetic, a renamed cards topic is what the owner asked for
    never to happen.
    """
    if decisions is None or not event.thread_id:
        return False
    try:
        return bool(await decisions.is_card_topic(event.chat_id, event.thread_id))
    except Exception:
        log.warning("card-topic lookup failed for chat %s", event.chat_id,
                    exc_info=True)
        return True

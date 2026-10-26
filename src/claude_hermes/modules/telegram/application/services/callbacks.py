"""One inline-keyboard press turned into popup text.

Moved verbatim from `gateway/telegram.py` in Wave 2.
"""
from __future__ import annotations

from ...domain.constants import CALLBACK_PREFIX
from ...domain.messages import _CB_OK, _CB_STALE
from ..ports import AskBrokerPort


async def handle_callback(data, user_id: int, broker: AskBrokerPort) -> str:
    """Turn one button press into popup text.

    Lives outside the aiogram handler for the same reason intake_attachments
    does: create_dispatcher is only exercised by the Docker smoke run, so any
    logic left inside it is untested by construction.

    Everything unparseable resolves to the same "no longer active" answer. A
    button that survived a restart, a second tap, and a stranger's tap are not
    worth distinguishing to the user, and one branch cannot drift from three.
    """
    parts = (data or "").split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return _CB_STALE
    _, qid, token = parts
    return _CB_OK if await broker.resolve(qid, token, user_id) else _CB_STALE

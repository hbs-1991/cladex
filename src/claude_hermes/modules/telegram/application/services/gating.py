"""The one gate every Telegram path repeats: may this update cost us anything?

Moved verbatim from `gateway/telegram.py` in Wave 2.
"""
from __future__ import annotations

from claude_hermes.core.config import Config

from ...domain.models import MessageEvent
from ..ports import AclPort


async def verdict(cfg: Config, event: MessageEvent, acl: AclPort) -> str:
    """"ignore" — not addressed to us; "deny" — not allowed; "run" — handle it."""
    if event.chat_type != "private" and cfg.group_require_mention and not event.mentioned:
        return "ignore"
    # The sender's profile rides along: this gate is the first (and for a
    # Telegram-only user the only) place their display name is ever learned, and
    # the ACL is what keeps their `users` row current.
    if await acl.check(event.user_id, event.chat_type, event.profile) == "deny":
        return "deny"
    return "run"


async def should_title(cfg: Config, event: MessageEvent, acl: AclPort) -> bool:
    """The titler runs before the turn, so it repeats the gate itself: an ignored or
    denied message must not cost a title call or rename someone else's topic. A command
    is not a topic subject either."""
    if event.text.startswith("/"):
        return False
    return await verdict(cfg, event, acl) == "run"

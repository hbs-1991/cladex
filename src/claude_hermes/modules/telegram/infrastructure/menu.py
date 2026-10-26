"""The bot's menu button, pointed at the Mini App.

Moved from `main.py` in Wave 2. Idempotent by construction — setChatMenuButton
replaces whatever is there — so it is safe to call on every boot, which is the
point: the button follows `web_public_origin` instead of a one-off BotFather step
nobody remembers to redo on a hostname change.
"""
from __future__ import annotations

import structlog

from ..application.ports import BotApiCall

log = structlog.get_logger(__name__)

MENU_BUTTON_TEXT = "CladeX"


async def ensure_menu_button(call: BotApiCall, origin: str,
                             text: str = MENU_BUTTON_TEXT) -> bool:
    """Point the menu button at `origin`. Returns whether the call landed.

    https only — Telegram refuses anything else — and a failure is logged, never
    raised: a stale menu button must not stop the bot from starting.
    """
    if not origin.startswith("https://"):
        return False
    try:
        await call("setChatMenuButton", {"menu_button": {
            "type": "web_app", "text": text,
            "web_app": {"url": origin + "/"}}})
    except Exception:
        log.exception("setChatMenuButton failed; the Mini App button is stale")
        return False
    log.info("menu button opens %s as a Mini App", origin)
    return True

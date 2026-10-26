"""Which transport this process runs, and what it tells Telegram about it.

`main.run()` used to end in `await ctx.telegram.run_polling()`; with webhook mode
live there are two endings and the choice between them is a decision worth
testing. It is here rather than in `main` because it is Telegram's half of the
boot, and here rather than in `presentation/webhook.py` because it calls the Bot
API — presentation and infrastructure are independent siblings under the
"telegram layers" contract, and the webhook *path* they must agree on lives in
`domain/constants.py`.

`bot` and `gateway` arrive as arguments (an aiogram `Bot` and this module's
`TelegramGateway`), so `tests/test_main_webhook.py` drives the whole decision with
a fake bot that records what it was asked to do.
"""
from __future__ import annotations

from typing import Any

import structlog

from ..domain.constants import WEBHOOK_PATH

log = structlog.get_logger(__name__)

__all__ = ["start_transport", "webhook_url"]


def webhook_url(origin: str, secret: str) -> str:
    """The URL Telegram POSTs to — `web_public_origin` plus the mounted path.

    The origin is stripped of a trailing slash: `config.yaml.example` says "no
    trailing slash" and a deployment that adds one anyway would register
    `https://host//telegram/webhook/…`, which Starlette does not match.
    """
    return origin.rstrip("/") + WEBHOOK_PATH.format(secret=secret)


async def start_transport(cfg: Any, *, bot: Any, gateway: Any,
                          web_task: Any = None) -> None:
    """Register the transport and block on it. Returns when the process is done.

    **Webhook mode** needs the web server, because the route that receives updates
    is one of its routes: without `web_enabled` and `web_public_origin` there is
    nothing to register and nothing to block on, so both are a `SystemExit` with
    the reason rather than a bot that comes up and never hears anything.

    `allowed_updates` is read off the dispatcher rather than hardcoded:
    aiogram's `start_polling` defaults to `resolve_used_update_types()`, so a
    hardcoded list here would make the two transports diverge the day a handler
    for a new update type is added — in exactly one of them, silently.

    `drop_pending_updates=False` in both directions: an update that arrived while
    this process was restarting is a message a person sent, and dropping it is a
    reply that never comes.
    """
    if getattr(cfg, "telegram_mode", "polling") == "webhook":
        secret = getattr(cfg, "telegram_webhook_secret", "") or ""
        if not secret:
            raise SystemExit(
                "telegram_mode is 'webhook' but CH_TELEGRAM_WEBHOOK_SECRET is not set")
        if not getattr(cfg, "web_enabled", False):
            raise SystemExit(
                "telegram_mode is 'webhook' but web_enabled is false: the webhook "
                "route is served by the web app, so there is nothing to receive "
                "updates on")
        origin = getattr(cfg, "web_public_origin", "") or ""
        if not origin:
            raise SystemExit(
                "telegram_mode is 'webhook' but web_public_origin is empty: "
                "Telegram needs a public https URL to deliver updates to")
        if web_task is None:
            raise SystemExit(
                "telegram_mode is 'webhook' but the web server was not started")
        url = webhook_url(origin, secret)
        # Idempotent: the same URL and secret from N instances is one `ok: true`
        # each. The hazard is a rolling deploy whose instances disagree about
        # `web_public_origin` — the last one to boot wins (docs/deploy.md).
        await bot.set_webhook(
            url=url, secret_token=secret,
            allowed_updates=gateway.dispatcher.resolve_used_update_types(),
            drop_pending_updates=False)
        # The path segment is a credential, so the URL never reaches a log line.
        log.info("claude-hermes on webhook at %s/telegram/webhook/<secret>",
                 origin.rstrip("/"))
        # The web server IS the main loop now: this process has no poll to block on.
        await web_task
        return

    # Polling. The `delete_webhook` is load-bearing, not documentation: verified
    # against aiogram 3.29.1, `start_polling` does NOT remove a webhook that is
    # still registered, and `getUpdates` behind one answers 409 forever. A deploy
    # that switched back from webhook mode would simply stop hearing anything.
    await bot.delete_webhook(drop_pending_updates=False)
    await gateway.run_polling()

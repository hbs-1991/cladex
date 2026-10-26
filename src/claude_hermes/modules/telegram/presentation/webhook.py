"""The webhook transport: one Bot API update over HTTP, answered immediately.

Off by default (`telegram_mode: polling`), and live since Wave 4a: `main.run()`
registers the URL with Telegram (`infrastructure/transport.start_transport`) and
blocks on the web server instead of polling, so N agent instances behind one
public origin all receive updates. Mounting is the shell's decision and
`webhook_enabled(cfg)` is what it asks — `build_web_app` includes this router only
in webhook mode, and refuses to boot in webhook mode without a secret rather than
publishing a door anyone may POST to. The route is inert even if it is mounted by
mistake: `accepted_secret(cfg)` is `""` in polling mode and no request matches an
empty secret.

**Two credentials, both required.** The path segment is the only thing Telegram
offers that survives a reverse proxy stripping headers — but it is also what a
proxy writes into its access log on every delivery, so on its own it is a secret
stored in plaintext on disk by a component nobody thinks of as a secret store.
`set_webhook(secret_token=...)` makes Telegram send the same value in the
`X-Telegram-Bot-Api-Secret-Token` header on every update, and that one reaches no
log. Both are compared with `compare_digest`, and a missing header is a 401.

**Delivery is at-least-once, so acceptance has to be idempotent.** Telegram
redelivers an update it did not get a 2xx for within its timeout, and what follows
an accepted update is intake (a file download, a MinIO put) and voice
transcription — one slow document was two turns and two charges. So the route
(a) drops a repeated `update_id` through the `UpdateDedup` port and (b) hands the
update to a background task and answers 200 at once, which trades "a crash loses
an update" for "a slow update is never duplicated". The first is the one a person
notices; the second is the one that costs money and posts twice.

This path is NOT under `CurrentUserMiddleware`: that middleware guards `/api/*`
only, and `/telegram/webhook/{secret}` is outside it — nothing has to be added to
any public-paths set. It authenticates by the two secrets above, never by a cookie.
"""
from __future__ import annotations

import asyncio
import hmac
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..domain.constants import SECRET_HEADER, WEBHOOK_PATH
from .dependencies import (
    UpdateDedup,
    UpdateSink,
    fallback_dedup,
    get_config,
    get_gateway,
    get_update_dedup,
)

log = structlog.get_logger(__name__)

__all__ = ["SECRET_HEADER", "WEBHOOK_PATH", "accepted_secret", "deliver", "router",
           "webhook_enabled"]

router = APIRouter(tags=["telegram"])

# asyncio only holds a weak reference to a running task, so a task nobody keeps can
# be garbage-collected mid-await and the update simply vanishes. This set is that
# strong reference; `add_done_callback` empties it again.
_INFLIGHT: set[asyncio.Task] = set()


def accepted_secret(cfg) -> str:
    """The secret this deployment accepts — `""` unless webhook mode is on."""
    if getattr(cfg, "telegram_mode", "polling") != "webhook":
        return ""
    return getattr(cfg, "telegram_webhook_secret", "") or ""


def webhook_enabled(cfg) -> bool:
    """Whether the shell should mount this router. Loud at boot rather than a public
    endpoint anyone can POST to: webhook mode without a secret refuses to start."""
    if getattr(cfg, "telegram_mode", "polling") != "webhook":
        return False
    if not accepted_secret(cfg):
        raise RuntimeError(
            "telegram_mode is 'webhook' but CH_TELEGRAM_WEBHOOK_SECRET is not set")
    return True


def _matches(given: str, expected: str) -> bool:
    """`compare_digest` over two strings that may hold anything at all.

    Not `==`: both credentials are compared on every public request, so a timing
    oracle on either is a forgery of Telegram itself. UTF-8 bytes rather than the
    strings, because `compare_digest` raises `TypeError` on a non-ASCII `str` and a
    header is whatever the caller typed.
    """
    return hmac.compare_digest(given.encode("utf-8", "surrogatepass"),
                               expected.encode("utf-8", "surrogatepass"))


def _dispatch(gateway: UpdateSink, payload: Any) -> None:
    """Run the update past the request, keeping a strong reference to the task."""

    async def run() -> None:
        try:
            await gateway.handle_update(payload)
        except Exception:
            # Nothing to answer with any more — the 200 went out before this task
            # started — so the log line is the whole report. It also covers the
            # payload aiogram refuses to validate: `Update.model_validate` raises
            # in here, not in the request, which is why no body is ever a 400 for
            # being the wrong SHAPE.
            log.exception("webhook update failed")

    task = asyncio.create_task(run(), name="telegram-webhook-update")
    _INFLIGHT.add(task)
    task.add_done_callback(_INFLIGHT.discard)


async def deliver(request: Request, secret: str, expected: str, gateway: UpdateSink,
                  dedup: UpdateDedup | None = None) -> Response:
    """The route body: both credentials, the JSON check, the dedup, the dispatch."""
    header = request.headers.get(SECRET_HEADER, "")
    if not expected or not _matches(secret, expected) or not _matches(header, expected):
        return JSONResponse(
            {"error": {"code": "unauthorized", "message": "bad webhook secret"}},
            status_code=401)
    try:
        payload = await request.json()
    except Exception:
        # A body that is not JSON at all cannot have come from Telegram, so the 400
        # is honest and there is nothing to retry.
        return JSONResponse(
            {"error": {"code": "invalid", "message": "body is not JSON"}},
            status_code=400)
    update_id = payload.get("update_id") if isinstance(payload, dict) else None
    if isinstance(update_id, int) and await (dedup or fallback_dedup()).seen(update_id):
        # Telegram's retry of something already accepted. Answer 200 so it stops,
        # and do NOT run it again: the second run is a second turn.
        log.info("dropping repeated telegram update %s", update_id)
        return JSONResponse({"ok": True})
    _dispatch(gateway, payload)
    # Answered before the update is handled, on purpose: the handler downloads
    # files and transcribes voice, and Telegram's retry timeout is shorter than
    # that. See the module docstring for what this trades away.
    return JSONResponse({"ok": True})


@router.post(WEBHOOK_PATH, operation_id="telegram_receive_update")
async def webhook(request: Request, secret: str, cfg: Any = Depends(get_config),
                  gateway: UpdateSink = Depends(get_gateway),
                  dedup: UpdateDedup = Depends(get_update_dedup)) -> Response:
    """One Bot API update, proven by the path secret AND the secret header.

    The body stays the raw update dict: it is Telegram's schema, not ours, and a
    model of it here would be a second copy of the Bot API that silently drops the
    fields it has not heard of yet. The gateway is the one that knows what an
    update is.
    """
    return await deliver(request, secret, accepted_secret(cfg), gateway, dedup)

"""What the webhook route asks for, and where it comes from.

Three providers, built once at module scope out of `core.di.provide` (FastAPI keys
`dependency_overrides` by the callable object, so a provider rebuilt per request
could not be overridden). They are the three bundle attributes this route reads,
and the module README lists them.

`UpdateSink` and `UpdateDedup` are structural types rather than imports of
`infrastructure.gateway.TelegramGateway` and `infrastructure.update_dedup`:
presentation and infrastructure are independent siblings here (the "telegram
layers" import contract), and the route calls exactly one method on each.
"""
from __future__ import annotations

from typing import Any, Protocol

from starlette.requests import Request

from claude_hermes.core.di import provide

from ..domain.dedup import MemoryUpdateDedup

__all__ = ["UpdateDedup", "UpdateSink", "fallback_dedup", "get_config",
           "get_gateway", "get_update_dedup"]


class UpdateSink(Protocol):
    """The one thing a webhook needs from the gateway: somewhere to put an update."""

    async def handle_update(self, payload: Any) -> None: ...


class UpdateDedup(Protocol):
    """Has this `update_id` already been accepted by this deployment?

    `True` means "already seen, drop it". Recording and answering are one call on
    purpose: the Redis implementation is a single `SET NX`, and a `seen?` followed
    by a `record!` would be a race between two instances handed the same retry.
    """

    async def seen(self, update_id: int) -> bool: ...


_cfg = provide("cfg")
_telegram = provide("telegram")
# Optional: a bundle a test built by hand may simply have no dedup, and gets the
# module-level fallback below. Nothing about the deployment
# is wrong in that case, which is exactly what `required=False` means here.
_update_dedup = provide("update_dedup", required=False)

# One per process, so it remembers anything at all. Reached through
# `fallback_dedup()` rather than the name, so the router and this module cannot end
# up holding two sets that each dedup half the retries.
_FALLBACK_DEDUP = MemoryUpdateDedup()


def fallback_dedup() -> UpdateDedup:
    """The process-wide in-memory dedup, for a bundle that wired none."""
    return _FALLBACK_DEDUP


def get_config(request: Request) -> Any:
    """`deps.cfg` — read for `telegram_mode` and `telegram_webhook_secret` only."""
    return _cfg(request)


def get_gateway(request: Request) -> UpdateSink:
    """`deps.telegram` — a `TelegramGateway`; only `handle_update(payload)` is called.

    Required: a deployment that mounts this route in webhook mode and has no gateway
    is a mounting mistake, and `provide`'s 503 says so rather than 500-ing on a None.
    """
    return _telegram(request)


def get_update_dedup(request: Request) -> UpdateDedup:
    """`deps.update_dedup` — `RedisUpdateDedup` in redis mode, else in-memory.

    Never 503s. The wired one is what makes the drop cross-instance; without it the
    route still refuses a retry that lands on the same process, which is the common
    case because a retry follows the timeout of the request still running here.
    """
    return _update_dedup(request) or _FALLBACK_DEDUP

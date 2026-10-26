"""`learning_review_until`: the review lease, as one `meta` row.

Wave 2 asked `LearningLoop` whether its `asyncio.Task` was still running. That
answer is worthless the moment the review runs in a worker process — the agent
holds no handle — and it is also lost across a restart. This is the replacement:
the review writes the instant its claim expires, and everyone who needs to know
"is a review in flight" reads that one row.

The row is an instant rather than a boolean on purpose. A boolean set to `true`
by a worker that is then OOM-killed mid-pass never goes back to `false`, and the
curator stays silently disabled until an operator notices the library has not
aged in a month. An expiry cannot get stuck: the worst case is that the curator
waits `review_timeout_seconds` once.

Works identically in both modes, because `core.meta.MetaStore` is the same
PostgreSQL table in both. Redis is not involved: the gate has to survive the
deployment that has no Redis at all.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import structlog

from claude_hermes.modules.learning.application.ports import MetaStore
from claude_hermes.modules.learning.domain.constants import REVIEW_GATE_META_KEY
from claude_hermes.modules.learning.domain.usage import parse_aware

log = structlog.get_logger(__name__)


class MetaReviewGate:
    """`ReviewGate` over the `meta` table. `timeout_seconds` is
    `cfg.review_timeout_seconds` — how long one claim is trusted for."""

    def __init__(self, meta: MetaStore, *, timeout_seconds: float,
                 now: Callable[[], datetime] | None = None) -> None:
        if timeout_seconds <= 0:
            raise ValueError(
                "review_timeout_seconds must be positive: a lease that never "
                "expires is exactly the wedged curator this class exists to avoid")
        self._meta = meta
        self._timeout = timeout_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def running(self) -> bool:
        raw = await self._meta.get(REVIEW_GATE_META_KEY)
        if not raw:
            return False
        try:
            until = parse_aware(raw)
        except ValueError:
            # A hand-edited or half-written row must not block the curator
            # forever; an unreadable lease is no lease.
            log.warning("learning: unreadable review gate %r; reading as free", raw)
            return False
        return self._now() < until

    async def claim(self) -> None:
        """Take the gate for `timeout_seconds`. Deliberately not conditional: the
        caller is the review itself, which has already been dispatched, and a
        second claim only pushes the same lease out."""
        until = self._now() + timedelta(seconds=self._timeout)
        await self._meta.set(REVIEW_GATE_META_KEY, until.isoformat())

    async def release(self) -> None:
        # `MetaStore` is get/set only, so the empty string is the cleared state —
        # `running()` reads it as free. A DELETE would mean a third method on a
        # port that several modules share, for one caller.
        await self._meta.set(REVIEW_GATE_META_KEY, "")

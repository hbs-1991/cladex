"""The asyncio loop that drives `SchedulerService.tick`.

All it owns is the clock and the sleep. In redis mode the `scheduler` container
drives the same `tick(now)` through a taskiq periodic task instead; nothing in the
service changes.

`extra` is how inline mode reaches the work that rides the same clock without a
second one — today that is `TurnLauncher.reap`, which frees the lane of a turn
whose worker died. Each extra runs in its own `try/except`: a reap that raises
must not stop the clock for the cron jobs, and neither must a job.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Sequence

import structlog

log = structlog.get_logger(__name__)

# `async (now) -> None`: the same clock every tick already has.
Extra = Callable[[datetime], Awaitable[None]]


class CronTicker:
    def __init__(self, service, tick_seconds: float, *,
                 extra: Sequence[Extra] = ()):
        self._service = service
        self._tick_seconds = tick_seconds
        self._extra = tuple(extra)

    async def tick(self, now: datetime | None = None) -> None:
        when = now or datetime.now(timezone.utc)
        await self._service.tick(when)
        await self._run_extra(when)

    async def run_forever(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            try:
                await self._service.tick(now)
            except Exception:
                # One bad job must not end the ticker for every other job.
                log.exception("cron tick failed")
            await self._run_extra(now)
            await asyncio.sleep(self._tick_seconds)

    async def _run_extra(self, now: datetime) -> None:
        for run in self._extra:
            try:
                await run(now)
            except Exception:
                log.exception("cron tick extra failed")

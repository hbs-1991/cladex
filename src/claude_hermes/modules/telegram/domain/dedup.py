"""At-least-once delivery, made at-most-once: the in-process half.

Telegram redelivers an update it did not get a 2xx for within its timeout, and the
webhook handler runs intake (a file download and a MinIO put) and voice
transcription before a turn even starts — so one slow document used to be two
turns and two charges. Dropping a repeated `update_id` is what makes the transport
idempotent, and this is the implementation a single process needs.

It lives in `domain/` rather than next to `RedisUpdateDedup` in `infrastructure/`
for one reason: the webhook route needs a fallback when the composition root wired
nothing, and `presentation` and `infrastructure` are independent siblings under the
"telegram layers" contract, so presentation may not import that module.
`infrastructure/update_dedup.py` re-exports this class, so both spellings name one
implementation. It is pure Python — no clock, no client, no logging.
"""
from __future__ import annotations

from collections import OrderedDict

__all__ = ["MemoryUpdateDedup"]

# ~4k updates is minutes of a busy bot and a few hundred kilobytes at most. The
# bound is what stops a long-lived process from growing one entry per update
# forever; the eviction it causes can only ever re-admit an update Telegram gave
# up retrying long ago.
DEFAULT_CAPACITY = 4096


class MemoryUpdateDedup:
    """An LRU set of `update_id`s. `seen(id)` is True when this process saw it.

    Not a boundary across instances: with `--scale agent=N` behind one origin each
    replica has its own set, which is exactly why redis mode swaps in
    `RedisUpdateDedup`. The failure this one still prevents — Telegram retrying
    into the *same* process that is already busy with the update — is the common
    one, because retries follow the timeout of the request that is still running.
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._capacity = max(1, capacity)
        self._seen: OrderedDict[int, None] = OrderedDict()

    async def seen(self, update_id: int) -> bool:
        """Record `update_id` and report whether it was already there.

        Async because the port is: the Redis implementation awaits a round trip and
        the caller may not know which one it holds.
        """
        if update_id in self._seen:
            self._seen.move_to_end(update_id)
            return True
        self._seen[update_id] = None
        while len(self._seen) > self._capacity:
            self._seen.popitem(last=False)
        return False

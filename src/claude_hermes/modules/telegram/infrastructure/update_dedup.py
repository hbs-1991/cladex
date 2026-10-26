"""The `UpdateDedup` port's two implementations, picked by `queue_mode`.

The webhook route drops an `update_id` it has already accepted (Telegram retries
one it did not get a 2xx for, and the handler that follows downloads files and
transcribes voice — a repeat is a second turn and a second charge). *Where* the
"already accepted" is written is the only difference between the two deployments:

* inline mode is one process, so an LRU in memory is the whole of it
  (`MemoryUpdateDedup`, re-exported from `domain/dedup.py` — the webhook route
  needs the same class as a fallback and presentation may not import this module);
* redis mode is N agents behind one public origin, and the point of the drop is
  that it is **cross-instance** — so the record is `SET NX EX` on a shared key.
"""
from __future__ import annotations

import structlog

from ..domain.dedup import MemoryUpdateDedup

log = structlog.get_logger(__name__)

__all__ = ["MemoryUpdateDedup", "RedisUpdateDedup"]

# Telegram gives up retrying an update long before this. Ten minutes is generous
# enough to cover a webhook that timed out on a slow document and short enough
# that the keys of a busy day do not accumulate in Redis.
DEFAULT_TTL_SECONDS = 600
KEY_PREFIX = "hermes:tg:update:"


class RedisUpdateDedup:
    """`SET hermes:tg:update:<id> 1 NX EX <ttl>` — the first setter wins.

    One round trip that both records and answers: `SET NX` returns true only to the
    caller that created the key, so "did I get the key" *is* "was I the first
    instance to accept this update". No lock, no release — an update is accepted
    once and the key simply expires.

    **Fails open.** A Redis that is down answers `False` (not seen) and logs, so a
    broken cache degrades to the pre-4a behaviour (a possible duplicate turn)
    rather than to silence — losing every update while Redis reconnects would be
    the strictly worse failure, and the webhook has already told Telegram 200.
    """

    def __init__(self, client, *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
                 prefix: str = KEY_PREFIX) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._prefix = prefix

    async def seen(self, update_id: int) -> bool:
        try:
            stored = await self._client.set(f"{self._prefix}{update_id}", "1",
                                            nx=True, ex=self._ttl)
        except Exception:
            log.warning("update dedup unavailable; accepting update %s", update_id,
                        exc_info=True)
            return False
        return not stored

"""The Redis client this process shares, and the cross-process lock built on it.

Only reached when `CH_REDIS_URL` is set (`config.queue_mode == "redis"`). With no
URL nothing here is constructed: the app keeps `asyncio.Lock` and in-process
state, which is the whole point of inline mode.

Note the module name shadows the third-party `redis` package *inside this
directory only* — Python 3 imports are absolute, so `import redis.asyncio` below
is the real client library.
"""
from __future__ import annotations

import uuid
from types import TracebackType

import redis.asyncio as aioredis
from redis.exceptions import ResponseError, WatchError

__all__ = ["create_client", "Lock"]


def create_client(url: str, *, decode_responses: bool = True) -> aioredis.Redis:
    """One client per process, from the URL. redis-py pools connections itself,
    so this is a factory for a long-lived object, not something to call per use.

    `decode_responses=True` by default because everything this app stores and
    publishes is text: lock tokens, ISO timestamps, and the JSON payloads
    `core.eventbus.RedisEventBus` carries on `hermes:turn:<lane>` and
    `hermes:ctl`. There is no byte payload anywhere in this app, so nothing
    should pass False — the keyword exists for a caller that has one, not as an
    invitation to invent one.

    `health_check_interval` is not a nicety: an idle agent holds a pooled
    connection open across a NAT or proxy timeout and the next command fails once
    with a dead socket. With it, redis-py PINGs a stale connection and reconnects
    instead of handing the failure to the caller.
    """
    return aioredis.Redis.from_url(
        url, decode_responses=decode_responses, health_check_interval=30)


# Release has to be atomic against "my lease expired and somebody else took the
# lock between my GET and my DEL" — the classic way to delete a lock you no
# longer hold. Compare and delete in one server-side step.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

_EXTEND_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class Lock:
    """A single-holder lock across processes: `SET key token NX PX ttl`.

    The token is what makes it safe. Two workers racing on `lock:curator` both
    call `SET NX`; one wins, and only the winner's token can delete the key. The
    TTL is the dead-man's switch — a worker that is killed mid-pass does not
    leave the lock held forever.

    Used as a context manager it reports rather than raises, because every
    current caller wants "someone else is already doing this, skip":

        async with Lock(client, "lock:curator", ttl_ms=30 * 60_000) as held:
            if not held:
                return
            await curator.run_pass()

    Pick a TTL longer than the work can take; `extend()` is there for work that
    outgrows its estimate.
    """

    def __init__(self, client: aioredis.Redis, key: str, *, ttl_ms: int,
                 token: str | None = None) -> None:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive: a lock with no TTL is a deadlock")
        self.client = client
        self.key = key
        self.ttl_ms = ttl_ms
        self.token = token or uuid.uuid4().hex
        self.held = False

    async def acquire(self) -> bool:
        self.held = bool(await self.client.set(self.key, self.token, nx=True, px=self.ttl_ms))
        return self.held

    async def release(self) -> bool:
        """True if this holder's key was the one deleted. False means the lease
        had already expired and the lock may belong to somebody else — which is
        exactly the case the token check exists to catch."""
        if not self.held:
            return False
        self.held = False
        return bool(await self._eval(_RELEASE_LUA, self.key, self.token))

    async def extend(self, ttl_ms: int | None = None) -> bool:
        """Push the lease out, but only while we still hold it."""
        if not self.held:
            return False
        ok = bool(await self._eval(_EXTEND_LUA, self.key, self.token,
                                   str(ttl_ms or self.ttl_ms)))
        self.held = ok
        return ok

    async def _eval(self, script: str, key: str, *args: str) -> int:
        try:
            return int(await self.client.eval(script, 1, key, *args))
        except ResponseError as e:
            # fakeredis only implements EVAL when `lupa` is installed, and lupa
            # is not in the dev extra. The WATCH/MULTI fallback is the same
            # compare-and-act in one transaction, so the unit tests exercise the
            # real semantics; the container tests take the Lua path above.
            if "unknown command" not in str(e).lower():
                raise
            return await self._eval_fallback(script, key, *args)

    async def _eval_fallback(self, script: str, key: str, *args: str) -> int:
        token, *rest = args
        async with self.client.pipeline(transaction=True) as pipe:
            await pipe.watch(key)
            current = await pipe.get(key)
            if isinstance(current, bytes):
                current = current.decode()
            if current != token:
                await pipe.reset()
                return 0
            pipe.multi()
            if script is _RELEASE_LUA:
                pipe.delete(key)
            else:
                pipe.pexpire(key, int(rest[0]))
            try:
                result = await pipe.execute()
            except WatchError:
                # The key changed between the WATCH and the EXEC — the lease
                # expired and somebody else took it. Same answer the Lua gives:
                # this holder no longer owns the lock.
                return 0
        return int(bool(result and result[0]))

    async def __aenter__(self) -> bool:
        return await self.acquire()

    async def __aexit__(self, _exc_type: type[BaseException] | None,
                        exc: BaseException | None, _tb: TracebackType | None) -> None:
        await self.release()

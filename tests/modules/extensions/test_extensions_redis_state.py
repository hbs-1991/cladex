"""The two pieces of extension state that move to Redis in redis mode.

`RedisPendingStates` is the OAuth flow surviving a restart — and a callback landing
on a different process than the one that pressed Connect. `RedisLocks` is the
refresh lock spanning processes, which is what the `asyncio.Lock` never could once
a worker refreshes the same connector the agent is composing.

Both run against the real container (`make test-stack`) and against `fakeredis`
otherwise, because the in-memory implementation is the one a laptop actually uses.
"""
from __future__ import annotations

import asyncio

import pytest

from claude_hermes.modules.extensions.domain.oauth import PENDING_TTL
from claude_hermes.modules.extensions.infrastructure.redis_pending_states import (
    RedisPendingStates,
)
from claude_hermes.modules.extensions.infrastructure.refresh_locks import (
    InProcessLocks,
    RedisLocks,
)


@pytest.fixture(params=["fake", "real"])
def client_factory(request):
    """A zero-arg factory for a Redis client.

    Sync on purpose: `request.getfixturevalue` on the async `redis_url` fixture from
    an async fixture raises "Runner.run() cannot be called from a running event
    loop". The factory defers the client's construction to inside the test.
    """
    if request.param == "fake":
        fakeredis = pytest.importorskip("fakeredis")
        return lambda: fakeredis.aioredis.FakeRedis(decode_responses=True)

    url = request.getfixturevalue("redis_url")
    from claude_hermes.core.redis import create_client
    return lambda: create_client(url)


async def test_pending_state_is_single_use_owner_bound_and_expiring(client_factory):
    client = client_factory()
    try:
        p = RedisPendingStates(client)
        state = await p.issue("notion", owner_id=7, verifier="v", now=0.0)
        # Someone else's callback consumes nothing: the owner check is before the
        # delete, so a guessed state cannot burn the owner's flow.
        assert await p.take(state, 9, 1.0) is None
        got = await p.take(state, 7, 1.0)
        assert got is not None and got.name == "notion" and got.verifier == "v"
        assert await p.take(state, 7, 1.0) is None          # single use

        stale = await p.issue("notion", owner_id=7, verifier="v", now=0.0)
        assert await p.take(stale, 7, PENDING_TTL + 1) is None
    finally:
        await client.aclose()


async def test_the_key_carries_the_ten_minute_ttl(client_factory):
    """Redis owns the expiry, so there is no sweep and no clock to get wrong across
    a restart."""
    client = client_factory()
    try:
        p = RedisPendingStates(client)
        state = await p.issue("notion", owner_id=7, verifier="v", now=0.0)
        ttl_ms = await client.pttl("oauth:pending:" + state)
        assert 0 < ttl_ms <= PENDING_TTL * 1000
    finally:
        await client.aclose()


async def test_a_flow_started_by_one_store_is_taken_by_another(client_factory):
    """The whole point: Connect and the callback are two processes."""
    client = client_factory()
    try:
        state = await RedisPendingStates(client).issue("notion", 7, "v", 0.0)
        got = await RedisPendingStates(client).take(state, 7, 1.0)
        assert got is not None and got.verifier == "v"
    finally:
        await client.aclose()


async def test_the_refresh_lock_serialises_two_holders(client_factory):
    """A provider that rotates refresh tokens refuses the second use of the same
    one, so the two callers must not be inside the refresh at once."""
    client = client_factory()
    try:
        locks = RedisLocks(client, ttl_ms=2_000)
        inside: list[str] = []

        async def refresh(tag: str) -> None:
            async with locks("notion"):
                inside.append(f"enter-{tag}")
                await asyncio.sleep(0.05)
                inside.append(f"exit-{tag}")

        await asyncio.gather(refresh("a"), refresh("b"))
        # Never interleaved: each enter is immediately followed by its own exit.
        assert inside[0].startswith("enter") and inside[1] == inside[0].replace("enter", "exit")
        assert len(inside) == 4
    finally:
        await client.aclose()


async def test_the_lock_is_per_connector_not_global(client_factory):
    client = client_factory()
    try:
        locks = RedisLocks(client, ttl_ms=2_000)
        async with locks("notion"):
            # A different connector must not queue behind this one; if it did, this
            # would block for the whole lease.
            async with asyncio.timeout(1.0), locks("linear"):
                pass
    finally:
        await client.aclose()


async def test_the_in_process_lock_is_the_same_object_per_name():
    """`for_scope` builds an `OAuthService` for `turn` and one for `cron`; a lock
    made per instance would silently stop locking anything."""
    locks = InProcessLocks()
    assert locks("notion") is locks("notion")
    assert locks("notion") is not locks("linear")

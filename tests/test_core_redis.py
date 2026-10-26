"""`core.redis.Lock`, against fakeredis and — when `make test-stack` is up —
against the real server, which is the one that actually runs the Lua."""
from __future__ import annotations

import asyncio

import pytest
from fakeredis.aioredis import FakeRedis

from claude_hermes.core.redis import Lock, create_client

KEY = "lock:curator"


@pytest.fixture
async def fake():
    client = FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def real(redis_url):
    """The container. Skipped with a `make test-stack` reason when it is down."""
    client = create_client(redis_url)
    try:
        yield client
    finally:
        await client.aclose()


# Both clients run the same assertions: fakeredis takes the WATCH/MULTI fallback
# in `_eval`, the container takes the Lua path, and the observable behaviour has
# to be identical or the fallback is a lie.
@pytest.fixture(params=["fake", "real"])
def client(request):
    # Sync on purpose: `getfixturevalue` of an async fixture has to run outside
    # pytest-asyncio's runner, and an async wrapper would already be inside it.
    return request.getfixturevalue(request.param)


async def test_second_holder_is_refused(client):
    first = Lock(client, KEY, ttl_ms=5000)
    second = Lock(client, KEY, ttl_ms=5000)
    assert await first.acquire() is True
    assert await second.acquire() is False
    assert await first.release() is True
    assert await second.acquire() is True
    await second.release()


async def test_release_only_deletes_your_own_key(client):
    """The failure this prevents: A's lease expires, B takes the lock, A finishes
    and deletes the key — and now C and B both think they hold it."""
    a = Lock(client, KEY, ttl_ms=5000, token="a")
    assert await a.acquire() is True
    # Simulate the expiry-then-steal: B's token is now what is in the key.
    await client.set(KEY, "b", px=5000)
    assert await a.release() is False
    assert await client.get(KEY) in ("b", b"b")


async def test_lease_expires_on_its_own(client):
    """The dead-man's switch: a worker killed mid-pass must not hold the lock
    forever, because there is nobody left to release it."""
    dead = Lock(client, KEY, ttl_ms=60)
    assert await dead.acquire() is True
    await asyncio.sleep(0.2)
    assert await Lock(client, KEY, ttl_ms=5000).acquire() is True


async def test_extend_pushes_the_lease_out_but_not_somebody_elses(client):
    lock = Lock(client, KEY, ttl_ms=120)
    assert await lock.acquire() is True
    assert await lock.extend(5000) is True
    await asyncio.sleep(0.25)
    assert await Lock(client, KEY, ttl_ms=1000).acquire() is False

    other = Lock(client, KEY, ttl_ms=1000, token="not-mine")
    other.held = True          # claims to hold it; the token says otherwise
    assert await other.extend(9000) is False
    await lock.release()


async def test_context_manager_reports_instead_of_raising(client):
    """Every caller wants "someone else is doing this, skip" — so `async with`
    yields the answer rather than throwing, and releases only what it took."""
    holder = Lock(client, KEY, ttl_ms=5000)
    assert await holder.acquire() is True

    async with Lock(client, KEY, ttl_ms=5000) as held:
        assert held is False
    # The loser's __aexit__ must not have deleted the winner's key.
    assert await client.get(KEY) is not None

    await holder.release()
    async with Lock(client, KEY, ttl_ms=5000) as held:
        assert held is True
    assert await client.get(KEY) is None


async def test_a_lock_without_a_ttl_is_refused(fake):
    with pytest.raises(ValueError):
        Lock(fake, KEY, ttl_ms=0)


async def test_create_client_decodes_by_default(redis_url):
    client = create_client(redis_url)
    try:
        await client.set("k", "v")
        assert await client.get("k") == "v"
    finally:
        await client.aclose()

    raw = create_client(redis_url, decode_responses=False)
    try:
        assert await raw.get("k") == b"v"
    finally:
        await raw.aclose()

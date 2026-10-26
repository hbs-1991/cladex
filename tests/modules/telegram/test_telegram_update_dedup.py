"""The two `UpdateDedup` implementations.

The port is one method and its whole contract is "record and answer in one step":
a `seen?` followed by a `record!` would be a race between two instances handed the
same Telegram retry, which is precisely the case the Redis one exists for.
"""
from __future__ import annotations

import pytest

from claude_hermes.modules.telegram.domain.dedup import MemoryUpdateDedup
from claude_hermes.modules.telegram.infrastructure.update_dedup import (
    KEY_PREFIX,
    RedisUpdateDedup,
)

fakeredis = pytest.importorskip("fakeredis")


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def redis_client(server):
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)


async def test_memory_reports_the_second_sight_of_an_id():
    dedup = MemoryUpdateDedup()
    assert await dedup.seen(7) is False
    assert await dedup.seen(7) is True
    assert await dedup.seen(8) is False


async def test_memory_evicts_the_oldest_beyond_its_capacity():
    """The bound is what stops a long-lived process growing one entry per update
    forever. What it can cost is re-admitting an update Telegram gave up retrying
    thousands of updates ago."""
    dedup = MemoryUpdateDedup(capacity=3)
    for update_id in (1, 2, 3):
        assert await dedup.seen(update_id) is False
    assert await dedup.seen(4) is False          # evicts 1
    assert await dedup.seen(1) is False          # …so 1 is new again
    assert await dedup.seen(3) is True           # still remembered


async def test_memory_keeps_a_repeated_id_alive_in_the_lru():
    """A retried id is touched, not aged out by its own retries."""
    dedup = MemoryUpdateDedup(capacity=2)
    await dedup.seen(1)
    await dedup.seen(2)
    assert await dedup.seen(1) is True           # moves 1 to the front
    await dedup.seen(3)                          # evicts 2, not 1
    assert await dedup.seen(1) is True
    assert await dedup.seen(2) is False


async def test_redis_setnx_is_the_arbiter_between_two_instances(server):
    """Two clients on one server IS what "two agent replicas" means here: the SET
    NX is the whole of the decision, so the instance that did not create the key
    is the one that drops the update."""
    first, second = redis_client(server), redis_client(server)
    a, b = RedisUpdateDedup(first), RedisUpdateDedup(second)
    assert await a.seen(42) is False
    assert await b.seen(42) is True
    assert await b.seen(43) is False
    await first.aclose()
    await second.aclose()


async def test_redis_keys_carry_the_prefix_and_a_ttl(server):
    """The key expires on its own: an update accepted once is never accepted
    again while Telegram might still retry it, and nothing has to clean up."""
    client = redis_client(server)
    await RedisUpdateDedup(client, ttl_seconds=60).seen(5)
    assert await client.get(f"{KEY_PREFIX}5") == "1"
    assert 0 < await client.ttl(f"{KEY_PREFIX}5") <= 60
    await client.aclose()


async def test_redis_fails_open_when_the_server_is_gone():
    """A cache that is down must not swallow every update: the webhook has already
    answered 200, so a False here is "run it" — the pre-4a behaviour, a possible
    duplicate — while a raise would be a message that never gets a reply."""

    class Dead:
        async def set(self, *args, **kwargs):
            raise ConnectionError("redis is gone")

    assert await RedisUpdateDedup(Dead()).seen(9) is False

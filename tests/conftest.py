"""Every test talks to a real PostgreSQL. One database per pytest process (so
parallel runs never collide), migrated with Alembic — which makes every run a
check that `upgrade head` builds the schema — and truncated before each test.

Redis and MinIO are different: the suite defaults to inline mode and the local
blob store, so only the lock, queue and blob-store integration tests need them.
Those fixtures *skip* with a "run `make test-stack`" reason instead of exiting the
run — the suite has to stay green on a laptop with only the database up."""
from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from claude_hermes.core.db.engine import make_engine
from claude_hermes.core.db.migrate import upgrade_head_sync
from claude_hermes.core.db.registry import metadata

SERVER_URL = os.environ.get(
    "CH_TEST_DATABASE_URL", "postgresql+asyncpg://hermes:hermes@127.0.0.1:55432/hermes")


def _dsn(url: str) -> str:
    """SQLAlchemy URL -> plain asyncpg DSN."""
    return str(make_url(url).set(drivername="postgresql").render_as_string(hide_password=False))


async def _admin(sql: str) -> None:
    conn = await asyncpg.connect(_dsn(SERVER_URL))
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    name = f"hermes_test_{uuid.uuid4().hex[:8]}"
    try:
        asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    except OSError as e:
        pytest.exit(f"PostgreSQL test server not reachable at {SERVER_URL} ({e}); "
                    "run `make test-db` or set CH_TEST_DATABASE_URL", returncode=3)
    url = str(make_url(SERVER_URL).set(database=name).render_as_string(hide_password=False))
    upgrade_head_sync(url)
    os.environ["CH_DATABASE_URL"] = url
    yield url
    asyncio.run(_admin(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def engine(test_database):
    """A pool over the per-process database, disposed after the test.

    What every repository, service and composition-root object takes. Function-
    scoped so a test that leaves a connection mid-transaction cannot hand it to
    the next one; the tables are truncated between tests either way.
    """
    made = make_engine(os.environ["CH_DATABASE_URL"])
    try:
        yield made
    finally:
        await made.dispose()


async def ensure_users(engine, *ids: int, owner: int | None = None) -> None:
    """Give these ids a `users` row, so the Wave 4a foreign keys hold.

    Since `0005` every `owner_id` — and `threads.owner_user_id` — references
    `users(id)`, so a repository test that inserts a thread or a session for user 7
    now needs user 7 to exist. This is that one line, and it goes through
    `UserRepository` rather than raw SQL so no test writes `users` SQL of its own.

    `owner` (when given) gets `role='owner'` and `workspace_dir=''` — the owner's
    root IS `CH_HOME`, which is the whole reason a live volume needed no file
    migration. Everyone else gets `role='user'` and `users/<id>`, exactly what
    `AccessControl.check` would have created on their first message. Idempotent:
    an id that already has a row is left alone, so a test may call this twice or
    pass the owner in `ids` as well.
    """
    from claude_hermes.modules.identity.application.services import (
        OWNER_WORKSPACE,
        user_workspace_dir,
    )
    from claude_hermes.modules.identity.domain.exceptions import (
        OwnerExists,
        UserExists,
    )
    from claude_hermes.modules.identity.domain.models import Role
    from claude_hermes.modules.identity.infrastructure.repositories import (
        UserRepository,
    )

    repo = UserRepository(engine)
    now = "2026-01-01T00:00:00+00:00"
    wanted: list[tuple[int, str, str]] = []
    if owner is not None:
        wanted.append((owner, Role.OWNER.value, OWNER_WORKSPACE))
    wanted += [(uid, Role.USER.value, user_workspace_dir(uid))
               for uid in ids if uid != owner]
    for user_id, role, workspace_dir in wanted:
        try:
            await repo.create(user_id, role, workspace_dir, None, now)
        except (OwnerExists, UserExists):
            pass


@pytest.fixture(autouse=True)
async def _clean_tables(test_database):
    tables = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    conn = await asyncpg.connect(_dsn(test_database))
    try:
        await conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


# --- Redis -----------------------------------------------------------------
# db 15: the same server may be holding a dev app's keys on db 0, and FLUSHDB on
# the wrong database is the kind of mistake you make exactly once.
REDIS_URL = os.environ.get("CH_TEST_REDIS_URL", "redis://127.0.0.1:56379/15")


@pytest.fixture
async def redis_url() -> str:
    """A reachable Redis URL, with db 15 emptied first.

    Function-scoped on purpose: a lock test that leaves `lock:curator` behind
    would otherwise decide the next test's result.
    """
    from redis.exceptions import RedisError

    from claude_hermes.core.redis import create_client

    client = create_client(REDIS_URL)
    try:
        await client.ping()
        await client.flushdb()
    except (OSError, RedisError) as e:
        pytest.skip(f"Redis not reachable at {REDIS_URL} ({e}); run `make test-stack`")
    finally:
        await client.aclose()
    return REDIS_URL


# --- MinIO -----------------------------------------------------------------
S3_ENDPOINT = os.environ.get("CH_TEST_S3_ENDPOINT", "http://127.0.0.1:59000")
S3_ACCESS_KEY = os.environ.get("CH_TEST_S3_ACCESS_KEY", "hermes")
S3_SECRET_KEY = os.environ.get("CH_TEST_S3_SECRET_KEY", "hermes-test")


def _purge(raw, bucket: str) -> None:
    """Delete every object in the bucket. S3 refuses to drop a bucket that still
    has contents, and a failed test leaves contents behind by definition."""
    paginator = raw.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        keys = [{"Key": item["Key"]} for item in page.get("Contents", ())]
        if keys:
            raw.delete_objects(Bucket=bucket, Delete={"Objects": keys})


@pytest.fixture(scope="session")
def s3_bucket() -> str:
    """One bucket per pytest process, dropped at the end.

    Same reasoning as the per-process database: parallel runs must not share a
    namespace, and a bucket left behind on a laptop is litter nobody notices.
    """
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import BotoCoreError, ClientError

    raw = boto3.client(
        "s3", endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY, aws_secret_access_key=S3_SECRET_KEY,
        region_name="us-east-1",
        config=BotoConfig(s3={"addressing_style": "path"},
                          retries={"max_attempts": 1, "mode": "standard"},
                          connect_timeout=3, read_timeout=10))
    name = f"hermes-test-{uuid.uuid4().hex[:8]}"
    try:
        raw.create_bucket(Bucket=name)
    except (BotoCoreError, ClientError, OSError) as e:
        pytest.skip(f"MinIO not reachable at {S3_ENDPOINT} ({e}); run `make test-stack`")
    try:
        yield name
    finally:
        _purge(raw, name)
        raw.delete_bucket(Bucket=name)
        raw.close()


@pytest.fixture
async def s3(s3_bucket):
    """The app's `S3Client` against the per-process bucket, emptied first."""
    from claude_hermes.integrations.s3 import S3Client

    client = S3Client(S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, s3_bucket)
    for key in await client.list_prefix(""):
        await client.delete_object(key)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def s3_for():
    """Build an `S3Client` for an arbitrary bucket with the stack's credentials.

    For the handful of tests that need a bucket the `s3` fixture does not own —
    `ensure_bucket` creating one from scratch, most of all.
    """
    from claude_hermes.integrations.s3 import S3Client

    built = []

    def make(bucket: str) -> "S3Client":
        client = S3Client(S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, bucket)
        built.append(client)
        return client

    try:
        yield make
    finally:
        for client in built:
            await client.aclose()

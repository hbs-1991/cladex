"""`SessionRouter` over the real `SessionRepository`: the route window
(`idle_reset_hours`, `daily_reset_hour`) and the per-owner reset, both against
PostgreSQL. The key grammar itself is `tests/test_core_session_keys.py`."""
from datetime import datetime, timezone

from conftest import ensure_users

from claude_hermes.core.config import Config
from claude_hermes.modules.sessions.application.services import SessionRouter
from claude_hermes.modules.sessions.infrastructure.repositories import SessionRepository


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def make_router(engine, **cfg_kw):
    store = SessionRepository(engine)
    return store, SessionRouter(store, Config(**cfg_kw))


async def test_route_returns_fresh_session(engine):
    store, router = make_router(engine)
    await router.bind("tg:private:1", "sdk-1", "APPEND", "2026-07-12T10:00:00+00:00")
    row = await router.route("tg:private:1", dt("2026-07-12T12:00:00"))
    assert row is not None and row["sdk_session_id"] == "sdk-1"


async def test_route_idle_reset(engine):
    store, router = make_router(engine, idle_reset_hours=24.0)
    await router.bind("tg:private:1", "sdk-1", "APPEND", "2026-07-10T10:00:00+00:00")
    assert await router.route("tg:private:1", dt("2026-07-12T12:00:00")) is None
    assert await store.get("tg:private:1") is None  # stale row purged


async def test_route_daily_reset_at_4am(engine):
    store, router = make_router(engine, daily_reset_hour=4, idle_reset_hours=999.0)
    # last activity yesterday 23:00, now today 09:00 → crossed the 04:00 boundary
    await router.bind("tg:private:1", "sdk-1", "APPEND", "2026-07-11T23:00:00+00:00")
    assert await router.route("tg:private:1", dt("2026-07-12T09:00:00")) is None
    # same day, before → after boundary not crossed: 01:00 → 03:00 stays alive
    await router.bind("tg:private:2", "sdk-2", "APPEND", "2026-07-12T01:00:00+00:00")
    assert await router.route("tg:private:2", dt("2026-07-12T03:00:00")) is not None


async def test_route_unknown_key_is_none(engine):
    store, router = make_router(engine)
    assert await router.route("tg:private:404", dt("2026-07-12T09:00:00")) is None


async def test_sessions_are_eternal_by_default(engine):
    store, router = make_router(engine)          # Config() → both timers None
    await router.bind("tg:private:1:t9", "sdk-1", "APPEND", "2026-01-01T10:00:00+00:00")
    row = await router.route("tg:private:1:t9", dt("2026-12-31T23:59:00"))
    assert row is not None and row["sdk_session_id"] == "sdk-1"   # a year later, still alive


# --- reset_owned (Wave 4b, B-4) ---------------------------------------------
# A skill one user edits changes the index in THEIR cached prefix and nobody
# else's, so the reset it costs is scoped to their lanes — `reset_all` here would
# evict every other user's warm cache for one person's edit.


async def test_reset_owned_drops_one_owners_lanes_and_leaves_the_rest(engine):
    await ensure_users(engine, 1, 2, owner=1)
    store = SessionRepository(engine)
    now = "2026-07-12T10:00:00+00:00"
    await store.upsert("web:1:a", "sdk-a", "A", now, owner_id=1)
    await store.upsert("tg:private:1", "sdk-b", "A", now, owner_id=1)
    await store.upsert("web:2:c", "sdk-c", "A", now, owner_id=2)
    await store.upsert("tg:group:-5:9", "sdk-d", "A", now)      # NULL: bound unclaimed

    await store.reset_owned(2)
    assert await store.get("web:2:c") is None
    assert await store.get("web:1:a") is not None
    assert await store.get("tg:private:1") is not None
    assert await store.get("tg:group:-5:9") is not None       # not user 2's to sweep

    await store.reset_owned(1)
    assert await store.get("web:1:a") is None
    assert await store.get("tg:private:1") is None
    assert await store.get("tg:group:-5:9") is not None       # NULL is not 1 either


async def test_reset_owned_with_the_null_belt_takes_the_unowned_lanes_too(engine):
    """A lane bound while `deploy_owner()` was still 0 wrote NULL (the runner
    passes `None` for owner 0). Those lanes are the deploy owner's, and only the
    caller that IS the owner passes `include_unowned` — a user's reset must never
    reach a row it cannot prove is theirs."""
    await ensure_users(engine, 1, 2, owner=1)
    store, router = make_router(engine)
    now = "2026-07-12T10:00:00+00:00"
    await router.bind("web:1:a", "sdk-a", "A", now, owner_id=1)
    await router.bind("web:2:c", "sdk-c", "A", now, owner_id=2)
    await router.bind("tg:group:-5:9", "sdk-d", "A", now)

    await router.reset_owned(1, include_unowned=True)
    assert await store.get("web:1:a") is None
    assert await store.get("tg:group:-5:9") is None
    assert await store.get("web:2:c") is not None              # untouched

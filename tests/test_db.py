"""The table behaviours no `tests/modules/*` repository test pins yet, each over
the repository that owns the table. Everything else the legacy facade tests
covered lives in `tests/modules/sessions/test_sessions_repositories.py`,
`tests/modules/turns/test_turns_repository.py` and
`tests/modules/files/test_files_service.py`."""
from claude_hermes.core.meta import MetaStore
from claude_hermes.modules.sessions.infrastructure.repositories import (
    MessageRepository,
    SessionRepository,
    ThreadRepository,
)
from claude_hermes.modules.turns.infrastructure.repositories import TurnEventRepository
from conftest import ensure_users

NOW = "2026-07-12T10:00:00+00:00"


async def test_meta_kv_roundtrip(engine):
    meta = MetaStore(engine)
    assert await meta.get("owner_user_id") is None
    await meta.set("owner_user_id", "42")
    assert await meta.get("owner_user_id") == "42"
    await meta.set("owner_user_id", "43")  # overwrite
    assert await meta.get("owner_user_id") == "43"


async def test_turn_events_append_and_cursor(engine):
    events = TurnEventRepository(engine)
    a = await events.append("web:1:x", "t1", 1, NOW, "turn.start", {"channel": "web"})
    b = await events.append("web:1:x", "t1", 2, NOW, "text.block", {"text": "hi"})
    await events.append("web:1:y", "t2", 1, NOW, "turn.start", {"channel": "web"})
    assert b == a + 1
    rows = await events.after("web:1:x")
    assert [r["type"] for r in rows] == ["turn.start", "text.block"]
    assert rows[1]["payload"] == {"text": "hi"}          # JSON is parsed back
    assert rows[1]["id"] == b and rows[1]["seq"] == 2
    assert await events.after("web:1:x", after_id=a) == rows[1:]


async def test_a_titled_telegram_lane_is_listed_even_before_a_turn(engine):
    """The backfill gives every historical Telegram lane a threads row, but their
    turns predate turn_events: to the sidebar they are empty shells. Such a lane
    earns its row with a first event — or a title, which only a person gives."""
    await ensure_users(engine, 1, 2)
    threads = ThreadRepository(engine)
    await threads.upsert("tg:private:1", "telegram", 1, NOW)          # backfilled, silent
    await threads.upsert("tg:private:2", "telegram", 2, NOW)
    await threads.set_title("tg:private:2", "Plans", manual=True)
    assert {t["session_key"] for t in await threads.list()} == {"tg:private:2"}


async def test_search_is_safe_on_operator_soup(engine):
    """plainto_tsquery parses no operators: whatever punctuation the model typed
    is dropped and the words around it stay words, so nothing here can raise."""
    messages = MessageRepository(engine)
    await messages.record("tg:private:1", "user", "ingress notes", NOW)
    hits = await messages.search("ingress & | ! (notes")
    assert [h["session_key"] for h in hits] == ["tg:private:1"]
    assert await messages.search("'unbalanced") == []
    assert await messages.search("") == []


async def test_last_message_ts_is_the_max(engine):
    messages = MessageRepository(engine)
    assert await messages.last_ts() is None
    await messages.record("k", "user", "a", "2026-07-12T10:00:00+00:00")
    await messages.record("k", "user", "b", "2026-07-12T12:00:00+00:00")
    assert await messages.last_ts() == "2026-07-12T12:00:00+00:00"


async def test_reset_all_leaves_the_rest_of_the_state_alone(engine):
    """A prefix change is not a wipe: messages and meta survive it."""
    sessions, messages, meta = (SessionRepository(engine), MessageRepository(engine),
                                MetaStore(engine))
    await sessions.upsert("tg:private:1", "sdk-a", "APPEND", NOW)
    await messages.record("tg:private:1", "user", "kubernetes ingress", NOW)
    await meta.set("owner_user_id", "1")
    await sessions.reset_all()
    assert await sessions.get("tg:private:1") is None
    assert await messages.last("tg:private:1") is not None
    assert await meta.get("owner_user_id") == "1"

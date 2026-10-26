"""The one-shot migration off SQLite. The fixture builds a database with the OLD
schema (fts5 and all), so this test fails the day the importer stops understanding
the file people actually have on disk."""
import sqlite3

from sqlalchemy import text

from claude_hermes.core.meta import MetaStore
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
from claude_hermes.modules.sessions.infrastructure.repositories import (
    MessageRepository,
    SessionRepository,
    ThreadRepository,
)
from claude_hermes.modules.turns.infrastructure.repositories import TurnEventRepository
from scripts.import_sqlite import import_sqlite

OLD = """
CREATE TABLE sessions (session_key TEXT PRIMARY KEY, sdk_session_id TEXT, system_append TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE VIRTUAL TABLE messages_fts USING fts5(session_key, role, content, ts UNINDEXED);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL,
  value TEXT NOT NULL, prompt TEXT NOT NULL, deliver_chat_id INTEGER NOT NULL, deliver_thread_id INTEGER,
  origin_session_key TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active', next_run_at TEXT,
  last_run_at TEXT, last_status TEXT, timeout_seconds INTEGER, created_at TEXT NOT NULL);
CREATE TABLE job_runs (job_id INTEGER NOT NULL, ts TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE turn_costs (ts TEXT NOT NULL, session_key TEXT NOT NULL, cost_usd REAL NOT NULL);
CREATE TABLE extensions (name TEXT PRIMARY KEY, kind TEXT NOT NULL, catalog_version TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'enabled', installed_at TEXT NOT NULL, installed_by INTEGER,
  last_error TEXT, tool_count INTEGER, tool_names TEXT);
CREATE TABLE oauth_tokens (name TEXT PRIMARY KEY, issuer TEXT NOT NULL DEFAULT '', client_id TEXT NOT NULL DEFAULT '',
  client_secret TEXT NOT NULL DEFAULT '', access_token TEXT NOT NULL DEFAULT '', refresh_token TEXT NOT NULL DEFAULT '',
  expires_at TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE TABLE turn_events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT NOT NULL, turn_id TEXT NOT NULL,
  seq INTEGER NOT NULL, ts TEXT NOT NULL, type TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE threads (session_key TEXT PRIMARY KEY, title TEXT, title_manual INTEGER NOT NULL DEFAULT 0,
  channel TEXT NOT NULL, owner_user_id INTEGER, archived_at TEXT, last_event_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE files (id TEXT PRIMARY KEY, session_key TEXT NOT NULL, path TEXT NOT NULL, name TEXT NOT NULL,
  media_type TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT NOT NULL);
"""
NOW = "2026-07-12T10:00:00+00:00"


def _old_db(path):
    c = sqlite3.connect(path)
    c.executescript(OLD)
    c.execute("INSERT INTO sessions VALUES ('tg:private:1','sdk-1','A',?,?)", (NOW, NOW))
    c.execute("INSERT INTO messages_fts VALUES ('tg:private:1','user','kubernetes ingress',?)", (NOW,))
    c.execute("INSERT INTO meta VALUES ('owner_user_id','42')")
    c.execute("INSERT INTO jobs (name,kind,value,prompt,deliver_chat_id,origin_session_key,next_run_at,created_at)"
              " VALUES ('daily','cron','0 9 * * *','hi',1,'tg:private:1',?,?)", (NOW, NOW))
    c.execute("INSERT INTO job_runs VALUES (1,?, 'ok')", (NOW,))
    c.execute("INSERT INTO turn_events (session_key,turn_id,seq,ts,type,payload)"
              " VALUES ('tg:private:1','t1',1,?,'turn.start','{\"channel\": \"telegram\"}')", (NOW,))
    c.execute("INSERT INTO threads (session_key,channel,owner_user_id,last_event_at,created_at)"
              " VALUES ('tg:private:1','telegram',42,?,?)", (NOW, NOW))
    c.commit()
    c.close()


async def test_roundtrip(tmp_path, test_database, engine):
    _old_db(tmp_path / "state.db")
    counts = await import_sqlite(tmp_path / "state.db", test_database)
    assert counts["sessions"] == 1 and counts["messages"] == 1 and counts["turn_events"] == 1
    assert (await SessionRepository(engine).get("tg:private:1"))["sdk_session_id"] == "sdk-1"
    assert await MetaStore(engine).get("owner_user_id") == "42"
    assert (await MessageRepository(engine).search("ingress"))[0]["session_key"] == "tg:private:1"
    events = TurnEventRepository(engine)
    ev = await events.after("tg:private:1")
    assert ev[0]["payload"] == {"channel": "telegram"}
    # has_turns is denormalized (migration 0002) and the SQLite file predates it, so
    # the importer recomputes it from the events that came across — otherwise every
    # imported lane reads as an empty shell and the sidebar comes up blank.
    assert (await ThreadRepository(engine).get("tg:private:1"))["has_turns"] is True
    # sequences continue after the imported ids: a new event must not collide
    new_id = await events.append("tg:private:1", "t2", 1, NOW, "turn.start", {})
    assert new_id > ev[-1]["id"]
    # running it again is a no-op, not a duplicate
    again = await import_sqlite(tmp_path / "state.db", test_database)
    assert again["sessions"] == 0


async def test_telegram_lanes_without_a_thread_row_are_backfilled(tmp_path, test_database,
                                                                  engine):
    """A state.db whose `threads` table predates the backfill has Telegram lanes with
    no thread row; without this the sidebar could never list them again."""
    _old_db(tmp_path / "state.db")
    c = sqlite3.connect(tmp_path / "state.db")
    c.execute("INSERT INTO sessions VALUES ('tg:private:7','sdk-7','A',?,?)", (NOW, NOW))
    c.execute("INSERT INTO sessions VALUES ('tg:supergroup:-5:7:t9','sdk-9','A',?,?)", (NOW, NOW))
    c.commit()
    c.close()
    await import_sqlite(tmp_path / "state.db", test_database)
    threads = ThreadRepository(engine)
    assert (await threads.get("tg:private:7"))["owner_user_id"] == 7
    # backfilled but silent: no events came across for this lane
    assert (await threads.get("tg:private:7"))["has_turns"] is False
    # a group lane is the deploy owner's (D3, `lane_owner`), as 0005 attributes it
    assert (await threads.get("tg:supergroup:-5:7:t9"))["owner_user_id"] == 42
    # the row the file already carried wins over the backfill's guess
    assert (await threads.get("tg:private:1"))["owner_user_id"] == 42


async def test_nul_bytes_in_the_old_history_are_dropped_not_fatal(tmp_path, test_database,
                                                                   engine):
    """A real volume had them: a tool once printed a NUL into a message and a
    `tool.result` payload carried a `\u0000` escape. PostgreSQL refuses both
    (`\u0000 cannot be converted to text`), and one such row must not abort the
    whole import — the character is dropped, everything else comes across."""
    _old_db(tmp_path / "state.db")
    c = sqlite3.connect(tmp_path / "state.db")
    c.execute("INSERT INTO messages_fts VALUES ('tg:private:1','assistant',?,?)",
              ("nul\x00 here", NOW))
    c.execute("INSERT INTO turn_events (session_key,turn_id,seq,ts,type,payload)"
              " VALUES ('tg:private:1','t1',2,?,'tool.result',?)",
              (NOW, '{"output": "a\\u0000b"}'))
    c.commit()
    c.close()
    counts = await import_sqlite(tmp_path / "state.db", test_database)
    assert counts["messages"] == 2 and counts["turn_events"] == 2
    last = await MessageRepository(engine).last("tg:private:1", role="assistant")
    assert last["content"] == "nul here"
    ev = await TurnEventRepository(engine).after("tg:private:1")
    assert ev[1]["payload"] == {"output": "ab"}


async def test_every_copied_row_gets_an_owner(tmp_path, test_database, engine):
    """`upgrade head` runs 0005 on an EMPTY database, so its backfill touches
    nothing, and the SQLite file has no `owner_id` to copy. Every read scoped to the
    asking user then leaves the imported rows out: on the production deployment the Routines page
    showed none of 17 live routines. The importer attributes them by 0005's rule."""
    _old_db(tmp_path / "state.db")
    c = sqlite3.connect(tmp_path / "state.db")
    c.execute("INSERT INTO sessions VALUES ('web:42:abc','sdk-w','A',?,?)", (NOW, NOW))
    c.execute("INSERT INTO messages_fts VALUES ('web:42:abc','user','terraform drift',?)",
              (NOW,))
    c.execute("INSERT INTO turn_costs VALUES (?, 'tg:private:1', 0.5)", (NOW,))
    c.execute("INSERT INTO files VALUES ('f1','web:42:abc','/data/agent_home/inbox/x/a.png',"
              "'a.png','image/png',3,?)", (NOW,))
    c.execute("INSERT INTO threads (session_key,channel,last_event_at,created_at)"
              " VALUES ('tg:private:42:t7','telegram',?,?)", (NOW, NOW))
    c.commit()
    c.close()
    await import_sqlite(tmp_path / "state.db", test_database)

    # the symptom: the owner's routines are listed to the owner
    assert [j["name"] for j in await JobRepository(engine).list_jobs(owner_id=42)] == ["daily"]
    assert (await MessageRepository(engine).search("terraform", owner_id=42))
    # a keyed row goes to the user its key names, not to the deploy owner
    assert (await MessageRepository(engine).search("ingress", owner_id=1))
    assert (await ThreadRepository(engine).get("tg:private:42:t7"))["owner_user_id"] == 42
    async with engine.connect() as conn:
        for table in ("sessions", "messages", "files", "turn_costs", "jobs"):
            nulls = await conn.scalar(text(f"SELECT count(*) FROM {table} WHERE owner_id IS NULL"))
            assert nulls == 0, table
        assert await conn.scalar(text("SELECT owner_id FROM files WHERE id = 'f1'")) == 42

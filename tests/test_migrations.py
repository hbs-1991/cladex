"""The migrations and the ORM must describe the same schema, or the next
`alembic revision --autogenerate` ships a surprise."""
import asyncio

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from claude_hermes.core.db.registry import metadata


async def test_upgrade_head_matches_orm_metadata(test_database):
    engine = create_async_engine(test_database)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn), metadata))
    finally:
        await engine.dispose()
    assert diff == []


# The ORM-vs-migration check above catches a *drift*, not a *miss*: a table nobody
# added to either side is invisible to it. These name what 0003 has to have put
# there, so deleting the migration fails here rather than in Wave 3b.
EXPECTED = {
    "extension_secrets": {"owner_id", "name", "ciphertext", "updated_at"},
    "catalog_drafts": {"kind", "name", "raw", "status", "reason", "created_at"},
    "approval_snapshots": {"kind", "name", "raw", "created_at"},
}


async def test_0003_tables_and_columns(test_database):
    engine = create_async_engine(test_database)
    try:
        async with engine.connect() as conn:
            inspected = await conn.run_sync(
                lambda sync_conn: {
                    table: {c["name"] for c in inspect(sync_conn).get_columns(table)}
                    for table in (*EXPECTED, "jobs")
                })
            pks = await conn.run_sync(
                lambda sync_conn: {
                    table: inspect(sync_conn).get_pk_constraint(table)["constrained_columns"]
                    for table in EXPECTED
                })
    finally:
        await engine.dispose()

    for table, columns in EXPECTED.items():
        assert columns <= inspected[table], table
    # The lease columns are what make a second scheduler process harmless.
    assert {"claimed_at", "lease_until"} <= inspected["jobs"]
    # (kind, name) composite keys: one draft per manifest name per kind.
    assert pks["catalog_drafts"] == ["kind", "name"]
    assert pks["approval_snapshots"] == ["kind", "name"]
    # 0006: a secret is one owner's, so the key carries the owner. `name` here is
    # an env-var name two people share until the key says otherwise.
    assert pks["extension_secrets"] == ["owner_id", "name"]


async def test_secret_and_snapshot_payloads_are_bytes(test_database):
    """BYTEA, not TEXT. A Fernet token is bytes; a text column would put an
    encoding assumption between a stored credential and reading it back."""
    engine = create_async_engine(test_database)
    try:
        async with engine.connect() as conn:
            types = await conn.run_sync(
                lambda sync_conn: {
                    (t, c["name"]): type(c["type"]).__name__
                    for t, col in (("extension_secrets", "ciphertext"),
                                   ("approval_snapshots", "raw"))
                    for c in inspect(sync_conn).get_columns(t) if c["name"] == col
                })
    finally:
        await engine.dispose()
    assert types[("extension_secrets", "ciphertext")] == "BYTEA"
    assert types[("approval_snapshots", "raw")] == "BYTEA"


# `turns` is what makes "one active turn per lane" and "a dead worker's lane comes
# back" true across processes, and all of that lives in the INDEXES rather than in
# the columns — which the ORM-vs-migration comparison above does not look at
# closely enough to notice a missing WHERE clause.
TURNS_COLUMNS = {
    "id", "session_key", "channel", "user_id", "text", "attachment_ids", "status",
    "created_at", "started_at", "finished_at", "lease_until", "worker_id",
    "cancel_requested_at", "reply", "error",
}


async def test_0004_turns_table_and_the_indexes_that_are_the_lock(test_database):
    from sqlalchemy import text as sql

    engine = create_async_engine(test_database)
    try:
        async with engine.connect() as conn:
            columns = await conn.run_sync(
                lambda c: {x["name"] for x in inspect(c).get_columns("turns")})
            sessions = await conn.run_sync(
                lambda c: {x["name"] for x in inspect(c).get_columns("sessions")})
            defs = dict((await conn.execute(sql(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='turns'"
            ))).all())
    finally:
        await engine.dispose()

    assert TURNS_COLUMNS <= columns
    # The wiki index version a lane has been shown moves off the process (NFR-1).
    assert "index_mtime" in sessions

    # UNIQUE and PARTIAL. Without UNIQUE two instances can both start a turn on
    # one lane; without the WHERE a lane could hold exactly one turn ever.
    lock = defs["turns_one_active_per_lane"]
    assert "CREATE UNIQUE INDEX" in lock and "(session_key)" in lock
    assert "WHERE (status = ANY" in lock and "'queued'" in lock and "'running'" in lock
    # What the reaper walks, and what pruning walks — both partial so neither scans
    # the far larger set of finished rows.
    assert "(lease_until)" in defs["turns_active_lease"]
    assert "WHERE (status = ANY" in defs["turns_active_lease"]
    assert "(finished_at)" in defs["turns_finished"]
    assert "WHERE (finished_at IS NOT NULL)" in defs["turns_finished"]


async def test_the_unique_partial_index_refuses_a_second_active_turn(test_database):
    """The index IS the lane lock — not a check in the repository. Asserted here
    against the real DDL so deleting the WHERE clause fails a migration test and
    not, silently, a production lane."""
    from sqlalchemy import text as sql

    engine = create_async_engine(test_database)
    insert = sql(
        "INSERT INTO turns (id, session_key, channel, user_id, text, status,"
        " created_at, lease_until)"
        " VALUES (:id, 'web:1:x', 'web', 1, 'hi', :status, '2026-07-12T10:00:00',"
        " '2026-07-12T10:05:00')")
    try:
        async with engine.begin() as conn:
            await conn.execute(insert, {"id": "a", "status": "queued"})
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(insert, {"id": "b", "status": "running"})
        # Finished turns are outside the index: a lane may hold any number.
        async with engine.begin() as conn:
            await conn.execute(sql("UPDATE turns SET status='ok' WHERE id='a'"))
            await conn.execute(insert, {"id": "c", "status": "queued"})
    finally:
        await engine.dispose()


# 0005 attributes every user-owned row. The one thing that can go wrong silently is
# the BACKFILL: "NULL -> the deploy owner" would hand an allowlisted user's private
# lanes, messages and files to the owner, and — because the workspace resolver
# applies the same rule to the key — would resolve that user's lanes into the
# owner's tree forever. So this seeds a database through 0004 with one lane per
# grammar and asserts what each row derived.
_SEED_0004 = (
    "INSERT INTO meta (key, value) VALUES ('owner_user_id', '11')",
    "INSERT INTO sessions (session_key, sdk_session_id, created_at, updated_at)"
    " VALUES ('tg:private:22', 'sdk-b', '2026-09-17T10:00:00', '2026-09-17T10:00:00')",
    "INSERT INTO sessions (session_key, sdk_session_id, created_at, updated_at)"
    " VALUES ('tg:private:11', 'sdk-a', '2026-09-17T10:00:00', '2026-09-17T10:00:00')",
    "INSERT INTO messages (session_key, role, content, ts)"
    " VALUES ('tg:private:22', 'user', 'hello', '2026-09-17T10:00:00')",
    "INSERT INTO files (id, session_key, path, name, media_type, size, created_at)"
    " VALUES ('f1', 'tg:private:22', '/p', 'p.png', 'image/png', 3,"
    " '2026-09-17T10:00:00')",
    "INSERT INTO turn_costs (ts, session_key, cost_usd)"
    " VALUES ('2026-09-17T10:00:00', 'web:22:abc', 0.5)",
    "INSERT INTO threads (session_key, channel, last_event_at, created_at)"
    " VALUES ('web:33:deadbeef', 'web', '2026-09-17T10:00:00', '2026-09-17T10:00:00')",
    "INSERT INTO threads (session_key, channel, last_event_at, created_at)"
    " VALUES ('tg:supergroup:-100:44', 'telegram', '2026-09-17T10:00:00',"
    " '2026-09-17T10:00:00')",
    "INSERT INTO jobs (name, kind, value, prompt, deliver_chat_id,"
    " origin_session_key, created_at) VALUES ('j', 'cron', '* * * * *', 'go', 5,"
    " 'cron:1', '2026-09-17T10:00:00')",
    "INSERT INTO catalog_drafts (kind, name, raw, created_at)"
    " VALUES ('mcp', 'd', 'yaml:', '2026-09-17T10:00:00')",
)


async def test_0005_derives_each_row_owner_from_its_session_key(test_database):
    """The backfill is `lane_owner` in SQL, not a blanket assignment."""
    from alembic import command
    from sqlalchemy import text as sql

    from claude_hermes.core.db.migrate import alembic_config

    cfg = alembic_config(test_database)
    await asyncio.to_thread(command.downgrade, cfg, "0004")
    try:
        engine = create_async_engine(test_database)
        try:
            async with engine.begin() as conn:
                for stmt in _SEED_0004:
                    await conn.execute(sql(stmt))
        finally:
            await engine.dispose()
        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = create_async_engine(test_database)
        try:
            async with engine.connect() as conn:
                users = dict((await conn.execute(sql(
                    "SELECT id, role || '|' || workspace_dir FROM users"))).all())
                sessions = dict((await conn.execute(sql(
                    "SELECT session_key, owner_id FROM sessions"))).all())
                sdk = dict((await conn.execute(sql(
                    "SELECT session_key, sdk_session_id FROM sessions"))).all())
                messages = [r[0] for r in (await conn.execute(sql(
                    "SELECT owner_id FROM messages"))).all()]
                files = [r[0] for r in (await conn.execute(sql(
                    "SELECT owner_id FROM files"))).all()]
                costs = [r[0] for r in (await conn.execute(sql(
                    "SELECT owner_id FROM turn_costs"))).all()]
                threads = dict((await conn.execute(sql(
                    "SELECT session_key, owner_user_id FROM threads"))).all())
                jobs = [r[0] for r in (await conn.execute(sql(
                    "SELECT owner_id FROM jobs"))).all()]
                drafts = [r[0] for r in (await conn.execute(sql(
                    "SELECT owner_id FROM catalog_drafts"))).all()]
        finally:
            await engine.dispose()
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")

    # The meta owner becomes the one `owner` row, and their workspace is CH_HOME
    # itself — that is what keeps /data/agent_home where it is on a live volume.
    assert users[11] == "owner|"
    # Every non-owner id the keys yielded got a row, so the foreign keys hold.
    assert users[22] == "user|users/22" and users[33] == "user|users/33"
    # The group lane's sender (44) is not a lane owner and gets no row.
    assert 44 not in users

    assert sessions["tg:private:22"] == 22 and sessions["tg:private:11"] == 11
    assert messages == [22] and files == [22] and costs == [22]
    assert threads["web:33:deadbeef"] == 33
    # A group lane belongs to the deploy owner, so the owner never loses it from
    # the sidebar and the turn runs in the owner's workspace.
    assert threads["tg:supergroup:-100:44"] == 11
    # Owner-only routes today; the owner is the honest answer, not a NULL.
    assert jobs == [11] and drafts == [11]
    # B's lane moves to B's own root, so the CLI transcript is unresumable.
    assert sdk["tg:private:22"] is None
    assert sdk["tg:private:11"] == "sdk-a"


async def test_0004_downgrades_and_upgrades_again(test_database):
    """A migration nobody can undo is a deploy nobody can roll back.

    This runs against the suite's own database and puts it back in a `finally`:
    every later test truncates `metadata.sorted_tables`, which needs the schema
    that head describes.
    """
    from alembic import command

    from claude_hermes.core.db.migrate import alembic_config

    cfg = alembic_config(test_database)
    try:
        await asyncio.to_thread(command.downgrade, cfg, "0003")
        engine = create_async_engine(test_database)
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
                sessions = await conn.run_sync(
                    lambda c: {x["name"] for x in inspect(c).get_columns("sessions")})
        finally:
            await engine.dispose()
        assert "turns" not in tables and "index_mtime" not in sessions
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")

    engine = create_async_engine(test_database)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda c: compare_metadata(MigrationContext.configure(c), metadata))
    finally:
        await engine.dispose()
    assert diff == []


# 0007 adds the wiki INDEX — a derived cache over each user's markdown tree. Its
# shape is what can go wrong silently: without the GIN index `memory_search` is a
# sequential scan of every page of every user, and a primary key of `path` alone
# would let one user's `shared/home` overwrite the owner's.
async def test_0007_wiki_pages_table(test_database):
    engine = create_async_engine(test_database)
    try:
        async with engine.connect() as conn:
            columns = await conn.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns("wiki_pages")})
            pk = await conn.run_sync(
                lambda c: inspect(c).get_pk_constraint("wiki_pages")["constrained_columns"])
            indexes = await conn.run_sync(
                lambda c: {i["name"] for i in inspect(c).get_indexes("wiki_pages")})
    finally:
        await engine.dispose()
    assert {"owner_id", "path", "ns", "title", "summary", "tags", "links", "content",
            "shared", "indexed_at", "content_tsv"} <= columns
    assert pk == ["owner_id", "path"]
    assert {"ix_wiki_pages_content_tsv", "ix_wiki_pages_links",
            "ix_wiki_pages_shared"} <= indexes


# 0008 puts the (model, effort) pair in four places, each meaning something
# different (the migration's docstring). All nullable with no backfill, because
# NULL is "the deploy default" — what every existing lane already runs on.
async def test_0008_model_choice_columns_and_round_trip(test_database):
    from alembic import command

    from claude_hermes.core.db.migrate import alembic_config

    expected = {"threads": {"model", "effort"}, "sessions": {"model", "effort"},
                "users": {"default_model", "default_effort"}, "jobs": {"model", "effort"}}

    async def columns() -> dict[str, dict[str, bool]]:
        engine = create_async_engine(test_database)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(lambda c: {
                    t: {col["name"]: col["nullable"] for col in inspect(c).get_columns(t)}
                    for t in expected})
        finally:
            await engine.dispose()

    have = await columns()
    for table, names in expected.items():
        assert names <= set(have[table]), table
        assert all(have[table][n] for n in names), table

    cfg = alembic_config(test_database)
    try:
        await asyncio.to_thread(command.downgrade, cfg, "0007")
        gone = await columns()
        assert not any(names & set(gone[t]) for t, names in expected.items())
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")
    back = await columns()
    assert all(names <= set(back[t]) for t, names in expected.items())


# 0009 makes the shell and the learning loop per-user grants and stamps each session
# with whether it was started with a shell. All three nullable with no default and
# no backfill: NULL on `users` is "inherit the config default" — exactly what every
# existing user runs on today — and NULL on `sessions` is "started under the
# pre-0009 rule", which the turn compares with that rule instead of guessing.
async def test_0009_user_grants_columns_and_round_trip(test_database):
    from alembic import command
    from sqlalchemy import text as sql

    from claude_hermes.core.db.migrate import alembic_config

    expected = {"users": {"shell_enabled", "learning_enabled"},
                "sessions": {"shell_enabled"}}

    async def columns() -> dict[str, dict[str, dict]]:
        engine = create_async_engine(test_database)
        try:
            async with engine.connect() as conn:
                return await conn.run_sync(lambda c: {
                    t: {col["name"]: col for col in inspect(c).get_columns(t)}
                    for t in expected})
        finally:
            await engine.dispose()

    have = await columns()
    for table, names in expected.items():
        assert names <= set(have[table]), table
        for name in names:
            col = have[table][name]
            assert col["nullable"] and col["default"] is None, (table, name)
            assert col["type"].python_type is bool, (table, name)

    cfg = alembic_config(test_database)
    await asyncio.to_thread(command.downgrade, cfg, "0008")
    try:
        before = await columns()
        # Exactly the three columns go, and nothing next to them.
        for table, names in expected.items():
            assert set(have[table]) - set(before[table]) == names, table

        # Rows written before 0009 — a user and a session, as a live volume has them.
        engine = create_async_engine(test_database)
        try:
            async with engine.begin() as conn:
                await conn.execute(sql(
                    "INSERT INTO users (id, role, workspace_dir, created_at,"
                    " last_seen_at) VALUES (7, 'user', 'users/7',"
                    " '2026-09-24T10:00:00', '2026-09-24T10:00:00')"))
                await conn.execute(sql(
                    "INSERT INTO sessions (session_key, sdk_session_id, system_append,"
                    " created_at, updated_at, owner_id) VALUES ('tg:private:7',"
                    " 'sdk-7', '', '2026-09-24T10:00:00', '2026-09-24T10:00:00', 7)"))
        finally:
            await engine.dispose()
        await asyncio.to_thread(command.upgrade, cfg, "head")

        engine = create_async_engine(test_database)
        try:
            async with engine.connect() as conn:
                user = (await conn.execute(sql(
                    "SELECT shell_enabled, learning_enabled FROM users"
                    " WHERE id = 7"))).one()
                session = (await conn.execute(sql(
                    "SELECT shell_enabled FROM sessions"
                    " WHERE session_key = 'tg:private:7'"))).one()
        finally:
            await engine.dispose()
    finally:
        await asyncio.to_thread(command.upgrade, cfg, "head")

    # No backfill: an existing user inherits, an existing session is unstamped.
    assert tuple(user) == (None, None)
    assert tuple(session) == (None,)
    back = await columns()
    assert all(names <= set(back[t]) for t, names in expected.items())

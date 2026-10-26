#!/usr/bin/env python
"""Copy an existing `state.db` (the old SQLite file) into PostgreSQL, once.

Run it on the host with the app stopped, after the container has been pointed at
its new database:

    python scripts/import_sqlite.py --sqlite data/state.db --url postgresql+asyncpg://...

It is safe to run twice. Every table with a natural key is inserted with
ON CONFLICT DO NOTHING, and the three append-only tables (`messages`, `job_runs`,
`turn_costs`) carry no key to deduplicate on, so they are skipped entirely once the
target already holds rows — re-running must not double the history.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlalchemy import Table, func, select, text  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection  # noqa: E402

from claude_hermes.core.db.engine import make_engine  # noqa: E402
from claude_hermes.core.db.migrate import upgrade_head_sync  # noqa: E402
from claude_hermes.core.db.registry import metadata  # noqa: E402

# Parents before children, so a half-finished import still reads sensibly.
TABLES = ("meta", "sessions", "messages", "threads", "jobs", "job_runs",
          "turn_events", "turn_costs", "extensions", "oauth_tokens", "files")

# The full-text virtual table was the message store; the real table replaces it.
SOURCE_NAME = {"messages": "messages_fts"}

# No natural key, so a second run cannot tell a copy from the original.
APPEND_ONLY = {"messages", "job_runs", "turn_costs"}

# Ids come across verbatim for these, so the sequence has to be moved past them.
KEEPS_IDS = ("jobs", "turn_events")

BATCH = 500


def _sqlite_columns(src: sqlite3.Connection, table: str) -> list[str]:
    """Column names of a SQLite table, or [] if the table is not there at all —
    an old file may predate a column, or a whole feature."""
    rows = src.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [r[1] for r in rows]


def _read(src: sqlite3.Connection, table: str, target: Table) -> list[dict]:
    source = SOURCE_NAME.get(table, table)
    cols = [c for c in _sqlite_columns(src, source) if c in target.c]
    if not cols:
        return []
    quoted = ", ".join(f'"{c}"' for c in cols)
    rows = [{k: _clean(v) for k, v in dict(r).items()}
            for r in src.execute(f'SELECT {quoted} FROM "{source}"')]
    if table == "turn_events":
        # payload was TEXT in SQLite and is JSONB here. Parsed, then cleaned again:
        # a `\u0000` ESCAPE in the JSON text is a NUL only after json.loads.
        for row in rows:
            row["payload"] = _clean(json.loads(row["payload"]))
    return rows


def _clean(value):
    """SQLite stored whatever bytes a tool printed; PostgreSQL `text` and `jsonb`
    refuse NUL (`\\u0000 cannot be converted to text`). One such character in a
    year of history would abort the whole import, so it is dropped here — the only
    edit the importer makes to a value. Recurses into a parsed JSON payload."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


async def _copy(conn: AsyncConnection, src: sqlite3.Connection, table: str) -> int:
    target = metadata.tables[table]
    if table in APPEND_ONLY:
        already = await conn.scalar(select(func.count()).select_from(target))
        if already:
            return 0
    rows = _read(src, table, target)
    inserted = 0
    for start in range(0, len(rows), BATCH):
        stmt = pg_insert(target).values(rows[start:start + BATCH]).on_conflict_do_nothing()
        inserted += (await conn.execute(stmt)).rowcount
    return inserted


# The old SQLite `_migrate` ran this on every open, so a `threads` table written
# before that backfill existed can be missing rows the sidebar needs. The importer
# copies only what the file holds, so the backfill has to happen here too: every
# Telegram lane gets a threads row once, private chats owned by their user
# (chat id == user id), groups and topics by nobody.
_BACKFILL_THREADS = text("""
    INSERT INTO threads (session_key, channel, owner_user_id, last_event_at, created_at)
    SELECT session_key, 'telegram',
           CASE WHEN session_key ~ '^tg:private:-?[0-9]+$'
                THEN substr(session_key, 12)::bigint END,
           updated_at, created_at
    FROM sessions WHERE session_key LIKE 'tg:%'
    ON CONFLICT (session_key) DO NOTHING""")

# `threads.has_turns` is denormalized (migration 0002) and the SQLite file predates
# it, so the flag has to be recomputed here from the events that came across — the
# same predicate the thread list used to run as a subquery. Without it every
# imported lane reads as an empty shell and the sidebar shows nothing.
_BACKFILL_HAS_TURNS = text("""
    UPDATE threads SET has_turns = true
    WHERE EXISTS (SELECT 1 FROM turn_events e
                  WHERE e.session_key = threads.session_key)""")

# Migration 0005's attribution, the one spelling of `lane_owner` in SQL. Imported,
# not copied: two copies of the rule are two places for it to drift apart.
_M0005 = importlib.import_module("claude_hermes.core.db.migrations.versions.0005_users")


async def _backfill_owners(conn: AsyncConnection, owner: int | None) -> None:
    """Attribute every copied row, as 0005 attributed the rows it found.

    `upgrade head` runs 0005 against an EMPTY database, so its backfill has nothing
    to touch, and the SQLite file has no `owner_id` columns to copy. Without this
    every imported row lands with `owner_id` NULL — and every read scoped to the
    asking user (the Routines page, message search, the cost report) silently
    leaves it out. On the production deployment that was all 17 routines, until 2026-09-24.

    Only NULLs are filled: a re-run, or a row a live deploy already attributed, is
    left alone. With no owner in the file there is nobody to give the unkeyed rows
    to, which is where 0005 stops too.
    """
    if owner is None:
        return
    params = {"owner": owner}
    for table in _M0005._KEYED_TABLES:
        await conn.execute(text(
            f"UPDATE {table} SET owner_id = ({_M0005._DERIVED_OWNER})"
            " WHERE owner_id IS NULL"), params)
    await conn.execute(text(
        f"UPDATE threads SET owner_user_id = ({_M0005._DERIVED_OWNER})"
        " WHERE owner_user_id IS NULL"), params)
    # Routines and drafts carry no lane key to derive from; 0005 gives them the owner.
    for table in ("jobs", "catalog_drafts"):
        await conn.execute(text(
            f"UPDATE {table} SET owner_id = :owner WHERE owner_id IS NULL"), params)


def _ids_from_source(src: sqlite3.Connection) -> tuple[int | None, set[int]]:
    """The deploy owner and every other user id this file implies.

    Wave 4a put a foreign key on `threads.owner_user_id` (and on every new
    `owner_id`), so the rows below cannot be inserted until their owners have a
    `users` row. Migration `0005` builds those rows from the data it finds — but an
    import runs `upgrade head` against an EMPTY database, where 0005 has nothing to
    look at, and then copies the owners in. So the same derivation happens here,
    from the SQLite file, before anything that references `users` is written.

    The rule is `lane_owner` (D3), the same one 0005 spells in SQL: a `web:<uid>:`
    lane belongs to `<uid>`, a `tg:private:<chat>` lane to `<chat>`, and everything
    else to the deploy owner. Whatever the old `threads.owner_user_id` already says
    is taken at face value on top of that — it was written by the same rule.
    """
    owner: int | None = None
    if _sqlite_columns(src, "meta"):
        row = src.execute(
            "SELECT value FROM meta WHERE key = 'owner_user_id'").fetchone()
        if row is not None:
            try:
                owner = int(str(row[0]).strip())
            except ValueError:
                owner = None

    others: set[int] = set()

    def _add(text_id) -> None:
        try:
            value = int(text_id)
        except (TypeError, ValueError):
            return
        if value and value != owner:
            others.add(value)

    if _sqlite_columns(src, "sessions"):
        for (key,) in src.execute("SELECT session_key FROM sessions"):
            parts = str(key).split(":")
            if parts[0] == "web" and len(parts) > 1:
                _add(parts[1])
            elif parts[:2] == ["tg", "private"] and len(parts) > 2:
                _add(parts[2])
    if "owner_user_id" in _sqlite_columns(src, "threads"):
        for (value,) in src.execute(
                "SELECT DISTINCT owner_user_id FROM threads"
                " WHERE owner_user_id IS NOT NULL"):
            _add(value)
    return owner, others


async def _ensure_users(conn: AsyncConnection, src: sqlite3.Connection) -> int:
    """Insert the `users` rows the copied data will reference. See above."""
    owner, others = _ids_from_source(src)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    if owner is not None:
        # The owner's workspace_dir is "" — their root IS CH_HOME, which is what
        # lets an imported volume keep /data/agent_home exactly where it is.
        rows.append({"id": owner, "role": "owner", "workspace_dir": "",
                     "display_name": "", "username": "",
                     "created_at": now, "last_seen_at": now})
    rows += [{"id": uid, "role": "user", "workspace_dir": f"users/{uid}",
              "display_name": "", "username": "",
              "created_at": now, "last_seen_at": now}
             for uid in sorted(others)]
    if not rows:
        return 0
    stmt = pg_insert(metadata.tables["users"]).values(rows).on_conflict_do_nothing()
    return (await conn.execute(stmt)).rowcount


async def import_sqlite(sqlite_path: Path, url: str) -> dict[str, int]:
    """Copy every table of `sqlite_path` into `url`. Returns rows inserted per table."""
    engine = make_engine(url)
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    try:
        # One transaction: a failure halfway leaves the new database untouched.
        async with engine.begin() as conn:
            # Before anything that references `users(id)` — which since 0005 is
            # `threads` and every `owner_id` column.
            users = await _ensure_users(conn, src)
            counts = {table: await _copy(conn, src, table) for table in TABLES}
            counts["users"] = users
            counts["threads"] += (await conn.execute(_BACKFILL_THREADS)).rowcount
            await conn.execute(_BACKFILL_HAS_TURNS)
            await _backfill_owners(conn, _ids_from_source(src)[0])
            for table in KEEPS_IDS:
                await conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'),"
                    f" COALESCE(max(id), 1)) FROM {table}"))
        return counts
    finally:
        src.close()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", required=True, type=Path, help="path to the old state.db")
    parser.add_argument("--url", default=os.environ.get("CH_DATABASE_URL"),
                        help="target database (default: $CH_DATABASE_URL)")
    args = parser.parse_args(argv)

    if not args.sqlite.is_file():
        print(f"No SQLite database at {args.sqlite}.")
        return 1
    if not args.url:
        print("No target database: pass --url or set CH_DATABASE_URL.")
        return 1

    upgrade_head_sync(args.url)
    counts = asyncio.run(import_sqlite(args.sqlite, args.url))
    for table, rows in counts.items():
        print(f"{table}: {rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

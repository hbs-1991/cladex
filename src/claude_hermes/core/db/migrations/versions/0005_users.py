"""users table, owner_id on every user-owned row

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-17

Wave 4a gives the deploy a `users` table. Until now "who is the owner" was one
`meta` row and "who else may talk" was a list in `config.yaml`; nobody else had a
record, and nothing a user produced said whose it was.

Three things in this migration are the whole design:

* **`users_one_owner`** — a UNIQUE partial index over `role = 'owner'`. The index
  is the claim arbiter: two first private chats racing produce one owner row and
  one `IntegrityError`, which `AccessControl.check` answers `"deny"`. Same shape
  as `turns_one_active_per_lane`, and for the same reason — a check-then-insert
  is a race.
* **The owner's `workspace_dir` is `''`.** Their root stays `CH_HOME` itself, so
  `/data/agent_home`, `/data/.claude-cli` and `/data/logs` do not move: a live
  volume needs no file migration and every SDK session keeps resuming. Everybody
  else gets `users/<id>`.
* **The backfill DERIVES the owner from the session key; it never blanket-assigns.**
  `UPDATE … SET owner_id = <the deploy owner> WHERE owner_id IS NULL` would hand
  an allowlisted user's private lanes, messages and files to the owner — and,
  because the workspace resolver applies the same rule, would resolve that user's
  lanes into the owner's tree forever, silently. So the rule is `lane_owner` (D3)
  spelled in SQL:

  ```
  web:<uid>:…              -> split_part(session_key, ':', 2)::bigint
  tg:private:<chat>[:t<n>] -> split_part(session_key, ':', 3)::bigint
  tg:group|supergroup:…    -> the deploy owner
  cron:…                   -> the deploy owner
  ```

  and every distinct non-owner id those keys yield gets a `users` row *before*
  the backfill, so the foreign keys hold.

`sessions.sdk_session_id` is set to NULL for every lane whose derived owner is not
the deploy owner: that lane's `cwd` and `CLAUDE_CONFIG_DIR` move to the user's own
root, so the CLI transcript is unresumable anyway, and nulling it saves one dead
launch per lane.

`turns.user_id` gets **no** foreign key. It is the *sender* of a turn, an audit
field, NOT NULL, and a group turn's sender may be somebody with no row at all;
constraining it would break every test that inserts a turn with an arbitrary id
and would buy nothing — the turn's *workspace* comes from `lane_owner`, not from
this column.

Every `owner_id` is nullable with `ON DELETE SET NULL`: an old row has to survive
and deleting a user must not delete their history. Code always writes a non-NULL
owner, and a NULL row is visible to nobody. On a fresh database (no
`meta.owner_user_id`) there is nothing to backfill and the data steps are no-ops;
a database with rows and no owner is not a state the app produces.

Since Step 5 the read of `meta.owner_user_id` below is the ONLY thing that ever
materialises the owner row from that key: the application writes and reads the
`users` table alone, so a deploy claimed after Step 5 has its owner as a row and
nothing in `meta` — which is why a downgrade past this revision on a claimed deploy
must never be followed by an upgrade (`docs/deploy.md`).
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# `lane_owner` in SQL, over a column named `session_key`. `split_part` rather than
# a LIKE: it is the same parse `core/session_keys.py` does, and the regexp guard
# means a key we have never seen before falls through to the deploy owner instead
# of raising a cast error mid-migration.
_DERIVED_OWNER = """
CASE
  WHEN split_part(session_key, ':', 1) = 'web'
       AND split_part(session_key, ':', 2) ~ '^-?[0-9]+$'
    THEN split_part(session_key, ':', 2)::bigint
  WHEN split_part(session_key, ':', 1) = 'tg'
       AND split_part(session_key, ':', 2) = 'private'
       AND split_part(session_key, ':', 3) ~ '^-?[0-9]+$'
    THEN split_part(session_key, ':', 3)::bigint
  ELSE :owner
END
"""

# Every table that carries a `session_key` and therefore knows its own lane.
_KEYED_TABLES = ("sessions", "messages", "files", "turn_costs")


def _now_iso() -> str:
    """The ISO timestamp every table in this schema stores, computed here rather
    than in SQL: `to_char(... 'HH24:MI:SS')` would put colons in the statement
    text, where SQLAlchemy looks for bind parameters."""
    return datetime.now(timezone.utc).isoformat()


def _owner_id(conn) -> int | None:
    """The deploy owner from `meta.owner_user_id`, or None on a fresh database."""
    raw = conn.execute(
        sa.text("SELECT value FROM meta WHERE key = 'owner_user_id'")).scalar()
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        # A meta value that is not a number is not an owner; treat the deploy as
        # unclaimed rather than failing the upgrade of a whole volume.
        return None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("workspace_dir", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), server_default="", nullable=False),
        sa.Column("username", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("disabled_at", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    # The claim arbiter. UNIQUE and PARTIAL: one owner ever, any number of users.
    op.create_index("users_one_owner", "users", ["role"], unique=True,
                    postgresql_where=sa.text("role = 'owner'"))

    conn = op.get_bind()
    owner = _owner_id(conn)
    now = _now_iso()

    # The owner first: every later insert and backfill refers to this row.
    if owner is not None:
        conn.execute(
            sa.text(
                "INSERT INTO users (id, role, workspace_dir, display_name, username,"
                " created_at, last_seen_at)"
                " VALUES (:id, 'owner', '', '', '', :now, :now)"
                " ON CONFLICT (id) DO NOTHING"),
            {"id": owner, "now": now})

    # --- the owner columns, the FKs and the indexes --------------------------
    for table in ("sessions", "messages", "files", "jobs", "turn_costs",
                  "catalog_drafts"):
        op.add_column(table, sa.Column("owner_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(op.f(f"fk_{table}_owner_id_users"), table, "users",
                              ["owner_id"], ["id"], ondelete="SET NULL")
    # `threads.owner_user_id` already exists (0001); 4a gives it the same FK.
    op.create_foreign_key(op.f("fk_threads_owner_user_id_users"), "threads", "users",
                          ["owner_user_id"], ["id"], ondelete="SET NULL")

    op.create_index("ix_sessions_owner", "sessions", ["owner_id"])
    op.create_index("ix_files_owner", "files", ["owner_id"])
    op.create_index("ix_jobs_owner", "jobs", ["owner_id"])
    op.create_index("ix_messages_owner_ts", "messages", ["owner_id", "ts"])

    if owner is None:
        return                       # a fresh database: nothing to backfill

    # --- the users the existing keys imply -----------------------------------
    # Before any backfill, or the foreign keys above reject the very rows we are
    # trying to attribute. `role='user'`, `workspace_dir='users/<id>'` — exactly
    # what `AccessControl.check` would have created on their first message.
    conn.execute(
        sa.text(
            "INSERT INTO users (id, role, workspace_dir, display_name, username,"
            " created_at, last_seen_at) SELECT DISTINCT derived, 'user',"
            " 'users/' || derived, '', '', :now, :now FROM ("
            + " UNION ".join(
                f"SELECT ({_DERIVED_OWNER}) AS derived FROM {table}"
                for table in (*_KEYED_TABLES, "threads"))
            + ") AS lanes WHERE derived IS NOT NULL AND derived <> :owner"
            " ON CONFLICT (id) DO NOTHING"),
        {"owner": owner, "now": now})

    # --- the backfill --------------------------------------------------------
    for table in _KEYED_TABLES:
        conn.execute(sa.text(f"UPDATE {table} SET owner_id = ({_DERIVED_OWNER})"),
                     {"owner": owner})
    conn.execute(
        sa.text(f"UPDATE threads SET owner_user_id = ({_DERIVED_OWNER})"
                " WHERE owner_user_id IS NULL"),
        {"owner": owner})
    # Routines and drafts reach the deploy through owner-only routes today, so
    # there is no key to derive from and the owner is the honest answer.
    for table in ("jobs", "catalog_drafts"):
        conn.execute(sa.text(f"UPDATE {table} SET owner_id = :owner"),
                     {"owner": owner})

    # A non-owner lane's cwd and CLAUDE_CONFIG_DIR move to that user's own root,
    # so the CLI transcript the id names is unresumable. Nulling it here turns a
    # guaranteed failed `resume` into a clean first turn.
    conn.execute(
        sa.text("UPDATE sessions SET sdk_session_id = NULL"
                " WHERE owner_id IS NOT NULL AND owner_id <> :owner"),
        {"owner": owner})


def downgrade() -> None:
    """Downgrade schema.

    The derived attribution is lost (there is nowhere to put it) but nothing else
    is: every column dropped here was added by this revision, and
    `sessions.sdk_session_id` values nulled above stay null — a lane simply starts
    a fresh CLI session, which is what it would have done anyway.
    """
    op.drop_index("ix_messages_owner_ts", table_name="messages")
    op.drop_index("ix_jobs_owner", table_name="jobs")
    op.drop_index("ix_files_owner", table_name="files")
    op.drop_index("ix_sessions_owner", table_name="sessions")
    op.drop_constraint(op.f("fk_threads_owner_user_id_users"), "threads",
                       type_="foreignkey")
    for table in ("sessions", "messages", "files", "jobs", "turn_costs",
                  "catalog_drafts"):
        op.drop_constraint(op.f(f"fk_{table}_owner_id_users"), table,
                           type_="foreignkey")
        op.drop_column(table, "owner_id")
    op.drop_index("users_one_owner", table_name="users")
    op.drop_table("users")

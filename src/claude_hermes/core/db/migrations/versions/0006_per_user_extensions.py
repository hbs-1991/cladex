"""per-user extensions: owner_id on extensions, extension_secrets, oauth_tokens

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22

Until now an install was deploy-wide: one row per catalog name, one set of
secrets, one OAuth token per connector — so a second user's agent ran with the
owner's credentials and could not have its own.

Three things here are the design:

* **`PRIMARY KEY (owner_id, name)`**, not an overlay table beside a shared
  install row. `state`, `last_error`, `tool_count` and `tool_names` are already
  one user's verdict on one server — a probe with their token can fail while
  another's succeeds — and a single row makes those two verdicts fight.
* **`owner_id` is NOT NULL with ON DELETE CASCADE**, unlike every `owner_id`
  migration 0005 added. There a row could outlive its owner harmlessly. Here an
  ownerless row is a decryptable credential belonging to nobody, and therefore
  to everybody.
* **The backfill assigns the deploy owner, and refuses to guess.** Only an owner
  could install, so rows imply an owner row; if that is somehow untrue the
  upgrade stops rather than handing the credentials to an invented id.

`downgrade` keeps the deploy owner's rows and DELETES everybody else's — a
primary key on `name` alone cannot hold two people. Their OAuth tokens do not
come back: `CH_SECRETS_KEY` protects a dump, not a deleted row. Dump the three
tables first (`docs/superpowers/specs/2026-09-22-per-user-connectors-design.md`).

The constraint names follow `core/db/base.NAMING`, the same convention 0005 used
(`pk_<table>`, `fk_<table>_owner_id_users`): the ORM names its constraints that
way, and `test_upgrade_head_matches_orm_metadata` compares the two.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("extensions", "extension_secrets", "oauth_tokens")


def _deploy_owner(conn) -> int | None:
    """The owner as a `users` row. Since Step 5 that table is the only record."""
    return conn.execute(
        sa.text("SELECT id FROM users WHERE role = 'owner'")).scalar()


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    owner = _deploy_owner(conn)
    for table in _TABLES:
        op.add_column(table, sa.Column("owner_id", sa.BigInteger(), nullable=True))
        rows = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() or 0
        if rows and owner is None:
            raise RuntimeError(
                f"{table} holds {rows} row(s) but this deploy has no owner in "
                "`users`. Only an owner can install an extension, so this state "
                "should be unreachable. Assigning these credentials to nobody, or "
                "guessing an id, would hand them to everybody — restore the owner "
                "row and run the upgrade again.")
        if owner is not None:
            conn.execute(sa.text(f"UPDATE {table} SET owner_id = :owner"),
                         {"owner": owner})
        op.alter_column(table, "owner_id", nullable=False)
        op.drop_constraint(op.f(f"pk_{table}"), table, type_="primary")
        op.create_primary_key(op.f(f"pk_{table}"), table, ["owner_id", "name"])
        op.create_foreign_key(op.f(f"fk_{table}_owner_id_users"), table, "users",
                              ["owner_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    """Downgrade schema. Destructive for every non-owner: see the module docstring."""
    conn = op.get_bind()
    owner = _deploy_owner(conn)
    for table in _TABLES:
        if owner is None:
            # No owner means no row here is anybody's; a name key cannot hold them
            # either way.
            conn.execute(sa.text(f"DELETE FROM {table}"))
        else:
            conn.execute(sa.text(f"DELETE FROM {table} WHERE owner_id <> :owner"),
                         {"owner": owner})
        op.drop_constraint(op.f(f"fk_{table}_owner_id_users"), table,
                           type_="foreignkey")
        op.drop_constraint(op.f(f"pk_{table}"), table, type_="primary")
        op.create_primary_key(op.f(f"pk_{table}"), table, ["name"])
        op.drop_column(table, "owner_id")

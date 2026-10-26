"""turns table with a lane lock and a lease; sessions.index_mtime

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-17

Wave 3b takes the turn off the request path. Until now "a turn is running in this
lane" was an `asyncio.Lock` and a dict inside one process, which is only a truth
while there is exactly one process: two API instances would each happily start a
turn on the same lane, and a restart mid-turn lost the turn entirely.

`turns` makes the turn a row. Two things in it are the whole design:

* `turns_one_active_per_lane` — a UNIQUE partial index on `session_key` over
  `('queued','running')`. **The index is the lock.** The second insert on a busy
  lane raises, and the repository translates that into `LaneBusy`; there is no
  check-then-insert anywhere, because a check-then-insert is a race.
* `lease_until` plus `worker_id` — the dead-man's switch. A worker heartbeats its
  lease while it runs; a lease that lapses is what lets the reaper mark the turn
  `lost`, publish a synthetic `turn.end` and free the lane. `turns_active_lease`
  is the index that reap walks, partial so it never touches finished rows, and
  `turns_finished` is the one pruning walks.

`sessions.index_mtime` moves a process-local dict onto the row. `index_seen` is
what decides whether a turn's user message carries the whole wiki index (~2k
uncached tokens); with N workers a lane lands on a process that has never seen it
on a large fraction of turns, which is a direct hit on the ≥60%-cached-input
target. NULL reads as "never injected", which is what an empty dict meant.

Nothing is backfilled: before this migration there were no turn rows to carry
over, and a lane whose index was injected by the old in-memory bookkeeping simply
gets it once more.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ACTIVE = "status IN ('queued', 'running')"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "turns",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("attachment_ids", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("started_at", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("lease_until", sa.Text(), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.Text(), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turns")),
    )
    # The lane lock. UNIQUE and partial: a lane may hold any number of finished
    # turns and at most one active one.
    op.create_index("turns_one_active_per_lane", "turns", ["session_key"],
                    unique=True, postgresql_where=sa.text(_ACTIVE))
    op.create_index("turns_active_lease", "turns", ["lease_until"],
                    postgresql_where=sa.text(_ACTIVE))
    op.create_index("turns_finished", "turns", ["finished_at"],
                    postgresql_where=sa.text("finished_at IS NOT NULL"))

    op.add_column("sessions",
                  sa.Column("index_mtime", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("sessions", "index_mtime")
    op.drop_index("turns_finished", table_name="turns")
    op.drop_index("turns_active_lease", table_name="turns")
    op.drop_index("turns_one_active_per_lane", table_name="turns")
    op.drop_table("turns")

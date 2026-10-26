"""threads.has_turns

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-15

The thread list asked "has anybody taken a turn in this lane?" with an EXISTS over
`turn_events` — the single place where the sessions module's query reached into the
turns module's table. The answer is denormalized onto `threads` so each module owns
its own tables again, and so the sidebar's list stops paying for a correlated
subquery on every page.

Backfilled from `turn_events`, which is the same predicate the query used, so the
first read after the upgrade returns exactly what the last read before it did.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "threads",
        sa.Column("has_turns", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.execute(
        "UPDATE threads SET has_turns = true"
        " WHERE EXISTS (SELECT 1 FROM turn_events e"
        " WHERE e.session_key = threads.session_key)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("threads", "has_turns")

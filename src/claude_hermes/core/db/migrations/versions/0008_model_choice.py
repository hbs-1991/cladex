"""model choice: the (model, effort) pair on lanes, sessions, users and routines

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23

Four places hold a pair, and each means something different:

* `threads.model/effort` — what the user CHOSE for the lane. On the thread row,
  not the session row, because a choice must outlive `/new` and every
  `reset_all`/`reset_owned` a Customize click performs.
* `sessions.model/effort` — what actually RAN the lane's current session. A
  changed personal default waits for the next session (P1: a live session's cached
  prefix is model-scoped), so the resolver needs to know what the live one is on.
* `users.default_model/default_effort` — a user's default for new sessions.
* `jobs.model/effort` — a routine's own pair; NULL falls back to `cron_model`.

All nullable TEXT, no backfill: NULL everywhere means "the deploy default", which is
exactly what every existing lane, user and routine runs on today. The values are
catalog ids (`claude-opus-5-5`), never the CLI's spelling (`claude-opus-5[1m]`);
`core.model_catalog` maps between them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("threads", "model"), ("threads", "effort"),
    ("sessions", "model"), ("sessions", "effort"),
    ("users", "default_model"), ("users", "default_effort"),
    ("jobs", "model"), ("jobs", "effort"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(table, sa.Column(column, sa.Text(), nullable=True))


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.drop_column(table, column)

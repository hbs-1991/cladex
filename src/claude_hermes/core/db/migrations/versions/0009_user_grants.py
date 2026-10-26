"""user grants: a shell and the learning loop, per person, plus the session's stamp

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-24

Two things that were deploy-wide `config.yaml` switches become per-user, and the
session learns which of them it was started under:

* `users.shell_enabled` — whether this person's lanes get `Bash`/`Agent`/`Task`.
* `users.learning_enabled` — whether the learning loop observes this person's
  conversations and sweeps their tree.
* `sessions.shell_enabled` — whether the lane's CURRENT session was started with
  a shell. The toolset is part of the cached prefix (P1), so a grant that moves a
  lane's shell has to start a fresh session; the turn compares this stamp with
  the lane's effective grant and resets only on a difference.

All nullable BOOLEAN with no server default, and no backfill. On `users` NULL
means "inherit": the effective value is `users_shell_enabled` /
`users_learning_enabled` from `config.yaml`, which is exactly what every existing
user runs on today. On `sessions` NULL means "not yet stamped": the session was
started under the pre-0009 rule (`is_owner_lane or users_shell_enabled`), and the
turn compares it with that rule rather than guessing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("users", "shell_enabled"), ("users", "learning_enabled"),
    ("sessions", "shell_enabled"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(table, sa.Column(column, sa.Boolean(), nullable=True))


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.drop_column(table, column)

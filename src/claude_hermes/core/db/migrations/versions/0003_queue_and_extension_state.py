"""job leases, extension secrets, catalog drafts, approval snapshots

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16

Wave 3a turns two in-process assumptions into rows.

`jobs.claimed_at` / `jobs.lease_until` make the scheduler safe to run in more than
one process: `claim_due` selects due jobs `FOR UPDATE SKIP LOCKED` and, in the
same transaction, moves `next_run_at` forward and stamps the lease. Two schedulers
ticking at the same second then hand out disjoint sets, and a duplicate scheduler
process is harmless instead of doubling every job. Both are NULL for every
existing row, which reads as "never claimed" — the pre-3a state exactly.

`extension_secrets`, `catalog_drafts` and `approval_snapshots` replace
`secrets.env`, `catalog-drafts/` and `pending-approvals/` on the data volume. The
volume is writable by the agent, which has Bash; a database the agent reaches only
through the application is not. Nothing is backfilled here — the one-shot import
of an existing data directory is `scripts/import_data_dir.py`, run deliberately,
because the secrets have to be encrypted on the way in with a key this migration
does not have.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("jobs", sa.Column("claimed_at", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("lease_until", sa.Text(), nullable=True))

    op.create_table(
        "extension_secrets",
        sa.Column("name", sa.Text(), nullable=False),
        # BYTEA: a Fernet token is bytes and stays bytes end to end.
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_extension_secrets")),
    )
    op.create_table(
        "catalog_drafts",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        # The YAML as submitted. A reviewer approves bytes, not a re-render.
        sa.Column("raw", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("kind", "name", name=op.f("pk_catalog_drafts")),
    )
    op.create_table(
        "approval_snapshots",
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("raw", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("kind", "name", name=op.f("pk_approval_snapshots")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("approval_snapshots")
    op.drop_table("catalog_drafts")
    op.drop_table("extension_secrets")
    op.drop_column("jobs", "lease_until")
    op.drop_column("jobs", "claimed_at")

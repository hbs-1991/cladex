"""content_items: the content plan

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-28

One row per publication on one channel (variants of one idea share a
`group_key`), owned by one user, with the lifecycle
`idea -> draft -> review -> scheduled -> queued -> publishing -> published`
(plus `failed`, `stuck`, `skipped`) that `modules/content/domain/status.py` owns.

What the columns are for, beyond the wire shape:

* `owner_id` — NOT NULL, `ON DELETE CASCADE`. Unlike `jobs.owner_id` there is no
  "keep the history of a deleted user" case: an ownerless item is readable and
  publishable by nobody.
* `approved_at` / `approved_by` and the `sha256` inside each `media` entry — the
  owner's approval, bound to the content it approved. Cleared whenever that
  content moves.
* `attempts` / `next_wake_at` — the tick's wake bookkeeping, advanced by one
  compare-and-set so two ticks never both count (or both send) a wake.
* `claimed_at` — when the publisher took the item; the stuck timeout counts from it.

Timestamps are ISO-8601 UTC `TEXT`, like `jobs`. Two indexes serve the two
readers: `(owner_id, publish_at)` for the calendar's range, `(status, publish_at)`
for the tick. Nothing to backfill: the table is new.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "content_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), server_default="", nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("media", postgresql.JSONB(astext_type=sa.Text()),
                  server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("publish_at", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="draft", nullable=False),
        sa.Column("group_key", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("created_via", sa.Text(), server_default="web", nullable=False),
        sa.Column("approved_at", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_wake_at", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.Text(), nullable=True),
        sa.Column("published_url", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"],
                                name=op.f("fk_content_items_owner_id_users"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_items")),
    )
    op.create_index("ix_content_items_owner", "content_items", ["owner_id"])
    op.create_index("ix_content_items_owner_publish", "content_items",
                    ["owner_id", "publish_at"])
    op.create_index("ix_content_items_status_publish", "content_items",
                    ["status", "publish_at"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_content_items_status_publish", table_name="content_items")
    op.drop_index("ix_content_items_owner_publish", table_name="content_items")
    op.drop_index("ix_content_items_owner", table_name="content_items")
    op.drop_table("content_items")

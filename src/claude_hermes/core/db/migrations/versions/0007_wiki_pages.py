"""wiki_pages: a derived full-text index over each user's markdown wiki

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-22

The markdown tree under `Workspace.wiki_root` stays the one source of truth. This
table is a CACHE: memory's `WikiIndexService` re-reads a page from disk and
upserts one row per `(owner_id, path)`; a Stop hook flushes what a turn touched, a
lint pass reindexes its tree, and boot reindexes every active user. Dropping the
table and rebooting rebuilds it, which is why nothing here backfills anything.

Three decisions are the whole shape:

* **The primary key is `(owner_id, path)`.** A non-owner may invent their own
  `shared/home` beside the owner's shared `shared/home`; both are legitimately in
  that user's search scope, so `path` alone would make one silently overwrite the
  other.
* **`ON DELETE CASCADE`, not the `SET NULL` of 0005.** A wiki belongs to exactly
  one person: an orphan row is not history worth keeping, it is a page searchable
  by nobody.
* **`content_tsv` is generated and `'simple'`** — the same configuration and the
  same `regexp_replace` as `messages.content_tsv` (0001). Russian text stems badly
  under any other built-in configuration, and the two search surfaces disagreeing
  about tokenisation would be a bug nobody could see.

Chained after `0006_per_user_extensions`, which was written on another branch
from the same `0005` and landed first; the two touch disjoint tables, and one
head is the only shape `upgrade head` accepts.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TSV = (
    "to_tsvector('simple', regexp_replace("
    "title || ' ' || summary || ' ' || content, '[^[:alnum:]]+', ' ', 'g'))"
)


def upgrade() -> None:
    op.create_table(
        "wiki_pages",
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("ns", sa.Text(), server_default="", nullable=False),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()),
                  server_default=sa.text("'{}'"), nullable=False),
        sa.Column("links", postgresql.ARRAY(sa.Text()),
                  server_default=sa.text("'{}'"), nullable=False),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("shared", sa.Boolean(), server_default=sa.text("false"),
                  nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_tsv", postgresql.TSVECTOR(),
                  sa.Computed(_TSV, persisted=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE",
                                name=op.f("fk_wiki_pages_owner_id_users")),
        sa.PrimaryKeyConstraint("owner_id", "path", name=op.f("pk_wiki_pages")),
    )
    op.create_index("ix_wiki_pages_content_tsv", "wiki_pages", ["content_tsv"],
                    postgresql_using="gin")
    op.create_index("ix_wiki_pages_links", "wiki_pages", ["links"],
                    postgresql_using="gin")
    op.create_index("ix_wiki_pages_shared", "wiki_pages", ["shared"],
                    postgresql_where=sa.text("shared"))


def downgrade() -> None:
    """Drop the cache. Nothing is lost: the markdown is the source of truth and
    the next boot rebuilds every row from disk."""
    op.drop_index("ix_wiki_pages_shared", table_name="wiki_pages")
    op.drop_index("ix_wiki_pages_links", table_name="wiki_pages",
                  postgresql_using="gin")
    op.drop_index("ix_wiki_pages_content_tsv", table_name="wiki_pages",
                  postgresql_using="gin")
    op.drop_table("wiki_pages")

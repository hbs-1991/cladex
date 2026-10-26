"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-14

The SQLite schema this replaces, table for table, with three deliberate changes:
`messages_fts` becomes a real `messages` table whose full-text index is a
generated `tsvector`; `job_runs` and `turn_costs` gain surrogate keys the ORM
needs; Telegram ids are BIGINT because they no longer fit in 32 bits.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "meta",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_meta")),
    )
    op.create_table(
        "sessions",
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("sdk_session_id", sa.Text(), nullable=True),
        sa.Column("system_append", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("session_key", name=op.f("pk_sessions")),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ts", sa.Text(), nullable=False),
        sa.Column("content_tsv", postgresql.TSVECTOR(),
                  sa.Computed(
                      "to_tsvector('simple',"
                      " regexp_replace(content, '[^[:alnum:]]+', ' ', 'g'))",
                      persisted=True),
                  nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index("ix_messages_session_key_ts", "messages", ["session_key", "ts"])
    op.create_index("ix_messages_content_tsv", "messages", ["content_tsv"],
                    postgresql_using="gin")
    op.create_table(
        "threads",
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("title_manual", sa.Integer(), server_default="0", nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column("archived_at", sa.Text(), nullable=True),
        sa.Column("last_event_at", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("session_key", name=op.f("pk_threads")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("deliver_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("deliver_thread_id", sa.BigInteger(), nullable=True),
        sa.Column("origin_session_key", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="active", nullable=False),
        sa.Column("next_run_at", sa.Text(), nullable=True),
        sa.Column("last_run_at", sa.Text(), nullable=True),
        sa.Column("last_status", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
    )
    op.create_index("job_runs_job", "job_runs", ["job_id", "ts"])
    op.create_table(
        "turn_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("ts", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turn_events")),
    )
    op.create_index("turn_events_lane", "turn_events", ["session_key", "id"])
    op.create_table(
        "turn_costs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.Text(), nullable=False),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_turn_costs")),
    )
    op.create_table(
        "extensions",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("catalog_version", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default="enabled", nullable=False),
        sa.Column("installed_at", sa.Text(), nullable=False),
        sa.Column("installed_by", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("tool_count", sa.Integer(), nullable=True),
        sa.Column("tool_names", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_extensions")),
    )
    op.create_table(
        "oauth_tokens",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("issuer", sa.Text(), server_default="", nullable=False),
        sa.Column("client_id", sa.Text(), server_default="", nullable=False),
        sa.Column("client_secret", sa.Text(), server_default="", nullable=False),
        sa.Column("access_token", sa.Text(), server_default="", nullable=False),
        sa.Column("refresh_token", sa.Text(), server_default="", nullable=False),
        sa.Column("expires_at", sa.Text(), server_default="", nullable=False),
        sa.Column("scope", sa.Text(), server_default="", nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_oauth_tokens")),
    )
    op.create_table(
        "files",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_key", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_files")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("files")
    op.drop_table("oauth_tokens")
    op.drop_table("extensions")
    op.drop_table("turn_costs")
    op.drop_index("turn_events_lane", table_name="turn_events")
    op.drop_table("turn_events")
    op.drop_index("job_runs_job", table_name="job_runs")
    op.drop_table("job_runs")
    op.drop_table("jobs")
    op.drop_table("threads")
    op.drop_index("ix_messages_content_tsv", table_name="messages",
                  postgresql_using="gin")
    op.drop_index("ix_messages_session_key_ts", table_name="messages")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("meta")

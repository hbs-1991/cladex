"""The content plan's one table: a row is ONE publication on ONE channel.

Variants of one idea (the LinkedIn post and the Telegram cut of it) are separate
rows sharing a `group_key`: one row is one provider call and one result, so a
failure on one channel never has to be untangled from a success on another.

Every timestamp is ISO-8601 UTC `Text`, like `jobs`, and every writer goes through
`domain.time.iso_utc` — the tick compares them as strings.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class ContentItem(Base):
    __tablename__ = "content_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Whose plan this is. NOT NULL and CASCADE, unlike `jobs.owner_id`: an item
    # without an owner is readable by nobody and publishable by nobody, so there
    # is no history worth keeping once the user is gone. v1 has one owner (the
    # deploy owner) but the column is here so a per-user plan needs no migration.
    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Exactly what is published. `source_path` and `thread_id` are links for the
    # owner to follow, never a place the publisher reads content from.
    body: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # `[{"path": <relative to the owner's agent_home>, "sha256": str | null}]`.
    # The hash is taken at approval and re-checked at claim (`status.py`).
    media: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    # NULL = backlog: an item nobody has put on the calendar yet.
    publish_at: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    group_key: Mapped[str | None] = mapped_column(Text)
    source_path: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    # "web", "chat" or "cron:<job_id>" — which channel wrote the row.
    created_via: Mapped[str] = mapped_column(Text, nullable=False, server_default="web")
    # The owner's approval. Both are cleared whenever the approved content moves
    # (an agent edit, an unapprove, a hash mismatch at claim).
    approved_at: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    # How many times the tick has woken the publisher for this item, and when it
    # may next do so. Both belong to the tick's compare-and-set alone.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    next_wake_at: Mapped[str | None] = mapped_column(Text)
    # When the publisher's `claim` moved the item to `publishing`; the stuck
    # timeout is measured from here.
    claimed_at: Mapped[str | None] = mapped_column(Text)
    published_url: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    # --- drafting (0011) ---------------------------------------------------------
    # When the owner approved the THEME (`idea -> planned`). Content approval is
    # still `approved_at`: approving a theme never schedules a post.
    theme_approved_at: Mapped[str | None] = mapped_column(Text)
    # Claimed drafter runs that failed or timed out; the third sends the item to
    # `failed`. Not `attempts`, which stays the publisher's wake count.
    draft_attempts: Mapped[int] = mapped_column(Integer, nullable=False,
                                                server_default="0")
    # Drafter wakes that got no `claim_draft`, and when the tick may wake it for
    # this item again — the publisher's `attempts`/`next_wake_at`, for the drafter.
    draft_wakes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    draft_next_wake_at: Mapped[str | None] = mapped_column(Text)
    # The owner's "redo it" replies to a card, and the last one's text, which the
    # drafter reads on its next claim.
    redrafts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    draft_comment: Mapped[str | None] = mapped_column(Text)
    # When `claim_draft` moved the item to `drafting`; the drafting timeout counts
    # from here.
    drafting_since: Mapped[str | None] = mapped_column(Text)
    # --- the approval card outbox (0011, `domain/cards.py`) -----------------------
    card_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    # The content revision the card's buttons carry.
    card_revision: Mapped[str | None] = mapped_column(Text)
    # Where the card's text message (the one with the buttons) landed: a reply to
    # exactly this message is a redraft.
    card_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    card_message_id: Mapped[int | None] = mapped_column(BigInteger)
    card_attempts: Mapped[int] = mapped_column(Integer, nullable=False,
                                               server_default="0")
    # When a tick took the card to `sending`; a stale one goes back to `pending`.
    card_claimed_at: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        Index("ix_content_items_owner", "owner_id"),
        # The calendar's range read.
        Index("ix_content_items_owner_publish", "owner_id", "publish_at"),
        # The tick's reads: `scheduled` items whose time has come, `queued` and
        # `publishing` items of every owner.
        Index("ix_content_items_status_publish", "status", "publish_at"),
    )

"""content_items: drafting and the Telegram approval card

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-28

The content plan v2: the owner approves a THEME (`idea -> planned`), a drafter
routine generates the post (`planned -> drafting -> review`) and the post reaches
the owner as a Telegram card whose button approves exactly the revision on it.
The two new statuses need no DDL (`status` is free text owned by
`modules/content/domain/status.py`); the columns are:

* `theme_approved_at` — the theme approval. `approved_at` keeps its meaning: the
  owner approved the CONTENT, which is what schedules a post.
* `draft_attempts` — claimed drafter runs that failed or timed out (3 -> failed).
  `attempts` keeps its meaning: the publisher's wake count.
* `draft_wakes` / `draft_next_wake_at` — the drafter's wake budget, advanced by
  the tick's compare-and-set exactly like `attempts` / `next_wake_at`.
* `redrafts` / `draft_comment` — the owner's "redo it" replies to a card.
* `drafting_since` — when the drafter claimed the item; the timeout counts from it.
* `card_state` (`none|pending|sending|sent|failed|superseded`), `card_revision`,
  `card_chat_id`, `card_message_id`, `card_attempts`, `card_claimed_at` — the card
  outbox, one compare-and-set per transition so N ticks send a card once.

Every new column is nullable or has a default: existing rows need no backfill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("theme_approved_at", sa.Text(), None),
    ("draft_attempts", sa.Integer(), "0"),
    ("draft_wakes", sa.Integer(), "0"),
    ("draft_next_wake_at", sa.Text(), None),
    ("redrafts", sa.Integer(), "0"),
    ("draft_comment", sa.Text(), None),
    ("drafting_since", sa.Text(), None),
    ("card_state", sa.Text(), "none"),
    ("card_revision", sa.Text(), None),
    ("card_chat_id", sa.BigInteger(), None),
    ("card_message_id", sa.BigInteger(), None),
    ("card_attempts", sa.Integer(), "0"),
    ("card_claimed_at", sa.Text(), None),
)


def upgrade() -> None:
    """Upgrade schema."""
    for name, type_, default in _COLUMNS:
        if default is None:
            op.add_column("content_items", sa.Column(name, type_, nullable=True))
        else:
            op.add_column("content_items", sa.Column(name, type_, nullable=False,
                                                     server_default=default))


def downgrade() -> None:
    """Downgrade schema."""
    for name, _type, _default in reversed(_COLUMNS):
        op.drop_column("content_items", name)

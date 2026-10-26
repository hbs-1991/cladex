"""The turn itself as a row, the turn event lane the web UI replays, and the
per-turn cost ledger."""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import BigInteger, Float, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base

# The two statuses that mean "this turn holds the lane". Spelled once: the unique
# partial index, the lease index, `claim`, `active` and `expired` all have to
# agree on them, and a list that drifted in one of those five places is a lane
# that can be double-booked.
ACTIVE_STATUSES = ("queued", "running")
_ACTIVE_SQL = "status IN ('queued', 'running')"


class TurnRow(Base):
    """One turn: what was asked, who is running it, and until when.

    The row — not a coroutine, not an in-memory lock — is what makes "exactly one
    active turn per lane" true across processes. Three invariants live here:

    * **`turns_one_active_per_lane` IS the lock.** A unique partial index on
      `session_key` over the active statuses; the second `INSERT` on a busy lane
      raises, and the repository translates that into `LaneBusy`. There is no
      check-then-write anywhere, because a check-then-write is a race.
    * **`lease_until` is a dead-man's switch, not a deadline.** While `queued` it
      is "claim me before this or I am abandoned"; while `running` it is what the
      worker heartbeats. A lapsed lease is the reaper's mandate to mark the turn
      `lost` and free the lane — never to re-run it.
    * **`worker_id` guards every terminal write.** A worker whose turn was reaped
      out from under it must not be able to flip the row back to `ok`.

    Timestamps are ISO text like every other table here.
    """

    __tablename__ = "turns"
    id: Mapped[str] = mapped_column(Text, primary_key=True)     # uuid4 hex
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # web | telegram
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # File ids, not paths: the worker re-resolves each one against this row's lane,
    # so a forged row cannot read another lane's file.
    attachment_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb"))
    # queued | running | ok | error | cancelled | lost
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[str] = mapped_column(Text, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(Text)
    cancel_requested_at: Mapped[str | None] = mapped_column(Text)
    reply: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index("turns_one_active_per_lane", "session_key", unique=True,
              postgresql_where=sa.text(_ACTIVE_SQL)),
        Index("turns_active_lease", "lease_until", postgresql_where=sa.text(_ACTIVE_SQL)),
        # Pruning only ever walks finished rows, and there are far more of those
        # than active ones — a partial index keeps the reaper's scan off them.
        Index("turns_finished", "finished_at",
              postgresql_where=sa.text("finished_at IS NOT NULL")),
    )


class TurnEventRow(Base):
    """The ORM row. Named `…Row` because the wire dataclass the bus publishes is
    called `TurnEvent` — two different things that shared one name, which is how a
    `from … import TurnEvent` could silently mean either."""

    __tablename__ = "turn_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("turn_events_lane", "session_key", "id"),)


class TurnCost(Base):
    __tablename__ = "turn_costs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    # Wave 4a — whose spend this was, so `cost_report` can be scoped to the asker.
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"))

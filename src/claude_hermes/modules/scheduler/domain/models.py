"""Cron jobs and their run history."""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    deliver_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    deliver_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    origin_session_key: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    next_run_at: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[str | None] = mapped_column(Text)
    last_status: Mapped[str | None] = mapped_column(Text)
    # NULL, not a default: "this job never asked for its own limit" has to stay
    # distinguishable from "this job asked for exactly the global limit", or
    # raising cron_timeout_seconds would stop reaching the jobs that inherit it.
    timeout_seconds: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    # Wave 3a — claim-based dispatch. `claim_due` moves `next_run_at` forward and
    # stamps these two in the same transaction as the SELECT ... FOR UPDATE SKIP
    # LOCKED that found the row, which is the at-most-once guarantee across N
    # workers. `lease_until` guards only the window between the claim and a
    # worker picking the task up; it is cleared when the run finishes.
    claimed_at: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[str | None] = mapped_column(Text)
    # Wave 4a — whose routine this is, and therefore whose workspace the cron turn
    # runs in (D6). Set at creation from the turn's owner or the requesting user;
    # the executor resolves the job owner's workspace and puts this id in the cron
    # `turn.start` payload where a `0` sentinel used to go.
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    # The routine's own (model, effort) (0008), as catalog ids. NULL, like
    # `timeout_seconds`, means "inherit": `cron_model`, then `model`.
    model: Mapped[str | None] = mapped_column(Text)
    effort: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_jobs_owner", "owner_id"),)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (Index("job_runs_job", "job_id", "ts"),)

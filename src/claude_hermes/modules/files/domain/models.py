"""Opaque ids the web file endpoint hands out, mapped back to a path on disk."""
from __future__ import annotations

from sqlalchemy import BigInteger, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base


class FileRecord(Base):
    __tablename__ = "files"
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_key: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    # Wave 4a: whose blob this is. The mirror root is resolved from THIS column,
    # never from the requester — a download re-resolves the path against the row's
    # owner, which is what stops one user's file id reading out of another's tree.
    # Spelled by table name, not by importing identity: modules stay independent.
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (Index("ix_files_owner", "owner_id"),)

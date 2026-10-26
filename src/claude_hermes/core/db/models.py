"""Tables that belong to no module: cross-cutting state the whole app shares."""
from __future__ import annotations

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

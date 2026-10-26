"""The ORM row of the wiki INDEX — a cache over the markdown tree, never the wiki
itself (see the module README).

In `tables.py` and not `models.py` for the same reason identity's row is: the
application layer imports `domain.models`, and the "application layers are
framework-free" contract forbids `sqlalchemy` there even transitively. Only
`infrastructure/page_index.py` and `core.db.registry` import this file.

`(owner_id, path)` is the primary key and `path` alone never is: a non-owner may
invent their own `shared/home` beside the owner's shared `shared/home`, and both
are legitimately in that user's scope. `ON DELETE CASCADE` rather than the
`SET NULL` of `0005`, because a wiki belongs to exactly one person — an orphan
row here is not history worth keeping, it is a page searchable by nobody.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base

# `'simple'`, the configuration `messages.content_tsv` already uses: the wiki is
# written in Russian as often as in English, and Russian text stems badly under
# any other built-in configuration. The `regexp_replace` matches it too — without
# it PostgreSQL keeps `report.pdf` or a URL as one indivisible token and a search
# for `report` misses the page.
_TSV = (
    "to_tsvector('simple', regexp_replace("
    "title || ' ' || summary || ' ' || content, '[^[:alnum:]]+', ' ', 'g'))"
)


class WikiPageRow(Base):
    """One indexed page of one user's wiki. Derived: disk always wins."""

    __tablename__ = "wiki_pages"

    owner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    # Wiki-root-relative posix, WITHOUT `.md` — the same string `read_page()` and
    # `WikiGraphService.graph()` use, so the graph route and the index name a page
    # identically and a `[[link]]` resolves against either.
    path: Mapped[str] = mapped_column(Text, primary_key=True)
    ns: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"))
    links: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'"))
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_tsv = mapped_column(TSVECTOR, Computed(_TSV, persisted=True))

    __table_args__ = (
        Index("ix_wiki_pages_content_tsv", "content_tsv", postgresql_using="gin"),
        # The backlink query is `links && ARRAY[...]`, which only a GIN index over
        # the array can answer without reading every row.
        Index("ix_wiki_pages_links", "links", postgresql_using="gin"),
        # Partial: a non-owner's search is `owner_id = :me OR shared`, and the
        # shared half is a handful of rows out of everybody's pages.
        Index("ix_wiki_pages_shared", "shared", postgresql_where=text("shared")),
    )

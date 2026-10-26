"""`wiki_pages` in PostgreSQL: the upsert the indexer calls and the search the
`memory_search` tool calls.

One `AsyncEngine` per store and one short transaction per call, the rule every
repository in this codebase follows — a caller never holds a session. The SQL
shape is `sessions`' `MessageRepository.search`: `plainto_tsquery` over a
punctuation-stripped query, `ts_headline` for the snippet, `ts_rank` for the
order, `'simple'` spelled as a literal because asyncpg types every bind and a
`regconfig` typed as varchar makes PostgreSQL fail to resolve the function at all.

What is new here is the **hop**. An FTS hit is rarely the whole answer: the page
the model wants is often one `[[link]]` away, and the page that knows WHY is
usually the one linking in. So after the hits, up to `depth` rounds of neighbours
— the pages a hit links to (`path IN links_out`) and the pages that link to a hit
(`links && ARRAY[frontier]`, which is what the GIN index over `links` is for).

**Identity is `(owner_id, path)`, never `path` alone.** A non-owner may invent
their own `shared/home` beside the owner's shared `shared/home` and both are
legitimately in their scope, so the `seen` set, the neighbour exclusion and every
returned dict carry the owner.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Text, cast
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, literal_column, or_, select, tuple_
from sqlalchemy.dialects.postgresql import ARRAY, array
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ..domain.tables import WikiPageRow

# See `MessageRepository`: a literal, not a bind.
_TS_CONFIG = literal_column("'simple'::regconfig")

# The same options the recall snippet uses, so a wiki hit and a message hit look
# alike in a tool result the model reads.
_HEADLINE_OPTS = ('StartSel=[, StopSel=], MaxWords=12, MinWords=6, '
                  'MaxFragments=1, FragmentDelimiter=" … "')

# A page is a page, not a paste bin: a model that dumps a 2 MB log into
# `topics/x.md` must not make one row that big. Truncating the INDEX loses
# nothing that matters — the markdown on disk is still complete.
MAX_CONTENT_CHARS = 200_000

# `limit` and `depth` arrive from a model-authored tool call, so both are clamped
# here rather than trusted. Two hops is where the neighbourhood stops being about
# the query.
MAX_LIMIT = 25
MAX_DEPTH = 2

_COLUMNS = (WikiPageRow.owner_id, WikiPageRow.path, WikiPageRow.ns, WikiPageRow.title,
            WikiPageRow.summary, WikiPageRow.tags, WikiPageRow.links, WikiPageRow.shared)


def _hit(row: Mapping[str, Any], *, via: str, hop: int, snippet: str = "") -> dict:
    return {"owner_id": row["owner_id"], "path": row["path"], "ns": row["ns"],
            "title": row["title"], "summary": row["summary"],
            "tags": list(row["tags"] or []), "links": list(row["links"] or []),
            "shared": bool(row["shared"]), "snippet": snippet, "via": via, "hop": hop}


class PgWikiIndex:
    """Satisfies memory's `WikiPageStore` and turns' `WikiSearch`."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def upsert(self, owner_id: int, page: Mapping[str, Any], *, content: str,
                     shared: bool, now: datetime) -> None:
        """One row per `(owner_id, path)`, replaced wholesale.

        Wholesale and not a merge: the page on disk is the whole truth about
        itself, so a link the model deleted has to leave the row too.
        """
        values = {
            "owner_id": owner_id,
            "path": page["path"],
            "ns": page.get("ns") or "",
            "title": page.get("title") or "",
            "summary": page.get("summary") or "",
            "tags": list(page.get("tags") or []),
            "links": list(page.get("links") or []),
            "content": (content or "")[:MAX_CONTENT_CHARS],
            "shared": shared,
            "indexed_at": now,
        }
        stmt = pg_insert(WikiPageRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["owner_id", "path"],
            set_={k: stmt.excluded[k] for k in values if k not in ("owner_id", "path")})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def delete(self, owner_id: int, path: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sql_delete(WikiPageRow).where(
                WikiPageRow.owner_id == owner_id, WikiPageRow.path == path))

    async def paths(self, owner_id: int) -> set[str]:
        """Every path this owner has a row for — what `reindex` diffs against disk."""
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                select(WikiPageRow.path).where(WikiPageRow.owner_id == owner_id))
        return {r[0] for r in rows.all()}

    async def search(self, query: str, *, owner_id: int, limit: int = 8,
                     depth: int = 1) -> list[dict]:
        """Full-text hits, then their neighbourhood, hop-ordered.

        The scope predicate is part of the SAME statement as the text match, never
        a filter over its result: `LIMIT` is applied by PostgreSQL, so filtering
        afterwards would turn "eight of the owner's matches" into "no matches" for
        everybody else — an isolation hole that looks like an empty index.
        """
        cleaned = "".join(ch if ch.isalnum() else " " for ch in query)
        if not cleaned.strip():
            # No alphanumeric character means `plainto_tsquery` would build an
            # empty query, which matches nothing anyway. Answer without a round trip.
            return []
        limit = max(1, min(int(limit), MAX_LIMIT))
        depth = max(0, min(int(depth), MAX_DEPTH))

        q = func.plainto_tsquery(_TS_CONFIG, func.regexp_replace(
            query, "[^[:alnum:]]+", " ", "g"))
        scope = or_(WikiPageRow.owner_id == owner_id, WikiPageRow.shared.is_(True))
        stmt = (
            select(*_COLUMNS,
                   func.ts_headline(_TS_CONFIG, WikiPageRow.content, q,
                                    _HEADLINE_OPTS).label("snippet"))
            .where(scope, WikiPageRow.content_tsv.op("@@")(q))
            .order_by(func.ts_rank(WikiPageRow.content_tsv, q).desc(), WikiPageRow.path)
            .limit(limit))

        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
            out = [_hit(r, via="search", hop=0, snippet=r["snippet"]) for r in rows]
            seen = {(h["owner_id"], h["path"]) for h in out}
            frontier = out
            for hop in range(1, depth + 1):
                if not frontier:
                    break
                links_out = {link for h in frontier for link in h["links"]}
                paths = {h["path"] for h in frontier}
                if not links_out and not paths:
                    break
                neighbours = (
                    select(*_COLUMNS)
                    .where(scope,
                           tuple_(WikiPageRow.owner_id, WikiPageRow.path).notin_(seen),
                           or_(WikiPageRow.path.in_(links_out),
                               # CAST because asyncpg types a literal array as
                               # `varchar[]`, and `text[] && varchar[]` is not an
                               # operator PostgreSQL has.
                               WikiPageRow.links.op("&&")(
                                   cast(array(sorted(paths)), ARRAY(Text)))))
                    .order_by(WikiPageRow.owner_id, WikiPageRow.path)
                    .limit(limit))
                found = (await conn.execute(neighbours)).mappings().all()
                step = [_hit(r, via="link" if r["path"] in links_out else "backlink",
                             hop=hop)
                        for r in found]
                seen |= {(h["owner_id"], h["path"]) for h in step}
                out += step
                frontier = step
        return out

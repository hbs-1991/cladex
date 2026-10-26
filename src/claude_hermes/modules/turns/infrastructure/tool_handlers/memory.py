"""`memory_search`: the wiki index, read as one tool result.

The markdown tree under the lane owner's `wiki_root` is the source of truth; the
rows behind `WikiSearch` are a derived index over it. Everything here follows
from that: a query that finds nothing says "read `wiki/index.md`" rather than
"you have no such page", and a port that raises becomes a sentence rather than
an exception — a turn must not end because a cache was cold.

Two rules are not formatting:

* **A row's identity is `(owner_id, path)`.** A row whose owner is not the
  lane's is the deploy owner's `shared/` page: it prints `[shared]`, and its
  outgoing links are filtered down to pages already visible in this very
  result. A shared page linking `[[user/health]]` would otherwise tell every
  admitted user which pages the owner keeps — the title alone is the leak.
* **The answer is clipped in characters, not rows.** Eight hits and two hops of
  neighbours, each with a summary and a headline, is the biggest tool result a
  turn sees, and every one of those tokens is paid for again on the next call of
  the same turn. The cut lands on a block boundary so no page is half-printed.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from claude_hermes.modules.turns.application.ports import WikiSearch

# Roughly one screen of wiki. Bigger results are not more useful: the tool exists
# to say WHICH pages to open, and the pages themselves are one `Read` away.
MEMORY_SEARCH_MAX_CHARS = 6000
MEMORY_SEARCH_LIMIT = 8
MAX_DEPTH = 2
_SNIPPET_MAX_CHARS = 300
_MAX_RELATIONS = 2


def _clean(text: Any, limit: int | None = None) -> str:
    """One line, whatever the page put on disk. `ts_headline` keeps the source's
    newlines, and a multi-line snippet would break the block layout the model
    reads by indentation."""
    out = " ".join(str(text or "").split())
    if limit is not None and len(out) > limit:
        out = out[: max(limit - 1, 0)] + "…"
    return out


def _depth(raw: Any) -> int:
    """`0..MAX_DEPTH`; anything that is not a number reads as the default.

    The model fills this field from its own prose, so `"deep"` and `7` are both
    things that arrive here, and neither is worth an error round-trip.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return max(0, min(MAX_DEPTH, value))


def _relation(row: Mapping[str, Any], earlier: Sequence[Mapping[str, Any]]) -> str:
    """Why a neighbour is in the list, named by the page it hangs off.

    The port says `link` or `backlink`; which page it came from is not on the
    row, so it is recovered from the rows printed before it — which is also the
    only set the reader can already see.
    """
    via = row.get("via") or "search"
    path = row.get("path") or ""
    if via == "link":
        sources = [r["path"] for r in earlier if path in (r.get("links") or ())]
        if sources:
            return f"(linked from {', '.join(sources[:_MAX_RELATIONS])})"
        return "(linked)"
    if via == "backlink":
        seen = {r.get("path") for r in earlier}
        targets = [p for p in (row.get("links") or ()) if p in seen]
        if targets:
            return f"(links to {', '.join(targets[:_MAX_RELATIONS])})"
        return "(links here)"
    return ""


def _links(row: Mapping[str, Any], *, foreign: bool, visible: set[str]) -> list[str]:
    """A row's outgoing links, filtered to what the asker may learn exists.

    The lane owner's own pages print verbatim — they are their own notes. A
    foreign (shared) row prints only links that name a page already in this
    result, because a link is a page TITLE and a title is information.
    """
    links = [str(link) for link in (row.get("links") or ())]
    if not foreign:
        return links
    return [link for link in links if link in visible]


def _block(row: Mapping[str, Any], *, lane_owner: int, visible: set[str],
           earlier: Sequence[Mapping[str, Any]]) -> str:
    path = str(row.get("path") or "")
    foreign = int(row.get("owner_id") or 0) != lane_owner
    head = [_clean(row.get("title")) or path]
    summary = _clean(row.get("summary"))
    if summary:
        head.append(summary)
    tags = [_clean(t) for t in (row.get("tags") or ()) if _clean(t)]
    if tags:
        head.append("tags: " + ", ".join(tags))
    relation = _relation(row, earlier)
    if relation:
        head.append(relation)
    if foreign:
        head.append("[shared]")
    lines = [f"{path} — " + " · ".join(head)]
    snippet = _clean(row.get("snippet"), _SNIPPET_MAX_CHARS)
    if snippet:
        lines.append(f"  {snippet}")
    links = _links(row, foreign=foreign, visible=visible)
    if links:
        lines.append("  links: " + ", ".join(links))
    return "\n".join(lines)


def _render(rows: Sequence[Mapping[str, Any]], *, lane_owner: int) -> str:
    """Blocks in the order the port returned them — hits first, then each hop —
    cut at the first block that would not fit whole."""
    visible = {str(r.get("path") or "") for r in rows}
    out: list[str] = []
    used = 0
    for index, row in enumerate(rows):
        block = _block(row, lane_owner=lane_owner, visible=visible,
                       earlier=rows[:index])
        cost = len(block) + (1 if out else 0)
        # The "… (N more)" line has to fit too, or the clip would be the thing
        # that overflows the budget it exists to keep.
        tail = len(f"\n… ({len(rows) - index} more)")
        if used + cost + tail > MEMORY_SEARCH_MAX_CHARS and out:
            return "\n".join(out) + f"\n… ({len(rows) - index} more)"
        out.append(block)
        used += cost
    return "\n".join(out)


def make_memory_search(search: WikiSearch, ctx=None):
    """Search the lane owner's wiki index, scoped exactly like `session_search`.

    `ctx.owner_id` is the LANE's owner, decided once by the runner — never the
    sender, so a stranger in a group lane searches the deploy owner's wiki and
    not a scope of their own. A `ctx` of `None` (a fork with no lane) searches as
    owner `0`, which the store answers with shared rows only: nobody's private
    pages belong to a context that has no owner.
    """
    lane_owner = int(getattr(ctx, "owner_id", 0) or 0)

    async def memory_search(args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            text = "memory_search: give me a query."
            return {"content": [{"type": "text", "text": text}]}
        try:
            rows = await search.search(
                query, owner_id=lane_owner, limit=MEMORY_SEARCH_LIMIT,
                depth=_depth(args.get("depth", 1)),
            )
        except Exception as e:      # the wiki is a hint; a cold cache is not a failure
            text = f"memory_search is unavailable right now ({type(e).__name__})."
            return {"content": [{"type": "text", "text": text}]}
        if not rows:
            text = (f"No wiki page matches «{query}». "
                    "Read wiki/index.md before concluding it is not there.")
        else:
            text = _render(list(rows), lane_owner=lane_owner)
        return {"content": [{"type": "text", "text": text}]}

    return memory_search

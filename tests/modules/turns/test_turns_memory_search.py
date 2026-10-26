"""`memory_search`: what the model is handed back, and what it is never handed.

The port is faked here on purpose — `turns` declares `WikiSearch` as a
consumer-side Protocol and never imports `memory`, so the contract under test is
the one written in `application/ports.py`, not the one `PgWikiIndex` happens to
implement (`tests/modules/memory/test_memory_index.py` is the other half).

Two of these are security tests rather than formatting ones: a shared page's
outgoing links must not tell an admitted user which pages the OWNER keeps, and a
handler that raised would end a turn the wiki was only ever a hint for.
"""
from __future__ import annotations

import pytest

from claude_hermes.modules.turns.domain.value_objects import TurnContext
from claude_hermes.modules.turns.infrastructure.tool_handlers.memory import (
    MEMORY_SEARCH_MAX_CHARS,
    make_memory_search,
)


def row(path, *, owner_id=1, title="", summary="", tags=(), links=(),
        shared=False, snippet="", via="search", hop=0):
    return {"owner_id": owner_id, "path": path, "ns": path.split("/")[0],
            "title": title or path, "summary": summary, "tags": list(tags),
            "links": list(links), "shared": shared, "snippet": snippet,
            "via": via, "hop": hop}


class FakeWikiSearch:
    """Records the kwargs it was called with; answers with canned rows."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls: list[dict] = []

    async def search(self, query, *, owner_id, limit=8, depth=1):
        self.calls.append({"query": query, "owner_id": owner_id,
                           "limit": limit, "depth": depth})
        return self.rows


class Boom:
    async def search(self, query, *, owner_id, limit=8, depth=1):
        raise RuntimeError("db down")


async def call(handler, **args):
    out = await handler(args)
    return out["content"][0]["text"]


def ctx_for(owner_id: int) -> TurnContext:
    return TurnContext(session_key="tg:private:7", chat_id=7, owner_id=owner_id)


# --- scope -------------------------------------------------------------------


async def test_the_lane_owner_comes_from_the_turn_context():
    """Never from the sender: a group lane's rows are the deploy owner's, and a
    stranger asking must search that scope, not invent one."""
    port = FakeWikiSearch()
    await call(make_memory_search(port, ctx_for(7)), query="tea")
    assert port.calls[0]["owner_id"] == 7 and port.calls[0]["limit"] == 8


async def test_no_context_searches_as_owner_zero():
    """A bare fork has no lane. The store answers only shared rows for `0`,
    which is the honest answer rather than an error."""
    port = FakeWikiSearch()
    await call(make_memory_search(port), query="tea")
    assert port.calls[0]["owner_id"] == 0


# --- formatting ---------------------------------------------------------------


async def test_a_hit_prints_its_title_summary_tags_snippet_and_links():
    port = FakeWikiSearch([
        row("user/prefs", title="Prefs", summary="what I like", tags=["me"],
            links=["topics/tea"], snippet="…drink [tea] every…"),
        row("topics/tea", title="Tea", via="link", hop=1),
    ])
    text = await call(make_memory_search(port, ctx_for(1)), query="tea")
    lines = text.splitlines()
    assert lines[0] == "user/prefs — Prefs · what I like · tags: me"
    assert lines[1] == "  …drink [tea] every…"
    assert lines[2] == "  links: topics/tea"


async def test_a_link_neighbour_says_who_linked_to_it():
    port = FakeWikiSearch([
        row("user/prefs", title="Prefs", links=["topics/kettle"]),
        row("topics/kettle", title="Kettle", via="link", hop=1),
    ])
    text = await call(make_memory_search(port, ctx_for(1)), query="kettle")
    assert "topics/kettle — Kettle · (linked from user/prefs)" in text


async def test_a_backlink_neighbour_says_what_it_links_to():
    port = FakeWikiSearch([
        row("topics/tea", title="Tea"),
        row("projects/cafe", title="Cafe", links=["topics/tea"],
            via="backlink", hop=1),
    ])
    text = await call(make_memory_search(port, ctx_for(1)), query="tea")
    assert "projects/cafe — Cafe · (links to topics/tea)" in text


async def test_a_foreign_row_is_marked_shared():
    """Identity is `(owner_id, path)`: a row whose owner is not the lane's is the
    deploy owner's shared page, and the model has to know it may not edit it."""
    port = FakeWikiSearch([
        row("shared/home", owner_id=1, title="Home", summary="the flat",
            shared=True),
    ])
    text = await call(make_memory_search(port, ctx_for(7)), query="flat")
    assert text.splitlines()[0] == "shared/home — Home · the flat · [shared]"


async def test_the_lane_owners_own_shared_path_is_not_marked():
    """A user may invent their own `shared/home`; it is theirs and private."""
    port = FakeWikiSearch([row("shared/home", owner_id=7, title="My draft")])
    text = await call(make_memory_search(port, ctx_for(7)), query="draft")
    assert "[shared]" not in text


# --- the link leak ------------------------------------------------------------


async def test_a_shared_rows_links_are_filtered_to_the_visible_set():
    """A shared page linking `[[user/health]]` must not tell every admitted user
    that the owner keeps a page there. Only links naming a page they can already
    see in this very result are printed."""
    port = FakeWikiSearch([
        row("shared/home", owner_id=1, title="Home", shared=True,
            links=["user/health", "shared/kids"]),
        row("shared/kids", owner_id=1, title="Kids", shared=True,
            via="link", hop=1),
    ])
    text = await call(make_memory_search(port, ctx_for(7)), query="home")
    assert "links: shared/kids" in text
    assert "user/health" not in text


async def test_the_lane_owners_own_links_are_printed_verbatim():
    port = FakeWikiSearch([
        row("user/me", owner_id=7, title="Me", links=["user/health", "nowhere"]),
    ])
    text = await call(make_memory_search(port, ctx_for(7)), query="me")
    assert "links: user/health, nowhere" in text


# --- the arguments ------------------------------------------------------------


async def test_an_empty_query_asks_for_one_without_touching_the_port():
    port = FakeWikiSearch()
    handler = make_memory_search(port, ctx_for(7))
    assert await call(handler, query="   ") == "memory_search: give me a query."
    assert await call(handler) == "memory_search: give me a query."
    assert port.calls == []


async def test_no_hits_points_at_the_index_rather_than_denying_the_memory():
    """The index is a cache over the markdown; "no row" is not "no page"."""
    text = await call(make_memory_search(FakeWikiSearch(), ctx_for(7)),
                      query="kettle")
    assert text == ("No wiki page matches «kettle». "
                    "Read wiki/index.md before concluding it is not there.")


@pytest.mark.parametrize("given,expected", [
    (None, 1), (0, 0), (1, 1), (2, 2), (5, 2), (-3, 0), ("2", 2),
    ("deep", 1), (None, 1),
])
async def test_depth_is_clamped_and_a_non_int_reads_as_one(given, expected):
    port = FakeWikiSearch()
    args = {"query": "tea"} if given is None else {"query": "tea", "depth": given}
    await (make_memory_search(port, ctx_for(7)))(args)
    assert port.calls[-1]["depth"] == expected


async def test_a_failing_port_is_an_answer_never_an_exception():
    """The wiki is a hint. A turn must not end because an index row was not
    there to read."""
    text = await call(make_memory_search(Boom(), ctx_for(7)), query="tea")
    assert text == "memory_search is unavailable right now (RuntimeError)."


# --- the size of the answer ---------------------------------------------------


async def test_a_long_result_is_clipped_on_a_block_boundary():
    """8 hits plus two hops of neighbours, each with a summary and a snippet, is
    the biggest tool result a turn sees. Clipped in characters, not rows, and
    never mid-block — a half-printed page is a page the model may misquote."""
    rows = [row(f"t/p{i}", title=f"Page {i}", summary="s" * 300,
                snippet="n" * 200) for i in range(40)]
    text = await call(make_memory_search(FakeWikiSearch(rows), ctx_for(7)),
                      query="needle")
    assert len(text) <= MEMORY_SEARCH_MAX_CHARS
    last = text.splitlines()[-1]
    assert last.startswith("… (") and last.endswith(" more)")
    shown = sum(1 for ln in text.splitlines() if ln.startswith("t/p"))
    assert 0 < shown < 40
    assert last == f"… ({40 - shown} more)"
    # nothing is half-printed: every shown block kept its summary line
    assert text.count("s" * 300) == shown

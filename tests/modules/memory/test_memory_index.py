"""The wiki index: the store's SQL and the service that fills it from disk.

Against a real PostgreSQL, because everything that could go wrong here is
PostgreSQL's: the generated `tsvector`, the GIN array overlap that answers the
backlink query, and the `(owner_id, path)` scoping that keeps one user's pages out
of another's search.
"""
from datetime import datetime, timezone

import pytest
from conftest import ensure_users

from claude_hermes.modules.memory.application.services.index import WikiIndexService
from claude_hermes.modules.memory.infrastructure.page_index import PgWikiIndex

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def page(path, title="", summary="", links=(), tags=()):
    ns = path.split("/")[0] if "/" in path else ""
    return {"path": path, "ns": ns, "title": title or path, "summary": summary,
            "tags": list(tags), "links": list(links), "updated": "", "in_index": True}


@pytest.fixture
async def store(engine):
    await ensure_users(engine, 1, 7, owner=1)
    return PgWikiIndex(engine)


async def test_upsert_is_idempotent_and_replaces(store):
    await store.upsert(1, page("user/prefs", "Prefs"), content="likes tea", shared=False, now=NOW)
    await store.upsert(1, page("user/prefs", "Prefs"), content="likes coffee", shared=False, now=NOW)
    hits = await store.search("coffee", owner_id=1)
    assert [h["path"] for h in hits] == ["user/prefs"]
    assert await store.search("tea", owner_id=1) == []


async def test_search_is_scoped_to_the_owner_plus_shared(store):
    await store.upsert(1, page("user/me", "Owner"), content="secret owner fact", shared=False, now=NOW)
    await store.upsert(1, page("shared/home", "Home"), content="the flat in Batumi", shared=True, now=NOW)
    await store.upsert(7, page("user/me", "Wife"), content="her own fact Batumi", shared=False, now=NOW)
    mine = await store.search("Batumi", owner_id=7)
    assert {h["path"] for h in mine} == {"user/me", "shared/home"}
    assert next(h for h in mine if h["path"] == "shared/home")["shared"] is True
    assert await store.search("secret", owner_id=7) == []


async def test_depth_one_adds_links_and_backlinks_marked_via(store):
    await store.upsert(1, page("topics/tea", "Tea", links=["topics/kettle"]), content="green tea", shared=False, now=NOW)
    await store.upsert(1, page("topics/kettle", "Kettle"), content="a kettle", shared=False, now=NOW)
    await store.upsert(1, page("projects/cafe", "Cafe", links=["topics/tea"]), content="a cafe plan", shared=False, now=NOW)
    await store.upsert(1, page("topics/far", "Far"), content="unrelated", shared=False, now=NOW)
    hits = await store.search("green", owner_id=1, depth=1)
    by = {h["path"]: h for h in hits}
    assert by["topics/tea"]["via"] == "search" and by["topics/tea"]["hop"] == 0
    assert by["topics/kettle"]["via"] == "link" and by["topics/kettle"]["hop"] == 1
    assert by["projects/cafe"]["via"] == "backlink" and by["projects/cafe"]["hop"] == 1
    assert "topics/far" not in by
    assert [h["path"] for h in await store.search("green", owner_id=1, depth=0)] == ["topics/tea"]


async def test_a_hit_carries_a_snippet_and_a_neighbour_does_not(store):
    await store.upsert(1, page("a", "A", links=["b"]), content="word " * 30 + "needle " + "word " * 30, shared=False, now=NOW)
    await store.upsert(1, page("b", "B"), content="plain", shared=False, now=NOW)
    hits = await store.search("needle", owner_id=1)
    assert "[needle]" in next(h for h in hits if h["path"] == "a")["snippet"]
    assert next(h for h in hits if h["path"] == "b")["snippet"] == ""


async def test_paths_and_delete(store):
    await store.upsert(1, page("x/one"), content="", shared=False, now=NOW)
    await store.upsert(1, page("x/two"), content="", shared=False, now=NOW)
    assert await store.paths(1) == {"x/one", "x/two"}
    await store.delete(1, "x/one")
    assert await store.paths(1) == {"x/two"}
    assert await store.paths(7) == set()


async def test_an_empty_or_punctuation_only_query_returns_nothing(store):
    await store.upsert(1, page("x/one"), content="anything", shared=False, now=NOW)
    assert await store.search("", owner_id=1) == []
    assert await store.search("!!! ???", owner_id=1) == []


async def test_limit_bounds_hits_and_neighbours_separately(store):
    for i in range(12):
        await store.upsert(1, page(f"t/p{i}", f"P{i}", links=[f"t/n{i}"]), content="needle", shared=False, now=NOW)
        await store.upsert(1, page(f"t/n{i}"), content="neighbour", shared=False, now=NOW)
    hits = await store.search("needle", owner_id=1, limit=5, depth=1)
    assert sum(h["via"] == "search" for h in hits) == 5
    assert sum(h["via"] != "search" for h in hits) <= 5


async def test_a_same_path_collision_between_shared_and_private_keeps_both(store):
    # the owner's shared page and user 7's own page with the SAME path
    await store.upsert(1, page("shared/home", "Our home", links=["shared/kids"]), content="family flat", shared=True, now=NOW)
    await store.upsert(1, page("shared/kids", "Kids"), content="school", shared=True, now=NOW)
    await store.upsert(7, page("shared/home", "My draft"), content="my private flat notes", shared=False, now=NOW)
    hits = await store.search("flat", owner_id=7, depth=1)
    keys = {(h["owner_id"], h["path"]) for h in hits}
    assert {(1, "shared/home"), (7, "shared/home"), (1, "shared/kids")} <= keys
    assert next(h for h in hits if h["owner_id"] == 7)["shared"] is False


# --- WikiIndexService: from disk to rows -------------------------------------


class WS:
    """The three attributes the service reads, in the real layout."""

    def __init__(self, root, is_owner):
        self.root = root
        self.wiki_root = root / "agent_home" / "wiki"
        self.is_owner = is_owner


def owner_ws(tmp_path):
    return WS(tmp_path, True)


def user_ws(tmp_path, uid):
    return WS(tmp_path / "users" / str(uid), False)


def write(root, rel, text):
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return f


async def test_a_mismatched_owner_id_and_workspace_writes_nothing(store, tmp_path):
    ws = user_ws(tmp_path, 7)
    write(ws.wiki_root, "a/one.md", "one")
    await WikiIndexService(store).touch(1, ws, {"a/one.md"})     # owner id 1, tree users/7
    assert await store.paths(1) == set() and await store.paths(7) == set()


async def test_touch_indexes_a_page_from_disk(store, tmp_path):
    ws = owner_ws(tmp_path)
    write(ws.wiki_root, "index.md", "- [[user/prefs]]")
    write(ws.wiki_root, "user/prefs.md",
          "---\ntitle: Prefs\nsummary: what I like\ntags: [me]\n---\nI drink [[topics/tea]].")
    svc = WikiIndexService(store)
    await svc.touch(1, ws, {"user/prefs.md", "index.md", "raw/src.md"})
    hits = await store.search("drink", owner_id=1)
    assert len(hits) == 1 and hits[0]["title"] == "Prefs" and hits[0]["links"] == ["topics/tea"]
    assert await store.paths(1) == {"user/prefs"}


async def test_touching_a_deleted_page_removes_its_row(store, tmp_path):
    ws = owner_ws(tmp_path)
    write(ws.wiki_root, "user/gone.md", "bye")
    svc = WikiIndexService(store)
    await svc.touch(1, ws, {"user/gone.md"})
    (ws.wiki_root / "user/gone.md").unlink()
    await svc.touch(1, ws, {"user/gone.md"})
    assert await store.paths(1) == set()


async def test_shared_is_only_the_owners_shared_folder(store, tmp_path):
    owner = owner_ws(tmp_path)
    user = user_ws(tmp_path, 7)
    write(owner.wiki_root, "shared/home.md", "our flat")
    write(user.wiki_root, "shared/mine.md", "my thing")
    svc = WikiIndexService(store)
    await svc.touch(1, owner, {"shared/home.md"})
    await svc.touch(7, user, {"shared/mine.md"})
    assert [h["path"] for h in await store.search("flat", owner_id=7)] == ["shared/home"]
    assert await store.search("thing", owner_id=1) == []


async def test_reindex_converges_the_table_to_the_tree(store, tmp_path):
    ws = owner_ws(tmp_path)
    write(ws.wiki_root, "a/one.md", "one")
    write(ws.wiki_root, "a/two.md", "two")
    await store.upsert(1, page("a/stale"), content="stale", shared=False, now=NOW)
    await WikiIndexService(store).reindex(1, ws)
    assert await store.paths(1) == {"a/one", "a/two"}


async def test_an_index_failure_never_raises(tmp_path):
    class Broken:
        async def upsert(self, *a, **k):
            raise RuntimeError("db down")

        async def delete(self, *a, **k):
            raise RuntimeError("db down")

        async def paths(self, *a, **k):
            raise RuntimeError("db down")

    ws = owner_ws(tmp_path)
    write(ws.wiki_root, "a/one.md", "one")
    svc = WikiIndexService(Broken())
    await svc.touch(1, ws, {"a/one.md"})
    await svc.reindex(1, ws)                                     # no raise


async def test_owner_zero_indexes_nothing(store, tmp_path):
    ws = owner_ws(tmp_path)
    write(ws.wiki_root, "a/one.md", "one")
    await WikiIndexService(store).touch(0, ws, {"a/one.md"})
    assert await store.paths(1) == set()


async def test_one_unreadable_page_does_not_stop_the_rest(store, tmp_path):
    """A note copied over SSH in Windows-1251 is one page, not a reason the
    whole tree stays stale on every boot and every lint."""
    ws = owner_ws(tmp_path)
    write(ws.wiki_root, "a/one.md", "one")
    bad = ws.wiki_root / "a" / "two.md"
    bad.write_bytes("привет".encode("cp1251"))
    write(ws.wiki_root, "a/three.md", "three")
    await store.upsert(1, page("a/stale"), content="stale", shared=False, now=NOW)
    svc = WikiIndexService(store)
    await svc.reindex(1, ws)
    assert await store.paths(1) == {"a/one", "a/two", "a/three"}   # stale swept too
    await store.delete(1, "a/three")
    await svc.touch(1, ws, {"a/two.md", "a/three.md"})
    assert "a/three" in await store.paths(1)


async def test_reindex_skips_raw_sources_like_touch_does(store, tmp_path):
    ws = owner_ws(tmp_path)
    write(ws.wiki_root, "raw/src.md", "a fetched source")
    write(ws.wiki_root, "topics/t.md", "a page")
    await store.upsert(1, page("raw/old"), content="stale source row", shared=False, now=NOW)
    await WikiIndexService(store).reindex(1, ws)
    assert await store.paths(1) == {"topics/t"}

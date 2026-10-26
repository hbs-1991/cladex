"""The wiki router: a graph built by scanning the directory, and one page in full.

The load-bearing test is the first one. `WikiStore._NAMESPACES` names the five
folders `seed()` creates; the agent then invents its own — `content/` exists on the
live deployment — and a graph assembled from that constant would hide them. Every
hub here comes from a directory that has a page in it.
"""
from tests.web_helpers import login, make_client

PAGE = (
    "---\ntitle: Nutrition targets\nsummary: calories and macros\n"
    "tags: [nutrition, weight]\nupdated: 2026-07-28\n---\n\n"
    "Targets live here. See [[user/biometrics]] and [[user/training|training]].\n"
    "Also [[user/biometrics]] again, and [[topics/gone#anchor]].\n"
)


def write(root, rel: str, text: str) -> None:
    # write_bytes, not write_text: on Windows text mode rewrites the line endings, and
    # the router hands back the bytes it read — the detail test compares them exactly.
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


async def setup(tmp_path, *, owner=1, wiki=True):
    client, deps = await make_client(tmp_path, owner=owner, allowed=[2], wiki=wiki)
    if deps.wiki_root is None:
        return client, deps
    root = deps.wiki_root
    write(root, "index.md", "# Wiki index\n\n- user/nutrition.md — targets\n"
                            "- user/biometrics.md — body\n- content/linkedin.md — posts\n")
    write(root, "log.md", "# Wiki log\n\n- 2026-07-28 ingest\n")
    write(root, "user/nutrition.md", PAGE)
    write(root, "user/biometrics.md", "---\ntitle: Biometrics\n---\n\nHeight, weight.\n")
    write(root, "user/training.md", "no frontmatter at all\n")
    write(root, "content/linkedin.md", "---\ntitle: LinkedIn voice\n---\n\nTone.\n")
    write(root, "topics/ai/agents.md", "---\ntitle: Agents\n---\n\nNotes.\n")
    return client, deps


async def graph(client) -> dict:
    r = await client.get("/api/wiki/graph")
    assert r.status_code == 200
    return r.json()


async def test_the_scan_finds_a_namespace_the_seed_never_created(tmp_path):
    client, _ = await setup(tmp_path)
    login(client, 1)
    body = await graph(client)
    # `content` is not in WikiStore._NAMESPACES; the agent made it.
    assert "content" in body["namespaces"]
    assert body["namespaces"] == ["content", "topics", "user"]
    assert {p["path"] for p in body["pages"]} == {
        "user/nutrition", "user/biometrics", "user/training",
        "content/linkedin", "topics/ai/agents",
    }


async def test_a_page_carries_its_frontmatter_and_its_links(tmp_path):
    client, _ = await setup(tmp_path)
    login(client, 1)
    (p,) = [x for x in (await graph(client))["pages"] if x["path"] == "user/nutrition"]
    assert p["ns"] == "user" and p["title"] == "Nutrition targets"
    assert p["summary"] == "calories and macros" and p["tags"] == ["nutrition", "weight"]
    assert p["updated"] == "2026-07-28"            # yaml parses it to a date; the wire is text
    # Deduplicated, alias and anchor stripped, in the order they appear.
    assert p["links"] == ["user/biometrics", "user/training", "topics/gone"]


async def test_a_link_with_no_page_behind_it_is_reported_missing(tmp_path):
    client, _ = await setup(tmp_path)
    login(client, 1)
    assert (await graph(client))["missing"] == ["topics/gone"]


async def test_a_page_the_agent_forgot_to_index_is_shown_and_flagged(tmp_path):
    client, _ = await setup(tmp_path)
    login(client, 1)
    by_path = {p["path"]: p for p in (await graph(client))["pages"]}
    assert by_path["user/nutrition"]["in_index"] is True
    assert by_path["user/training"]["in_index"] is False     # never listed in index.md


async def test_broken_frontmatter_still_gets_a_node_named_after_the_file(tmp_path):
    client, deps = await setup(tmp_path)
    write(deps.wiki_root, "env/notes.md", "---\ntitle: [unclosed\n  bad: -\n---\n\nBody.\n")
    login(client, 1)
    by_path = {p["path"]: p for p in (await graph(client))["pages"]}
    assert by_path["env/notes"]["title"] == "notes"
    assert by_path["user/training"]["title"] == "training"   # no frontmatter at all


async def test_a_nested_page_hangs_off_its_first_segment(tmp_path):
    client, _ = await setup(tmp_path)
    login(client, 1)
    (p,) = [x for x in (await graph(client))["pages"] if x["path"] == "topics/ai/agents"]
    assert p["ns"] == "topics"          # not "topics/ai": the graph is two rings deep


async def test_a_legacy_root_page_keeps_the_empty_namespace(tmp_path):
    client, deps = await setup(tmp_path)
    write(deps.wiki_root, "legacy.md", "---\ntitle: Legacy\n---\n\nOld.\n")
    login(client, 1)
    body = await graph(client)
    (p,) = [x for x in body["pages"] if x["path"] == "legacy"]
    assert p["ns"] == "" and body["namespaces"][-1] == ""     # the loose hub sorts last


async def test_the_bookkeeping_files_are_never_nodes(tmp_path):
    client, _ = await setup(tmp_path)
    login(client, 1)
    paths = {p["path"] for p in (await graph(client))["pages"]}
    assert "index" not in paths and "log" not in paths


async def test_the_detail_carries_the_content_and_an_etag(tmp_path):
    client, _ = await setup(tmp_path)
    login(client, 1)
    r = await client.get("/api/wiki/pages/user/nutrition")
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == PAGE and len(body["etag"]) == 64
    assert body["path"] == "user/nutrition" and body["title"] == "Nutrition targets"
    r2 = await client.get("/api/wiki/pages/topics/ai/agents")
    assert r2.status_code == 200 and r2.json()["path"] == "topics/ai/agents"


async def test_nothing_outside_the_wiki_can_be_read(tmp_path):
    client, deps = await setup(tmp_path)
    (tmp_path / "secret.md").write_text("not memory", encoding="utf-8")
    login(client, 1)
    for path in ("../secret", "user/../../secret", "/etc/passwd", "C:/Windows/x",
                 "index", "log", "user/nope"):
        r = await client.get(f"/api/wiki/pages/{path}")
        assert r.status_code == 404, path
        assert "not memory" not in r.text
    # A traversal that survives URL encoding is refused the same way.
    assert (await client.get("/api/wiki/pages/..%2Fsecret")).status_code == 404


async def test_an_allowed_user_who_is_not_the_owner_is_refused(tmp_path):
    client, _ = await setup(tmp_path, owner=9)
    login(client, 2)                       # allowed to chat, not to read the memory
    assert (await client.get("/api/wiki/graph")).status_code == 403
    assert (await client.get("/api/wiki/pages/user/nutrition")).status_code == 403


async def test_a_deployment_without_a_wiki_registers_no_routes(tmp_path):
    client, deps = await setup(tmp_path, wiki=False)
    assert deps.wiki_root is None
    login(client, 1)
    r = await client.get("/api/wiki/graph")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

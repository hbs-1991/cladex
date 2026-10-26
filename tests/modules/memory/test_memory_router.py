"""The wiki router's seams: the service it asks for, and the owner gate.

This is the reason `Depends` exists in this codebase. `routes(deps)` built its
`WikiGraphService` inside the closure, so a test that wanted a different one had to
build a different app out of a different bundle; `get_graph` is a named callable, and
`app.dependency_overrides[get_graph]` replaces it for one app without touching the
bundle, the disk, or the other nine routers.

The second test is the half an override must NOT reach: swapping the service does not
swap the owner check, because `require_owner` is a separate dependency.
"""
import httpx

from claude_hermes.modules.memory.presentation.dependencies import get_graph
from claude_hermes.http.app import build_web_app

from tests.web_helpers import ORIGIN, login, make_client

GRAPH = {"pages": [{"path": "fake/page", "ns": "fake", "title": "Fake", "summary": "",
                    "tags": [], "updated": "", "links": [], "in_index": False}],
         "namespaces": ["fake"], "missing": []}


class FakeGraphService:
    """A `WikiGraphService` by shape only — no root, no disk, no scan."""

    def __init__(self):
        self.asked_for = []

    def graph(self) -> dict:
        return GRAPH

    def page(self, raw: str):
        self.asked_for.append(raw)
        return dict(GRAPH["pages"][0]), b"fake bytes"


async def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def test_the_graph_service_is_swappable_without_rebuilding_the_bundle(tmp_path):
    _c, deps = await make_client(tmp_path)
    app = build_web_app(deps)
    fake = FakeGraphService()
    app.dependency_overrides[get_graph] = lambda: fake
    client = await client_for(app)
    login(client, 1)

    r = await client.get("/api/wiki/graph")
    assert r.status_code == 200 and r.json() == GRAPH

    # The path converter still hands the whole tail to the service, override or not.
    r = await client.get("/api/wiki/pages/topics/ai/agents")
    assert r.status_code == 200
    assert r.json()["content"] == "fake bytes" and len(r.json()["etag"]) == 64
    assert fake.asked_for == ["topics/ai/agents"]


async def test_overriding_the_service_does_not_open_the_owner_gate(tmp_path):
    """`require_owner` is its own dependency; a test that fakes the wiki still has to
    be the owner to read it."""
    _c, deps = await make_client(tmp_path, owner=9, allowed=[2])
    app = build_web_app(deps)
    app.dependency_overrides[get_graph] = FakeGraphService
    client = await client_for(app)
    login(client, 2)                       # allowed to chat, not to read the memory
    r = await client.get("/api/wiki/graph")
    assert r.status_code == 403
    assert r.json() == {"error": {"code": "forbidden", "message": "owner only"}}
    assert (await client.get("/api/wiki/pages/fake/page")).status_code == 403


async def test_the_real_provider_reads_the_root_off_the_app_state(tmp_path):
    """No override: `provide("wiki_root")` resolves through `app.state.deps`, so the
    same module object serves whatever tree the bundle points at."""
    _c, deps = await make_client(tmp_path)
    (deps.wiki_root / "user").mkdir(parents=True, exist_ok=True)
    (deps.wiki_root / "user" / "nutrition.md").write_bytes(b"---\ntitle: T\n---\n\nBody.\n")
    app = build_web_app(deps)
    client = await client_for(app)
    login(client, 1)
    r = await client.get("/api/wiki/graph")
    assert r.status_code == 200
    assert [p["path"] for p in r.json()["pages"]] == ["user/nutrition"]

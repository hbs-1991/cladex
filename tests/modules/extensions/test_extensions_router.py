"""The three extensions routers' seams: the view they ask for, and the guards.

`routes(deps)` closed over an `ExtensionsDeps`, so a test that wanted a different
catalog had to build a different app out of a different bundle. `get_extensions` is a
named callable: `app.dependency_overrides[get_extensions]` swaps all eighteen
collaborators at once, for one app, without touching the bundle, the database or the
disk — which is what these tests do.

The rest is the parity the conversion must not lose: the owner gate an override must
NOT open, and the two name guards that stand between a route param and a filesystem
call (`valid_name` before the plugin rmtree, before the draft unlink).

Since Wave 5 they also pin *which* `owner_id` each route passes. The fakes record it,
so what is asserted here is the gate and the caller — the row-level guarantees live in
`test_extensions_per_user.py`, over a real database.
"""
from types import SimpleNamespace

import httpx

from claude_hermes.modules.extensions.domain.manifest import MANIFEST_VERSION, parse
from claude_hermes.modules.extensions.presentation.dependencies import get_extensions
from claude_hermes.http.app import build_web_app

from tests.web_helpers import ORIGIN, json_headers, login, make_client

CONNECTOR = parse({"manifest_version": MANIFEST_VERSION, "name": "fake-api",
                   "label": "Fake API", "description": "  Nothing real.  ",
                   "version": "1", "transport": "http",
                   "url": "https://fake.example/mcp", "scopes": ["turn"]}, "mcp")

PLUGIN = parse({"manifest_version": MANIFEST_VERSION, "name": "fake-plugin",
                "label": "Fake Plugin", "description": "Nothing real either.",
                "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
                "scopes": ["turn"]}, "plugin")

CATALOG = {CONNECTOR.name: CONNECTOR, PLUGIN.name: PLUGIN}


class Boom:
    """Anything this stands in for must never be reached."""

    def __getattr__(self, name):
        def refuse(*_a, **_kw):
            raise AssertionError(f"the guard let the request reach {name}()")
        return refuse


def view(**over) -> SimpleNamespace:
    """An `ExtensionsDeps` by shape only — no database, no catalog on disk."""
    base = dict(
        catalog=SimpleNamespace(load_all=lambda root: dict(CATALOG),
                                get=lambda name, root=None: CATALOG.get(name)),
        catalog_root=None,
        store=SimpleNamespace(list=_empty, get=_none),
        secrets=SimpleNamespace(load=_no_secrets),
        extensions=SimpleNamespace(source_of=lambda m: "custom"),
        installer=Boom(), approvals=Boom(), drafts=Boom(), plugins=Boom(),
        home=None, invalidate=Boom(),
    )
    return SimpleNamespace(**{**base, **over})


async def _empty(kind, *, owner_id):
    return []


async def _no_secrets(*, owner_id):
    """`SecretStore.load` is async since Wave 3a and per owner since Wave 5; the shape
    of the fake has to say so."""
    return {}


async def _none(name, *, owner_id):
    return None


def recording_view(**over):
    """A `view()` whose collaborators record the `owner_id` they were asked for."""
    seen: dict[str, list] = {"list": [], "load": [], "invalidate": [], "remove": []}

    async def _list(kind=None, *, owner_id):
        seen["list"].append(owner_id)
        return []

    async def _load(*, owner_id):
        seen["load"].append(owner_id)
        return {}

    async def _get(name, *, owner_id):
        return None

    async def _invalidate(owner_id):
        seen["invalidate"].append(owner_id)

    async def _enable(name, *, owner_id):
        return True

    async def _remove(name, m=None, *, owner_id, remove_clone=False):
        seen["remove"].append((owner_id, remove_clone))
        return True

    async def _token_get(name, *, owner_id):
        return None

    async def _token_delete(name, *, owner_id):
        return None

    base = dict(
        store=SimpleNamespace(list=_list, get=_get),
        secrets=SimpleNamespace(load=_load),
        tokens=SimpleNamespace(get=_token_get, delete=_token_delete),
        # The clone is shared, so `present` is not a per-owner fact and needs no id.
        reader=SimpleNamespace(is_present=lambda home, m: False),
        extensions=SimpleNamespace(source_of=lambda m: "custom", enable=_enable,
                                   remove=_remove),
        invalidate=_invalidate,
    )
    return SimpleNamespace(**{**view().__dict__, **base, **over}), seen


async def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


async def app_with(tmp_path, fake, **kw):
    _c, deps = await make_client(tmp_path, **kw)
    app = build_web_app(deps)
    app.dependency_overrides[get_extensions] = lambda: fake
    return await client_for(app)


async def test_the_view_is_swappable_without_rebuilding_the_bundle(tmp_path):
    client = await app_with(tmp_path, view())
    login(client, 1)
    r = await client.get("/api/connectors")
    assert r.status_code == 200
    (c,) = r.json()["connectors"]
    # Straight off the fake: no catalog directory, no registry row, no secrets file.
    assert c["name"] == "fake-api" and c["source"] == "custom" and c["state"] == "available"
    assert c["description"] == "Nothing real." and c["secrets"] == []


async def test_overriding_the_view_does_not_open_the_owner_gate(tmp_path):
    """`require_owner` is its own dependency, and a dependency is solved before the
    handler runs — so a non-owner is refused before the catalog is even asked for the
    manifest.

    The three left here are the three that stay the owner's after Wave 5: creating a
    catalog entry, cloning a plugin and deleting a clone all write into something the
    whole deployment shares. Enabling a connector left this list — it acts on the
    caller's own row now, and `test_a_non_owner_may_enable_their_own_connector` is
    what pins that.
    """
    client = await app_with(tmp_path, view(catalog=Boom()), owner=9, allowed=[2])
    login(client, 2)                       # allowed to chat, not to install
    for path in ("/api/connectors", "/api/plugins/fake-api/install",
                 "/api/drafts/fake-api/review"):
        r = await client.post(path, headers=json_headers())
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", path
    r = await client.delete("/api/plugins/fake-api", headers=json_headers())
    assert r.status_code == 403


async def test_a_name_that_is_a_path_never_reaches_the_disk(tmp_path):
    r"""`DELETE /api/plugins/{name}` hands its name to an rmtree and the drafts routes
    hand theirs to an unlink. `valid_name` is what stands in between — the `installer`
    and `approvals` above raise if the request ever gets past it."""
    client = await app_with(tmp_path, view())
    login(client, 1)
    bad = "..\\..\\config"
    r = await client.delete(f"/api/plugins/{bad}", headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    for path in (f"/api/drafts/{bad}", "/api/drafts/Weather"):
        r = await client.delete(path, headers=json_headers())
        assert r.status_code == 404 and r.json()["error"]["code"] == "no_draft", path
    r = await client.post(f"/api/drafts/{bad}/review", headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "no_draft"


async def test_a_body_that_is_not_an_object_keeps_the_handler_s_own_code(tmp_path):
    """The leniency rule: `POST /api/drafts` answers `bad_manifest` for a body that is
    not an object, and a Pydantic model here would have made that `bad_request`."""
    client = await app_with(tmp_path, view())
    login(client, 1)
    r = await client.post("/api/drafts", headers=json_headers(), json=["not", "an object"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_manifest"


# --- Wave 5: whose connector is it -------------------------------------------


async def test_the_connector_list_is_scoped_to_the_caller(tmp_path):
    fake, seen = recording_view()
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    assert (await client.get("/api/connectors")).status_code == 200
    assert seen["list"] == [2] and seen["load"] == [2]


async def test_a_non_owner_may_enable_their_own_connector(tmp_path):
    """The gate that moved: enabling acts on the caller's row, so it is theirs."""
    fake, seen = recording_view()
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    r = await client.post("/api/connectors/fake-api/enable", headers=json_headers())
    assert r.status_code == 200
    assert seen["invalidate"] == [2]


async def test_deleting_a_connector_removes_the_callers_row(tmp_path):
    fake, seen = recording_view()
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    r = await client.delete("/api/connectors/fake-api", headers=json_headers())
    assert r.status_code == 204
    assert seen["remove"] == [(2, False)]


async def test_signing_in_is_not_owner_gated_any_more(tmp_path):
    """A second user must be able to start their OWN OAuth flow — the pending
    state has always been bound to the caller. Whatever this fake makes of the
    request, 403 is the one answer it must not be."""
    fake, _ = recording_view()
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    r = await client.post("/api/connectors/fake-api/oauth/start",
                          headers=json_headers())
    assert r.status_code != 403


async def test_a_non_owner_may_not_create_a_catalog_entry(tmp_path):
    """The one connectors route that is still the owner's: it writes a manifest
    into the shared `user_catalog/`."""
    fake, _ = recording_view()
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    r = await client.post("/api/connectors", headers=json_headers(), json={
        "name": "mine", "label": "Mine", "description": "x", "transport": "http",
        "url": "https://example.test/mcp"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


# --- Wave 5: a shared clone, a personal enablement ----------------------------


async def test_a_non_owner_may_not_install_or_delete_a_clone(tmp_path):
    """`git clone` puts code in the shared container; deleting takes it from
    everybody. Both stay the owner's."""
    fake, _ = recording_view(plugins=Boom(), installer=Boom())
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    assert (await client.post("/api/plugins/fake-plugin/install",
                              headers=json_headers())).status_code == 403
    assert (await client.delete("/api/plugins/fake-plugin",
                                headers=json_headers())).status_code == 403


async def test_enabling_a_plugin_adopts_the_shared_clone(tmp_path):
    """A second user has no row, so "enable" cannot be a state flip. It adopts."""
    adopted: list[int] = []

    async def _enable(name, *, owner_id):
        return False                      # no row of their own yet

    async def _adopt(m, *, user_id, now, owner_id):
        adopted.append(owner_id)
        return True

    fake, seen = recording_view()
    fake.extensions = SimpleNamespace(source_of=lambda m: "custom",
                                      enable=_enable, adopt=_adopt)
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    r = await client.post("/api/plugins/fake-plugin/enable", headers=json_headers())
    assert r.status_code == 200
    assert adopted == [2] and seen["invalidate"] == [2]


async def test_enabling_a_plugin_with_no_clone_is_404(tmp_path):
    async def _false(*_a, **_kw):
        return False

    fake, _ = recording_view()
    fake.extensions = SimpleNamespace(source_of=lambda m: "custom",
                                      enable=_false, adopt=_false)
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 2)
    r = await client.post("/api/plugins/fake-plugin/enable", headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_deleting_a_clone_clears_every_owners_row(tmp_path):
    """Leaving one behind would leave that user an enabled plugin pointing at a
    directory that is gone — an error on every one of their turns."""
    removed: list[tuple] = []
    cleared: list[int] = []

    async def _owners_of(name):
        return [9, 2]

    async def _get(name, *, owner_id):
        return {"name": name, "kind": "plugin", "state": "enabled"}

    async def _remove(name, m=None, *, owner_id, remove_clone=False):
        removed.append((owner_id, remove_clone))
        return True

    async def _invalidate(owner_id):
        cleared.append(owner_id)

    fake, _ = recording_view()
    fake.store = SimpleNamespace(list=fake.store.list, get=_get,
                                 owners_of=_owners_of)
    fake.extensions = SimpleNamespace(source_of=lambda m: "custom", remove=_remove)
    fake.installer = SimpleNamespace(remove=lambda name: removed.append(("clone", name)))
    fake.invalidate = _invalidate
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 9)                       # the owner
    r = await client.delete("/api/plugins/fake-plugin", headers=json_headers())
    assert r.status_code == 204
    assert removed == [(9, False), (2, False), ("clone", "fake-plugin")]
    assert cleared == [9, 2]


async def test_the_plugin_cascade_never_touches_a_connectors_rows(tmp_path):
    """`owners_of` answers ids for a name, blind to kind.

    A custom connector whose manifest the owner unlinked leaves rows behind under
    a name the plugins route will still accept (the `m is None` branch exists so a
    plugin whose manifest vanished stays removable). Without a per-row kind check
    the cascade would delete every user's CONNECTOR row — and `extension_service.
    remove` takes their OAuth token and mirrored secret with it. The pre-cascade
    code guarded this with `row["kind"] == "plugin"`; so does this.
    """
    removed: list[tuple] = []

    async def _owners_of(name):
        return [9, 2]

    async def _get(name, *, owner_id):
        return {"name": name, "kind": "mcp", "state": "enabled"}

    async def _remove(name, m=None, *, owner_id, remove_clone=False):
        removed.append((owner_id, remove_clone))
        return True

    fake, _ = recording_view()
    # `m is None`: the manifest is gone, which is the branch this guards.
    fake.catalog = SimpleNamespace(load_all=lambda root: {},
                                   get=lambda name, root=None: None)
    fake.store = SimpleNamespace(list=fake.store.list, get=_get,
                                 owners_of=_owners_of)
    fake.extensions = SimpleNamespace(source_of=lambda m: "custom", remove=_remove)
    fake.installer = Boom()                # the rmtree must not be reached either
    client = await app_with(tmp_path, fake, owner=9, allowed=[2])
    login(client, 9)
    r = await client.delete("/api/plugins/fake-api", headers=json_headers())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert removed == []

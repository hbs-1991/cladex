"""The connectors router: derived states, custom manifests, secrets → connect, OAuth
start/callback, owner vs user. The OAuth provider is faked on httpx.MockTransport."""
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import yaml

from claude_hermes.modules.extensions.infrastructure import catalog_repository as catalog
from claude_hermes.modules.extensions.infrastructure import oauth_client as oauth
from claude_hermes.modules.extensions.infrastructure.mcp_prober import ProbeResult
from tests.web_helpers import json_headers, login, make_client

MCP = "https://mcp.example.com/mcp"
AS = "https://auth.example.com"
REDIRECT = "https://cladex.test/api/connectors/oauth/callback"


class FakeProvider:
    """A resource server that 401s with resource_metadata, and an authorization
    server with metadata, registration, token and revocation endpoints."""

    def __init__(self, *, registration=True, well_known_only=False, refresh_ok=True):
        self.registration, self.well_known_only, self.refresh_ok = registration, well_known_only, refresh_ok
        self.token_requests: list[dict] = []
        self.revoked: list[str] = []

    def handle(self, req: httpx.Request) -> httpx.Response:
        url, path = str(req.url), req.url.path
        if url == MCP:
            hdr = "" if self.well_known_only else f'Bearer resource_metadata="{MCP.rsplit("/", 1)[0]}/.well-known/oauth-protected-resource/mcp"'
            return httpx.Response(401, headers={"WWW-Authenticate": hdr})
        if path.startswith("/.well-known/oauth-protected-resource") and req.url.host == "mcp.example.com":
            if self.well_known_only and path != "/.well-known/oauth-protected-resource":
                return httpx.Response(404)
            return httpx.Response(200, json={"resource": MCP, "authorization_servers": [AS], "scopes_supported": ["mcp:read"]})
        if path == "/.well-known/oauth-authorization-server" and req.url.host == "auth.example.com":
            meta = {"issuer": AS, "authorization_endpoint": f"{AS}/authorize", "token_endpoint": f"{AS}/token",
                    "revocation_endpoint": f"{AS}/revoke"}
            if self.registration:
                meta["registration_endpoint"] = f"{AS}/register"
            return httpx.Response(200, json=meta)
        if path == "/register":
            body = json.loads(req.content)
            assert body["redirect_uris"] == [REDIRECT] and body["token_endpoint_auth_method"] == "none"
            return httpx.Response(201, json={"client_id": "cid-1"})
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
            # RFC 8707: every token request — code exchange and refresh alike — names
            # the resource, and names the same one, or the token comes back scoped
            # to something else (or not at all).
            assert form.get("resource") == MCP, form
            self.token_requests.append(form)
            if form["grant_type"] == "refresh_token" and not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            if form["grant_type"] == "authorization_code" and form.get("code") != "good":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "at-" + form["grant_type"][:4], "refresh_token": "rt-1",
                                             "expires_in": 3600, "scope": "mcp:read", "token_type": "Bearer"})
        if path == "/revoke":
            self.revoked.append(parse_qs(req.content.decode())["token"][0])
            return httpx.Response(200)
        return httpx.Response(404)


def client_for(p: FakeProvider) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(p.handle))


HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp", "scopes": ["turn"],
    "auth": [{"name": "EXAMPLE_API_KEY", "prompt": "Example API key", "header": "Authorization",
              "format": "Bearer ${EXAMPLE_API_KEY}"}],
}
NOTION = {
    "manifest_version": 1, "name": "notion", "label": "Notion", "description": "d", "version": "1",
    "transport": "http", "url": MCP, "scopes": ["turn", "cron"],
    "auth": [{"name": "NOTION_MCP_TOKEN", "kind": "oauth", "header": "Authorization", "format": "Bearer ${NOTION_MCP_TOKEN}"}],
}


async def ok_probe(home, name, spec, timeout=60.0):
    return ProbeResult(status="connected", tools=["search", "create"])


async def setup(tmp_path, monkeypatch, *, owner=1, probe=ok_probe):
    """A repo catalog with two entries and an empty user-catalog under CH_HOME."""
    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")
    (root / "mcp" / "notion.yaml").write_text(yaml.safe_dump(NOTION), encoding="utf-8")
    monkeypatch.setattr(catalog, "_CATALOG_DIR", root)
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    client, deps = await make_client(tmp_path, owner=owner, allowed=[2], probe=probe)
    return client, deps


def secrets(deps):
    """The `SecretStore` the app itself uses. Wave 3a moved extension secrets out of
    `CH_HOME/secrets.env` and into `extension_secrets`, so a test that seeds or reads
    one goes through the same store the routers do."""
    return deps.extensions.secrets


async def test_the_list_derives_states_and_hides_values(tmp_path, monkeypatch):
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 2)                                           # a plain user may read
    r = await client.get("/api/connectors")
    assert r.status_code == 200
    by = {c["name"]: c for c in r.json()["connectors"]}
    assert by["example"]["state"] == "available" and by["example"]["secrets"] == [{"name": "EXAMPLE_API_KEY", "prompt": "Example API key", "set": False}]
    assert by["notion"]["auth"] == "oauth" and by["notion"]["source"] == "catalog"
    # User 2's own row and own key: since Wave 5 the list derives the CALLER's
    # state, so seeding the owner's would say nothing about what they see.
    await secrets(deps).put("EXAMPLE_API_KEY", "shh", owner_id=2)
    await deps.extensions.store.add("example", "mcp", "1.0.0", installed_by=2, now="2026-09-09T10:00:00+00:00", tool_count=4, owner_id=2)
    await deps.extensions.store.set_error("example", "needs_reauth", owner_id=2)
    r = await client.get("/api/connectors")
    ex = next(c for c in r.json()["connectors"] if c["name"] == "example")
    assert ex["state"] == "needs_reauth" and ex["tools"] == 4 and ex["secrets"][0]["set"] is True
    assert "shh" not in r.text


async def test_secrets_then_connect_registers(tmp_path, monkeypatch):
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await client.post("/api/connectors/example/connect", headers=json_headers())
    assert r.status_code == 400 and r.json()["error"]["code"] == "secrets_missing" and r.json()["error"]["missing"] == ["EXAMPLE_API_KEY"]
    r = await client.post("/api/connectors/example/secrets", headers=json_headers(), json={"EXAMPLE_API_KEY": "abc", "OTHER": "x"})
    assert r.status_code == 400                                # only declared names
    r = await client.post("/api/connectors/example/secrets", headers=json_headers(), json={"EXAMPLE_API_KEY": "abc"})
    assert r.status_code == 204 and (await secrets(deps).load(owner_id=1))["EXAMPLE_API_KEY"] == "abc"
    r = await client.post("/api/connectors/example/connect", headers=json_headers())
    assert r.status_code == 200 and r.json()["state"] == "connected" and r.json()["tools"] == 2
    assert r.json()["tool_names"] == ["search", "create"] and r.json()["endpoint"] == "https://mcp.example.com/mcp"
    r = await client.post("/api/connectors/example/test", headers=json_headers())
    assert r.json() == {"ok": True, "tools": ["search", "create"], "error": ""}
    assert (await client.post("/api/connectors/example/disable", headers=json_headers())).json()["state"] == "disabled"
    assert (await client.post("/api/connectors/example/enable", headers=json_headers())).json()["state"] == "connected"
    assert (await client.delete("/api/connectors/example", headers=json_headers())).status_code == 204
    assert await deps.extensions.store.get("example", owner_id=1) is None
    assert "EXAMPLE_API_KEY" not in await secrets(deps).load(owner_id=1)   # "credentials are forgotten"


async def test_only_writing_the_shared_catalog_is_owner_only(tmp_path, monkeypatch):
    """Wave 5 moved the gate. Connecting, setting a key and signing in act on the
    CALLER's own row and credentials, so they are every admitted user's; creating a
    connector writes a manifest into the shared `user_catalog/` and is not."""
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 2)
    for path in ("/api/connectors/example/connect", "/api/connectors/example/secrets",
                 "/api/connectors/notion/oauth/start"):
        r = await client.post(path, headers=json_headers(), json={})
        assert r.status_code != 403, path
    r = await client.post("/api/connectors", headers=json_headers(), json={"name": "x", "label": "X", "url": "https://x.example/mcp", "auth": "none"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


async def test_a_custom_connector_is_a_user_catalog_manifest(tmp_path, monkeypatch):
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    body = {"name": "my-api", "label": "My API", "url": "https://api.example.com/mcp", "auth": "header", "header": "X-Api-Key", "token": "k-1"}
    r = await client.post("/api/connectors", headers=json_headers(), json=body)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["source"] == "custom" and c["auth"] == "secret" and c["secrets"] == [{"name": "MY_API_TOKEN", "prompt": c["secrets"][0]["prompt"], "set": True}]
    m = catalog.get("my-api")
    assert m is not None and m.auth[0].header == "X-Api-Key" and m.auth[0].format == "${MY_API_TOKEN}"
    assert (tmp_path / "user-catalog" / "mcp" / "my-api.yaml").is_file()
    r = await client.post("/api/connectors", headers=json_headers(), json={**body, "name": "example"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "name_taken"
    r = await client.post("/api/connectors", headers=json_headers(), json={**body, "name": "Bad Name"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_manifest"
    r = await client.post("/api/connectors", headers=json_headers(), json={**body, "name": "plain", "url": "http://insecure"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_manifest"
    r = await client.post("/api/connectors", headers=json_headers(), json={"name": "oa", "label": "OA", "url": "https://oa.example/mcp", "auth": "oauth", "client_id": "cid-9", "client_secret": "s"})
    assert r.status_code == 201 and r.json()["auth"] == "oauth"
    assert (await deps.extensions.tokens.get("oa", owner_id=1))["client_id"] == "cid-9"
    assert (await client.delete("/api/connectors/oa", headers=json_headers())).status_code == 204
    assert catalog.get("oa") is None                         # a custom entry's manifest goes too


async def test_oauth_start_and_callback(tmp_path, monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(oauth, "make_client", lambda: client_for(p))
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await client.post("/api/connectors/notion/connect", headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "oauth_required"
    r = await client.post("/api/connectors/notion/oauth/start", headers=json_headers())
    assert r.status_code == 200, r.text
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.json()["url"]).query).items()}
    assert q["client_id"] == "cid-1" and q["redirect_uri"] == "https://cladex.test/api/connectors/oauth/callback"
    assert (await deps.extensions.tokens.get("notion", owner_id=1))["client_id"] == "cid-1"
    r = await client.get("/api/connectors/oauth/callback", params={"code": "good", "state": q["state"]})
    assert r.status_code == 302 and r.headers["location"] == "/customize/connectors?connected=notion"
    row = await deps.extensions.tokens.get("notion", owner_id=1)
    assert row["access_token"] == "at-auth" and row["refresh_token"] == "rt-1" and row["issuer"] == AS
    assert (await secrets(deps).load(owner_id=1))["NOTION_MCP_TOKEN"] == "at-auth"
    assert (await deps.extensions.store.get("notion", owner_id=1))["state"] == "enabled"
    # The callback finished a connect, so it owes the same reset the POST route
    # does — the CALLER's lanes, and the deploy owner's carry the unowned ones.
    assert deps.runner.reset_owneds == [(1, True)]
    r = await client.get("/api/connectors/oauth/callback", params={"code": "good", "state": q["state"]})
    assert r.headers["location"] == "/customize/connectors?error=state_mismatch"    # single use
    r = await client.get("/api/connectors/oauth/callback", params={"error": "access_denied", "state": "x"})
    assert r.headers["location"] == "/customize/connectors?error=access_denied"
    r = await client.delete("/api/connectors/notion", headers=json_headers())
    assert r.status_code == 204 and p.revoked == ["rt-1"] and await deps.extensions.tokens.get("notion", owner_id=1) is None


async def test_a_failed_probe_keeps_the_sign_in_and_disconnect_forgets_it(tmp_path, monkeypatch):
    """The tokens survive a failed probe so Connect can retry without a second
    consent screen — and Disconnect then forgets them, registry row or not."""
    async def failing_probe(home, name, spec, timeout=60.0):
        return ProbeResult(status="failed", error="the server did not answer")

    monkeypatch.setattr(oauth, "make_client", lambda: client_for(FakeProvider()))
    client, deps = await setup(tmp_path, monkeypatch, probe=failing_probe)
    login(client, 1)
    r = await client.post("/api/connectors/notion/oauth/start", headers=json_headers())
    state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
    r = await client.get("/api/connectors/oauth/callback", params={"code": "good", "state": state})
    assert r.headers["location"] == "/customize/connectors?error=probe_failed"
    assert (await secrets(deps).load(owner_id=1))["NOTION_MCP_TOKEN"] == "at-auth"
    assert await deps.extensions.store.get("notion", owner_id=1) is None   # nothing was installed
    assert deps.runner.reset_owneds == []                       # ...so no prefix changed
    assert (await client.delete("/api/connectors/notion", headers=json_headers())).status_code == 204
    assert "NOTION_MCP_TOKEN" not in await secrets(deps).load(owner_id=1)
    assert await deps.extensions.tokens.get("notion", owner_id=1) is None


async def test_a_callback_without_a_session_lands_on_the_page(tmp_path, monkeypatch):
    """An expired cookie (or another browser profile) must not answer a top-level
    navigation with a JSON 401."""
    client, _ = await setup(tmp_path, monkeypatch)
    r = await client.get("/api/connectors/oauth/callback", params={"code": "x", "state": "y"})
    assert r.status_code == 302 and r.headers["location"] == "/customize/connectors?error=forbidden"
    login(client, 2)                        # signed in: their own flow, not the owner's
    r = await client.get("/api/connectors/oauth/callback", params={"code": "x", "state": "y"})
    # `take(state, owner_id)` refuses a state that is not theirs, and nobody issued
    # this one — so the page says the state did not match, never 403.
    assert r.status_code == 302 and r.headers["location"] == "/customize/connectors?error=state_mismatch"


async def test_oauth_start_reports_registration_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "make_client", lambda: client_for(FakeProvider(registration=False)))
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    r = await client.post("/api/connectors/notion/oauth/start", headers=json_headers())
    assert r.status_code == 502 and r.json()["error"]["code"] == "registration_unsupported"
    await deps.extensions.tokens.put("notion", owner_id=1, now="2026-09-09T10:00:00+00:00", client_id="manual")
    r = await client.post("/api/connectors/notion/oauth/start", headers=json_headers())
    assert r.status_code == 200 and "client_id=manual" in r.json()["url"]  # Advanced fields skip registration


async def test_a_prefix_changing_mutation_resets_every_lane(tmp_path, monkeypatch):
    """Connecting from the browser changes the toolset, which lives in the cached
    prompt prefix — so every lane must start a fresh session. Reads and the probe
    change nothing and must not reset."""
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 1)
    await client.get("/api/connectors")
    assert deps.runner.reset_owneds == []
    await secrets(deps).put("EXAMPLE_API_KEY", "abc", owner_id=1)
    r = await client.post("/api/connectors/example/connect", headers=json_headers())
    assert r.status_code == 200 and deps.runner.reset_owneds == [(1, True)]
    await client.post("/api/connectors/example/test", headers=json_headers())
    assert len(deps.runner.reset_owneds) == 1                    # a probe installs nothing
    assert (await client.post("/api/connectors/example/disable", headers=json_headers())).status_code == 200
    assert (await client.post("/api/connectors/example/enable", headers=json_headers())).status_code == 200
    assert len(deps.runner.reset_owneds) == 3
    assert (await client.delete("/api/connectors/example", headers=json_headers())).status_code == 204
    # Four mutations, every one of them the caller's own lanes and nobody else's.
    assert deps.runner.reset_owneds == [(1, True)] * 4
    assert deps.runner.reset_alls == 0


async def test_a_mutation_that_changed_nothing_resets_nothing(tmp_path, monkeypatch):
    """A refusal is not a prefix change. Since Wave 5 a plain user is refused for
    having no row rather than for not being the owner, and 404 is as quiet as 403.

    `DELETE` is not in the list: it is idempotent by design — "the credentials are
    forgotten" has to hold when a failed probe left a token and no row — so it
    answers 204 and does reset, whoever asks."""
    client, deps = await setup(tmp_path, monkeypatch)
    login(client, 2)                                            # a plain user
    for path in ("/api/connectors/example/enable", "/api/connectors/example/disable"):
        assert (await client.post(path, headers=json_headers())).status_code == 404
    login(client, 1)
    r = await client.post("/api/connectors/example/connect", headers=json_headers())
    assert r.status_code == 400                                 # secrets_missing, nothing installed
    assert deps.runner.reset_owneds == [] and deps.runner.reset_alls == 0

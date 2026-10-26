"""The OAuth client against a fake authorization server on httpx.MockTransport."""
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from claude_hermes.modules.extensions.application.services.oauth_service import (
    OAuthService,
)
from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.infrastructure import oauth_client as oauth
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (
    SqlOauthTokenRepository,
)

from conftest import ensure_users
from tests.ext_helpers import secrets_for

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


async def test_discovery_from_the_401_and_registration():
    p = FakeProvider()
    async with client_for(p) as c:
        d = await oauth.discover(c, MCP)
        assert d.issuer == AS and d.token_endpoint == f"{AS}/token" and d.resource == MCP and d.scopes == ("mcp:read",)
        assert await oauth.register(c, d, REDIRECT) == ("cid-1", "")


async def test_discovery_falls_back_to_the_well_known_resource_document():
    async with client_for(FakeProvider(well_known_only=True)) as c:
        d = await oauth.discover(c, MCP)
        assert d.authorization_endpoint == f"{AS}/authorize"


async def test_discovery_probes_with_initialize_when_get_is_refused():
    """A streamable-HTTP server may keep GET for the server-to-client stream and
    refuse it outright — Meta's `mcp.facebook.com/devtools` answers 405 — so the
    challenge only comes back from the `initialize` POST a real client sends first."""
    p, posted = FakeProvider(), []

    def handle(req: httpx.Request) -> httpx.Response:
        if str(req.url) == MCP:
            if req.method == "GET":
                return httpx.Response(405)
            posted.append(json.loads(req.content))
        return p.handle(req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        d = await oauth.discover(c, MCP)
    assert d.issuer == AS and d.resource == MCP
    assert posted and posted[0]["method"] == "initialize"


async def test_registration_unsupported_is_its_own_code():
    async with client_for(FakeProvider(registration=False)) as c:
        d = await oauth.discover(c, MCP)
        with pytest.raises(oauth.OAuthError) as e:
            await oauth.register(c, d, REDIRECT)
        assert e.value.code == "registration_unsupported"


async def test_discovery_failure_when_nothing_answers():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))) as c:
        with pytest.raises(oauth.OAuthError) as e:
            await oauth.discover(c, MCP)
        assert e.value.code == "discovery_failed"


async def test_a_non_https_authorization_server_is_refused():
    """The resource server picks the authorization server; a compromised one must not
    be able to send the client secret — or the owner's browser — to an internal host."""
    def handle(req: httpx.Request) -> httpx.Response:
        if str(req.url) == MCP:
            return httpx.Response(401, headers={"WWW-Authenticate": ""})
        if req.url.path.startswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(200, json={"resource": MCP,
                                             "authorization_servers": ["http://169.254.169.254"]})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        with pytest.raises(oauth.OAuthError) as e:
            await oauth.discover(c, MCP)
        assert e.value.code == "discovery_failed" and "169.254.169.254" in e.value.message


def test_authorize_url_carries_pkce_state_and_resource():
    d = oauth.Discovery(issuer=AS, authorization_endpoint=f"{AS}/authorize?tenant=1", token_endpoint=f"{AS}/token", resource=MCP)
    verifier, challenge = oauth.pkce_pair()
    url = oauth.authorize_url(d, client_id="cid-1", redirect_uri=REDIRECT, state="st", challenge=challenge, scope="mcp:read")
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert q["tenant"] == "1" and q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert q["code_challenge"] == challenge and q["state"] == "st" and q["resource"] == MCP and q["scope"] == "mcp:read"
    assert len(verifier) >= 43


async def test_exchange_refresh_and_revoke():
    p = FakeProvider()
    now = 1_800_000_000.0
    async with client_for(p) as c:
        d = await oauth.discover(c, MCP)
        ts = await oauth.exchange(c, d, code="good", verifier="v" * 43, client_id="cid-1", client_secret="", redirect_uri=REDIRECT, now=now)
        assert ts.access_token == "at-auth" and ts.refresh_token == "rt-1" and ts.expires_at == oauth.iso(now + 3600)
        assert p.token_requests[-1]["code_verifier"] == "v" * 43 and p.token_requests[-1]["resource"] == MCP
        ts2 = await oauth.refresh(c, d, refresh_token="rt-1", client_id="cid-1", client_secret="", now=now)
        assert ts2.access_token == "at-refr"
        with pytest.raises(oauth.OAuthError) as e:
            await oauth.exchange(c, d, code="bad", verifier="v" * 43, client_id="cid-1", client_secret="", redirect_uri=REDIRECT, now=now)
        assert e.value.code == "exchange_failed"
        await oauth.revoke(c, d, "rt-1", "cid-1", "")
        assert p.revoked == ["rt-1"]


async def test_pending_states_are_single_use_owner_bound_and_expire():
    ps = oauth.PendingStates()
    st = await ps.issue("notion", owner_id=1, verifier="v", now=100.0)
    assert await ps.take(st, owner_id=2, now=101.0) is None    # someone else's state
    got = await ps.take(st, owner_id=1, now=101.0)
    assert got.name == "notion" and got.verifier == "v"
    assert await ps.take(st, owner_id=1, now=102.0) is None    # used up
    st2 = await ps.issue("notion", owner_id=1, verifier="v", now=100.0)
    assert await ps.take(st2, owner_id=1, now=100.0 + oauth.PENDING_TTL + 1) is None


OAUTH_MANIFEST = {
    "manifest_version": 1, "name": "notion", "label": "Notion", "description": "d", "version": "1",
    "transport": "http", "url": MCP, "scopes": ["turn"],
    "auth": [{"name": "NOTION_MCP_TOKEN", "kind": "oauth", "header": "Authorization", "format": "Bearer ${NOTION_MCP_TOKEN}"}],
}


def refresher(engine) -> OAuthService:
    """The refresh path as `wiring.build` assembles it: the service over the http wire,
    which resolves `oauth.make_client` at call time, mirroring into the sealed secrets."""
    return OAuthService(wire=oauth.http_wire, secrets=secrets_for(engine))


async def test_ensure_fresh_refreshes_an_expiring_token_and_mirrors_it(tmp_path, engine, monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(oauth, "make_client", lambda: client_for(p))
    await ensure_users(engine, owner=1)
    tokens = SqlOauthTokenRepository(engine)
    now = 1_800_000_000.0
    await tokens.put("notion", owner_id=1, now=oauth.iso(now), issuer=AS, client_id="cid-1", access_token="old", refresh_token="rt-1",
                     expires_at=oauth.iso(now + 30))
    m = parse(OAUTH_MANIFEST, "mcp")
    await refresher(engine).ensure_fresh(tmp_path, tokens, m, owner_id=1, now=now)
    assert (await tokens.get("notion", owner_id=1))["access_token"] == "at-refr"
    assert (await secrets_for(engine).load(owner_id=1))["NOTION_MCP_TOKEN"] == "at-refr"
    p.token_requests.clear()
    await refresher(engine).ensure_fresh(tmp_path, tokens, m, owner_id=1, now=now)     # fresh now: no request
    assert p.token_requests == []


async def test_a_second_owners_grant_is_refreshed_on_its_own_lock_and_row(tmp_path, engine, monkeypatch):
    """Two people signed into one connector hold two grants. The lock name carries
    the owner (`<owner_id>:<name>`), so neither queues behind the other and neither
    refresh lands on the other's row."""
    p = FakeProvider()
    monkeypatch.setattr(oauth, "make_client", lambda: client_for(p))
    await ensure_users(engine, 7, owner=1)
    tokens = SqlOauthTokenRepository(engine)
    now = 1_800_000_000.0
    for uid in (1, 7):
        await tokens.put("notion", owner_id=uid, now=oauth.iso(now), issuer=AS,
                         client_id="cid-1", access_token=f"old-{uid}",
                         refresh_token="rt-1", expires_at=oauth.iso(now + 30))
    m = parse(OAUTH_MANIFEST, "mcp")
    taken: list[str] = []

    class _Recording:
        """A `RefreshLocks` that records the name it was asked for. The port is
        unchanged by Wave 5 — the owner is in the name the caller composes, and
        this is what says so."""

        def __init__(self, name):
            taken.append(name)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    service = OAuthService(wire=oauth.http_wire, secrets=secrets_for(engine),
                           locks=_Recording)
    await service.ensure_fresh(tmp_path, tokens, m, owner_id=7, now=now)
    assert taken == ["7:notion"]
    assert (await tokens.get("notion", owner_id=7))["access_token"] == "at-refr"
    assert (await tokens.get("notion", owner_id=1))["access_token"] == "old-1"
    assert await secrets_for(engine).load(owner_id=1) == {}


async def test_ensure_fresh_raises_needs_reauth_when_refresh_is_refused_or_impossible(tmp_path, engine, monkeypatch):
    p = FakeProvider(refresh_ok=False)
    monkeypatch.setattr(oauth, "make_client", lambda: client_for(p))
    await ensure_users(engine, owner=1)
    tokens = SqlOauthTokenRepository(engine)
    m = parse(OAUTH_MANIFEST, "mcp")
    now = 1_800_000_000.0
    with pytest.raises(oauth.NeedsReauth):
        await refresher(engine).ensure_fresh(tmp_path, tokens, m, owner_id=1, now=now)         # never signed in
    await tokens.put("notion", owner_id=1, now=oauth.iso(now), issuer=AS, client_id="cid-1", access_token="old", refresh_token="rt-1",
                     expires_at=oauth.iso(now - 1))
    with pytest.raises(oauth.NeedsReauth):
        await refresher(engine).ensure_fresh(tmp_path, tokens, m, owner_id=1, now=now)         # refresh refused

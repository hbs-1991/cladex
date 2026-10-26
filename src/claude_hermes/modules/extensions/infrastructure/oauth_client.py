"""OAuth 2.1 for remote MCP servers, run by the hermes process.

The bundled CLI has an OAuth client of its own, but it is interactive and listens
on localhost inside the container; a flow that starts in the owner's browser has to
be completed here. This module does discovery (RFC 9728 -> RFC 8414), dynamic client
registration (RFC 7591), PKCE, the code exchange, refresh and revocation. Token values
never reach a log.

The use case behind a refresh is `application/services/oauth_service.py`; this module
is its wire (`HttpOAuthWire` over httpx, handed to `OAuthService` as the `http_wire`
factory by `wiring.build`). Tests patch `make_client`, and every function below
resolves it through this module at call time. The domain's value objects and
`PendingStates` are re-exported so a caller of the wire has one import.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
import structlog

from ..domain.oauth import (CLIENT_NAME, PENDING_TTL, REFRESH_MARGIN, Discovery,
                            NeedsReauth, OAuthError, Pending, TokenSet, _origin,
                            _require_https, _www_authenticate, authorize_url, iso,
                            pkce_pair)
from .pending_states import PendingStates

log = structlog.get_logger(__name__)

__all__ = [
    "CLIENT_NAME", "PENDING_TTL", "REFRESH_MARGIN", "Discovery", "HttpOAuthWire",
    "NeedsReauth", "OAuthError", "Pending", "PendingStates", "TokenSet",
    "authorize_url", "discover", "discover_issuer", "exchange", "http_wire", "iso",
    "make_client", "pkce_pair", "refresh", "register", "revoke",
]


def make_client() -> httpx.AsyncClient:
    """One place to build the HTTP client, so tests can hand in a MockTransport."""
    return httpx.AsyncClient(timeout=15.0, follow_redirects=False)


async def _json(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        body = r.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def discover_issuer(client: httpx.AsyncClient, issuer: str, resource: str = "",
                          scopes: tuple[str, ...] = ()) -> Discovery:
    """RFC 8414 metadata for an authorization server, path-aware then plain."""
    _require_https(issuer, "the authorization server")
    p = urlsplit(issuer)
    origin, path = f"{p.scheme}://{p.netloc}", p.path.rstrip("/")
    candidates = [f"{origin}/.well-known/oauth-authorization-server{path}",
                  f"{origin}/.well-known/openid-configuration{path}"]
    if path:
        candidates.append(f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server")
    for url in candidates:
        meta = await _json(client, url)
        if meta and "authorization_endpoint" in meta and "token_endpoint" in meta:
            disc = Discovery(
                issuer=str(meta.get("issuer") or issuer),
                authorization_endpoint=str(meta["authorization_endpoint"]),
                token_endpoint=str(meta["token_endpoint"]),
                registration_endpoint=str(meta.get("registration_endpoint") or ""),
                revocation_endpoint=str(meta.get("revocation_endpoint") or ""),
                resource=resource, scopes=scopes,
            )
            for what, endpoint in (("the authorization endpoint", disc.authorization_endpoint),
                                   ("the token endpoint", disc.token_endpoint),
                                   ("the registration endpoint", disc.registration_endpoint),
                                   ("the revocation endpoint", disc.revocation_endpoint)):
                if endpoint:
                    _require_https(endpoint, what)
            return disc
    raise OAuthError("discovery_failed", f"no authorization server metadata at {issuer}")


_INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": CLIENT_NAME, "version": "1"}},
}


async def _challenge(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """The 401 that names the authorization server, from whichever request draws one.

    A plain GET is the cheap probe and most servers challenge on it. A streamable-HTTP
    server that keeps GET for the server-to-client stream may refuse it outright
    instead (Meta's `mcp.facebook.com/devtools` answers 405), and only an `initialize`
    POST — what a real MCP client sends first — gets the challenge. A 200 means the
    server never asked for authorization, and that is the caller's to refuse, so it is
    handed back as it is rather than re-probed."""
    headers = {"Accept": "application/json, text/event-stream"}
    try:
        r = await client.get(url, headers=headers)
        if r.status_code not in (200, 401):
            r = await client.post(url, json=_INITIALIZE, headers=headers)
    except httpx.HTTPError as e:
        raise OAuthError("discovery_failed", f"{url}: {e}") from e
    return r


async def discover(client: httpx.AsyncClient, url: str) -> Discovery:
    """From the MCP URL to the authorization server: the 401's resource_metadata,
    else the well-known protected-resource document, else the URL's own origin."""
    r = await _challenge(client, url)
    if r.status_code != 401:
        raise OAuthError("discovery_failed",
                         f"{url} answered {r.status_code} without asking for authorization")
    metadata_url = _www_authenticate(r.headers.get("www-authenticate", "")).get("resource_metadata")
    if metadata_url:
        _require_https(metadata_url, "the resource metadata")
    p = urlsplit(url)
    origin = _origin(url)
    candidates = [metadata_url] if metadata_url else []
    candidates += [f"{origin}/.well-known/oauth-protected-resource{p.path.rstrip('/')}",
                   f"{origin}/.well-known/oauth-protected-resource"]
    resource, issuer, scopes = url, origin, ()
    for cand in candidates:
        doc = await _json(client, cand)
        if doc and doc.get("authorization_servers"):
            resource = str(doc.get("resource") or url)
            issuer = str(doc["authorization_servers"][0])
            scopes = tuple(doc.get("scopes_supported") or ())
            break
    return await discover_issuer(client, issuer, resource, scopes)


async def register(client: httpx.AsyncClient, disc: Discovery, redirect_uri: str) -> tuple[str, str]:
    """Dynamic client registration as a public client. Returns (client_id, client_secret)."""
    if not disc.registration_endpoint:
        raise OAuthError("registration_unsupported",
                         f"{disc.issuer} does not register clients dynamically")
    try:
        r = await client.post(disc.registration_endpoint, json={
            "client_name": CLIENT_NAME, "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        })
    except httpx.HTTPError as e:
        raise OAuthError("registration_unsupported", str(e)) from e
    if r.status_code not in (200, 201):
        raise OAuthError("registration_unsupported", f"{r.status_code}: {r.text[:200]}")
    body = r.json()
    if "client_id" not in body:
        raise OAuthError("registration_unsupported", "no client_id in the registration reply")
    return str(body["client_id"]), str(body.get("client_secret") or "")


async def _token_request(client: httpx.AsyncClient, disc: Discovery, data: dict, *,
                         client_id: str, client_secret: str, now: float, code: str) -> TokenSet:
    data = {**data, "client_id": client_id}
    if disc.resource:
        data["resource"] = disc.resource
    auth = (client_id, client_secret) if client_secret else None
    try:
        r = await client.post(disc.token_endpoint, data=data, auth=auth,
                              headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise OAuthError(code, str(e)) from e
    if r.status_code != 200:
        raise OAuthError(code, f"{r.status_code}: {r.text[:200]}")
    body = r.json()
    if "access_token" not in body:
        raise OAuthError(code, "no access_token in the token reply")
    expires_in = body.get("expires_in")
    return TokenSet(
        access_token=str(body["access_token"]),
        refresh_token=str(body.get("refresh_token") or ""),
        expires_at=iso(now + float(expires_in)) if expires_in else "",
        scope=str(body.get("scope") or ""),
    )


async def exchange(client: httpx.AsyncClient, disc: Discovery, *, code: str, verifier: str,
                   client_id: str, client_secret: str, redirect_uri: str, now: float) -> TokenSet:
    return await _token_request(client, disc, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }, client_id=client_id, client_secret=client_secret, now=now, code="exchange_failed")


async def refresh(client: httpx.AsyncClient, disc: Discovery, *, refresh_token: str,
                  client_id: str, client_secret: str, now: float) -> TokenSet:
    return await _token_request(client, disc, {
        "grant_type": "refresh_token", "refresh_token": refresh_token,
    }, client_id=client_id, client_secret=client_secret, now=now, code="needs_reauth")


async def revoke(client: httpx.AsyncClient, disc: Discovery, token: str,
                 client_id: str, client_secret: str) -> None:
    """Best effort: a provider that refuses still loses the token on our side."""
    if not disc.revocation_endpoint or not token:
        return
    try:
        await client.post(disc.revocation_endpoint, data={"token": token, "client_id": client_id},
                          auth=(client_id, client_secret) if client_secret else None)
    except httpx.HTTPError:
        log.info("revocation at %s failed; the token is dropped locally", disc.issuer)


class HttpOAuthWire:
    """The `OAuthWire` port over one open client. Every call goes through this module's
    functions, so a test that replaces `make_client` replaces what this dials."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def discover_issuer(self, issuer: str, resource: str = "",
                              scopes: tuple[str, ...] = ()) -> Discovery:
        return await discover_issuer(self._client, issuer, resource, scopes)

    async def refresh(self, disc: Discovery, *, refresh_token: str, client_id: str,
                      client_secret: str, now: float) -> TokenSet:
        return await refresh(self._client, disc, refresh_token=refresh_token,
                             client_id=client_id, client_secret=client_secret, now=now)


@asynccontextmanager
async def http_wire():
    """`OAuthService`'s `wire` factory: one client per refresh, closed on the way out.
    Resolves `make_client` at call time, so a patched client reaches a refresh too."""
    async with make_client() as client:
        yield HttpOAuthWire(client)

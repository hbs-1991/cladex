"""The OAuth 2.1 values and the pure rules over them. The wire work (httpx) is
`infrastructure/oauth_client.py`; the "is this token still good" use case is
`application/services/oauth_service.py`."""
from __future__ import annotations

import base64
import hashlib
import re
import secrets as pysecrets
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

PENDING_TTL = 600           # seconds a started flow waits for the callback
REFRESH_MARGIN = 60         # refresh when the token expires within this many seconds
CLIENT_NAME = "CladeX"


class OAuthError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code, self.message = code, message or code


class NeedsReauth(Exception):
    """The stored token cannot be made valid without the owner signing in again."""


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str = ""
    revocation_endpoint: str = ""
    resource: str = ""
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str = ""
    expires_at: str = ""
    scope: str = ""


@dataclass
class Pending:
    name: str
    owner_id: int
    verifier: str
    expires: float


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _require_https(url: str, what: str) -> None:
    """The resource server names these URLs, and we then POST the client secret and
    the refresh token to them and send the owner's browser there — so a compromised
    or careless server must not be able to point us at plain http or an internal
    address. The MCP auth spec requires https; localhost stays open for local dev."""
    p = urlsplit(url)
    if p.scheme == "https" or (p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1")):
        return
    raise OAuthError("discovery_failed", f"{what} is not https: {url}")


_PARAM_RE = re.compile(r'(\w+)="([^"]*)"')


def _www_authenticate(header: str) -> dict[str, str]:
    return dict(_PARAM_RE.findall(header))


def pkce_pair() -> tuple[str, str]:
    verifier = pysecrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def new_state() -> str:
    """The opaque `state` a started flow is keyed by. Here rather than in the store so
    the CSRF value and the PKCE verifier come from the same generator."""
    return pysecrets.token_urlsafe(32)


def authorize_url(disc: Discovery, *, client_id: str, redirect_uri: str, state: str,
                  challenge: str, scope: str = "") -> str:
    q = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
         "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if disc.resource:
        q["resource"] = disc.resource
    if scope:
        q["scope"] = scope
    sep = "&" if "?" in disc.authorization_endpoint else "?"
    return disc.authorization_endpoint + sep + urlencode(q)

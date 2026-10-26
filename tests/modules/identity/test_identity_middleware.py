"""core.auth.CurrentUserMiddleware — built by identity's stream, wired in Phase C."""
from types import SimpleNamespace

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from claude_hermes.core.auth import CurrentUserMiddleware
from claude_hermes.modules.identity.domain.session_token import COOKIE, read_session, sign_session
from claude_hermes.modules.identity.presentation.router import PUBLIC_PATHS

SECRET = "unit-secret"
NOW = 1_700_000_000


async def _me(request):
    return JSONResponse({"user_id": request.state.user_id,
                         "role": "owner" if request.state.is_owner else "user"})


async def _open(request):
    return JSONResponse({"ok": True})


def make_client(owner=1, redirect=None):
    seen = SimpleNamespace(owner_checks=[])

    async def is_owner(uid):
        seen.owner_checks.append(uid)
        return uid == owner

    app = Starlette(routes=[Route("/api/me", _me), Route("/api/config", _open),
                            Route("/api/connectors/oauth/callback", _open),
                            Route("/health", _open)])
    app.add_middleware(
        CurrentUserMiddleware,
        read_session=lambda r: read_session(r.cookies.get(COOKIE, ""), SECRET, NOW),
        is_owner=is_owner,
        public_paths=PUBLIC_PATHS,
        redirect_on_401=redirect or {},
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                             base_url="https://cladex.test"), seen


async def test_cookie_becomes_request_state():
    client, seen = make_client()
    r = await client.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "login required"}}
    client.cookies.set(COOKIE, sign_session(1, NOW + 3600, SECRET))
    r = await client.get("/api/me")
    assert r.status_code == 200 and r.json() == {"user_id": 1, "role": "owner"}
    assert seen.owner_checks == [1]


async def test_public_paths_and_non_api_paths_skip_the_check():
    client, seen = make_client()
    assert (await client.get("/api/config")).status_code == 200
    assert (await client.get("/health")).status_code == 200
    assert seen.owner_checks == []


async def test_a_browser_navigation_is_redirected_not_json_errored():
    path = "/api/connectors/oauth/callback"
    client, _ = make_client(redirect={path: "/customize/connectors?error=forbidden"})
    r = await client.get(path)
    assert r.status_code == 302
    assert r.headers["location"] == "/customize/connectors?error=forbidden"

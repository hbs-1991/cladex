"""identity's auth router, mounted on a bare FastAPI app with a fake bundle.

Wave 2b: `auth_routes(deps)` is a module-level `router: APIRouter` and the bundle it
used to close over lives on `app.state.deps`, where `core.di.provide` reads it per
request. The app below is the smallest thing that can serve the router — no
middleware, no database, no SPA — so every assertion here is about the router itself.
The end-to-end versions (cookie gate, Origin check, real ACL) are in
`tests/test_web_app.py`, which passed unmodified across the conversion.
"""
import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import urlencode

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse

from claude_hermes.core.auth import CurrentUser, current_user
from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.modules.identity.domain.exceptions import UnknownUser
from claude_hermes.modules.identity.domain.session_token import sign_session
from claude_hermes.modules.identity.domain.telegram_proof import login_key, webapp_key
from claude_hermes.modules.identity.presentation.dependencies import get_acl
from claude_hermes.modules.identity.presentation.router import (
    PUBLIC_PATHS, is_local_origin, router,
)

TOKEN = "123:ABC"
SECRET = "unit-secret"
NOW = 1_700_000_000


class FakeAcl:
    """Records the chat_type the routes claim with — the security invariant."""

    def __init__(self, owner=1, allowed=(), *, claims=False, unknown=()):
        self.owner = owner
        self.allowed = set(allowed)
        self.claims = claims
        self.checks = []
        self.profiles = []
        self.defaults: dict[int, tuple] = {}
        self.unknown = set(unknown)     # ids whose `users` row is missing

    async def check(self, user_id, chat_type, profile=None):
        self.checks.append((user_id, chat_type))
        # Wave 4a: the widget's signed first_name/last_name/username reach the
        # `users` row through here. Recorded, never part of the decision.
        self.profiles.append(profile)
        if self.owner is None:
            if self.claims and chat_type == "private":
                self.owner = user_id
                return "claimed"
            return "deny"
        return "allow" if user_id == self.owner or user_id in self.allowed else "deny"

    async def is_owner(self, user_id):
        return self.owner is not None and user_id == self.owner

    async def user(self, user_id):
        """`/api/me` reads the row for its two display fields; this fake has no
        table, so nobody has a name — which is the `""` the route documents."""
        return None

    async def default_of(self, user_id):
        return self.defaults.get(user_id, (None, None))

    async def set_default(self, user_id, model, effort):
        if user_id in self.unknown:
            raise UnknownUser(f"no users row for {user_id}")
        pair = (model, effort) if model else (None, None)
        self.defaults[user_id] = pair
        return pair


def build_app(deps) -> FastAPI:
    """The router plus the two exception handlers the shell registers.

    `app.state.deps` is what `core.di.bundle` returns, so the providers in
    `presentation/dependencies.py` resolve exactly as they do in the real app.
    """
    app = FastAPI()
    app.state.deps = deps
    app.include_router(router)

    async def _render(request, exc: ApplicationException):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}},
                            status_code=exc.status)

    async def _invalid(request, exc: RequestValidationError):
        """The shell's shape, mirrored here so a body the router was supposed to
        answer itself cannot pass unnoticed: without it FastAPI's own 422 would
        stand in for the 400 the real app returns, and neither is a login answer."""
        return JSONResponse({"error": {"code": "bad_request", "message": "invalid"}},
                            status_code=400)

    app.add_exception_handler(ApplicationException, _render)
    app.add_exception_handler(RequestValidationError, _invalid)
    return app


def make_client(acl=None, origin="https://cladex.test", webapp=True, *, user=None):
    deps = SimpleNamespace(
        acl=acl or FakeAcl(),
        # The three model knobs are what `PUT /api/me/model-default` validates
        # against: `config.yaml`'s offer, the deploy default, the review fork's.
        cfg=SimpleNamespace(web_session_days=7, web_secret=SECRET, web_public_origin=origin,
                            model="claude-sonnet-5", review_model="",
                            models=["claude-sonnet-5", "claude-opus-5-5"]),
        now=lambda: NOW,
        login_key=login_key(TOKEN),
        webapp_key=webapp_key(TOKEN) if webapp else b"",
        bot_username="cladex_bot",
    )
    app = build_app(deps)
    if user is not None:
        # The identity middleware is the shell's, not this router's: a test that
        # needs a logged-in caller says so here rather than building the shell.
        app.dependency_overrides[current_user] = lambda: user
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin)
    return client, deps, app


def widget(user_id, key, auth_date=NOW):
    data = {"id": user_id, "first_name": "A", "auth_date": auth_date}
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    data["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return data


def init_data(user_id, key, auth_date=NOW):
    fields = {"auth_date": str(auth_date), "user": json.dumps({"id": user_id})}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_public_paths_are_the_four_pre_login_routes():
    assert PUBLIC_PATHS == {"/api/auth/telegram", "/api/auth/webapp", "/api/auth/session",
                            "/api/config"}
    assert [r.path for r in router.routes] == [
        "/api/auth/telegram", "/api/auth/webapp", "/api/auth/session",
        "/api/auth/logout", "/api/me", "/api/me/model-default", "/api/config"]
    assert [sorted(r.methods) for r in router.routes] == [
        ["POST"], ["POST"], ["GET"], ["POST"], ["GET"], ["PUT"], ["GET"]]


def test_the_operation_ids_are_frozen():
    """A generated client names its methods after these; they never change."""
    assert [r.operation_id for r in router.routes] == [
        "identity_telegram_login", "identity_webapp_login", "identity_session_link",
        "identity_logout", "identity_get_me", "identity_set_model_default",
        "identity_get_config"]
    assert {tag for r in router.routes for tag in r.tags} == {"identity"}


@pytest.mark.parametrize("origin,local", [
    ("http://localhost", True), ("http://localhost:8080", True),
    ("http://127.0.0.1:5173", True), ("https://cladex.test", False),
    ("http://localhost.evil.test", False), ("", False)])
def test_is_local_origin(origin, local):
    assert is_local_origin(origin) is local


async def test_widget_login_sets_a_lax_cookie_for_allowed_users_only():
    client, deps, _app = make_client(FakeAcl(owner=1, allowed=[2]))
    r = await client.post("/api/auth/telegram", json=widget(2, deps.login_key))
    assert r.status_code == 200 and r.json() == {"user_id": 2, "role": "user"}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    r = await client.post("/api/auth/telegram", json=widget(3, deps.login_key))
    assert r.status_code == 401 and r.json()["error"]["code"] == "login_failed"
    r = await client.post("/api/auth/telegram", json=widget(2, b"wrong"))
    assert r.status_code == 401


async def test_the_widget_payload_reaches_the_verifier_with_every_field_it_signed():
    """The body reaches `verify_login` exactly as it arrived: the check string is
    built from all of its fields, so a schema that dropped `username` or `photo_url`
    would turn a real Login Widget POST into a 401."""
    client, deps, _app = make_client(FakeAcl(owner=7))
    payload = {"id": 7, "first_name": "A", "auth_date": NOW,
               "username": "ada", "photo_url": "https://t.me/i/x.jpg"}
    check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    payload["hash"] = hmac.new(deps.login_key, check.encode(), hashlib.sha256).hexdigest()
    r = await client.post("/api/auth/telegram", json=payload)
    assert r.status_code == 200 and r.json() == {"user_id": 7, "role": "owner"}


async def test_the_widget_profile_reaches_the_access_policy():
    """`_admit` hands the ACL the `first_name`/`last_name`/`username` Telegram
    signed, so a web-only user's `users` row learns their name — the response body
    is unchanged, which is what keeps the login wire contract frozen."""
    from claude_hermes.modules.identity.domain.models import Profile
    from claude_hermes.modules.identity.presentation.schemas import user_wire

    acl = FakeAcl(owner=7)
    client, deps, _app = make_client(acl)
    payload = {"id": 7, "first_name": "Ada", "last_name": "L", "auth_date": NOW,
               "username": "@ada"}
    check = "\n".join(f"{k}={payload[k]}" for k in sorted(payload))
    payload["hash"] = hmac.new(deps.login_key, check.encode(), hashlib.sha256).hexdigest()
    r = await client.post("/api/auth/telegram", json=payload)
    assert r.status_code == 200 and r.json() == {"user_id": 7, "role": "owner"}
    assert acl.profiles == [Profile(display_name="Ada L", username="ada")]
    # The two keys are `/api/me`'s, and only when a profile is passed — that is what
    # keeps the two login bodies byte-identical.
    assert user_wire(7, True) == {"user_id": 7, "role": "owner"}
    assert user_wire(7, True, Profile("Ada L", "ada")) == {
        "user_id": 7, "role": "owner", "display_name": "Ada L", "username": "ada"}


async def test_mini_app_login_sets_a_cross_site_cookie():
    client, deps, _app = make_client(FakeAcl(owner=1))
    r = await client.post("/api/auth/webapp", json={"init_data": init_data(1, deps.webapp_key)})
    assert r.status_code == 200 and r.json() == {"user_id": 1, "role": "owner"}
    assert "samesite=none" in r.headers["set-cookie"].lower()
    # A missing field and a wrong-typed one are still 401 login_failed, not the 400
    # a strict `init_data: str` would have produced.
    for body in ({"init_data": init_data(1, deps.login_key)}, {}, {"init_data": 5}):
        r = await client.post("/api/auth/webapp", json=body)
        assert r.status_code == 401 and r.json()["error"]["code"] == "login_failed"


async def test_no_webapp_key_shuts_that_door():
    client, _deps, _app = make_client(webapp=False)
    r = await client.post("/api/auth/webapp",
                          json={"init_data": init_data(1, webapp_key(TOKEN))})
    assert r.status_code == 401


async def test_the_web_never_claims_an_unowned_deploy():
    """Security invariant: `_admit` checks with chat_type="web", never "private",
    so the first stranger to find the public hostname cannot own the agent."""
    acl = FakeAcl(owner=None, claims=True)
    client, deps, _app = make_client(acl)
    r = await client.post("/api/auth/telegram", json=widget(7, deps.login_key))
    assert r.status_code == 401 and r.json()["error"]["code"] == "login_failed"
    r = await client.post("/api/auth/webapp", json={"init_data": init_data(7, deps.webapp_key)})
    assert r.status_code == 401
    assert acl.owner is None
    assert {c for _, c in acl.checks} == {"web"}


async def test_the_access_policy_is_swappable_without_rebuilding_the_bundle():
    """The reason `Depends` exists: `get_acl` is a named callable, so one app can be
    pointed at another policy without touching `app.state.deps`."""
    client, deps, app = make_client(FakeAcl(owner=1))
    strict = FakeAcl(owner=None)
    app.dependency_overrides[get_acl] = lambda: strict
    r = await client.post("/api/auth/telegram", json=widget(1, deps.login_key))
    assert r.status_code == 401 and strict.checks == [(1, "web")]
    del app.dependency_overrides[get_acl]
    r = await client.post("/api/auth/telegram", json=widget(1, deps.login_key))
    assert r.status_code == 200


async def test_session_link_is_local_only():
    token = sign_session(1, NOW + 3600, SECRET)
    client, _deps, _app = make_client()
    assert (await client.get("/api/auth/session", params={"t": token})).status_code == 404
    client, _deps, _app = make_client(origin="http://localhost:8080")
    r = await client.get("/api/auth/session", params={"t": token})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "HttpOnly" in r.headers["set-cookie"]
    assert (await client.get("/api/auth/session",
                             params={"t": token + "x"})).status_code == 401
    # No `t` at all is the same "bad or expired token", not a missing-parameter 400.
    assert (await client.get("/api/auth/session")).status_code == 401


async def test_logout_clears_the_cookie_and_config_is_public():
    client, _deps, _app = make_client()
    r = await client.post("/api/auth/logout")
    assert r.status_code == 204 and "max-age=0" in r.headers["set-cookie"].lower()
    r = await client.get("/api/config")
    assert r.status_code == 200 and r.json() == {"bot_username": "cladex_bot"}


async def test_me_without_the_middleware_is_the_middlewares_own_401():
    """`current_user` is the second door: a router mounted without
    `CurrentUserMiddleware` answers the same `401 unauthorized` / "login required"
    rather than reading a bare `State` and 500-ing."""
    client, _deps, _app = make_client()
    r = await client.get("/api/me")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "login required"}}


@pytest.mark.parametrize("path", ["/api/auth/telegram", "/api/auth/webapp"])
@pytest.mark.parametrize("body", [b"{not json", b"[1]", b'"hello"', b""])
async def test_a_body_that_is_not_a_json_object_is_a_failed_login_not_a_bad_request(
        path, body):
    """Wire parity with the hand-written handlers, which caught `JSONDecodeError`
    around `request.json()` and the `TypeError`/`AttributeError` a non-object payload
    raises inside the verifier, and answered 401 for all of it. A declared request
    model would route these to the validation handler as `400 bad_request` instead,
    and `client.ts` branches on the code."""
    client, _deps, _app = make_client(FakeAcl(owner=1))
    r = await client.post(path, content=body, headers={"content-type": "application/json"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "login_failed"


async def test_the_mini_app_profile_reaches_the_access_policy():
    """Wave 4b, D10: before it, `webapp_login` admitted with no profile, so a
    user who only ever opened the Mini App had a `users` row with an empty
    `display_name` forever. The response body is unchanged (frozen wire)."""
    from claude_hermes.modules.identity.domain.models import Profile

    acl = FakeAcl(owner=7)
    client, deps, _app = make_client(acl)
    fields = {"auth_date": str(NOW),
              "user": json.dumps({"id": 7, "first_name": "Ada", "last_name": "L",
                                  "username": "ada"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    fields["hash"] = hmac.new(deps.webapp_key, check.encode(), hashlib.sha256).hexdigest()
    r = await client.post("/api/auth/webapp", json={"init_data": urlencode(fields)})
    assert r.status_code == 200 and r.json() == {"user_id": 7, "role": "owner"}
    assert acl.profiles == [Profile(display_name="Ada L", username="ada")]


# --- the personal model default ----------------------------------------------


async def test_me_always_carries_the_two_default_keys():
    """Never conditionally absent: `/api/me` passes a pair even when it is
    `(None, None)`, so the SPA reads two keys and not "sometimes two keys"."""
    acl = FakeAcl(owner=7)
    client, _deps, _app = make_client(acl, user=CurrentUser(7, True))
    assert (await client.get("/api/me")).json() == {
        "user_id": 7, "role": "owner", "display_name": "", "username": "",
        "default_model": None, "default_effort": None}

    from claude_hermes.modules.identity.presentation.schemas import user_wire

    acl.defaults[7] = ("claude-opus-5-5", "high")
    assert (await client.get("/api/me")).json()["default_model"] == "claude-opus-5-5"
    # …and the two login bodies stay byte-identical, which is the whole reason
    # `user_wire` adds the keys only for a caller that passes a pair.
    assert user_wire(7, True) == {"user_id": 7, "role": "owner"}
    assert user_wire(7, True, None, (None, None)) == {
        "user_id": 7, "role": "owner", "default_model": None, "default_effort": None}


async def test_anybody_admitted_may_set_their_own_default():
    """An allowlisted user picks from the same list as the owner — an explicit
    owner decision, and a preference about their own lanes either way."""
    acl = FakeAcl(owner=1, allowed=[2])
    client, _deps, _app = make_client(acl, user=CurrentUser(2, False))

    r = await client.put("/api/me/model-default",
                         json={"model": "claude-opus-5-5", "effort": "high"})
    assert r.status_code == 200
    assert r.json() == {"default_model": "claude-opus-5-5", "default_effort": "high"}
    assert acl.defaults[2] == ("claude-opus-5-5", "high")


async def test_clearing_the_default_takes_the_effort_with_it():
    acl = FakeAcl(owner=1)
    client, _deps, _app = make_client(acl, user=CurrentUser(1, True))
    await client.put("/api/me/model-default",
                     json={"model": "claude-opus-5-5", "effort": "high"})
    r = await client.put("/api/me/model-default", json={"model": None, "effort": "max"})
    assert r.status_code == 200
    assert r.json() == {"default_model": None, "default_effort": None}


async def test_the_two_model_refusals_are_the_catalogs_own_codes():
    """The offer is `cfg.models` plus the deploy default, and the same list the
    lane's own resolver validates against — a personal default is not a private
    list. `claude-fable-5-1` is a real catalog model this deployment does not
    offer, which is exactly the case a `lookup`-only check would let through."""
    client, _deps, _app = make_client(FakeAcl(owner=1), user=CurrentUser(1, True))

    for body, code in (({"model": "claude-fable-5-1"}, "bad_model"),
                       ({"model": "claude-opus-9-9"}, "bad_model"),
                       ({"model": 5}, "bad_model"),
                       ({"model": "claude-opus-5-5", "effort": "turbo"}, "bad_effort")):
        r = await client.put("/api/me/model-default", json=body)
        assert r.status_code == 400, body
        assert r.json()["error"]["code"] == code, body


async def test_the_default_body_is_lenient_like_the_rest_of_this_router():
    client, _deps, _app = make_client(FakeAcl(owner=1), user=CurrentUser(1, True))
    r = await client.put("/api/me/model-default",
                         json={"model": "claude-opus-5-5", "colour": "red"})
    assert r.status_code == 200 and r.json()["default_model"] == "claude-opus-5-5"
    # No keys at all means "clear it", which is what an empty body says here.
    r = await client.put("/api/me/model-default", json={})
    assert r.status_code == 200 and r.json()["default_model"] is None


async def test_a_default_for_somebody_with_no_row_is_a_404_not_a_silent_success():
    acl = FakeAcl(owner=1, unknown=[9])
    client, _deps, _app = make_client(acl, user=CurrentUser(9, False))
    r = await client.put("/api/me/model-default", json={"model": "claude-opus-5-5"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_setting_a_default_without_the_middleware_is_the_same_401():
    client, _deps, _app = make_client(FakeAcl(owner=1))
    r = await client.put("/api/me/model-default", json={"model": "claude-opus-5-5"})
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "message": "login required"}}

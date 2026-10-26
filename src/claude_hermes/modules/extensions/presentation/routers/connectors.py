"""Connectors router: the browser face of the extensions machinery.

Reads are open to every logged-in user and answer with the CALLER's state: the
catalog is shared, "connected" and "needs re-auth" are not. Every mutation acts on
the caller's own row, the caller's own secrets and the caller's own token — connect,
test, enable, disable and remove go through `ExtensionService`, the same code
`ext_manage` runs from chat. `POST /api/connectors` is the one that stays the
owner's, because it writes a manifest into the shared `user_catalog/`. OAuth is the
one thing only this channel can do: the flow starts here, the user signs in at the
provider, and the callback lands here with the cookie. Secret and token values never
appear in a response or a log line.

The owner gate is `core.auth.require_owner` — a route dependency where the handler does
not need the object, a parameter where it does. Route dependencies are solved first, so
a non-owner is still refused before the catalog is asked for a manifest, which is the
order `owner_only(request)` had as every handler's first line. The OAuth callback has
no gate beyond the session: a top-level navigation must come back as a redirect, never
a JSON 403, and the state it carries was issued to one user — `pending.take(state,
user_id)` is what refuses somebody else's.
"""
from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from urllib.parse import urlencode

import yaml
from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, RedirectResponse, Response

from claude_hermes.core.auth import CurrentUser, current_user, require_owner
from claude_hermes.core.exceptions import ApplicationException as ApiError
from claude_hermes.core.workspace import user_catalog_dir

from ...domain.exceptions import ManifestError, SecretNameError
from ...domain.manifest import MANIFEST_VERSION, Manifest, parse
from ...domain.oauth import OAuthError, authorize_url, iso, pkce_pair
from ..dependencies import ExtensionsDeps, get_connector, get_extensions
from ..schemas import ConnectorCreate, RawJsonBody, connector_to_wire

CALLBACK_PATH = "/api/connectors/oauth/callback"
RETURN_PATH = "/customize/connectors"
AUTH_MODES = ("none", "oauth", "header")
_SLUG_RE = re.compile(r"[^A-Z0-9]")

router = APIRouter(tags=["extensions"])


def token_var(name: str) -> str:
    """The secret/token variable a custom connector uses: MY-API → MY_API_TOKEN."""
    return _SLUG_RE.sub("_", name.upper()) + "_TOKEN"


def custom_manifest(body: dict) -> dict:
    """The web form as a manifest mapping. `catalog.parse` is the validator; this only
    shapes the dictionary, so a bad name or an http:// URL fails there, not here."""
    name = str(body.get("name", "")).strip()
    label = str(body.get("label", "")).strip() or name
    auth = str(body.get("auth", "none"))
    if auth not in AUTH_MODES:
        raise ApiError(400, "bad_manifest", f"auth must be one of {AUTH_MODES}")
    transport = str(body.get("transport", "http"))
    data: dict = {
        "manifest_version": MANIFEST_VERSION, "name": name, "label": label,
        "description": str(body.get("description", "")).strip() or label, "version": "1",
        "transport": transport, "url": str(body.get("url", "")).strip(), "scopes": ["turn", "cron"],
    }
    var = token_var(name) if name else "TOKEN"
    if auth == "oauth":
        data["auth"] = [{"name": var, "kind": "oauth", "header": "Authorization",
                         "format": f"Bearer ${{{var}}}"}]
    elif auth == "header":
        header = str(body.get("header", "")).strip() or "Authorization"
        fmt = f"Bearer ${{{var}}}" if header == "Authorization" else f"${{{var}}}"
        data["auth"] = [{"name": var, "prompt": f"API token sent in the {header} header",
                         "header": header, "format": fmt}]
    return data


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redirect_uri(ext: ExtensionsDeps) -> str:
    return ext.cfg.web_public_origin.rstrip("/") + CALLBACK_PATH


async def wire(ext: ExtensionsDeps, m: Manifest, *, owner_id: int) -> dict:
    return connector_to_wire(m, await ext.store.get(m.name, owner_id=owner_id),
                             await ext.secrets.load(owner_id=owner_id),
                             source=ext.extensions.source_of(m))


@router.get("/api/connectors", operation_id="extensions_list_connectors")
async def list_connectors(user: CurrentUser = Depends(current_user),
                          ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Every catalog connector with the CALLER's derived state.

    Readable by any logged-in user, and the catalog half of an entry is the same for
    all of them. The row half is not: whether this connector is connected, and what
    its last probe said, is a fact about one person's credentials.
    """
    rows = {r["name"]: r for r in await ext.store.list("mcp", owner_id=user.user_id)}
    values = await ext.secrets.load(owner_id=user.user_id)
    items = [connector_to_wire(m, rows.get(m.name), values, source=ext.extensions.source_of(m))
             for m in ext.catalog.load_all(ext.catalog_root).values() if m.kind == "mcp"]
    items.sort(key=lambda c: (c["source"] != "catalog", c["label"].lower()))
    return JSONResponse({"connectors": items})


@router.post("/api/connectors", operation_id="extensions_create_connector",
             status_code=201)
async def create(body: ConnectorCreate,
                 user: CurrentUser = Depends(require_owner),
                 ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """A connector the owner typed into the form, written to the user catalog.

    Owner-only, and the one connectors route that still is: this writes a manifest
    into the shared `user_catalog/`, which is what the deployment may install at
    all. Enabling one and signing into it is every user's own business.
    """
    data = custom_manifest(body.model_dump())
    try:
        m = parse(data, "mcp")
    except ManifestError as e:
        raise ApiError(400, "bad_manifest", str(e)) from e
    if ext.catalog.is_repo_entry(m.name) or ext.catalog.get(m.name, ext.catalog_root) is not None:
        raise ApiError(409, "name_taken", f"'{m.name}' already exists")
    ext.writer.promote(m, yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8"))
    if ext.catalog.get(m.name, ext.catalog_root) is None:
        raise ApiError(500, "write_failed", "the manifest was written but the catalog cannot see it")
    token = str(body.token or "")
    if data.get("auth") and body.auth == "header" and token:
        await ext.secrets.put(data["auth"][0]["name"], token, owner_id=user.user_id)
    client_id = str(body.client_id or "")
    if client_id:
        # Advanced fields: a provider that does not register clients dynamically
        # is configured once here, and oauth/start then skips registration.
        await ext.tokens.put(m.name, owner_id=user.user_id, now=now_iso(),
                             client_id=client_id,
                             client_secret=str(body.client_secret or ""))
    return JSONResponse(await wire(ext, m, owner_id=user.user_id), status_code=201)


@router.get(CALLBACK_PATH, operation_id="extensions_oauth_callback")
async def oauth_callback(code: str = "", state: str = "", error: str = "",
                         user: CurrentUser = Depends(current_user),
                         ext: ExtensionsDeps = Depends(get_extensions)) -> Response:
    """A top-level navigation back from the provider: the Lax cookie identifies the
    caller, and the state must be the one we issued to THEM. On any failure nothing
    is stored and the page says why.

    No owner gate, because `/oauth/start` has none either: `pending.take(state,
    user.user_id)` refuses a state issued to somebody else, and the grant that lands
    here is written against the caller's own id.
    """
    def back(**q: str) -> Response:
        return RedirectResponse(f"{RETURN_PATH}?{urlencode(q)}", status_code=302)

    if error:
        return back(error=error)
    pending = await ext.pending.take(state, user.user_id, time.time())
    if pending is None:
        return back(error="state_mismatch")
    m = ext.catalog.get(pending.name, ext.catalog_root)
    row = await ext.tokens.get(pending.name, owner_id=user.user_id)
    if m is None or row is None:
        return back(error="state_mismatch")
    now = time.time()
    async with ext.oauth.make_client() as client:
        try:
            disc = await ext.oauth.discover_issuer(client, row["issuer"], resource=m.url)
            ts = await ext.oauth.exchange(client, disc, code=code,
                                          verifier=pending.verifier, client_id=row["client_id"],
                                          client_secret=row["client_secret"],
                                          redirect_uri=redirect_uri(ext), now=now)
        except OAuthError as e:
            return back(error=e.code)
    await ext.tokens.put(m.name, owner_id=user.user_id, now=iso(now),
                         access_token=ts.access_token, refresh_token=ts.refresh_token,
                         expires_at=ts.expires_at, scope=ts.scope)
    await ext.secrets.put(m.oauth_entries()[0].name, ts.access_token,
                          owner_id=user.user_id)
    out = await ext.extensions.connect(m, user_id=user.user_id, now=now_iso(),
                                       timeout=ext.cfg.ext_probe_timeout_seconds,
                                       probe_fn=ext.probe_fn, owner_id=user.user_id)
    if not out.ok:
        return back(error="probe_failed")
    # The callback is a *connect* — `ExtensionService.connect` wrote the registry row
    # and the toolset in the cached prompt prefix just changed. It is the most common
    # connect path of the three, so forgetting the reset here would leave the bug the
    # other two just fixed alive on the route people actually take.
    await ext.invalidate(user.user_id)
    return back(connected=m.name)


@router.post("/api/connectors/{name}/secrets", status_code=204,
             operation_id="extensions_put_connector_secrets")
async def put_secrets(body: RawJsonBody = None,
                      user: CurrentUser = Depends(current_user),
                      m: Manifest = Depends(get_connector),
                      ext: ExtensionsDeps = Depends(get_extensions)) -> Response:
    """The declared secret names, set at once, for the caller. Values are written,
    never echoed — and never over another user's: `NOTION_TOKEN` is an env-var name
    two people share, not a key they share.
    """
    if not isinstance(body, dict):
        raise ApiError(400, "bad_request", "expected an object of secret names")
    unknown = set(body) - set(m.plain_secret_names())
    if unknown:
        raise ApiError(400, "bad_request", f"not declared by '{m.name}': {sorted(unknown)}")
    for name, value in body.items():
        try:
            await ext.secrets.put(name, str(value), owner_id=user.user_id)
        except SecretNameError as e:
            raise ApiError(400, "bad_request", str(e)) from e
    return Response(status_code=204)


@router.post("/api/connectors/{name}/connect", operation_id="extensions_connect_connector")
async def connect(user: CurrentUser = Depends(current_user),
                  m: Manifest = Depends(get_connector),
                  ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Probe it with the caller's credentials, register their row, reset their lanes."""
    out = await ext.extensions.connect(m, user_id=user.user_id, now=now_iso(),
                                       timeout=ext.cfg.ext_probe_timeout_seconds,
                                       probe_fn=ext.probe_fn, owner_id=user.user_id)
    if out.code == "secrets_missing":
        # The one error that carries a field, so the dialog can ask for exactly it.
        return JSONResponse({"error": {"code": "secrets_missing", "message": out.message,
                                       "missing": list(out.missing)}}, status_code=400)
    if out.code == "oauth_required":
        raise ApiError(409, "oauth_required", out.message)
    if not out.ok:
        raise ApiError(502, "probe_failed", out.message)
    # The toolset just changed and it lives in the cached prompt prefix. Reset
    # after the registry row is written, never before — the ext_manage ordering
    # rule: a lane that rebinds in between would resurrect the stale session.
    await ext.invalidate(user.user_id)
    return JSONResponse(await wire(ext, m, owner_id=user.user_id))


@router.post("/api/connectors/{name}/oauth/start",
             operation_id="extensions_start_connector_oauth")
async def oauth_start(user: CurrentUser = Depends(current_user),
                      m: Manifest = Depends(get_connector),
                      ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """The provider's authorize URL, with a PKCE challenge and a one-shot state.

    Every user signs in for themselves: the state is issued to the caller, the grant
    that comes back is written against their id, and what it buys is their own row.
    Not the owner's to gate.
    """
    if not m.oauth_entries():
        raise ApiError(400, "bad_request", f"'{m.name}' does not use OAuth")
    row = await ext.tokens.get(m.name, owner_id=user.user_id) or {}
    async with ext.oauth.make_client() as client:
        try:
            # RFC 8707: authorize, exchange and refresh must all name the same
            # resource, and the callback only has the manifest URL — so that is
            # the one every request carries, not the document's own spelling.
            disc = replace(await ext.oauth.discover(client, m.url), resource=m.url)
            client_id, client_secret = row.get("client_id") or "", row.get("client_secret") or ""
            if not client_id:
                client_id, client_secret = await ext.oauth.register(client, disc,
                                                                    redirect_uri(ext))
        except OAuthError as e:
            raise ApiError(502, e.code, e.message) from e
    await ext.tokens.put(m.name, owner_id=user.user_id, now=now_iso(), issuer=disc.issuer,
                         client_id=client_id, client_secret=client_secret)
    verifier, challenge = pkce_pair()
    state = await ext.pending.issue(m.name, user.user_id, verifier, time.time())
    url = authorize_url(disc, client_id=client_id, redirect_uri=redirect_uri(ext),
                        state=state, challenge=challenge, scope=" ".join(disc.scopes))
    return JSONResponse({"url": url})


@router.post("/api/connectors/{name}/test", operation_id="extensions_test_connector")
async def test(user: CurrentUser = Depends(current_user),
               m: Manifest = Depends(get_connector),
               ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """A probe that installs nothing — so it never invalidates a prompt prefix."""
    out = await ext.extensions.connect(m, user_id=user.user_id, now=now_iso(),
                                       timeout=ext.cfg.ext_probe_timeout_seconds,
                                       probe_fn=ext.probe_fn, install=False,
                                       owner_id=user.user_id)
    if out.ok:
        await ext.store.set_tools(m.name, list(out.tools), owner_id=user.user_id)
    return JSONResponse({"ok": out.ok, "tools": list(out.tools),
                         "error": out.message if not out.ok else ""})


@router.post("/api/connectors/{name}/enable", operation_id="extensions_enable_connector")
async def enable(user: CurrentUser = Depends(current_user),
                 m: Manifest = Depends(get_connector),
                 ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    if not await ext.extensions.enable(m.name, owner_id=user.user_id):
        raise ApiError(404, "not_found", "not installed")
    await ext.invalidate(user.user_id)
    return JSONResponse(await wire(ext, m, owner_id=user.user_id))


@router.post("/api/connectors/{name}/disable", operation_id="extensions_disable_connector")
async def disable(user: CurrentUser = Depends(current_user),
                  m: Manifest = Depends(get_connector),
                  ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    if not await ext.extensions.disable(m.name, owner_id=user.user_id):
        raise ApiError(404, "not_found", "not installed")
    await ext.invalidate(user.user_id)
    return JSONResponse(await wire(ext, m, owner_id=user.user_id))


@router.delete("/api/connectors/{name}", status_code=204,
               operation_id="extensions_delete_connector")
async def delete(user: CurrentUser = Depends(current_user),
                 m: Manifest = Depends(get_connector),
                 ext: ExtensionsDeps = Depends(get_extensions)) -> Response:
    """Disconnect: the caller's row, their clone of a token, their secrets — and a
    custom manifest, which only the owner can have written.

    The manifest is the one shared thing this route can reach, so the unlink is
    kept behind `user.is_owner`: a second user disconnecting drops their own
    credentials and leaves the entry standing for everybody else.
    """
    row = await ext.tokens.get(m.name, owner_id=user.user_id)
    if row and row["refresh_token"]:
        async with ext.oauth.make_client() as client:
            try:
                disc = await ext.oauth.discover_issuer(client, row["issuer"])
                await ext.oauth.revoke(client, disc, row["refresh_token"], row["client_id"],
                                       row["client_secret"])
            except OAuthError:
                pass                                     # best effort; dropped locally anyway
    await ext.extensions.remove(m.name, m, owner_id=user.user_id)
    # also a never-connected custom entry's client_id
    await ext.tokens.delete(m.name, owner_id=user.user_id)
    # Disconnect says the credentials are forgotten, so they are — including
    # when there was no registry row for `remove` to find (a probe that failed
    # after the sign-in), which would otherwise leave a live token on disk.
    for a in m.auth:
        await ext.secrets.forget(a.name, owner_id=user.user_id)
    if user.is_owner and ext.extensions.source_of(m) == "custom":
        (user_catalog_dir(ext.home) / "mcp" / f"{m.name}.yaml").unlink(missing_ok=True)
    await ext.invalidate(user.user_id)
    return Response(status_code=204)

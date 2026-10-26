"""Plugins router: the browser face of `kind: plugin` catalog entries.

The same shape as the connectors router — reads for every logged-in user, the
caller's own row under every enable and disable, one service doing the work — with
three differences that come from a plugin being local code rather than a remote URL:

* what a plugin *adds* is read from the clone on every listing, never stored
  (`infrastructure/plugin_reader.py`), so a row can also say the clone is gone;
* there is no create route. The browser never authors a plugin manifest: the path
  for a new one is draft -> approve -> install, and the drafts router owns the middle;
* install is two calls, not one. A plugin brings its own MCP servers — the CLI
  loads a plugin's `.mcp.json` itself, without `catalog.parse`, a registry row or a
  probe — so the surface has to be readable *before* anything is registered.
  `POST /install` fetches and answers with that surface plus a token bound to it;
  `POST /install/confirm` sends the token back and only then is the row written.
  A fetched-but-unconfirmed clone is inert, because `for_scope` reads rows.

The clone itself is deploy-wide code: `git clone` writes into the shared container
and deleting it takes it from everybody, so install, confirm and DELETE stay the
owner's. Which of the cloned plugins a user loads is their own business, so enable
and disable run as `current_user` — and enabling with no row of one's own *adopts*
the shared tree rather than failing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from starlette.responses import JSONResponse, Response

from claude_hermes.core.auth import CurrentUser, current_user, require_owner
from claude_hermes.core.exceptions import ApplicationException as ApiError

from ...application.services.plugin_service import (LocalServerRefused, NotFetched,
                                                    StaleInstall)
from ...domain.exceptions import InstallError
from ...domain.manifest import Manifest, valid_name
from ..dependencies import ExtensionsDeps, get_extensions, get_plugin
from ..schemas import RawJsonBody, orphan_plugin_wire, plugin_to_wire

# Cloudflare cuts a request at 100 s (web/turns.py answers 202 for the same reason).
# The configured clone timeout defaults to 120 s, which would mean the browser's
# request dies while git is still running and nothing ever records the outcome.
MAX_INSTALL_SECONDS = 85.0

router = APIRouter(tags=["extensions"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_wire(ext: ExtensionsDeps, m: Manifest, row) -> dict:
    return plugin_to_wire(m, row, ext.home, reader=ext.reader,
                          source=ext.extensions.source_of(m))


async def wire(ext: ExtensionsDeps, m: Manifest, *, owner_id: int) -> dict:
    return to_wire(ext, m, await ext.store.get(m.name, owner_id=owner_id))


@router.get("/api/plugins", operation_id="extensions_list_plugins")
async def list_plugins(user: CurrentUser = Depends(current_user),
                       ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Every catalog plugin, plus the CALLER's rows whose manifest is gone.

    Any logged-in user. The clone on disk is shared, so `present` and the components
    read off it are the same for everybody; the row — enabled, disabled, its last
    error — is the caller's own.
    """
    rows = {r["name"]: r for r in await ext.store.list("plugin", owner_id=user.user_id)}
    entries = [m for m in ext.catalog.load_all(ext.catalog_root).values()
               if m.kind == "plugin"]
    items = [to_wire(ext, m, rows.get(m.name)) for m in entries]
    items.sort(key=lambda p: (p["source"] != "catalog", p["label"].lower()))
    known = {m.name for m in entries}
    items += [orphan_plugin_wire(r) for n, r in sorted(rows.items()) if n not in known]
    return JSONResponse({"plugins": items})


# Fixed segments first: '/{name}' must never be offered a path that ends in a
# verb, and DELETE is the only method the bare '/{name}' route answers.


@router.post("/api/plugins/{name}/install", operation_id="extensions_install_plugin",
             dependencies=[Depends(require_owner)])
async def install_plugin(m: Manifest = Depends(get_plugin),
                         ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Phase one: fetch, and show what was fetched. Installs nothing.

    Owner-only: `git clone` puts code in the shared container, which is a decision
    about what the deployment runs at all, not about one person's lane.

    The clone lands on disk but no registry row is written, and `for_scope` reads
    only `store.enabled(owner_id=...)` — so until the owner confirms, the tree is
    inert: the SDK is never told about it, its hooks never run, its bundled servers
    are never loaded. What comes back is the surface `render_surface` produces and a
    token bound to those exact bytes, the same guarantee the drafts router gives.

    This is also the update path: `clone` removes and re-fetches, so confirming
    again moves an installed plugin to the manifest's current ref.
    """
    try:
        review, affected = await ext.plugins.fetch_for_review(
            m, timeout=min(ext.cfg.plugin_clone_timeout_seconds, MAX_INSTALL_SECONDS))
    except InstallError as e:
        # git's stderr, already trimmed by the installer. No row is written: a
        # failed clone removes its own directory, so nothing was installed.
        raise ApiError(502, "install_failed", str(e)) from e
    except LocalServerRefused as e:
        raise ApiError(400, "local_server", str(e)) from e

    # A re-fetch over an installed plugin has already replaced the tree the SDK
    # loads, while every row — and every affected lane's cached prefix — still
    # describes the old one. Confirm may never come, so the reset belongs here, not
    # only there. A first install needs none: there is no row, so nothing loads it.
    # The clone is shared, so "affected" is every owner holding a row, not just this one.
    for owner_id in affected:
        await ext.invalidate(owner_id)

    return JSONResponse({
        "name": review.name, "label": review.label, "surface": review.surface,
        "token": review.token,
        "servers": [{"name": s.name, "transport": s.transport, "target": s.target}
                    for s in review.servers],
        "components": review.components,
    })


@router.post("/api/plugins/{name}/install/confirm",
             operation_id="extensions_confirm_plugin_install")
async def confirm_install(user: CurrentUser = Depends(require_owner),
                          body: RawJsonBody = None,
                          m: Manifest = Depends(get_plugin),
                          ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Phase two: register what was read. The token is the whole check.

    Recomputing the surface from the tree on disk and comparing means a clone that
    changed between the two calls — a second install of a different manifest, a
    hand-edit under `agent_home/plugins/` — cannot be registered against an
    approval the owner gave for something else.

    The row written is the owner's own. Everybody else adopts the clone by enabling
    it, which is the only way a second user gets one.
    """
    token = str(body.get("token") or "") if isinstance(body, dict) else ""
    try:
        await ext.plugins.confirm_install(m, token, user_id=user.user_id,
                                          now=now_iso(), owner_id=user.user_id)
    except NotFetched as e:
        raise ApiError(409, "not_fetched", str(e)) from e
    except StaleInstall as e:
        raise ApiError(409, "stale_install", str(e)) from e
    # The plugin's skills, commands and hooks are in the cached prompt prefix.
    # Reset after the row is written, never before — the ext_manage ordering rule.
    await ext.invalidate(user.user_id)
    return JSONResponse(await wire(ext, m, owner_id=user.user_id))


@router.post("/api/plugins/{name}/enable", operation_id="extensions_enable_plugin")
async def enable(user: CurrentUser = Depends(current_user),
                 m: Manifest = Depends(get_plugin),
                 ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    """Enable this plugin for the caller — adopting the shared clone if they have
    no row yet.

    A clone is deploy-wide code only the owner may fetch, so a second user's
    "enable" cannot be a state flip on a row they do not have. `adopt` writes one,
    and answers False when there is no tree to adopt — which is a 404, never a row
    pointing at a directory that is not there.
    """
    if not await ext.extensions.enable(m.name, owner_id=user.user_id):
        if not await ext.extensions.adopt(m, user_id=user.user_id, now=now_iso(),
                                          owner_id=user.user_id):
            raise ApiError(404, "not_found", "not installed")
    await ext.invalidate(user.user_id)
    return JSONResponse(await wire(ext, m, owner_id=user.user_id))


@router.post("/api/plugins/{name}/disable", operation_id="extensions_disable_plugin")
async def disable(user: CurrentUser = Depends(current_user),
                  m: Manifest = Depends(get_plugin),
                  ext: ExtensionsDeps = Depends(get_extensions)) -> JSONResponse:
    if not await ext.extensions.disable(m.name, owner_id=user.user_id):
        raise ApiError(404, "not_found", "not installed")
    await ext.invalidate(user.user_id)
    return JSONResponse(await wire(ext, m, owner_id=user.user_id))


@router.delete("/api/plugins/{name}", status_code=204,
               operation_id="extensions_delete_plugin",
               dependencies=[Depends(require_owner)])
async def delete(name: str, ext: ExtensionsDeps = Depends(get_extensions)) -> Response:
    """Drops the clone and EVERY owner's row for it.

    Owner-only: the clone is code every tree loads, and deleting it takes it from
    everybody. Which is also why the rows cascade — leaving another user's row
    behind would leave them an enabled plugin pointing at a directory that is gone,
    an error `for_scope` would report on every one of their turns.

    Deliberately does NOT go through `get_plugin`: the row whose manifest vanished
    is exactly the one that has to be removable. With no row at all it is Cancel —
    the phase-one clone the owner read and declined, which must not be left on disk.
    """
    if not valid_name(name):
        # The name reaches the installer's rmtree. A route param is only guaranteed
        # not to contain '/', and the catalog's own rule is what makes it a directory
        # name rather than a path.
        raise ApiError(404, "not_found", "no such plugin")
    m = ext.catalog.get(name, ext.catalog_root)
    if m is not None and m.kind != "plugin":
        # A connector's name on the plugins route: the rmtree below is not for it.
        raise ApiError(404, "not_found", "no such plugin")
    # `owners_of` answers ids for a NAME, blind to kind — so the kind is checked
    # here, per row. The manifest check above cannot do it alone: with `m is None`
    # (a manifest the owner unlinked) a connector's rows would otherwise cascade
    # away on the plugins route, taking that user's tokens and secrets with them.
    # The pre-cascade code had this guard as `row["kind"] == "plugin"`; it is the
    # same guard, applied to every owner rather than to the single row there was.
    owners: list[int] = []
    for owner_id in await ext.store.owners_of(name):
        row = await ext.store.get(name, owner_id=owner_id)
        if row is not None and row["kind"] == "plugin":
            owners.append(owner_id)
    if not owners:
        if m is None:
            raise ApiError(404, "not_found", "no such plugin")
        ext.installer.remove(name)
        return Response(status_code=204)     # nothing registered: nothing to reset
    for owner_id in owners:
        # `remove_clone=False` on every one of them: the tree is shared, so it goes
        # once, below, after the last row that pointed at it is gone.
        await ext.extensions.remove(name, m, owner_id=owner_id, remove_clone=False)
    ext.installer.remove(name)
    for owner_id in owners:
        await ext.invalidate(owner_id)
    return Response(status_code=204)

"""What the three routers are handed, and where it comes from.

`ExtensionsDeps` is the view the routers read — eighteen collaborators the
composition root already built (`http.deps.extensions_view`). Wave 2b did not split
it into eighteen providers:
the *bundle attribute* a router reads is `extensions`, and `get_extensions` is the one
provider for it. A test that wants a different catalog, store or OAuth wire overrides
that single callable and gets all of them at once, which is how the three routers were
always driven.

Built once at module scope (FastAPI keys `dependency_overrides` by the callable
object, so a provider rebuilt per request could not be overridden). The three
dependencies under it are the old per-request helpers that every handler opened with —
`manifest_or_404` twice and drafts' `named` — lifted out of the closures so the 404
they raise is resolved before a handler body runs, and so `Depends(require_owner)` on
the route can still refuse first (route dependencies are solved before parameter ones,
which is the order `owner_only(request)` had as every handler's first line).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends
from starlette.requests import Request

from claude_hermes.core.di import provide
from claude_hermes.core.exceptions import ApplicationException as ApiError

from ..domain.manifest import Manifest, valid_name

__all__ = ["ExtensionsDeps", "draft_name", "get_connector", "get_extensions",
           "get_plugin"]


@dataclass
class ExtensionsDeps:
    cfg: Any                       # web_public_origin, ext_probe_timeout_seconds,
                                   # plugin_clone_timeout_seconds
    home: Path
    catalog_root: Path | None      # tests point this at a fixture; None -> repo + user catalog
    catalog: Any                   # CatalogReader
    secrets: Any                   # SecretStore
    store: Any                     # ExtensionRepository
    tokens: Any                    # TokenRepository
    writer: Any                    # CatalogWriter
    pending: Any                   # PendingStateStore
    drafts: Any                    # DraftStore
    approvals: Any                 # ApprovalService
    extensions: Any                # ExtensionService
    plugins: Any                   # PluginService
    reader: Any                    # PluginReader
    probe_fn: Any                  # ServerProber
    installer: Any                 # PluginInstaller
    oauth: Any                     # the OAuth wire namespace (make_client/discover/…)
    invalidate: Any                # PromptCacheInvalidator — awaited AFTER every write


_extensions = provide("extensions")


def get_extensions(request: Request) -> ExtensionsDeps:
    """`deps.extensions` — the view above, as the composition root built it.

    The shell does not mount these routers when it is `None`, so `provide`'s 503 guard
    is unreachable in a correctly assembled app.
    """
    return _extensions(request)


def get_connector(name: str, ext: ExtensionsDeps = Depends(get_extensions)) -> Manifest:
    """`{name}` as a `kind: mcp` manifest, or 404. The old `manifest_or_404`."""
    m = ext.catalog.get(name, ext.catalog_root)
    if m is None or m.kind != "mcp":
        raise ApiError(404, "not_found", "no such connector")
    return m


def get_plugin(name: str, ext: ExtensionsDeps = Depends(get_extensions)) -> Manifest:
    """`{name}` as a `kind: plugin` manifest, or 404. The plugins router's
    `manifest_or_404` — `DELETE /api/plugins/{name}` deliberately does not use it."""
    m = ext.catalog.get(name, ext.catalog_root)
    if m is None or m.kind != "plugin":
        raise ApiError(404, "not_found", "no such plugin")
    return m


def draft_name(name: str) -> str:
    r"""The name out of the URL, proven to be a name.

    Unlike every other handler there, `discard` hands its name straight to code that
    builds a path from it (`ApprovalService.clear` -> `draft_path`), and a route param
    is only guaranteed not to contain '/'. On Windows that leaves '..\..\x' as a
    traversal into an unlink; the container never sees it, but a developer's machine
    would. The catalog's own name rule refuses it, and refuses nothing a real draft
    could be called.
    """
    if not valid_name(name):
        raise ApiError(404, "no_draft", f"no draft named '{name}' is queued")
    return name

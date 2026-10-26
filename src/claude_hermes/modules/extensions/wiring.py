"""The extensions module's composition: infrastructure adapters and application
services, assembled once into an `ExtensionsBundle`.

It sits outside the four layers on purpose — presentation and infrastructure are
siblings and may not import each other, so something has to introduce them, and that
something is this file. `main.build_app` calls `build(...)` once; the three routers
read the bundle through `http.deps.extensions_view`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_hermes.core.crypto import SecretBox

from .application.services.admin_service import ExtensionAdminService
from .application.services.approval_service import ApprovalService
from .application.services.compose_service import ComposeService
from .application.services.extension_service import ExtensionService
from .application.services.oauth_service import OAuthService
from .application.services.plugin_service import PluginService
from .application.services.queries import ExtensionsQueryService
from .infrastructure import catalog_repository, git_installer, oauth_client
from .infrastructure.file_catalog_writer import FileCatalogWriter
from .infrastructure.mcp_prober import SdkServerProber
from .infrastructure.pending_states import PendingStates
from .infrastructure.plugin_reader import DiskPluginReader
from .infrastructure.repositories.extension_repository import SqlExtensionRepository
from .infrastructure.repositories.oauth_token_repository import SqlOauthTokenRepository
from .infrastructure.repositories.pg_drafts import PgDrafts
from .infrastructure.repositories.pg_secrets import PgSecrets
from .infrastructure.repositories.pg_snapshots import PgSnapshots
from .infrastructure.secret_key import secret_box

__all__ = ["ExtensionsBundle", "OAuthOps", "build", "secret_box"]


class OAuthOps:
    """The OAuth wire, as a namespace the routers hold rather than import.

    Every method resolves the function through `infrastructure.oauth_client` at call
    time, so a test that patches `oauth_client.make_client` patches exactly what a
    route dials.
    """

    def make_client(self):
        return oauth_client.make_client()

    async def discover(self, client, url):
        return await oauth_client.discover(client, url)

    async def discover_issuer(self, client, issuer, resource=""):
        return await oauth_client.discover_issuer(client, issuer, resource)

    async def register(self, client, disc, redirect_uri):
        return await oauth_client.register(client, disc, redirect_uri)

    async def exchange(self, client, disc, **kw):
        return await oauth_client.exchange(client, disc, **kw)

    async def revoke(self, client, disc, token, client_id, client_secret):
        return await oauth_client.revoke(client, disc, token, client_id, client_secret)


@dataclass
class ExtensionsBundle:
    """Everything the composition root hands to other modules."""
    home: Path
    catalog: Any
    secrets: Any
    store: Any
    tokens: Any
    writer: Any
    drafts: Any
    approvals: Any
    snapshots: Any
    installer: Any
    reader: Any
    pending: Any
    probe_fn: Any
    extensions: ExtensionService
    plugins: PluginService
    compose: ComposeService
    admin: ExtensionAdminService
    query: ExtensionsQueryService
    # The `RefreshLocks` the OAuth refresh runs under. None = the in-process
    # default; redis mode passes `RedisLocks`, which is what makes "one refresh
    # per connector" hold once a worker composes the same connector the agent is.
    locks: Any = None


def build(home: Path, engine, *, cfg=None, catalog_root: Path | None = None,
          pending=None, probe_fn=None, writer=None, store=None, tokens=None,
          on_change=None, box: SecretBox | None = None,
          locks=None) -> ExtensionsBundle:
    """Build every extensions object over one engine and one home.

    `store`, `tokens`, `writer`, `pending` and `probe_fn` can be supplied so the
    composition root and the web tests can hand in what they already have. `box` is
    the `SecretBox` the secrets table is sealed with; `main.build_app` passes the one
    it built before the startup pop, and a caller without one gets the process
    fallback (`secret_key.secret_box`). `locks` is the `RefreshLocks` the OAuth
    refresh takes: the composition root passes `RedisLocks` when there is more than
    one process, and None leaves the in-process `asyncio.Lock` per connector, which
    is all one process needs.
    """
    catalog = catalog_repository.CatalogRepository()
    store = store if store is not None else SqlExtensionRepository(engine)
    tokens = tokens if tokens is not None else SqlOauthTokenRepository(engine)
    secrets = PgSecrets(engine, box if box is not None else secret_box())
    writer = writer if writer is not None else FileCatalogWriter(home)
    drafts = PgDrafts(engine)
    snapshots = PgSnapshots(engine)
    approvals = ApprovalService(drafts=drafts, snapshots=snapshots)
    installer = git_installer.GitPluginInstaller(home)
    reader = DiskPluginReader()
    pending = pending if pending is not None else PendingStates()
    probe_fn = probe_fn if probe_fn is not None else SdkServerProber()

    extensions = ExtensionService(home, store=store, tokens=tokens, secrets=secrets,
                                  catalog=catalog, installer=installer)
    plugins = PluginService(home, store=store, installer=installer, reader=reader)
    # The refresher seals a refreshed token into the SAME secrets store the rest of
    # the bundle reads, with the same box: a token refreshed after `run()` popped
    # `CH_SECRETS_KEY` must be readable by the next process.
    oauth = OAuthService(wire=oauth_client.http_wire, secrets=secrets, locks=locks)
    compose = ComposeService(home, catalog=catalog, secrets=secrets, store=store,
                             tokens=tokens, oauth=oauth, root=catalog_root)
    admin = ExtensionAdminService(
        home, catalog=catalog, store=store, drafts=drafts, secrets=secrets,
        extensions=extensions, installer=installer, probe_fn=probe_fn,
        probe_timeout=getattr(cfg, "ext_probe_timeout_seconds", 60.0),
        clone_timeout=getattr(cfg, "plugin_clone_timeout_seconds", 120.0),
        catalog_root=catalog_root, on_change=on_change)
    query = ExtensionsQueryService(home, store=store, catalog=catalog, root=catalog_root)

    return ExtensionsBundle(
        home=home, catalog=catalog, secrets=secrets, store=store, tokens=tokens,
        writer=writer, drafts=drafts, approvals=approvals, snapshots=snapshots,
        installer=installer, reader=reader, pending=pending, probe_fn=probe_fn,
        extensions=extensions, plugins=plugins, compose=compose, admin=admin,
        query=query, locks=locks)

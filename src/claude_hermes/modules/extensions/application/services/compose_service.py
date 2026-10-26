"""Everything enabled and in scope, as the SDK wants it."""
from __future__ import annotations

from pathlib import Path

import structlog

from ...domain.compose import Composed, server_spec
from ...domain.exceptions import ComposeError
from ...domain.oauth import NeedsReauth
from ...domain.paths import plugin_root
from ..ports import (CatalogReader, ExtensionRepository, OAuthRefresher, SecretStore,
                     TokenRepository)

log = structlog.get_logger(__name__)


class ComposeService:
    """`for_scope` is the only thing the runner and the cron pass ever ask of this
    module. It is deliberately total: a broken extension is dropped from this turn and
    the conversation continues."""

    def __init__(self, home: Path, *, catalog: CatalogReader, secrets: SecretStore,
                 store: ExtensionRepository, tokens: TokenRepository,
                 oauth: OAuthRefresher | None = None, root: Path | None = None):
        self._home = home
        self._catalog = catalog
        self._secrets = secrets
        self._store = store
        self._tokens = tokens
        self._oauth = oauth
        self._root = root

    async def for_scope(self, scope: str, *, owner_id: int) -> Composed:
        """Everything THIS OWNER has enabled and in scope, as the SDK wants it.

        Never raises. A broken extension is dropped from this turn, its reason
        recorded in `last_error` on that owner's row, and the conversation
        continues — one dead server must not take the chat down with it.
        """
        home, store = self._home, self._store
        entries = self._catalog.load_all(self._root)
        secret_values = await self._secrets.load(owner_id=owner_id)

        servers: dict[str, dict] = {}
        names: list[str] = []
        plugins: list[dict] = []
        errors: dict[str, str] = {}
        rows = await store.enabled(owner_id=owner_id)
        judged: dict[str, str | None] = {}   # only what THIS scope actually evaluated

        for row in rows:
            name = row["name"]
            m = entries.get(name)
            if m is None:
                errors[name] = "no longer in the catalog"
                judged[name] = errors[name]
                continue
            if scope not in m.scopes:
                continue        # out of scope here — say nothing about its health
            try:
                if m.kind == "plugin":
                    path = plugin_root(home, m)      # raises if subdir escapes the clone
                    # A re-install that failed removed the directory but left the row
                    # enabled. Handing the SDK a path that is not there is exactly the
                    # half-state the registry is supposed to be incapable of.
                    if not (path / ".claude-plugin" / "plugin.json").is_file():
                        raise ComposeError(
                            f"plugin directory is missing or incomplete: {path}")
                    plugins.append({"type": "local", "path": str(path)})
                else:
                    if m.oauth_entries() and self._oauth is not None:
                        # Refresh writes the new access token into this owner's
                        # secrets; re-read so the spec carries it.
                        await self._oauth.ensure_fresh(home, self._tokens, m,
                                                       owner_id=owner_id)
                        secret_values = await self._secrets.load(owner_id=owner_id)
                    servers[name] = server_spec(m, secret_values)
                    # Server-level entry: allows every tool this server exposes. Belt and
                    # braces — permission_mode is bypassPermissions anyway.
                    names.append(f"mcp__{name}")
                judged[name] = None
            except NeedsReauth as e:
                # Exactly this string: the connectors tab derives `needs_reauth` from it.
                errors[name] = "needs_reauth"
                judged[name] = errors[name]
                log.warning("extension %s needs user %s to sign in again: %s",
                            name, owner_id, e)
            except Exception as e:                               # noqa: BLE001
                # Not just ComposeError: a corrupt manifest field or an unreadable plugin
                # directory must cost this one extension, not the whole conversation.
                errors[name] = str(e)
                judged[name] = errors[name]
                log.warning("extension %s dropped from this %s: %s", name, scope, e)

        # Persist only verdicts this scope is entitled to give: a cron pass must not clear
        # an error the turn recorded about a turn-only server it never looked at.
        for row in rows:
            name = row["name"]
            if name in judged and row["last_error"] != judged[name]:
                await store.set_error(name, judged[name], owner_id=owner_id)

        return Composed(mcp_servers=servers, tool_names=names, plugins=plugins,
                        errors=errors)

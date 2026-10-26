"""One implementation of connect / test / enable / disable / remove for MCP
extensions. `ext_manage` (chat) and the connectors router (browser) both call this,
so the two channels cannot drift on what "connected" means."""
from __future__ import annotations

from pathlib import Path

from ...domain.compose import server_spec
from ...domain.exceptions import ComposeError
from ...domain.manifest import Manifest
from ...domain.paths import plugin_root
from ...domain.registry import STATES, auth_kind, state_of  # noqa: F401  (re-exported)
from ...domain.secrets import missing
from ..dto import Outcome
from ..ports import (CatalogReader, ExtensionRepository, PluginInstaller, SecretStore,
                     ServerProber, TokenRepository)


class ExtensionService:
    """The use cases behind both channels. `home` is still a parameter of `connect`
    and `remove` because the prober and the clone both need it; everything else the
    service needs arrives as a port."""

    def __init__(self, home: Path, *, store: ExtensionRepository, tokens: TokenRepository,
                 secrets: SecretStore, catalog: CatalogReader,
                 installer: PluginInstaller | None = None):
        self._home = home
        self._store = store
        self._tokens = tokens
        self._secrets = secrets
        self._catalog = catalog
        self._installer = installer

    async def connect(self, m: Manifest, *, user_id: int, now: str, timeout: float,
                      probe_fn: ServerProber, install: bool = True,
                      owner_id: int) -> Outcome:
        """Secrets → spec → probe → (register), all in ONE owner's name.

        `user_id` stays the sender for the audit column; `owner_id` is whose
        credentials are read and whose row is written. On every lane that can
        reach this they are the same person, and saying both is what keeps it
        true when one day they are not.
        """
        values = await self._secrets.load(owner_id=owner_id)
        plain = missing(m.plain_secret_names(), values)
        if plain:
            return Outcome(False, "secrets_missing",
                           f"missing secret(s): {', '.join(plain)}",
                           missing=tuple(plain))
        if missing([a.name for a in m.oauth_entries()], values):
            return Outcome(False, "oauth_required", f"'{m.label}' needs a sign-in first")
        try:
            spec = server_spec(m, values)
        except ComposeError as e:
            return Outcome(False, "compose_failed", str(e))
        result = await probe_fn(self._home, m.name, spec, timeout=timeout)
        if result.status != "connected":
            return Outcome(False, "probe_failed", result.error or result.status)
        if install:
            await self._store.add(m.name, m.kind, m.version, installed_by=user_id,
                                  now=now, tool_count=len(result.tools),
                                  tool_names=list(result.tools), owner_id=owner_id)
        return Outcome(True, tools=tuple(result.tools))

    async def enable(self, name: str, *, owner_id: int) -> bool:
        if await self._store.get(name, owner_id=owner_id) is None:
            return False
        await self._store.set_state(name, "enabled", owner_id=owner_id)
        return True

    async def disable(self, name: str, *, owner_id: int) -> bool:
        if await self._store.get(name, owner_id=owner_id) is None:
            return False
        await self._store.set_state(name, "disabled", owner_id=owner_id)
        return True

    async def adopt(self, m: Manifest, *, user_id: int, now: str,
                    owner_id: int) -> bool:
        """Write this owner's row for a plugin whose clone is already on disk.

        A plugin's clone is deploy-wide and only the owner may fetch or delete it,
        so "enable it for me" cannot be a state flip on a row a second user does
        not have. It is this: adopt the shared tree. `False` when there is nothing
        to adopt, which the route answers 404 — never a row pointing at a
        directory that is not there.
        """
        try:
            root = plugin_root(self._home, m)
        except ComposeError:
            return False
        if not (root / ".claude-plugin" / "plugin.json").is_file():
            return False
        await self._store.add(m.name, m.kind, m.version, installed_by=user_id,
                              now=now, owner_id=owner_id)
        return True

    async def remove(self, name: str, m: Manifest | None = None, *, owner_id: int,
                     remove_clone: bool = False) -> bool:
        """Forget an extension for ONE owner: their registry row, their token row
        and their mirrored access token. The manifest stays.

        `remove_clone` is off by default and is the owner-only half: a plugin's
        clone is shared code, so a second user dropping their row must leave the
        tree where it is.
        """
        row = await self._store.get(name, owner_id=owner_id)
        if row is None:
            return False
        if remove_clone and row["kind"] == "plugin" and self._installer is not None:
            self._installer.remove(name)
        if m is not None:
            for a in m.oauth_entries():
                await self._secrets.forget(a.name, owner_id=owner_id)
        await self._tokens.delete(name, owner_id=owner_id)
        await self._store.remove(name, owner_id=owner_id)
        return True

    def source_of(self, m: Manifest) -> str:
        return "catalog" if self._catalog.is_repo_entry(m.name) else "custom"

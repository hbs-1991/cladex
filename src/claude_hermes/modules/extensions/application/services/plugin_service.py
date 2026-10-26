"""Installing a plugin is two calls, not one.

A plugin brings its own MCP servers — the CLI loads a plugin's `.mcp.json` itself,
without `catalog.parse`, a registry row or a probe — so the surface has to be readable
*before* anything is registered. `fetch_for_review` fetches and answers with that
surface plus a token bound to it; `confirm_install` recomputes the surface from the tree
on disk and only then writes the row. A fetched-but-unconfirmed clone is inert, because
`for_scope` reads rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...domain.exceptions import ComposeError
from ...domain.manifest import Manifest
from ...domain.paths import plugin_root
from ...domain.plugin_surface import BundledServer, surface_token
from ..ports import ExtensionRepository, PluginInstaller, PluginReader


class LocalServerRefused(Exception):
    """The fetched plugin would start a process in our container. Nothing was kept."""

    def __init__(self, servers: list[BundledServer]):
        self.servers = servers
        super().__init__("; ".join(f"{s.name} would run: {s.target}" for s in servers))


class NotFetched(Exception):
    """There is nothing on disk to confirm."""


class StaleInstall(Exception):
    """The clone changed between the two calls, so the token covers different bytes."""


@dataclass(frozen=True)
class Review:
    name: str
    label: str
    surface: str
    token: str
    servers: list[BundledServer]
    components: dict | None


class PluginService:
    def __init__(self, home: Path, *, store: ExtensionRepository,
                 installer: PluginInstaller, reader: PluginReader):
        self._home = home
        self._store = store
        self._installer = installer
        self._reader = reader

    async def fetch_for_review(self, m: Manifest, *,
                               timeout: float) -> tuple[Review, list[int]]:
        """Phase one: fetch, and show what was fetched. Installs nothing.

        Returns the review and every owner who already holds a row for this name.
        A re-fetch replaces the tree the SDK loads for ALL of them while their
        cached prefixes still describe the old one, so the caller resets each —
        confirm may never come.
        """
        root = await self._installer.clone(m, timeout=timeout)

        local = self._reader.local_servers(root)
        if local:
            # A remote server the owner did not pick is a URL they can read on the
            # screen below; a local one is an arbitrary argv arriving by a route that
            # never passed the catalog's npx/uvx/python allowlist. Refuse, and take the
            # clone with us — leaving it would be a fetched tree nobody asked to keep.
            self._installer.remove(m.name)
            raise LocalServerRefused(local)

        affected = await self._store.owners_of(m.name)
        text = self._reader.surface(m, root)
        return Review(name=m.name, label=m.label, surface=text,
                      token=surface_token(text),
                      servers=self._reader.bundled_servers(root),
                      components=self._reader.components(root)), affected

    async def confirm_install(self, m: Manifest, token: str, *, user_id: int,
                              now: str, owner_id: int) -> None:
        """Phase two: register what was read. The token is the whole check.

        Recomputing the surface from the tree on disk and comparing means a clone that
        changed between the two calls — a second install of a different manifest, a
        hand-edit under `agent_home/plugins/` — cannot be registered against an
        approval the owner gave for something else.
        """
        try:
            root = plugin_root(self._home, m)
        except ComposeError as e:
            raise NotFetched(str(e)) from e
        if self._reader.components(root) is None:
            raise NotFetched("fetch it again: there is nothing on disk")
        if surface_token(self._reader.surface(m, root)) != token:
            raise StaleInstall(
                "the fetched plugin changed since you read it; fetch it again")
        await self._store.add(m.name, m.kind, m.version, installed_by=user_id,
                              now=now, owner_id=owner_id)

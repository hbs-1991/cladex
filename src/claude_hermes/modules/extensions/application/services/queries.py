"""Read-only questions another module may ask about installed extensions.

`web/commands.py` (turns) needs the roots of the enabled plugins so it can list the
commands they register. It must not learn the registry's shape to get them, so this is
the whole of what it is given.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from ...domain.exceptions import ComposeError
from ...domain.paths import plugin_root
from ..ports import CatalogReader, ExtensionRepository

log = structlog.get_logger(__name__)


class ExtensionsQueryService:
    def __init__(self, home: Path, *, store: ExtensionRepository, catalog: CatalogReader,
                 root: Path | None = None):
        self._home = home
        self._store = store
        self._catalog = catalog
        self._root = root

    async def enabled_plugin_roots(self, *, owner_id: int) -> list[tuple[str, Path]]:
        """(catalog name, plugin root) for every plugin THIS OWNER has enabled whose
        manifest still exists and whose subdir stays inside its clone.

        The clone is shared; which of them a user loads is not. A plugin that fails
        either test is left out with a warning, never raised on: a command list that
        dies on one bad clone is worse than one that is short.
        """
        out: list[tuple[str, Path]] = []
        for row in await self._store.list("plugin", owner_id=owner_id):
            if row["state"] != "enabled":
                continue
            m = self._catalog.get(row["name"], self._root)
            if m is None:
                continue
            try:
                out.append((m.name, plugin_root(self._home, m)))
            except ComposeError as e:
                log.warning("plugin %s left out of the command list: %s", m.name, e)
        return out

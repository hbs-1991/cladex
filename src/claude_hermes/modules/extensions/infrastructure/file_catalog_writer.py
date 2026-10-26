"""S2: an approved manifest becomes a file in the user-catalog on /data."""
from __future__ import annotations

import os
from pathlib import Path

from claude_hermes.core.workspace import user_catalog_dir

from ..domain.manifest import Manifest

# user_catalog_dir is re-exported, not redefined: the writer and catalog.load_all() must
# resolve this one path through one function or they diverge silently. On /data (soft
# boundary) the agent can technically write here, so the real gate is the /approve
# command, not this path. The seam to a hard boundary is the CatalogWriter port.
__all__ = ["FileCatalogWriter", "user_catalog_dir"]


def _subdir(kind: str) -> str:
    return "plugins" if kind == "plugin" else "mcp"


class FileCatalogWriter:
    """S2: write the approved manifest straight into the user-catalog on /data.

    A future S1 implementation ships the same bytes to an isolated approver sidecar
    that owns the only writable mount; every caller depends on the `CatalogWriter`
    port, never on a path, so that swap costs one line in the composition root.
    """

    def __init__(self, home: Path):
        self._home = home

    def promote(self, m: Manifest, raw: bytes) -> None:
        dest = user_catalog_dir(self._home) / _subdir(m.kind) / f"{m.name}.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".yaml.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, dest)          # atomic: a reader never sees a half-written entry

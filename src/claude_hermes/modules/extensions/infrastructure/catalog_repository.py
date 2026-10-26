"""Reading the catalog off disk: the reviewed half that ships with the image, and the
chat-approved half on the /data volume.

`parse`, `Manifest` and `_CATALOG_DIR` are re-exported here so a caller of the
reader has one import, and a test that patches `catalog_repository._CATALOG_DIR`
patches the one every function below reads.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog
import yaml

from claude_hermes.core.workspace import user_catalog_dir

from ..domain.exceptions import ManifestError
from ..domain.manifest import (MANIFEST_VERSION, STDIO_COMMANDS, VALID_AUTH_KINDS,
                               VALID_SCOPES, VALID_TRANSPORTS, Auth, Manifest, parse,
                               valid_name)

log = structlog.get_logger(__name__)

# The catalog is read in place, never seeded into agent_home: workspace._copy_missing()
# copies only when absent, so a seeded catalog would freeze at first-run and never gain
# a new entry. The catalog is code, not state.
#
# Where it lives depends on how the package was installed. On the dev host this is a
# src layout and catalog/ sits at the repo root, FIVE levels up from this file
# (infrastructure → extensions → modules → claude_hermes → src → repo). In the container
# the package is pip-installed into site-packages, where that walk is nonsense — so the
# image copies catalog/ in and points CH_CATALOG at it. Without that env var the
# container would silently run with an EMPTY catalog: every install refused, no error.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_CATALOG_DIR = Path(os.environ["CH_CATALOG"]) if os.environ.get("CH_CATALOG") \
    else _REPO_ROOT / "catalog"

# The user-catalog — the chat-approved half — is deliberately NOT a constant here, and
# has no env var of its own. It is state, so core.workspace.user_catalog_dir() derives it
# from CH_HOME on every call (NFR-5). The asymmetry with _CATALOG_DIR above is the point,
# not an inconsistency to tidy up: the reviewed catalog is CODE whose location cannot be
# derived (src layout vs site-packages), while state's location is a fact the system
# already knows. A second env var restating it bought nothing and cost a silent bug.

__all__ = [
    "MANIFEST_VERSION", "STDIO_COMMANDS", "VALID_AUTH_KINDS", "VALID_SCOPES",
    "VALID_TRANSPORTS", "Auth", "CatalogRepository", "Manifest", "ManifestError",
    "get", "is_repo_entry", "load_all", "parse", "valid_name",
]


def _scan(base: Path) -> dict[str, Manifest]:
    """Every valid manifest under one catalog root, keyed by name. A broken file is
    logged and skipped — one bad manifest must not blind the agent to the whole catalog."""
    out: dict[str, Manifest] = {}
    for kind, subdir in (("mcp", "mcp"), ("plugin", "plugins")):
        directory = base / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                m = parse(data, kind)
            except (yaml.YAMLError, ManifestError):
                log.warning("skipping invalid catalog manifest %s", path, exc_info=True)
                continue
            out[m.name] = m
    return out


def _merge(repo_dir: Path, user_dir: Path) -> dict[str, Manifest]:
    """Reviewed catalog overlaid with the chat-approved user-catalog. The repo always
    wins: a user entry may only fill a name the repo does not define. This is the
    anti-downgrade rule — a chat-authored 'filesystem' must not replace the reviewed one."""
    entries = _scan(repo_dir)
    for name, m in _scan(user_dir).items():
        if name in entries:
            log.warning("user-catalog entry %r shadows a reviewed entry; ignored", name)
            continue
        entries[name] = m
    return entries


def load_all(root: Path | None = None) -> dict[str, Manifest]:
    """Every valid manifest, keyed by name. An explicit `root` (tests, fixtures) scans
    exactly that directory. In production (`root is None`) the reviewed repo catalog is
    merged with the writable user-catalog on /data, repo winning on collision."""
    if root is not None:
        return _scan(root)
    return _merge(_CATALOG_DIR, user_catalog_dir())


def is_repo_entry(name: str) -> bool:
    """True iff `name` is a reviewed entry — used to refuse a user-catalog write that
    would shadow one."""
    return name in _scan(_CATALOG_DIR)


def get(name: str, root: Path | None = None) -> Manifest | None:
    return load_all(root).get(name)


class CatalogRepository:
    """The `CatalogReader` port. Delegates to the module functions above rather than
    holding the path itself, so the one `_CATALOG_DIR` a test patches is the one every
    caller reads — the object and the module functions cannot disagree."""

    def load_all(self, root: Path | None = None) -> dict[str, Manifest]:
        return load_all(root)

    def get(self, name: str, root: Path | None = None) -> Manifest | None:
        return get(name, root)

    def is_repo_entry(self, name: str) -> bool:
        return is_repo_entry(name)

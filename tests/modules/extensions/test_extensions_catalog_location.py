"""Where the reviewed catalog is found from.

The walk up from this file is depth-sensitive, and the module moved two packages deeper
in Wave 2. If the depth is wrong, `load_all()` silently returns an empty catalog on a dev
host: every install refused, no error anywhere. The container is covered by CH_CATALOG
(`tests/test_catalog.py`), so this is the other half of the same rule.
"""
from __future__ import annotations

import importlib
from pathlib import Path

from claude_hermes.modules.extensions.infrastructure import catalog_repository

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_the_dev_host_walk_lands_on_the_repo_catalog(monkeypatch):
    monkeypatch.delenv("CH_CATALOG", raising=False)
    reloaded = importlib.reload(catalog_repository)
    try:
        assert reloaded._CATALOG_DIR == REPO_ROOT / "catalog"
        assert reloaded._CATALOG_DIR.is_dir()
        # Not just a path that exists — the reader must actually see entries through it.
        assert reloaded.is_repo_entry("notion")
    finally:
        importlib.reload(catalog_repository)


async def test_the_reader_never_looks_in_the_draft_queue(tmp_path, monkeypatch, engine):
    """Invariant 1: agent proposes, human promotes. A draft is inert because the reader
    has no way to see one, not because something downstream checks — and since Wave 3a
    the queue is a table the reader has no connection to at all."""
    from tests.ext_helpers import drafts_for

    monkeypatch.setenv("CH_HOME", str(tmp_path))
    drafts = drafts_for(engine)
    await drafts.save("""
manifest_version: 1
name: sneaky
label: Sneaky
description: d
version: 1.0.0
transport: http
url: https://example.test/mcp
scopes: [turn]
""")
    assert await drafts.read("mcp", "sneaky") is not None
    assert "sneaky" not in catalog_repository.load_all()

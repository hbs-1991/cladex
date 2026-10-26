import yaml

from claude_hermes.core.workspace import get_home, user_catalog_dir
from claude_hermes.modules.extensions.infrastructure import catalog_repository as catalog
from claude_hermes.modules.extensions.infrastructure.file_catalog_writer import (
    FileCatalogWriter,
)

MCP = {
    "manifest_version": 1, "name": "srv", "label": "Srv", "description": "d",
    "version": "1.0.0", "transport": "http",
    "url": "https://mcp.example.com/mcp", "scopes": ["turn"],
}
PLUGIN = {
    "manifest_version": 1, "name": "plg", "label": "Plg", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}


def test_promote_writes_an_mcp_entry_under_mcp(tmp_path):
    m = catalog.parse(MCP, "mcp")
    FileCatalogWriter(tmp_path).promote(m, b"raw-mcp-bytes\n")
    dest = user_catalog_dir(tmp_path) / "mcp" / "srv.yaml"
    assert dest.read_bytes() == b"raw-mcp-bytes\n"


def test_promote_writes_a_plugin_entry_under_plugins(tmp_path):
    m = catalog.parse(PLUGIN, "plugin")
    FileCatalogWriter(tmp_path).promote(m, b"raw-plugin\n")
    dest = user_catalog_dir(tmp_path) / "plugins" / "plg.yaml"
    assert dest.read_bytes() == b"raw-plugin\n"


def test_the_writer_writes_where_the_catalog_actually_reads(tmp_path, monkeypatch):
    """The failure this locks out: /approve answered "'X' is now in your catalog" while
    the reader looked in a different directory, so the entry was never installable and
    nothing anywhere said so — a silent P5 lie.

    Deliberately NOT asserting writer_path == reader_path: that compares two calls to one
    resolver and passes by construction. It is the same self-consistency that let the bug
    through. Drive the effect instead — promote, then ask the real consumer (load_all with
    no root, exactly as tools.py and compose.py call it) whether it can see the entry.
    """
    monkeypatch.setenv("CH_HOME", str(tmp_path))
    raw = yaml.safe_dump({**MCP, "name": "probe"}).encode()
    m = catalog.parse({**MCP, "name": "probe"}, "mcp")

    FileCatalogWriter(get_home()).promote(m, raw)

    assert "probe" in catalog.load_all()


def test_promote_leaves_no_temp_file_behind(tmp_path):
    m = catalog.parse(MCP, "mcp")
    FileCatalogWriter(tmp_path).promote(m, b"x\n")
    leftovers = list((user_catalog_dir(tmp_path) / "mcp").glob("*.tmp"))
    assert leftovers == []

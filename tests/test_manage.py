from claude_hermes.modules.extensions.application.services.extension_service import (
    ExtensionService,
)
from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.domain.probe import ProbeResult
from claude_hermes.modules.extensions.domain.registry import state_of
from claude_hermes.modules.extensions.infrastructure.catalog_repository import (
    CatalogRepository,
)
from claude_hermes.modules.extensions.infrastructure.git_installer import (
    GitPluginInstaller,
)
from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (
    SqlExtensionRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (
    SqlOauthTokenRepository,
)
from claude_hermes.modules.extensions.presentation.schemas import connector_to_wire as to_wire

from conftest import ensure_users
from tests.ext_helpers import secrets_for

NOW = "2026-09-09T10:00:00+00:00"
HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp", "scopes": ["turn"],
    "auth": [{"name": "EXAMPLE_API_KEY", "prompt": "Example API key", "header": "Authorization",
              "format": "Bearer ${EXAMPLE_API_KEY}"}],
}
OAUTH = {**HTTP, "name": "notion", "label": "Notion",
         "auth": [{"name": "NOTION_MCP_TOKEN", "kind": "oauth", "header": "Authorization", "format": "Bearer ${NOTION_MCP_TOKEN}"}]}


async def ok_probe(home, name, spec, timeout=60.0):
    return ProbeResult(status="connected", tools=["search", "create"])


async def bad_probe(home, name, spec, timeout=60.0):
    return ProbeResult(status="failed", error="connection refused")


def make(engine, home):
    """The service exactly as `wiring.build` assembles it, over the test engine."""
    store = SqlExtensionRepository(engine)
    tokens = SqlOauthTokenRepository(engine)
    svc = ExtensionService(home, store=store, tokens=tokens, secrets=secrets_for(engine),
                           catalog=CatalogRepository(), installer=GitPluginInstaller(home))
    return store, tokens, svc


async def test_connect_reports_missing_secrets_and_registers_nothing(tmp_path, engine):
    await ensure_users(engine, owner=1)
    store, _, svc = make(engine, tmp_path)
    out = await svc.connect(parse(HTTP, "mcp"), user_id=1, now=NOW, timeout=1,
                            probe_fn=ok_probe, owner_id=1)
    assert not out.ok and out.code == "secrets_missing" and out.missing == ("EXAMPLE_API_KEY",)
    assert await store.get("example", owner_id=1) is None


async def test_connect_needs_a_sign_in_for_an_oauth_server(tmp_path, engine):
    await ensure_users(engine, owner=1)
    _, _, svc = make(engine, tmp_path)
    out = await svc.connect(parse(OAUTH, "mcp"), user_id=1, now=NOW, timeout=1,
                            probe_fn=ok_probe, owner_id=1)
    assert not out.ok and out.code == "oauth_required"


async def test_connect_probes_registers_and_counts_tools(tmp_path, engine):
    await ensure_users(engine, owner=1)
    store, _, svc = make(engine, tmp_path)
    await secrets_for(engine).put("EXAMPLE_API_KEY", "abc", owner_id=1)
    m = parse(HTTP, "mcp")
    out = await svc.connect(m, user_id=1, now=NOW, timeout=1, probe_fn=ok_probe,
                            install=False, owner_id=1)
    assert out.ok and out.tools == ("search", "create") and await store.get("example", owner_id=1) is None      # test only
    out = await svc.connect(m, user_id=1, now=NOW, timeout=1, probe_fn=ok_probe, owner_id=1)
    row = await store.get("example", owner_id=1)
    assert out.ok and row["state"] == "enabled" and row["tool_count"] == 2 and row["installed_by"] == 1
    assert to_wire(m, row, {}, source="catalog")["tool_names"] == ["search", "create"]
    out = await svc.connect(m, user_id=1, now=NOW, timeout=1, probe_fn=bad_probe, owner_id=1)
    assert not out.ok and out.code == "probe_failed" and "connection refused" in out.message


async def test_enable_disable_remove(tmp_path, engine):
    await ensure_users(engine, owner=1)
    store, tokens, svc = make(engine, tmp_path)
    assert await svc.disable("example", owner_id=1) is False    # not installed
    await secrets_for(engine).put("NOTION_MCP_TOKEN", "at", owner_id=1)
    await store.add("notion", "mcp", "1", installed_by=1, now=NOW, tool_count=1,
                    owner_id=1)
    await tokens.put("notion", owner_id=1, now=NOW, access_token="at",
                     refresh_token="rt")
    assert await svc.disable("notion", owner_id=1) and (await store.get("notion", owner_id=1))["state"] == "disabled"
    assert await svc.enable("notion", owner_id=1) and (await store.get("notion", owner_id=1))["state"] == "enabled"
    assert await svc.remove("notion", parse(OAUTH, "mcp"), owner_id=1)
    assert await store.get("notion", owner_id=1) is None and await tokens.get("notion", owner_id=1) is None
    assert "NOTION_MCP_TOKEN" not in await secrets_for(engine).load(owner_id=1)


def test_state_and_wire_shape():
    m = parse(HTTP, "mcp")
    assert state_of(None) == "available"
    assert state_of({"state": "enabled", "last_error": None}) == "connected"
    assert state_of({"state": "disabled", "last_error": None}) == "disabled"
    assert state_of({"state": "enabled", "last_error": "needs_reauth"}) == "needs_reauth"
    assert state_of({"state": "enabled", "last_error": "boom"}) == "error"
    w = to_wire(m, {"state": "enabled", "last_error": None, "tool_count": 2, "installed_at": None}, {"EXAMPLE_API_KEY": "x"}, source="catalog")
    assert w == {"name": "example", "label": "Example", "description": "d", "source": "catalog", "transport": "http",
                 "auth": "secret", "secrets": [{"name": "EXAMPLE_API_KEY", "prompt": "Example API key", "set": True}],
                 "state": "connected", "tools": 2, "tool_names": [], "last_error": None, "scopes": ["turn"],
                 "installed_at": None, "endpoint": "https://mcp.example.com/mcp"}
    w = to_wire(parse(OAUTH, "mcp"), None, {}, source="custom")
    assert w["auth"] == "oauth" and w["secrets"] == [] and w["state"] == "available" and w["tools"] is None

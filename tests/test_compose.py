import yaml

from claude_hermes.modules.extensions.application.services.compose_service import (
    ComposeService,
)
from claude_hermes.modules.extensions.application.services.oauth_service import (
    OAuthService,
)
from claude_hermes.modules.extensions.domain.compose import server_spec
from claude_hermes.modules.extensions.domain.exceptions import ComposeError
from claude_hermes.modules.extensions.domain.manifest import parse
from claude_hermes.modules.extensions.domain.oauth import NeedsReauth
from claude_hermes.modules.extensions.domain.paths import plugin_dir
from claude_hermes.modules.extensions.infrastructure import oauth_client
from claude_hermes.modules.extensions.infrastructure.catalog_repository import (
    CatalogRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (
    SqlExtensionRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (
    SqlOauthTokenRepository,
)

from conftest import ensure_users
from tests.ext_helpers import secrets_for

NOW = "2026-07-14T10:00:00+00:00"

HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn", "cron"],
    "auth": [{"name": "EXAMPLE_API_KEY", "prompt": "key", "header": "Authorization",
              "format": "Bearer ${EXAMPLE_API_KEY}"}],
}
STDIO_TURN_ONLY = {
    "manifest_version": 1, "name": "fs", "label": "FS", "description": "d",
    "version": "1.0.0", "transport": "stdio", "command": "npx",
    "args": ["-y", "server-filesystem", "/data"], "scopes": ["turn"],
}
PLUGIN = {
    "manifest_version": 1, "name": "thing", "label": "Thing", "description": "d",
    "version": "1.0.0", "repo": "https://github.com/o/r", "ref": "v1.0.0",
    "scopes": ["turn"],
}


def write_catalog(root):
    (root / "mcp").mkdir(parents=True)
    (root / "plugins").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")
    (root / "mcp" / "fs.yaml").write_text(yaml.safe_dump(STDIO_TURN_ONLY), encoding="utf-8")
    (root / "plugins" / "thing.yaml").write_text(yaml.safe_dump(PLUGIN), encoding="utf-8")


def make(engine, home, root):
    """The composer exactly as `wiring.build` assembles it: the registry, the token
    table and the sealed secrets over one engine, and the real `OAuthService` over the
    http wire as the refresher."""
    store = SqlExtensionRepository(engine)
    secrets = secrets_for(engine)
    oauth = OAuthService(wire=oauth_client.http_wire, secrets=secrets)
    svc = ComposeService(home, catalog=CatalogRepository(), secrets=secrets, store=store,
                         tokens=SqlOauthTokenRepository(engine), oauth=oauth, root=root)
    return store, svc


async def put_secret(engine, name, value, owner_id=1):
    """The `SecretStore` over the same engine the registry uses — Wave 3a put both
    tables on the state database, so a test no longer writes a file under tmp_path.

    Wave 5: a secret belongs to somebody. The default is the deploy owner, which
    is who every single-user case here means."""
    await secrets_for(engine).put(name, value, owner_id=owner_id)


def install_plugin_dir(home, name="thing"):
    """What a successful clone leaves behind. A plugin the composer will hand to the
    SDK must actually be on disk — see test_a_plugin_whose_directory_vanished_is_dropped."""
    d = plugin_dir(home, name) / ".claude-plugin"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("{}", encoding="utf-8")


def test_server_spec_http_puts_the_secret_in_a_header():
    m = parse(HTTP, "mcp")
    spec = server_spec(m, {"EXAMPLE_API_KEY": "abc"})
    assert spec == {"type": "http", "url": "https://mcp.example.com/mcp",
                    "headers": {"Authorization": "Bearer abc"}}


def test_server_spec_stdio_shape():
    m = parse(STDIO_TURN_ONLY, "mcp")
    spec = server_spec(m, {})
    assert spec == {"type": "stdio", "command": "npx",
                    "args": ["-y", "server-filesystem", "/data"], "env": {}}


def test_server_spec_raises_when_a_secret_is_missing():
    m = parse(HTTP, "mcp")
    try:
        server_spec(m, {})
    except ComposeError as e:
        assert "EXAMPLE_API_KEY" in str(e)
    else:
        raise AssertionError("expected ComposeError")


async def test_for_scope_turn_includes_both_kinds(tmp_path, engine):
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await put_secret(engine, "EXAMPLE_API_KEY", "abc")
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    install_plugin_dir(tmp_path)

    out = await compose.for_scope("turn", owner_id=1)
    assert set(out.mcp_servers) == {"example"}
    assert out.tool_names == ["mcp__example"]
    assert out.plugins == [
        {"type": "local", "path": str(plugin_dir(tmp_path, "thing"))}
    ]
    assert out.errors == {}


async def test_a_plugin_whose_directory_vanished_is_dropped(tmp_path, engine):
    """The retry path: clone_plugin removes the old directory before re-cloning, and the
    re-clone failed. The row survives, enabled — but the directory does not. Handing the
    SDK a path that is not there is the half-state the registry may not have."""
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW, owner_id=1)   # never cloned

    out = await compose.for_scope("turn", owner_id=1)
    assert out.plugins == []
    assert "missing or incomplete" in out.errors["thing"]
    assert (await store.get("thing", owner_id=1))["last_error"]


async def test_for_scope_cron_drops_a_turn_only_server(tmp_path, engine):
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await put_secret(engine, "EXAMPLE_API_KEY", "abc")
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("fs", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)

    out = await compose.for_scope("cron", owner_id=1)
    assert set(out.mcp_servers) == {"example"}   # 'fs' is scopes: [turn]


async def test_a_disabled_extension_is_absent(tmp_path, engine):
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await put_secret(engine, "EXAMPLE_API_KEY", "abc")
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.set_state("example", "disabled", owner_id=1)

    out = await compose.for_scope("turn", owner_id=1)
    assert out.mcp_servers == {}


async def test_a_server_whose_secret_vanished_is_dropped_not_raised(tmp_path, engine):
    """One broken server must not kill the turn."""
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)   # no secret

    out = await compose.for_scope("turn", owner_id=1)
    assert out.mcp_servers == {}
    assert "EXAMPLE_API_KEY" in out.errors["example"]
    assert (await store.get("example", owner_id=1))["last_error"]


async def test_a_row_whose_manifest_vanished_is_dropped_not_raised(tmp_path, engine):
    """The image was rolled back and the entry is gone. Do not crash."""
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await store.add("ghost", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)

    out = await compose.for_scope("turn", owner_id=1)
    assert out.mcp_servers == {}
    assert "catalog" in out.errors["ghost"]


async def test_a_healthy_server_clears_a_stale_error(tmp_path, engine):
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.set_error("example", "old failure", owner_id=1)
    await put_secret(engine, "EXAMPLE_API_KEY", "abc")

    await compose.for_scope("turn", owner_id=1)
    assert (await store.get("example", owner_id=1))["last_error"] is None


OAUTH = {
    "manifest_version": 1, "name": "notion", "label": "Notion", "description": "d", "version": "1",
    "transport": "http", "url": "https://mcp.notion.com/mcp", "scopes": ["turn"],
    "auth": [{"name": "NOTION_MCP_TOKEN", "kind": "oauth", "header": "Authorization",
              "format": "Bearer ${NOTION_MCP_TOKEN}"}],
}


async def test_an_expiring_oauth_token_is_refreshed_before_the_spec_is_built(tmp_path, engine, monkeypatch):
    root = tmp_path / "catalog"
    write_catalog(root)
    (root / "mcp" / "notion.yaml").write_text(yaml.safe_dump(OAUTH), encoding="utf-8")
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await store.add("notion", "mcp", "1", installed_by=1, now=NOW, owner_id=1)
    await put_secret(engine, "NOTION_MCP_TOKEN", "stale")
    calls = []

    async def fresh(self, home, tokens, m, *, owner_id, now=None):
        calls.append(m.name)
        await put_secret(engine, "NOTION_MCP_TOKEN", "new")

    monkeypatch.setattr(OAuthService, "ensure_fresh", fresh)
    c = await compose.for_scope("turn", owner_id=1)
    assert calls == ["notion"]
    assert c.mcp_servers["notion"]["headers"] == {"Authorization": "Bearer new"}


async def test_a_refused_refresh_drops_the_server_with_needs_reauth(tmp_path, engine, monkeypatch):
    root = tmp_path / "catalog"
    write_catalog(root)
    (root / "mcp" / "notion.yaml").write_text(yaml.safe_dump(OAUTH), encoding="utf-8")
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, owner=1)
    await store.add("notion", "mcp", "1", installed_by=1, now=NOW, owner_id=1)

    async def refused(self, home, tokens, m, *, owner_id, now=None):
        raise NeedsReauth("invalid_grant")

    monkeypatch.setattr(OAuthService, "ensure_fresh", refused)
    c = await compose.for_scope("turn", owner_id=1)
    assert "notion" not in c.mcp_servers and c.errors["notion"] == "needs_reauth"
    assert (await store.get("notion", owner_id=1))["last_error"] == "needs_reauth"


async def test_each_owner_composes_their_own_servers(tmp_path, engine):
    """B's turn must not be handed A's server, and A's must not lose theirs."""
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, 7, owner=1)
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await put_secret(engine, "EXAMPLE_API_KEY", "owners", owner_id=1)

    assert "example" in (await compose.for_scope("turn", owner_id=1)).mcp_servers
    assert (await compose.for_scope("turn", owner_id=7)).mcp_servers == {}


async def test_a_missing_secret_is_that_owners_problem_alone(tmp_path, engine):
    root = tmp_path / "catalog"
    write_catalog(root)
    store, compose = make(engine, tmp_path, root)
    await ensure_users(engine, 7, owner=1)
    for uid in (1, 7):
        await store.add("example", "mcp", "1.0.0", installed_by=uid, now=NOW,
                        owner_id=uid)
    await put_secret(engine, "EXAMPLE_API_KEY", "owners", owner_id=1)

    assert (await compose.for_scope("turn", owner_id=1)).errors == {}
    assert "example" in (await compose.for_scope("turn", owner_id=7)).errors
    # and the verdict landed on the right row
    assert (await store.get("example", owner_id=1))["last_error"] is None

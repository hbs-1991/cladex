"""An installed extension reaches the SDK options of a turn, and nothing does
when no composer is wired — the option composition `SdkTurnExecutor` does over
`ExtensionComposePort`, driven through the real `ComposeService`."""
import yaml

from claude_hermes.core.app_context import ExtensionCompose
from claude_hermes.core.crypto import SecretBox
from claude_hermes.modules.extensions.application.services.compose_service import (
    ComposeService,
)
from claude_hermes.modules.extensions.infrastructure.catalog_repository import (
    CatalogRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (
    SqlExtensionRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (
    SqlOauthTokenRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.pg_secrets import (
    PgSecrets,
)
# The SDK seam moved with the loop in Wave 2: `query` is read off the module
# that calls it, so this is where it has to be patched.
from claude_hermes.modules.turns.infrastructure import sdk_runner

from conftest import ensure_users
from tests.test_runner import make_stack

NOW = "2026-07-14T10:00:00+00:00"
HTTP = {
    "manifest_version": 1, "name": "example", "label": "Example", "description": "d",
    "version": "1.0.0", "transport": "http", "url": "https://mcp.example.com/mcp",
    "scopes": ["turn", "cron"],
}


async def build(engine, tmp_path, with_ext: bool):
    root = tmp_path / "catalog"
    (root / "mcp").mkdir(parents=True)
    (root / "mcp" / "example.yaml").write_text(yaml.safe_dump(HTTP), encoding="utf-8")

    compose = None
    if with_ext:
        # `tg:private:1` is user 1's lane, so user 1's row is the one the turn
        # composes. The `users` row has to exist before the foreign key does —
        # `make_stack` writes it too, but only after this.
        await ensure_users(engine, 1)
        store = SqlExtensionRepository(engine)
        await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW,
                        owner_id=1)
        # The composer the composition root builds (`extensions.wiring`), over
        # the same repositories, and `ExtensionCompose` is its `for_turn` face.
        compose = ExtensionCompose(ComposeService(
            tmp_path, catalog=CatalogRepository(),
            secrets=PgSecrets(engine, SecretBox(SecretBox.generate_key())),
            store=store, tokens=SqlOauthTokenRepository(engine), root=root))
    # Constructor arguments since Phase C: the composer is built once, not
    # re-derived from attributes at turn time.
    stack = await make_stack(engine, tmp_path, compose=compose,
                             catalog_root=root if with_ext else None)
    return stack.runner


async def test_an_installed_server_reaches_the_options(engine, tmp_path, monkeypatch):
    runner = await build(engine, tmp_path, with_ext=True)
    seen = {}

    async def fake_query(*, prompt, options):
        seen["options"] = options

        class R:
            session_id = "s"
            result = "ok"
        yield R()

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    await runner.run_turn("tg:private:1", "привет")

    opts = seen["options"]
    assert "example" in opts.mcp_servers          # alongside 'hermes'
    assert "hermes" in opts.mcp_servers
    assert "mcp__example" in opts.allowed_tools


async def test_without_a_store_the_options_are_unchanged(engine, tmp_path, monkeypatch):
    """The feature must be inert until main.py wires it."""
    runner = await build(engine, tmp_path, with_ext=False)
    seen = {}

    async def fake_query(*, prompt, options):
        seen["options"] = options

        class R:
            session_id = "s"
            result = "ok"
        yield R()

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    await runner.run_turn("tg:private:1", "привет")

    assert set(seen["options"].mcp_servers) == {"hermes"}

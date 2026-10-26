from claude_hermes.modules.extensions.domain.registry import tool_names
from claude_hermes.modules.extensions.infrastructure.repositories.extension_repository import (
    SqlExtensionRepository,
)
from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (
    SqlOauthTokenRepository,
)

from conftest import ensure_users

NOW = "2026-07-14T10:00:00+00:00"


async def test_add_then_get(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=7, now=NOW, owner_id=1)
    row = await store.get("fetch", owner_id=1)
    assert row["kind"] == "mcp"
    assert row["state"] == "enabled"
    assert row["installed_by"] == 7
    assert row["catalog_version"] == "1.0.0"
    assert row["last_error"] is None


async def test_get_of_a_missing_name_is_none(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    assert await store.get("nope", owner_id=1) is None


async def test_add_twice_upserts_rather_than_raising(engine):
    """Re-adding is how a user reinstalls after a failure. It must not blow up."""
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=7, now=NOW, owner_id=1)
    await store.add("fetch", "mcp", "2.0.0", installed_by=8, now=NOW, owner_id=1)
    row = await store.get("fetch", owner_id=1)
    assert row["catalog_version"] == "2.0.0" and row["installed_by"] == 8
    assert len(await store.list(owner_id=1)) == 1


async def test_list_filters_by_kind(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    assert [r["name"] for r in await store.list("plugin", owner_id=1)] == ["thing"]
    assert len(await store.list(owner_id=1)) == 2


async def test_enabled_excludes_disabled(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("filesystem", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.set_state("fetch", "disabled", owner_id=1)
    assert [r["name"] for r in await store.enabled(owner_id=1)] == ["filesystem"]


async def test_set_error_and_clear_it(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.set_error("fetch", "connection refused", owner_id=1)
    assert (await store.get("fetch", owner_id=1))["last_error"] == "connection refused"
    await store.set_error("fetch", None, owner_id=1)
    assert (await store.get("fetch", owner_id=1))["last_error"] is None


async def test_remove(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.remove("fetch", owner_id=1)
    assert await store.get("fetch", owner_id=1) is None


async def test_remove_of_a_missing_name_is_a_no_op(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.remove("nope", owner_id=1)


async def test_tool_count_is_kept_on_the_row(engine):
    await ensure_users(engine, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("example", "mcp", "1.0.0", installed_by=1, now=NOW,
                    tool_count=12, owner_id=1)
    assert (await store.get("example", owner_id=1))["tool_count"] == 12
    await store.set_tool_count("example", 3, owner_id=1)
    assert (await store.get("example", owner_id=1))["tool_count"] == 3
    await store.set_tools("example", ["search", "create"], owner_id=1)
    row = await store.get("example", owner_id=1)
    assert row["tool_count"] == 2 and tool_names(row) == ["search", "create"]
    assert tool_names({"tool_names": None}) == [] and tool_names(None) == []
    # Registering a server writes nothing into the token table beside it.
    assert await SqlOauthTokenRepository(engine).get("example", owner_id=1) is None


async def test_two_owners_keep_separate_rows(engine):
    await ensure_users(engine, 7, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("fetch", "mcp", "2.0.0", installed_by=7, now=NOW, owner_id=7)
    assert (await store.get("fetch", owner_id=1))["catalog_version"] == "1.0.0"
    assert (await store.get("fetch", owner_id=7))["catalog_version"] == "2.0.0"


async def test_one_owners_error_does_not_reach_the_other(engine):
    """A probe with one person's token failing is not a verdict on anybody else."""
    await ensure_users(engine, 7, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("fetch", "mcp", "1.0.0", installed_by=7, now=NOW, owner_id=7)
    await store.set_error("fetch", "needs_reauth", owner_id=7)
    assert (await store.get("fetch", owner_id=1))["last_error"] is None
    assert (await store.get("fetch", owner_id=7))["last_error"] == "needs_reauth"


async def test_enabled_and_list_see_only_their_owner(engine):
    await ensure_users(engine, 7, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("thing", "plugin", "1.0.0", installed_by=7, now=NOW, owner_id=7)
    assert [r["name"] for r in await store.enabled(owner_id=1)] == ["fetch"]
    assert [r["name"] for r in await store.list(owner_id=7)] == ["thing"]


async def test_remove_takes_one_owners_row_only(engine):
    await ensure_users(engine, 7, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("fetch", "mcp", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("fetch", "mcp", "1.0.0", installed_by=7, now=NOW, owner_id=7)
    await store.remove("fetch", owner_id=7)
    assert await store.get("fetch", owner_id=1) is not None
    assert await store.get("fetch", owner_id=7) is None


async def test_owners_of_lists_everyone_holding_the_name(engine):
    await ensure_users(engine, 7, owner=1)
    store = SqlExtensionRepository(engine)
    await store.add("thing", "plugin", "1.0.0", installed_by=1, now=NOW, owner_id=1)
    await store.add("thing", "plugin", "1.0.0", installed_by=7, now=NOW, owner_id=7)
    assert await store.owners_of("thing") == [1, 7]


async def test_oauth_tokens_are_per_owner(engine):
    await ensure_users(engine, 7, owner=1)
    tokens = SqlOauthTokenRepository(engine)
    await tokens.put("notion", now=NOW, access_token="a", owner_id=1)
    await tokens.put("notion", now=NOW, access_token="b", owner_id=7)
    assert (await tokens.get("notion", owner_id=1))["access_token"] == "a"
    assert (await tokens.get("notion", owner_id=7))["access_token"] == "b"
    await tokens.delete("notion", owner_id=7)
    assert await tokens.get("notion", owner_id=1) is not None
    assert await tokens.get("notion", owner_id=7) is None

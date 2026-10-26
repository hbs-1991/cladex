import pytest

from conftest import ensure_users

from claude_hermes.modules.extensions.infrastructure.repositories.oauth_token_repository import (
    SqlOauthTokenRepository,
)

NOW = "2026-09-09T10:00:00+00:00"


async def test_put_merges_fields_and_get_returns_the_row(engine):
    await ensure_users(engine, owner=1)
    tokens = SqlOauthTokenRepository(engine)
    assert await tokens.get("notion", owner_id=1) is None
    await tokens.put("notion", owner_id=1, now=NOW, issuer="https://as.example",
                     client_id="c1")
    await tokens.put("notion", owner_id=1, now=NOW, access_token="at",
                     refresh_token="rt", expires_at="2026-09-09T11:00:00+00:00")
    row = await tokens.get("notion", owner_id=1)
    assert row["client_id"] == "c1" and row["access_token"] == "at" and row["refresh_token"] == "rt"
    assert row["client_secret"] == "" and row["scope"] == "" and row["updated_at"] == NOW
    await tokens.delete("notion", owner_id=1)
    assert await tokens.get("notion", owner_id=1) is None


async def test_unknown_fields_are_refused(engine):
    await ensure_users(engine, owner=1)
    with pytest.raises(ValueError):
        await SqlOauthTokenRepository(engine).put("x", owner_id=1, now=NOW,
                                                  password="no")

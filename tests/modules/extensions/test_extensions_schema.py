"""The shape migration 0006 gives the three credential tables."""
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from claude_hermes.modules.extensions.domain.models import Extension, ExtensionSecret
from conftest import ensure_users

NOW = "2026-09-22T10:00:00+00:00"


def _row(uid: int, name: str = "fetch") -> dict:
    return dict(owner_id=uid, name=name, kind="mcp", catalog_version="1.0.0",
                state="enabled", installed_at=NOW, installed_by=uid)


async def test_two_owners_may_hold_the_same_extension_name(engine):
    """The whole point: the owner's `fetch` and a second user's `fetch` coexist."""
    await ensure_users(engine, 7, owner=1)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(Extension).values(_row(1)))
        await conn.execute(sa.insert(Extension).values(_row(7)))
        count = await conn.scalar(sa.select(sa.func.count()).select_from(Extension))
    assert count == 2


async def test_one_owner_cannot_hold_the_same_name_twice(engine):
    await ensure_users(engine, owner=1)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(Extension).values(_row(1)))
    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(sa.insert(Extension).values(_row(1)))


async def test_a_row_must_name_an_owner(engine):
    """An ownerless credential belongs to nobody and therefore to everybody."""
    values = _row(1)
    values.pop("owner_id")
    with pytest.raises((IntegrityError, DBAPIError)):
        async with engine.begin() as conn:
            await conn.execute(sa.insert(Extension).values(values))


async def test_deleting_a_user_takes_their_secrets_with_them(engine):
    """ON DELETE CASCADE, not SET NULL: a secret with no owner must not survive."""
    await ensure_users(engine, 7, owner=1)
    async with engine.begin() as conn:
        await conn.execute(sa.insert(ExtensionSecret).values(
            owner_id=7, name="NOTION_TOKEN", ciphertext=b"sealed", updated_at=NOW))
        await conn.execute(sa.text("DELETE FROM users WHERE id = 7"))
        left = await conn.scalar(
            sa.select(sa.func.count()).select_from(ExtensionSecret))
    assert left == 0

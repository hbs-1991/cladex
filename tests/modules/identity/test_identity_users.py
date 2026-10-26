"""The `users` table, and the policy over it — against a real PostgreSQL.

`test_identity_access_control.py` drives the policy over dicts. This file is the
half a dict cannot model: `users_one_owner` is a UNIQUE PARTIAL INDEX and it is what
arbitrates the claim race, so the race has to be run against the real index or the
test proves nothing.
"""
import asyncio

import pytest

from claude_hermes.core.meta import MetaStore
from claude_hermes.modules.identity.application.ports import UserStore
from claude_hermes.modules.identity.application.services import AccessControl
from claude_hermes.modules.identity.domain.exceptions import (
    OwnerExists,
    UnknownUser,
    UserExists,
)
from claude_hermes.modules.identity.domain.models import Profile
from claude_hermes.modules.identity.infrastructure.repositories import UserRepository

NOW = "2026-09-17T10:00:00+00:00"


def acl_over(engine, *, allowed=()) -> AccessControl:
    return AccessControl(UserRepository(engine), allowed_users=allowed)


async def test_the_repository_satisfies_the_port(engine):
    assert isinstance(UserRepository(engine), UserStore)


async def test_a_claim_writes_the_owner_row_and_nothing_else(engine):
    acl, meta = acl_over(engine), MetaStore(engine)
    assert await acl.check(7, "private", {"first_name": "Ada", "last_name": "L",
                                          "username": "@ada"}) == "claimed"
    owner = await acl.owner()
    assert owner is not None and owner.id == 7 and owner.role == "owner"
    # The owner's root IS CH_HOME — that is why a live volume needed no migration.
    assert owner.workspace_dir == ""
    assert owner.display_name == "Ada L" and owner.username == "ada"
    # The row is the whole record of the claim: nothing goes into `meta` (the key
    # migration 0005 read from is inert on a live volume and never written again).
    assert await meta.get("owner_user_id") is None


async def test_the_second_private_user_is_denied(engine):
    acl = acl_over(engine)
    assert await acl.check(7, "private") == "claimed"
    assert await acl.check(8, "private") == "deny"
    assert await UserRepository(engine).get(8) is None


async def test_an_allowlisted_user_gets_a_user_row_with_their_own_workspace(engine):
    acl = acl_over(engine, allowed=[9])
    await acl.check(7, "private")
    assert await acl.check(9, "private") == "allow"
    row = await UserRepository(engine).get(9)
    assert row is not None and row.role == "user" and row.workspace_dir == "users/9"
    assert await acl.is_owner(9) is False
    assert await acl.workspace_dir_of(9) == "users/9"
    assert await acl.workspace_dir_of(7) == ""


async def test_touch_updates_the_profile_and_the_last_seen_stamp(engine):
    repo = UserRepository(engine)
    await repo.create(9, "user", "users/9", Profile("Ada", "ada"), NOW)
    await repo.touch(9, Profile("Ada Lovelace", "ada_l"), "2026-09-18T00:00:00+00:00")
    row = await repo.get(9)
    assert row.display_name == "Ada Lovelace" and row.username == "ada_l"
    assert row.last_seen_at == "2026-09-18T00:00:00+00:00"
    # "No news" must not blank what we already knew: Telegram omits `username` for
    # people who have none, and one such update would otherwise erase a handle.
    await repo.touch(9, None, "2026-09-19T00:00:00+00:00")
    row = await repo.get(9)
    assert row.display_name == "Ada Lovelace" and row.username == "ada_l"
    assert row.last_seen_at == "2026-09-19T00:00:00+00:00"


async def test_the_partial_index_is_what_refuses_a_second_owner(engine):
    repo = UserRepository(engine)
    await repo.create(7, "owner", "", None, NOW)
    with pytest.raises(OwnerExists):
        await repo.create(8, "owner", "", None, NOW)
    # A duplicate id is a different answer, and the caller acts on it differently.
    with pytest.raises(UserExists):
        await repo.create(7, "user", "users/7", None, NOW)
    # …and a second non-owner is simply fine.
    await repo.create(9, "user", "users/9", None, NOW)
    assert await repo.count() == 2


async def test_two_concurrent_claims_produce_exactly_one_owner(engine):
    """The race the index exists for. Both coroutines see an unclaimed deploy and
    both INSERT; PostgreSQL picks one and the loser is answered `"deny"` — with the
    transaction rolled back, so the loser costs nothing."""
    acl = acl_over(engine)
    verdicts = await asyncio.gather(acl.check(7, "private"), acl.check(8, "private"))
    assert sorted(verdicts) == ["claimed", "deny"]
    repo = UserRepository(engine)
    assert await repo.count() == 1
    owner = await repo.owner()
    assert owner.id == (7 if verdicts[0] == "claimed" else 8)


async def test_a_disabled_user_is_denied(engine):
    from sqlalchemy import text as sql

    acl = acl_over(engine, allowed=[9])
    await acl.check(7, "private")
    assert await acl.check(9, "private") == "allow"
    async with engine.begin() as conn:
        await conn.execute(sql("UPDATE users SET disabled_at = :t WHERE id = 9"),
                           {"t": NOW})
    assert await acl.check(9, "private") == "deny"
    # Nothing in 4a SETS disabled_at; an operator does it with one UPDATE, and the
    # owner is unaffected.
    assert await acl.check(7, "private") == "allow"


async def test_a_stale_meta_key_does_not_make_a_deploy_claimed(engine):
    """`meta.owner_user_id` is inert since Step 5: the `users` row is the only owner
    record, and a volume with the key but no row (only reachable by hand-truncating
    `users`) is an unclaimed deploy the next private chat claims — and the key is
    left exactly as it was."""
    await MetaStore(engine).set("owner_user_id", "7")
    acl = acl_over(engine, allowed=[9])
    assert await acl.owner() is None
    assert await acl.check(8, "private") == "claimed"
    owner = await UserRepository(engine).owner()
    assert owner is not None and owner.id == 8 and owner.workspace_dir == ""
    assert await MetaStore(engine).get("owner_user_id") == "7"


async def test_workspace_dir_of_an_unknown_id_raises(engine):
    acl = acl_over(engine)
    await acl.check(7, "private")
    with pytest.raises(UnknownUser):
        await acl.workspace_dir_of(12345)


async def test_list_puts_the_owner_first(engine):
    repo = UserRepository(engine)
    await repo.create(9, "user", "users/9", None, "2026-09-16T00:00:00+00:00")
    await repo.create(7, "owner", "", None, NOW)
    await repo.create(11, "user", "users/11", None, "2026-09-18T00:00:00+00:00")
    assert [u.id for u in await repo.list()] == [7, 9, 11]
    assert [u.id for u in await repo.list(limit=2)] == [7, 9]


async def test_the_model_default_round_trips_on_the_row(engine):
    """0008's two columns, read as two columns: the model resolver asks on every
    turn of every private lane and has no use for the rest of the `users` row."""
    repo = UserRepository(engine)
    await repo.create(1, "owner", "", None, NOW)
    assert await repo.default_of(1) == (None, None)

    assert await repo.set_default(1, "claude-opus-5-5", "high") is True
    assert await repo.default_of(1) == ("claude-opus-5-5", "high")
    # The row is otherwise untouched — a preference is not an admission.
    assert (await repo.get(1)).role == "owner"

    assert await repo.set_default(1, None, None) is True
    assert await repo.default_of(1) == (None, None)


async def test_a_default_for_an_id_with_no_row_writes_nothing(engine):
    """An UPDATE, like `touch` and `set_disabled`: a preference must not create a
    user nobody admitted, and "there was nobody to write" is an answer."""
    repo = UserRepository(engine)
    assert await repo.set_default(999, "claude-opus-5-5", None) is False
    assert await repo.default_of(999) == (None, None)
    assert await repo.count() == 0


# --- per-user grants (0009) --------------------------------------------------


async def test_set_grants_writes_only_the_named_columns(engine):
    """One UPDATE of exactly what was named: turning learning off must not reset a
    shell the owner granted in another click, and `None` is "say nothing"."""
    repo = UserRepository(engine)
    await repo.create(1, "owner", "", None, NOW)
    await repo.create(2, "user", "users/2", None, NOW)
    row = await repo.get(2)
    assert (row.shell_enabled, row.learning_enabled) == (None, None)   # inherits

    assert await repo.set_grants(2, shell=True, learning=None) is True
    row = await repo.get(2)
    assert (row.shell_enabled, row.learning_enabled) == (True, None)

    assert await repo.set_grants(2, shell=None, learning=False) is True
    row = await repo.get(2)
    assert (row.shell_enabled, row.learning_enabled) == (True, False)

    assert await repo.set_grants(2, shell=False, learning=True) is True
    row = await repo.get(2)
    assert (row.shell_enabled, row.learning_enabled) == (False, True)
    # Nothing else on the row moved, and nobody else's row did.
    assert (row.role, row.workspace_dir, row.disabled_at) == ("user", "users/2", None)
    owner = await repo.get(1)
    assert (owner.shell_enabled, owner.learning_enabled) == (None, None)

    # Naming nothing writes nothing, and still answers "is there a row".
    assert await repo.set_grants(2, shell=None, learning=None) is True
    assert (await repo.get(2)).shell_enabled is False


async def test_a_grant_for_an_id_with_no_row_writes_nothing(engine):
    """A grant is set on a person who exists; it never enrols one."""
    repo = UserRepository(engine)
    assert await repo.set_grants(999, shell=True, learning=True) is False
    assert await repo.set_grants(999, shell=None, learning=None) is False
    assert await repo.count() == 0


async def test_the_policy_resolves_grants_off_the_real_row(engine):
    from claude_hermes.modules.identity.domain.models import Grants

    repo = UserRepository(engine)
    await repo.create(1, "owner", "", None, NOW)
    await repo.create(2, "user", "users/2", None, NOW)
    acl = AccessControl(repo, shell_default=False, learning_default=True)
    assert await acl.grants(1) == Grants(shell=True, learning=True)
    assert await acl.grants(2) == Grants(shell=False, learning=True)
    assert (await acl.set_grants(2, shell=True)).shell_enabled is True
    assert await acl.grants(2) == Grants(shell=True, learning=True)
    assert await acl.grants(3) == Grants(shell=False, learning=True)   # no row

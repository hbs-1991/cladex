"""The access policy over its one port, and the store that satisfies it.

The policy reads the `users` table (`UserStore`) and nothing else. These are the
in-memory versions — the table-backed ones are in `test_identity_users.py`, which
drives the real repository against PostgreSQL.
"""
from dataclasses import replace

import pytest

from claude_hermes.modules.identity.application.ports import UserStore
from claude_hermes.modules.identity.application.services import AccessControl
from claude_hermes.modules.identity.domain.exceptions import (
    OwnerExists,
    OwnerImmutable,
    UnknownUser,
    UserExists,
)
from claude_hermes.modules.identity.domain.models import Grants, Profile, User


class FakeUserStore:
    """`UserStore` over a dict, with the partial index enforced by hand — the one
    thing a dict cannot borrow from PostgreSQL."""

    def __init__(self, rows: dict[int, User] | None = None):
        self.rows: dict[int, User] = dict(rows or {})
        self.touched: list[tuple[int, Profile | None]] = []
        # How many single-row reads the policy made — `grants` promises ONE.
        self.gets = 0
        # The two 0008 columns. They are not on `User` — the model resolver wants
        # them without the rest of the row — so the fake keeps them beside it.
        self.defaults: dict[int, tuple[str | None, str | None]] = {}

    async def get(self, user_id):
        self.gets += 1
        return self.rows.get(user_id)

    async def owner(self):
        return next((u for u in self.rows.values() if u.role == "owner"), None)

    async def create(self, user_id, role, workspace_dir, profile, now_iso):
        if role == "owner" and await self.owner() is not None:
            raise OwnerExists("already owned")       # what users_one_owner does
        if user_id in self.rows:
            raise UserExists(str(user_id))           # what ON CONFLICT (id) does
        known = profile or Profile()
        self.rows[user_id] = User(
            id=user_id, role=role, workspace_dir=workspace_dir,
            display_name=known.display_name, username=known.username,
            created_at=now_iso, last_seen_at=now_iso)
        return self.rows[user_id]

    async def touch(self, user_id, profile, now_iso):
        self.touched.append((user_id, profile))

    async def set_disabled(self, user_id, when):
        row = self.rows.get(user_id)
        if row is None:
            return False                             # what an UPDATE of no rows does
        self.rows[user_id] = replace(row, disabled_at=when)
        return True

    async def default_of(self, user_id):
        return self.defaults.get(user_id, (None, None))

    async def set_default(self, user_id, model, effort):
        if user_id not in self.rows:
            return False                             # what an UPDATE of no rows does
        self.defaults[user_id] = (model, effort)
        return True

    async def set_grants(self, user_id, *, shell, learning):
        row = self.rows.get(user_id)
        if row is None:
            return False                             # what an UPDATE of no rows does
        if shell is not None:                        # None = leave that column alone
            row = replace(row, shell_enabled=shell)
        if learning is not None:
            row = replace(row, learning_enabled=learning)
        self.rows[user_id] = row
        return True

    async def list(self, limit=100):
        return list(self.rows.values())[:limit]

    async def active_ids(self):
        return [u.id for u in self.rows.values() if not u.disabled]


def acl_over(users=None, allowed=()) -> AccessControl:
    return AccessControl(users or FakeUserStore(), allowed_users=allowed)


def owned(owner_id: int = 1) -> FakeUserStore:
    return FakeUserStore({owner_id: User(id=owner_id, role="owner", workspace_dir="")})


async def test_the_fake_satisfies_the_port():
    assert isinstance(FakeUserStore(), UserStore)


async def test_first_private_user_claims_then_owner_and_allowlist_only():
    users = FakeUserStore()
    acl = AccessControl(users, allowed_users=[9])
    assert await acl.check(7, "private") == "claimed"
    assert users.rows[7].role == "owner" and users.rows[7].workspace_dir == ""
    assert await acl.check(7, "private") == "allow"
    assert await acl.check(9, "private") == "allow"
    # An allowlisted user is created on FIRST CONTACT, never eagerly from the list.
    assert users.rows[9].role == "user" and users.rows[9].workspace_dir == "users/9"
    assert await acl.check(8, "private") == "deny"
    assert 8 not in users.rows


async def test_only_a_private_chat_can_claim_an_unowned_deploy():
    users = FakeUserStore()
    acl = AccessControl(users)
    assert await acl.check(7, "supergroup") == "deny"
    assert await acl.check(7, "web") == "deny"      # the public URL never claims
    assert users.rows == {}


async def test_is_owner_is_stricter_than_check():
    acl = AccessControl(owned(1), allowed_users=[2])
    assert await acl.check(2, "private") == "allow"
    assert await acl.is_owner(1) is True
    assert await acl.is_owner(2) is False
    assert await acl_over().is_owner(1) is False


async def test_allowed_users_may_be_a_callable_read_per_call():
    allowed: list[int] = []
    acl = AccessControl(owned(1), allowed_users=lambda: allowed)
    assert await acl.check(5, "private") == "deny"
    allowed.append(5)
    assert await acl.check(5, "private") == "allow"


async def test_a_losing_claim_is_denied_not_promoted():
    """Two first private chats at once: PostgreSQL's partial index picks one, the
    loser's IntegrityError becomes `"deny"`. Modelled here by an owner row that
    appears between the read and the write."""

    class RacingStore(FakeUserStore):
        """Looks unclaimed to every reader, and refuses every owner INSERT — which
        is exactly what the loser of the race observes."""

        async def owner(self):
            return None

        async def create(self, user_id, role, workspace_dir, profile, now_iso):
            if role == "owner":
                raise OwnerExists("already owned")
            return await super().create(user_id, role, workspace_dir, profile,
                                        now_iso)

    racing = RacingStore()
    acl = AccessControl(racing)
    assert await acl.check(2, "private") == "deny"
    assert 2 not in racing.rows


async def test_a_disabled_user_is_refused_and_not_touched():
    users = owned(1)
    users.rows[5] = User(id=5, role="user", workspace_dir="users/5",
                         disabled_at="2026-09-17T00:00:00")
    acl = AccessControl(users, allowed_users=[5])
    assert await acl.check(5, "private") == "deny"
    assert users.touched == []


async def test_check_touches_a_known_user_with_whatever_the_channel_knows():
    users = owned(1)
    users.rows[5] = User(id=5, role="user", workspace_dir="users/5")
    acl = AccessControl(users, allowed_users=[5])
    assert await acl.check(5, "private", {"first_name": "Ada", "username": "@ada"}) \
        == "allow"
    assert users.touched == [(5, Profile("Ada", "ada"))]
    # None = "no news about this person", which is not "they have no name".
    await acl.check(5, "private")
    assert users.touched[-1] == (5, None)


async def test_workspace_dir_of_raises_rather_than_handing_over_the_owners_tree():
    users = owned(1)
    users.rows[5] = User(id=5, role="user", workspace_dir="users/5")
    acl = AccessControl(users)
    assert await acl.workspace_dir_of(1) == ""
    assert await acl.workspace_dir_of(5) == "users/5"
    with pytest.raises(UnknownUser):
        await acl.workspace_dir_of(99)


async def test_admitted_is_checks_answer_without_checks_writes():
    """Wave 4b, R-8 (advisor risk 4, code pass): the web middleware asks this on
    every request, so it must not stamp `last_seen_at` or create a row — and it
    must still agree with `check` wherever a cookie can exist."""
    users = owned(1)
    users.rows[2] = User(id=2, role="user", workspace_dir="users/2")
    users.rows[3] = User(id=3, role="user", workspace_dir="users/3",
                         disabled_at="2026-09-18T00:00:00+00:00")
    acl = acl_over(users, allowed=[2, 3, 4])
    assert await acl.admitted(1) and await acl.admitted(2)
    assert not await acl.admitted(3)                      # disabled
    assert await acl.admitted(4)                          # allowlisted, no row yet
    assert not await acl.admitted(5) and not await acl.admitted(0)
    assert users.touched == [] and set(users.rows) == {1, 2, 3}


async def test_admitted_admits_nobody_on_an_unclaimed_deploy():
    """`check(uid, "web")` denies everyone while nobody has claimed the deploy
    (only a private chat may claim), and `admitted` agrees — an allowlisted id is
    admitted only once there is an owner."""
    empty = FakeUserStore()
    assert not await acl_over(empty, allowed=[7]).admitted(7)
    assert empty.rows == {}                                # nothing materialised
    acl = acl_over(owned(9), allowed=[7])
    assert await acl.admitted(9)
    assert await acl.admitted(7)                          # claimed deploy, allowlisted


async def test_invite_writes_the_row_check_would_have_written():
    """The owner's own door. What it writes has to be indistinguishable from what
    a first message writes, or an invited user's tree would land somewhere else."""
    users = owned(1)
    acl = acl_over(users)
    invited = await acl.invite(42)
    assert invited.role == "user" and invited.workspace_dir == "users/42"
    assert await acl.admitted(42) and await acl.check(42, "private") == "allow"
    with pytest.raises(UserExists):
        await acl.invite(42)
    with pytest.raises(UnknownUser):
        await acl.invite(0)


async def test_disabling_is_reversible_and_never_touches_the_owner():
    users = owned(1)
    await acl_over(users).invite(2)
    acl = acl_over(users)
    assert (await acl.set_disabled(2, True)).disabled
    assert not await acl.admitted(2) and await acl.check(2, "private") == "deny"
    assert not (await acl.set_disabled(2, False)).disabled
    assert await acl.admitted(2)

    with pytest.raises(OwnerImmutable):
        await acl.set_disabled(1, True)
    assert await acl.admitted(1)
    with pytest.raises(UnknownUser):
        await acl.set_disabled(999, True)


async def test_disabling_a_config_only_id_materialises_a_disabled_row():
    """`allowed_users` admits an id with no row, and a row is the only thing that
    outranks the file — so the disable has to create one."""
    users = owned(1)
    acl = acl_over(users, allowed=[7])
    assert await acl.admitted(7) and 7 not in users.rows
    assert (await acl.set_disabled(7, True)).disabled
    assert not await acl.admitted(7) and users.rows[7].disabled


# --- the personal model default (0008) ---------------------------------------


async def test_a_user_with_no_row_and_no_choice_has_no_default():
    """`(None, None)` is both "we have never met them" and "they cleared it": the
    deploy default stands either way, which is why the two need not be told apart."""
    acl = acl_over(owned(1))
    assert await acl.default_of(1) == (None, None)
    assert await acl.default_of(999) == (None, None)
    assert await acl.default_of(0) == (None, None)     # not a Telegram id at all


async def test_setting_and_clearing_the_default():
    users = owned(1)
    acl = acl_over(users)
    assert await acl.set_default(1, "claude-opus-5-5", "high") == \
        ("claude-opus-5-5", "high")
    assert await acl.default_of(1) == ("claude-opus-5-5", "high")

    # Clearing the model clears the effort with it: an effort without a model is a
    # preference about nothing, and it would silently re-attach to whatever the
    # deploy default becomes.
    assert await acl.set_default(1, None, "max") == (None, None)
    assert await acl.default_of(1) == (None, None)


async def test_the_default_is_stored_as_the_catalog_id():
    """A caller may name a model by its `[1m]` CLI spelling; the id the database
    keeps and the wire carries never carries the suffix."""
    users = owned(1)
    assert await acl_over(users).set_default(1, "claude-opus-5-5[1m]", None) == \
        ("claude-opus-5-5", None)


async def test_a_default_for_somebody_with_no_row_is_said_out_loud():
    """Unreachable through a real login — every door materialises the row before a
    cookie or a Telegram turn exists — but a silent success would be worse."""
    with pytest.raises(UnknownUser):
        await acl_over(owned(1)).set_default(999, "claude-opus-5-5", None)


# --- per-user grants (0009) --------------------------------------------------


def granting(users, *, shell=False, learning=False, allowed=()) -> AccessControl:
    return AccessControl(users, allowed_users=allowed, shell_default=shell,
                         learning_default=learning)


async def test_the_owner_has_every_grant_whatever_the_defaults_say():
    acl = granting(owned(1))
    assert await acl.grants(1) == Grants(shell=True, learning=True)


async def test_a_user_with_no_choice_inherits_the_defaults():
    users = owned(1)
    users.rows[2] = User(id=2, role="user", workspace_dir="users/2")
    assert await granting(users).grants(2) == Grants(shell=False, learning=False)
    assert await granting(users, shell=True, learning=True).grants(2) == \
        Grants(shell=True, learning=True)


async def test_an_explicit_choice_beats_the_default_column_by_column():
    users = owned(1)
    users.rows[2] = User(id=2, role="user", workspace_dir="users/2",
                         shell_enabled=True)
    users.rows[3] = User(id=3, role="user", workspace_dir="users/3",
                         shell_enabled=False, learning_enabled=False)
    assert await granting(users).grants(2) == Grants(shell=True, learning=False)
    assert await granting(users, shell=True, learning=True).grants(3) == \
        Grants(shell=False, learning=False)


async def test_grants_is_one_read_and_the_owner_is_known_by_the_row():
    """One `get` per question — the turn asks on every non-owner turn, and the
    owner is recognised by `row.is_owner`, not by a second `owner()` query."""
    users = owned(1)
    users.rows[2] = User(id=2, role="user", workspace_dir="users/2")
    acl = granting(users)
    await acl.grants(2)
    await acl.grants(1)
    assert users.gets == 2


async def test_no_row_and_user_zero_resolve_to_the_defaults():
    """Fail closed, like `default_of` and `admitted`: a stranger has no choice of
    their own to honour — and `0` (a fork with no lane) is not even asked about."""
    users = owned(1)
    acl = granting(users, shell=True, learning=False, allowed=[9])
    assert await acl.grants(999) == Grants(shell=True, learning=False)
    assert await acl.grants(9) == Grants(shell=True, learning=False)   # config-only
    before = users.gets
    assert await acl.grants(0) == Grants(shell=True, learning=False)
    assert await acl.grants(-3) == Grants(shell=True, learning=False)
    assert users.gets == before                                   # no query at all
    assert await granting(users).grants(0) == Grants(shell=False, learning=False)


async def test_the_defaults_are_read_at_call_time():
    """The same timing as `allowed_users`: the policy holds a callable over the
    live config, never a copy made at boot."""
    cfg = {"shell": False, "learning": False}
    users = owned(1)
    users.rows[2] = User(id=2, role="user", workspace_dir="users/2")
    acl = AccessControl(users, shell_default=lambda: cfg["shell"],
                        learning_default=lambda: cfg["learning"])
    assert await acl.grant_defaults() == Grants(shell=False, learning=False)
    assert await acl.grants(2) == Grants(shell=False, learning=False)
    cfg["shell"] = True
    cfg["learning"] = True
    assert await acl.grant_defaults() == Grants(shell=True, learning=True)
    assert await acl.grants(2) == Grants(shell=True, learning=True)


async def test_a_policy_built_without_defaults_grants_nobody_anything():
    users = owned(1)
    users.rows[2] = User(id=2, role="user", workspace_dir="users/2")
    acl = AccessControl(users)
    assert await acl.grant_defaults() == Grants(shell=False, learning=False)
    assert await acl.grants(2) == Grants(shell=False, learning=False)


async def test_set_grants_writes_only_what_it_was_given():
    users = owned(1)
    users.rows[2] = User(id=2, role="user", workspace_dir="users/2")
    acl = granting(users)
    row = await acl.set_grants(2, shell=True)
    assert (row.shell_enabled, row.learning_enabled) == (True, None)
    row = await acl.set_grants(2, learning=False)
    assert (row.shell_enabled, row.learning_enabled) == (True, False)
    assert await acl.grants(2) == Grants(shell=True, learning=False)
    row = await acl.set_grants(2, shell=False, learning=True)
    assert (row.shell_enabled, row.learning_enabled) == (False, True)


async def test_set_grants_refuses_the_owner_and_never_creates_a_row():
    """The owner's lanes have everything by construction, so a switch on their row
    would do nothing and say it did. A grant never materialises a row — not even
    for a config-allowlisted id, unlike `set_disabled`: a row is an admission."""
    users = owned(1)
    acl = granting(users, allowed=[7])
    with pytest.raises(OwnerImmutable):
        await acl.set_grants(1, shell=False)
    assert users.rows[1].shell_enabled is None
    with pytest.raises(UnknownUser):
        await acl.set_grants(7, shell=True)                      # allowlisted, no row
    with pytest.raises(UnknownUser):
        await acl.set_grants(999, learning=True)                 # never seen
    with pytest.raises(UnknownUser):
        await acl.set_grants(0, learning=True)
    assert set(users.rows) == {1}

"""Access policy: who may talk to the agent, who they are, where their workspace
is — and, since 0009, what each of them may do beyond talking (`grants`).

The policy reads one record: the `users` table (Wave 4a). Three things about it
are load-bearing and are spelled out in the bodies below:

* **The claim is an INSERT, not a check.** `users_one_owner` is the arbiter; two
  first private chats racing produce one owner and one `IntegrityError`, which is
  caught here and answered `"deny"`.
* **An unclaimed deploy is one with no owner row.** Every volume reaches this
  code through `alembic upgrade head`, and migration `0005` materialised the
  owner row from the old `meta.owner_user_id` key, so "the table is empty" means
  "nobody has claimed the deploy" and nothing else. The one state that used to
  need a second source — key set, table empty — is reachable only by
  hand-truncating `users`, which the app never does.
* **`workspace_dir_of` raises rather than guesses.** A caller that cannot be
  identified gets an exception, never the owner's directory — handing a stranger
  the owner's tree is the exact failure this module exists to make impossible.
"""
from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime, timezone
from typing import Any

from claude_hermes.core.model_catalog import lookup
from claude_hermes.modules.identity.application.ports import UserStore
from claude_hermes.modules.identity.domain.exceptions import (
    OwnerExists,
    OwnerImmutable,
    UnknownUser,
    UserExists,
)
from claude_hermes.modules.identity.domain.models import Grants, Profile, Role, User

__all__ = ["AccessControl"]

AllowedUsers = Collection[int] | Callable[[], Collection[int]]
# A config default: a plain value, or a callable read at call time — the
# `allowed_users` pattern, so a grant default follows the live `Config` object.
Flag = bool | Callable[[], bool]

# The owner's root is CH_HOME itself, which is what keeps /data/agent_home,
# /data/.claude-cli and /data/logs where they are on a live volume (D5).
OWNER_WORKSPACE = ""


def user_workspace_dir(user_id: int) -> str:
    """Where a non-owner's tree lives, relative to CH_HOME. One spelling: the
    migration's backfill, `check` and the resolver all have to agree."""
    return f"users/{user_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AccessControl:
    """First private-chat user claims ownership; then owner + allowlist only.

    `allowed_users` accepts a callable so a caller holding a mutable `Config` can
    pass `lambda: cfg.allowed_users` and keep the read-at-call-time timing the
    allowlist has always had.

    `shell_default` / `learning_default` (0009) are the same shape and the same
    timing: `users_shell_enabled` / `users_learning_enabled`, the answer for a
    person the owner has made no choice about. Both default to False — a policy
    built without them grants nobody a shell, which is the side to fail on.
    """

    def __init__(self, users: UserStore, *, allowed_users: AllowedUsers = (),
                 shell_default: Flag = False, learning_default: Flag = False,
                 now: Callable[[], str] = _now_iso):
        self._users = users
        self._allowed = allowed_users
        self._shell_default = shell_default
        self._learning_default = learning_default
        self._now = now

    def _allowed_users(self) -> Collection[int]:
        return self._allowed() if callable(self._allowed) else self._allowed

    @staticmethod
    def _flag(value: Flag) -> bool:
        return bool(value() if callable(value) else value)

    async def owner(self) -> User | None:
        """The deploy owner, or None when the deploy is genuinely unclaimed."""
        return await self._users.owner()

    async def check(self, user_id: int, chat_type: str,
                    profile: Any = None) -> str:
        """`"claimed"` | `"allow"` | `"deny"`.

        `profile` is whatever the channel knows about the sender — a `Profile`, a
        Login Widget payload, or another module's own value object with the same
        two attributes (`Profile.of` normalises). `None` means "no news", and the
        stored names are then left alone.
        """
        known = Profile.of(profile)
        owner = await self.owner()
        if owner is None:
            # Nobody has claimed the deploy. Only a private Telegram chat may:
            # the public URL must never be where ownership is taken, or the first
            # stranger to find the tunnel hostname owns the agent.
            if chat_type != "private":
                return "deny"
            try:
                await self._users.create(user_id, Role.OWNER.value, OWNER_WORKSPACE,
                                         known, self._now())
            except (OwnerExists, UserExists):
                return "deny"
            return "claimed"

        row = owner if owner.id == user_id else await self._users.get(user_id)
        if row is not None:
            if row.disabled:
                return "deny"
            await self._users.touch(user_id, known, self._now())
            return "allow"
        if user_id in self._allowed_users():
            # The row is the record of who actually showed up (open question 1):
            # created on first contact, never eagerly from the allowlist, so a
            # typo in `allowed_users` creates nothing.
            try:
                await self._users.create(user_id, Role.USER.value,
                                         user_workspace_dir(user_id), known,
                                         self._now())
            except UserExists:
                pass                      # another process admitted them first
            return "allow"
        return "deny"

    async def admitted(self, user_id: int) -> bool:
        """Is this user still allowed in — `check`'s answer, without `check`'s writes.

        The web middleware asks on every request (Wave 4b, R-8), and `check`
        writes: it stamps `last_seen_at` and materialises the row of an
        allowlisted first-timer. One UPDATE per API call is not what "re-check
        the cookie" should cost, and a row must only ever be created by an
        admission through a door (`check`), never by a middleware — so this
        reads the table and nothing else. It agrees with `check` wherever a
        cookie can exist: a row answers by `disabled`, an unclaimed deploy admits
        nobody (only a private chat may claim), and an allowlisted id with no row
        yet is admitted, which is what `check` would say on their first request
        too.
        """
        if user_id <= 0:
            return False
        row = await self._users.get(user_id)
        if row is not None:
            return not row.disabled
        if await self._users.owner() is None:
            return False
        return user_id in self._allowed_users()

    async def is_owner(self, user_id: int) -> bool:
        """Stricter than check(): an allowlisted user may talk to the agent, but only
        the owner may change what the agent *is* — install extensions, store secrets."""
        owner = await self.owner()
        return owner is not None and owner.id == user_id

    async def user(self, user_id: int) -> User | None:
        """The row, or None. What `/api/me` reads for the two display fields."""
        if user_id <= 0:
            return None
        return await self._users.get(user_id)

    async def default_of(self, user_id: int) -> tuple[str | None, str | None]:
        """This person's default (model, effort). Satisfies sessions' `OwnerDefaults`.

        The lane's owner is asked for it by the model resolver, and only for a
        lane that is not shared — a group, a forum topic and a `cron:` lane belong
        to the room. `(None, None)` for an id we have never met, which is the same
        answer as "they have not chosen one": the deploy default stands.
        """
        if user_id <= 0:
            return (None, None)
        return await self._users.default_of(user_id)

    async def set_default(self, user_id: int, model: str | None,
                          effort: str | None) -> tuple[str | None, str | None]:
        """Set (or clear, with `model=None`) the default. Returns what now stands.

        Not validated here: the offered list is `cfg.models`, which this module
        does not read — the caller validates with `core.model_catalog.validate`
        and answers `400 bad_model` / `400 bad_effort` in its own channel's words.
        A pair that slips through anyway is still harmless, because `resolve`
        skips a default whose model is no longer offered rather than running it.

        Clearing the model clears the effort with it: an effort without a model is
        a preference about nothing, and it would otherwise silently re-attach to
        whatever model the deploy default becomes.
        """
        # Through `lookup`: a caller may name the model by an alias or by its
        # `[1m]` CLI spelling, and what is stored is always the catalog id.
        pair = (lookup(model).id, effort) if model else (None, None)
        if not await self._users.set_default(user_id, *pair):
            # Unreachable through a real login: every door (`check`) materialises
            # the row before a cookie or a Telegram turn exists. Said out loud
            # rather than reported as a silent success.
            raise UnknownUser(f"no users row for {user_id}")
        return pair

    async def grant_defaults(self) -> Grants:
        """What a person the owner has made no choice about gets — the two config
        keys, read NOW rather than at construction, so this object never holds a
        copy that disagrees with the `Config` it was built from.

        Satisfies the `grant_defaults` half of turns' `LaneGrants`: a session
        stamped before 0009 was started under exactly this default, which is what
        the turn compares an unstamped session with.
        """
        return Grants(shell=self._flag(self._shell_default),
                      learning=self._flag(self._learning_default))

    async def grants(self, user_id: int) -> Grants:
        """This person's EFFECTIVE grants — satisfies turns' `LaneGrants`,
        scheduler's `OwnerGrants` and what learning asks per user.

        Read per call from the table, never cached: the agent and the worker are
        two processes, and a grant the owner flips must reach whichever of them
        runs that person's next turn without a restart.

        One `get` and `Grants.of` — the owner is recognised by `row.is_owner`,
        not by a second `owner()` query. `user_id <= 0` (a fork with no lane) and
        an id with no row resolve to the defaults, the same fail-closed answer
        `default_of` and `admitted` give a stranger. A failing store PROPAGATES:
        a caller that must not guess (the turn) needs the failure, and one that
        may (a routine) decides for itself what to fall back to.
        """
        defaults = await self.grant_defaults()
        if user_id <= 0:
            return defaults
        return Grants.of(await self._users.get(user_id), defaults)

    async def set_grants(self, user_id: int, *, shell: bool | None = None,
                         learning: bool | None = None) -> User:
        """Give or withdraw a shell and/or learning. Returns the row as it now
        stands; `None` for either keyword leaves that grant as it was.

        Two refusals:

        * **The owner's grants are not settable** (`OwnerImmutable`). The owner's
          lanes get everything by construction (`Grants.of`); a column on their
          row would be a switch that does nothing, shown as if it did.
        * **An id with no row is `UnknownUser`, and no row is created.** Unlike
          `set_disabled`, there is no config-allowlist exception: a row is an
          admission, and creating one to hang a grant on would enrol a never-seen
          id in `active_user_ids()` and let the curator seed their tree.

        No session is reset from here. The turn compares the lane's session stamp
        with the grant at its own start and resets there — a reset from this call
        could race a running turn's write-back and resurrect the old session.
        """
        row = await self.user(user_id)
        if row is None:
            raise UnknownUser(f"no users row for {user_id}")
        if row.is_owner:
            raise OwnerImmutable("the owner's grants cannot be changed")
        if not await self._users.set_grants(user_id, shell=shell, learning=learning):
            raise UnknownUser(f"no users row for {user_id}")    # deleted under us
        row = await self._users.get(user_id)
        if row is None:
            raise UnknownUser(f"no users row for {user_id}")
        return row

    async def workspace_dir_of(self, user_id: int) -> str:
        """Where this user's tree lives, relative to CH_HOME — what the workspace
        resolver asks. Raises `UnknownUser` rather than defaulting to the owner's."""
        row = await self.user(user_id)
        if row is None:
            raise UnknownUser(f"no users row for {user_id}")
        return row.workspace_dir

    async def invite(self, user_id: int) -> User:
        """Admit somebody who has not shown up yet — the owner's own door.

        Every other row in this table is created by an admission through a channel
        (`check`), and that rule stands: what changes is that the owner asking for
        it *is* an admission, made in advance and by the one person allowed to make
        it. The row is what `check` would have written on first contact, minus the
        profile — Telegram has no way to look a display name up from an id, so the
        two fields fill themselves in on the first `touch`.

        Nothing is created on disk: `WorkspaceResolver.ensure()` builds the tree
        lazily on first use, exactly as it does for an allowlisted id today.

        `UserExists` is left to the caller: "already invited" is a different answer
        from "invited", and only the caller knows which status says so.
        """
        if user_id <= 0:
            raise UnknownUser(f"not a Telegram user id: {user_id}")
        return await self._users.create(user_id, Role.USER.value,
                                        user_workspace_dir(user_id), None, self._now())

    async def set_disabled(self, user_id: int, disabled: bool) -> User:
        """Take access away, or give it back. Returns the row as it now stands.

        Two refusals, both about not locking the deployment out of itself:

        * **The owner cannot be disabled.** `is_owner` gates every mutation in the
          app, so an owner who disabled themselves could not undo it from any
          channel — it would take SQL on the host.
        * **An unknown id cannot be disabled**, unless it is on the config
          allowlist. That exception is the point rather than a special case: an id
          in `allowed_users` with no row is admitted by `check` and `admitted`
          alike, and the only way to overrule the file from the database is to give
          it the row the check reads first — created already disabled.
        """
        owner = await self.owner()
        if owner is not None and owner.id == user_id:
            raise OwnerImmutable("the owner cannot be disabled")
        when = self._now() if disabled else None
        if not await self._users.set_disabled(user_id, when):
            if not (disabled and user_id in self._allowed_users()):
                raise UnknownUser(f"no users row for {user_id}")
            await self._users.create(user_id, Role.USER.value,
                                     user_workspace_dir(user_id), None, self._now())
            await self._users.set_disabled(user_id, when)
        row = await self._users.get(user_id)
        if row is None:                      # deleted under us; nothing to report
            raise UnknownUser(f"no users row for {user_id}")
        return row

    async def allowlisted_ids(self) -> list[int]:
        """The config allowlist, as the owner's user list shows it. Reading it here
        keeps `cfg.allowed_users` behind the same object every other access
        question goes through."""
        return sorted(self._allowed_users())

    async def list_users(self, limit: int = 100) -> list[User]:
        """Everyone with a row, up to `limit` — what `GET /api/users` (Settings →
        Users) lists, and one call for an operator to reach for in a REPL."""
        return await self._users.list(limit)

    async def active_user_ids(self) -> list[int]:
        """Every non-disabled user id, no limit — what the curator sweep walks
        (Wave 4b). Not `list_users`: a limit here is a user the sweep silently
        forgets."""
        return await self._users.active_ids()

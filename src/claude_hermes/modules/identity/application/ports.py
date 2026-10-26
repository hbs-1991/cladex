"""What identity needs from the outside world, stated as a Protocol.

One port: `UserStore` is the `users` table — the record of who the agent talks
to, and since Step 5 the only record of who owns the deploy. Satisfied by
`infrastructure.repositories.UserRepository`; a fake with the same methods
satisfies it just as well, which is the point of stating it here.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import Profile, User


@runtime_checkable
class UserStore(Protocol):
    """The `users` table.

    `create` is the only writer that can fail, and both of its failures are
    answers rather than accidents: `OwnerExists` is the partial index refusing a
    second owner (the claim race's arbiter), `UserExists` is "somebody inserted
    this id between our read and our write". Neither is retried here — the caller
    turns them into a verdict.

    `touch` takes `profile=None` to mean "no news about this person", which is not
    the same as "they have no name": an update with no `from_user` must not blank a
    display name the Login Widget already gave us.

    `set_disabled` is the only writer that can say "there was nobody to write":
    it answers whether a row was touched, so the caller can turn a missing id into
    a 404 rather than a silent success. `set_default` answers the same way and for
    the same reason.

    `default_of` / `set_default` (0008) are the person's own default (model,
    effort) for new conversations on their own lanes. They are narrow column
    reads rather than a `User` because the model resolver asks on every turn; the
    pair is stored as catalog ids, and a stored model that later leaves the
    offered list is skipped by `core.model_catalog.resolve`, so a stale row is
    harmless rather than a trap.

    `set_grants` (0009) writes the owner's per-person grants. `None` for either
    keyword leaves that column alone — a PATCH naming only `learning` must not
    reset somebody's shell — and like `set_disabled` it answers whether a row was
    touched: a grant is set on a person who exists, never used to create one. It
    is also the only writer here that can move a column the turn reads to pick a
    toolset, and it never resets a session itself: the turn notices the change at
    its own start, which is the only place a reset cannot race a running turn.
    """

    async def get(self, user_id: int) -> User | None: ...

    async def owner(self) -> User | None: ...

    async def create(self, user_id: int, role: str, workspace_dir: str,
                     profile: Profile | None, now_iso: str) -> User: ...

    async def touch(self, user_id: int, profile: Profile | None,
                    now_iso: str) -> None: ...

    async def set_disabled(self, user_id: int, when: str | None) -> bool: ...

    async def default_of(self, user_id: int) -> tuple[str | None, str | None]: ...

    async def set_default(self, user_id: int, model: str | None,
                          effort: str | None) -> bool: ...

    async def set_grants(self, user_id: int, *, shell: bool | None,
                         learning: bool | None) -> bool: ...

    async def list(self, limit: int = 100) -> list[User]: ...

    async def active_ids(self) -> list[int]: ...

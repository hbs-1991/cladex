"""Where identity's one fact actually lives: the `users` table.

Every method below is **one statement**, for the same reason `turns` is: the claim
race is decided by a unique partial index, and an index cannot arbitrate a
read-then-write. `create` inserts and lets PostgreSQL answer; there is no
"is there an owner yet?" anywhere in this file.

Takes an `AsyncEngine`, and each call is its own short transaction — a caller never
holds a session.
"""
from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from claude_hermes.modules.identity.domain.exceptions import OwnerExists, UserExists
from claude_hermes.modules.identity.domain.models import Profile, Role, User
from claude_hermes.modules.identity.domain.tables import UserRow

_TABLE = UserRow.__table__
_COLUMNS = tuple(_TABLE.columns)


class UserRepository:
    """`users`: one row per person. Satisfies `application.ports.UserStore`."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def get(self, user_id: int) -> User | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(*_COLUMNS).where(_TABLE.c.id == user_id))).mappings().first()
        return User.from_row(row) if row else None

    async def owner(self) -> User | None:
        """The one `role = 'owner'` row. `users_one_owner` guarantees "one"."""
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(*_COLUMNS).where(_TABLE.c.role == Role.OWNER.value)
            )).mappings().first()
        return User.from_row(row) if row else None

    async def create(self, user_id: int, role: str, workspace_dir: str,
                     profile: Profile | None, now_iso: str) -> User:
        """Insert a user, or say which constraint said no.

        `ON CONFLICT (id) DO NOTHING` takes the primary key out of the picture, so
        the only constraint left that can raise is `users_one_owner` — which means
        an `IntegrityError` here is unambiguously "somebody else is the owner",
        and an empty result is unambiguously "this id already has a row". Two
        different answers the caller acts on differently, told apart without
        parsing a driver's error string.
        """
        known = profile or Profile()
        stmt = (pg_insert(_TABLE)
                .values(id=user_id, role=role, workspace_dir=workspace_dir,
                        display_name=known.display_name, username=known.username,
                        created_at=now_iso, last_seen_at=now_iso)
                .on_conflict_do_nothing(index_elements=[_TABLE.c.id])
                .returning(*_COLUMNS))
        try:
            async with self._engine.begin() as conn:
                row = (await conn.execute(stmt)).mappings().first()
        except IntegrityError as exc:
            # The engine's `begin()` block has already rolled the transaction back
            # by the time this is raised, which is what makes a losing claim cost
            # nothing.
            raise OwnerExists("this deployment already has an owner") from exc
        if row is None:
            raise UserExists(f"user {user_id} already exists")
        return User.from_row(row)

    async def touch(self, user_id: int, profile: Profile | None,
                    now_iso: str) -> None:
        """Stamp `last_seen_at`, and refresh the two display fields if we learned
        anything.

        An UPDATE and not the upsert the rest of this codebase favours: `touch`
        is handed no role and no `workspace_dir`, so an INSERT branch would have
        to invent them — and a user the ACL never admitted would appear in the
        table as a side effect of saying hello.

        `COALESCE(NULLIF(…, ''), column)` is the "no news" rule: Telegram omits
        `username` for people who have none, and an update without it must not
        blank a handle the Login Widget already gave us. The cost is that a user
        who *deletes* their username keeps the stale one until they set another
        (known debt; there is nothing yet that shows either field to anyone but
        its owner).
        """
        known = profile or Profile()
        values: dict = {"last_seen_at": now_iso}
        if known.display_name:
            values["display_name"] = known.display_name
        if known.username:
            values["username"] = known.username
        stmt = update(_TABLE).where(_TABLE.c.id == user_id).values(**values)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def set_disabled(self, user_id: int, when: str | None) -> bool:
        """Set or clear `disabled_at`; True when a row was actually touched.

        The column has been read since Wave 4a — `check` refuses a disabled row and
        the web middleware asks `admitted` on every request — and until now nothing
        wrote it, so taking somebody's access away meant SQL by hand. One statement,
        like everything else here, and the row is left otherwise intact: the threads,
        files and workspace of a disabled user are still theirs if they come back.
        """
        stmt = update(_TABLE).where(_TABLE.c.id == user_id).values(disabled_at=when)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def default_of(self, user_id: int) -> tuple[str | None, str | None]:
        """This person's default (model, effort) for their own new conversations.

        Two columns, not the row: the model resolver asks this on every turn of
        every private lane, and `get` would build a whole `User` — with the
        timestamps and the workspace path — to answer a question about two short
        strings. `(None, None)` for an id with no row, which is the same answer as
        "they have never chosen one" and resolves the same way.
        """
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(_TABLE.c.default_model, _TABLE.c.default_effort)
                .where(_TABLE.c.id == user_id))).first()
        return (row[0], row[1]) if row is not None else (None, None)

    async def set_default(self, user_id: int, model: str | None,
                          effort: str | None) -> bool:
        """Write the pair; True when a row was actually touched.

        An UPDATE, like `touch` and `set_disabled`: this call is handed no role
        and no `workspace_dir`, so an INSERT branch would have to invent them and
        a preference would create a user nobody admitted. The two columns move
        together — an effort without a model is a preference about nothing.
        """
        stmt = (update(_TABLE).where(_TABLE.c.id == user_id)
                .values(default_model=model, default_effort=effort))
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def set_grants(self, user_id: int, *, shell: bool | None,
                         learning: bool | None) -> bool:
        """Write the named grants; True when a row was actually touched.

        One UPDATE of ONLY the columns the caller named: `None` is "say nothing
        about this one", so turning somebody's learning off cannot quietly reset a
        shell the owner granted them in another click. An UPDATE and not an
        upsert, for the reason `set_default` gives — a grant must never create a
        row, because a row is an admission (`active_user_ids` would enrol the id
        and the curator would seed their tree).

        Nothing to write is answered by a read, not a no-op UPDATE: "does this
        row exist" is still the question the caller turns into a 404.
        """
        values: dict = {}
        if shell is not None:
            values["shell_enabled"] = shell
        if learning is not None:
            values["learning_enabled"] = learning
        if not values:
            return await self.get(user_id) is not None
        stmt = update(_TABLE).where(_TABLE.c.id == user_id).values(**values)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return bool(result.rowcount)

    async def list(self, limit: int = 100) -> list[User]:
        """Everyone, owner first, then by when they arrived."""
        stmt = (select(*_COLUMNS)
                .order_by((_TABLE.c.role != Role.OWNER.value), _TABLE.c.created_at)
                .limit(limit))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [User.from_row(row) for row in rows]

    async def active_ids(self) -> list[int]:
        """Every non-disabled id, owner first, then by arrival — with NO limit.

        The curator sweep walks this (Wave 4b, advisor R-5). `list(limit=100)`
        would have silently dropped user 101 from every sweep for the life of
        the deployment, and nothing would have said so. Ids only: the sweep
        resolves each user's workspace through the resolver anyway.
        """
        stmt = (select(_TABLE.c.id)
                .where(_TABLE.c.disabled_at.is_(None))
                .order_by((_TABLE.c.role != Role.OWNER.value), _TABLE.c.created_at))
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).scalars().all()
        return [int(row) for row in rows]

    async def count(self) -> int:
        """How many rows. Only the tests and a REPL read this; it is here rather
        than in a test helper so nothing outside this file writes `users` SQL."""
        async with self._engine.connect() as conn:
            return int((await conn.execute(
                select(func.count()).select_from(_TABLE))).scalar_one())


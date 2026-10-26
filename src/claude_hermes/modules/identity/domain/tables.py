"""The `users` table, alone in its own module.

**Why this is not in `domain/models.py`.** Every other module keeps its ORM rows in
`domain/models.py`, and identity would too — except that identity's *application*
layer needs `User` and `Profile` for its own signatures, and the import contract
"application layers are framework-free" forbids `sqlalchemy` **including through a
chain**. One file holding both the dataclasses and a declarative model would put
SQLAlchemy one hop behind `application/services.py` and one hop behind
`presentation/{router,schemas}.py`, and break two contracts at once. Nothing else in
the codebase has an application layer that imports its own `domain/models.py`, which
is why identity is the first module to need the split.

So: the shape the app passes around lives in `models.py` and is pure; the schema
lives here and is imported only by `core/db/registry.py` (Alembic needs every model
in one `MetaData`) and by `infrastructure/repositories.py`.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from claude_hermes.core.db.base import Base

__all__ = ["UserRow"]


class UserRow(Base):
    """One person the agent talks to.

    **`id` is the Telegram user id** (D1), not a surrogate: `CurrentUser.user_id`,
    `threads.owner_user_id`, `turns.user_id`, the `web:<user_id>:` lane grammar, the
    ask-broker's owner check and the session cookie all carry that number today, and
    a second id would mean a translation at every one of them. Spec req. 29 ("a
    Telegram user id maps to exactly one user") is then the primary key itself. A
    non-Telegram login later gets a synthetic id range.

    **`users_one_owner` is the claim race's arbiter.** A unique partial index over
    `role = 'owner'`: two first private chats arriving together both INSERT,
    PostgreSQL picks one, and the loser's `IntegrityError` is what
    `AccessControl.check` answers `"deny"` to. Like `turns_one_active_per_lane`, the
    index IS the lock — there is no check-then-insert anywhere, because a
    check-then-insert is a race.

    **`workspace_dir` is relative to `CH_HOME`,** and the owner's is `""` so their
    root stays `CH_HOME` itself: `/data/agent_home`, `/data/.claude-cli` and
    `/data/logs` do not move, a live volume needs no file migration, and SDK sessions
    keep resuming. Everyone else gets `users/<id>`.

    Timestamps are ISO text like every other table here.
    """

    __tablename__ = "users"
    # autoincrement=False: the id arrives from Telegram; a sequence here would be a
    # second opinion about who somebody is.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)           # owner | user
    workspace_dir: Mapped[str] = mapped_column(Text, nullable=False)  # "" | users/<id>
    display_name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    username: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    last_seen_at: Mapped[str] = mapped_column(Text, nullable=False)
    # Nothing sets this in 4a — there is no UI for it — but `check` refuses a row
    # that carries it, so an operator can lock somebody out with one UPDATE.
    disabled_at: Mapped[str | None] = mapped_column(Text)
    # The user's default (model, effort) for new sessions on their own lanes (0008),
    # as catalog ids. NULL = the deploy default.
    default_model: Mapped[str | None] = mapped_column(Text)
    default_effort: Mapped[str | None] = mapped_column(Text)
    # The owner's per-person grants (0009): a shell (`Bash`/`Agent`/`Task`) on this
    # person's lanes, and the learning loop over their conversations. NULL =
    # inherit `users_shell_enabled` / `users_learning_enabled` from config, which
    # is what every row written before 0009 keeps doing. Only `Grants.of` turns
    # these into an answer; nothing else reads them.
    shell_enabled: Mapped[bool | None] = mapped_column(Boolean)
    learning_enabled: Mapped[bool | None] = mapped_column(Boolean)
    __table_args__ = (
        Index("users_one_owner", "role", unique=True,
              postgresql_where=text("role = 'owner'")),
    )

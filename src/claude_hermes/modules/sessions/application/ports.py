"""What the session services need from the world, as structural Protocols.

Nothing here imports SQLAlchemy, Starlette or the SDK: a port says *what* is
needed, and `infrastructure/` (or, at composition time, another module's object)
says how. `EventPublisher` and `LaneActivity` are the two the turns module
satisfies — `TurnBus.emit` and the `turns` repository's `active_lanes` have these
exact signatures, so the wiring is a plain assignment and neither module imports
the other.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """The `sessions` row: one SDK session per lane.

    `index_mtime` is a keyword with a default on both writers, so every caller
    that does not care is unchanged. `None` means "leave it alone", never "clear
    it": a plain `touch` after a turn that injected nothing must not un-say the
    injection an earlier turn made.

    `owner_id` (Wave 4a) is the lane's owner, always `lane_owner(...)` at the call
    site — this module never derives it, because `sessions` may not know what a
    deploy owner is. It follows the same "None leaves it alone" rule: a `touch`
    from a caller that has no owner to offer must not null a column the bind
    wrote.

    `model`/`effort` (0008) are what actually RAN the session, and the two travel
    together under the same rule: `model=None` leaves both columns alone, and a
    caller that names a model writes its effort with it — `None` there is the real
    value "no effort flag was sent", not an absence. `set_session_pair` is the
    one writer that changes them without a turn, and the reason it is a method of
    its own.

    `shell_enabled` (0009) is whether the session was started with a shell —
    decided by the caller (turns), stamped here, read back on `get` and dropped
    with the row by every reset. Same "None leaves it alone" rule. This module
    never interprets it: what a shell is and who has one is not `sessions`'
    business, only that the value survives until the next turn compares it.
    """

    async def get(self, session_key: str) -> dict | None: ...

    async def upsert(self, session_key: str, sdk_session_id: str | None,
                     system_append: str, now: str, *,
                     index_mtime: float | None = None,
                     owner_id: int | None = None,
                     model: str | None = None, effort: str | None = None,
                     shell_enabled: bool | None = None) -> None: ...

    async def touch(self, session_key: str, now: str, *,
                    index_mtime: float | None = None,
                    owner_id: int | None = None,
                    model: str | None = None, effort: str | None = None,
                    shell_enabled: bool | None = None) -> None: ...

    async def set_session_pair(self, session_key: str, model: str | None,
                               effort: str | None) -> None: ...

    async def owner_of(self, session_key: str) -> int | None: ...

    async def reset(self, session_key: str) -> None: ...

    async def reset_all(self) -> None: ...

    async def reset_owned(self, owner_id: int, *,
                          include_unowned: bool = False) -> None: ...


@runtime_checkable
class ThreadStore(Protocol):
    """Thread metadata — the part of a lane that outlives a session reset.

    `model_of` / `set_model` (0008) are the lane's EXPLICIT (model, effort): the
    pair a person chose, which is why it sits on the thread and not on the session
    — it has to survive `/new`, `reset_all` and every Customize mutation.
    `model_of` answers `None` for a lane with no thread row at all, which is a
    different thing from a row with no choice (`(None, None)`), and `set_model`
    writes only to a row that exists.
    """

    async def upsert(self, session_key: str, channel: str, owner_user_id: int | None,
                     now: str, *, talked: bool = False) -> None: ...

    async def get(self, session_key: str) -> dict | None: ...

    async def list(self, *, include_archived: bool = False, before: str | None = None,
                   limit: int = 50) -> list[dict]: ...

    async def set_title(self, session_key: str, title: str, manual: bool) -> None: ...

    async def set_archived(self, session_key: str, archived_at: str | None) -> None: ...

    async def model_of(self, session_key: str) -> tuple[str | None, str | None] | None: ...

    async def set_model(self, session_key: str, model: str | None,
                        effort: str | None) -> None: ...


@runtime_checkable
class MessageStore(Protocol):
    """The cross-session recall corpus.

    Wave 4a: `owner_id` is written with every row and is the ONLY thing `search`
    scopes by — one GIN query with `WHERE owner_id = :uid AND content_tsv @@ …`,
    never a Python filter after the fact, because a filter after `LIMIT 8` would
    hand a user eight of somebody else's rows and call it "no matches".
    `owner_id=None` on a read means "every owner" and exists for the
    deploy-wide curator; no request path passes it.
    """

    async def record(self, session_key: str, role: str, content: str, ts: str, *,
                     owner_id: int | None = None) -> None: ...

    async def last(self, session_key: str, role: str | None = None) -> dict | None: ...

    async def search(self, query: str, limit: int = 8, *,
                     owner_id: int | None = None) -> list[dict]: ...

    async def last_ts(self, *, owner_id: int | None = None) -> str | None: ...


@runtime_checkable
class TitleGenerator(Protocol):
    """Names a thread from its first message; "" when the model gave nothing usable."""

    async def generate(self, text: str) -> str: ...


@runtime_checkable
class EventPublisher(Protocol):
    """Satisfied by the turns module's `TurnBus`: tells an open web tab about a
    change that did not come from a turn (a new title, today)."""

    async def emit(self, session_key: str, turn_id: str, type: str, payload: dict): ...


@runtime_checkable
class OwnerDefaults(Protocol):
    """One person's default (model, effort) for their own new conversations.

    Satisfied by identity's `AccessControl.default_of` — the `users` row is where
    a personal preference belongs, and `sessions` may not import identity, so the
    gatherer asks through this shape. `(None, None)` is both "we have never been
    told" and "they cleared it", which resolve the same way: fall through to the
    deploy default.

    Asked ONLY for a lane that is not shared (`lane_is_shared`): a group, a forum
    topic and a `cron:` lane belong to the room, not to whoever spoke last, so
    nobody's personal default may move them.
    """

    async def default_of(self, user_id: int) -> tuple[str | None, str | None]: ...


@runtime_checkable
class LaneActivity(Protocol):
    """Which of these lanes have a turn in flight.

    Satisfied by the turns module's `turns` table — the ROW is the one source of
    truth for "running", in every process. The bus's in-flight snapshot is not:
    a dead worker's snapshot outlives its lease by up to the TTL, and a sidebar
    that spins on a lane nobody is working in is worse than one that is a second
    late.

    Batched, not per-row: a page of the sidebar asks once. One query per thread is
    how a thread list becomes slow, and with the answer in another module's table
    it would also be N round trips.
    """

    async def active_lanes(self, session_keys: list[str]) -> set[str]: ...

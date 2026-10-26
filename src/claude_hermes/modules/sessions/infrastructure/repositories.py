"""PostgreSQL behind the session, message and thread ports.

Each repository takes an `AsyncEngine` (never the `Database` facade) and each
call is one statement on a short transaction, so a caller holds no session. The
SQL moved here verbatim in Wave 2.
"""
from __future__ import annotations

from sqlalchemy import delete, func, insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ..domain.models import Message, Session, Thread

# The text-search configuration, spelled as a literal rather than a bind parameter:
# asyncpg types every bind, and a `regconfig` argument typed as varchar makes
# PostgreSQL fail to resolve to_tsquery/ts_headline at all.
_TS_CONFIG = literal_column("'simple'::regconfig")

# Same shape the SQLite snippet() call produced, so the web UI's highlighting and
# the Telegram recall block keep rendering unchanged.
_HEADLINE_OPTS = ('StartSel=[, StopSel=], MaxWords=12, MinWords=6, '
                  'MaxFragments=1, FragmentDelimiter=" … "')


class SessionRepository:
    """`sessions`: one SDK session per lane. Satisfies `SessionStore`."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def get(self, session_key: str) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(Session).where(Session.session_key == session_key))).mappings().first()
        return dict(row) if row else None

    async def upsert(self, session_key: str, sdk_session_id: str | None,
                     system_append: str, now: str, *,
                     index_mtime: float | None = None,
                     owner_id: int | None = None,
                     model: str | None = None, effort: str | None = None,
                     shell_enabled: bool | None = None) -> None:
        """Bind a lane to an SDK session.

        `index_mtime` is written only when the caller passes one. A `None` means
        "this turn injected no index", which must leave whatever an earlier turn
        recorded alone — writing NULL would make the next turn re-inject 8000
        characters the lane has already seen, which is the prompt-cache
        regression this column exists to prevent.

        `owner_id` follows the same rule for the same reason: `None` is "the
        caller has no owner to offer", and a row whose owner is already known must
        not lose it to a caller that has none (an import script).

        `model`/`effort` (0008) record what actually RAN this session, and the two
        travel together: `model=None` is "the caller has nothing to say about the
        model" and leaves both columns alone, while a caller that names a model
        writes its effort as well — including `None`, which is the real value
        "no effort flag was sent" and not an absence. Only `set_session_pair`
        overwrites the pair without a turn, and it does so on purpose.

        `shell_enabled` (0009) records whether this session was started with a
        shell, and follows the `owner_id` rule: `None` leaves the column alone, a
        bool writes it. The turn compares it with the lane's grant at the next
        turn's start, so a caller with nothing to say must not erase it — a NULL
        there reads back as "started under the pre-0009 rule", a different answer.
        """
        stmt = pg_insert(Session).values(
            session_key=session_key, sdk_session_id=sdk_session_id,
            system_append=system_append, created_at=now, updated_at=now,
            index_mtime=index_mtime, owner_id=owner_id,
            model=model, effort=effort, shell_enabled=shell_enabled)
        updates = {
            "sdk_session_id": stmt.excluded.sdk_session_id,
            "system_append": stmt.excluded.system_append,
            "updated_at": stmt.excluded.updated_at}
        if index_mtime is not None:
            updates["index_mtime"] = stmt.excluded.index_mtime
        if owner_id is not None:
            updates["owner_id"] = stmt.excluded.owner_id
        if model is not None:
            updates["model"] = stmt.excluded.model
            updates["effort"] = stmt.excluded.effort
        if shell_enabled is not None:
            updates["shell_enabled"] = stmt.excluded.shell_enabled
        stmt = stmt.on_conflict_do_update(index_elements=[Session.session_key],
                                          set_=updates)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def touch(self, session_key: str, now: str, *,
                    index_mtime: float | None = None,
                    owner_id: int | None = None,
                    model: str | None = None, effort: str | None = None,
                    shell_enabled: bool | None = None) -> None:
        values = {"updated_at": now}
        if index_mtime is not None:                 # see `upsert` for why
            values["index_mtime"] = index_mtime
        if owner_id is not None:                    # …and the same for the owner
            values["owner_id"] = owner_id
        if model is not None:                       # …and the pair travels together
            values["model"] = model
            values["effort"] = effort
        if shell_enabled is not None:               # …and the shell stamp (0009)
            values["shell_enabled"] = shell_enabled
        async with self._engine.begin() as conn:
            await conn.execute(update(Session)
                               .where(Session.session_key == session_key)
                               .values(**values))

    async def set_session_pair(self, session_key: str, model: str | None,
                               effort: str | None) -> None:
        """Overwrite what this session runs, without resetting it and without
        touching `updated_at` — a menu click is not activity, and must not stretch
        an idle session's life.

        Clearing a lane's explicit pair writes the lane's DEFAULT here, not NULL:
        a NULL pair on a live session reads back as "began before 0008", i.e. the
        deploy default, and would hide the owner's own default until `/new`. The
        SDK session itself survives: the switch happens on the next turn as a
        priced, shown cold write, never as a lost conversation. No row is not an
        error — there is no session to redirect.
        """
        async with self._engine.begin() as conn:
            await conn.execute(update(Session)
                               .where(Session.session_key == session_key)
                               .values(model=model, effort=effort))

    async def owner_of(self, session_key: str) -> int | None:
        """The lane's owner as recorded, or None for a lane with no row yet.

        One column, not the row: the caller wants an id, and selecting
        `system_append` to find it would pull 8 KB of prompt across the wire per
        ask.
        """
        async with self._engine.connect() as conn:
            return (await conn.execute(
                select(Session.owner_id)
                .where(Session.session_key == session_key))).scalar_one_or_none()

    async def reset(self, session_key: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(Session).where(Session.session_key == session_key))

    async def reset_all(self) -> None:
        """`reset` with no WHERE: every lane starts a fresh SDK session.

        The bulk form exists because a change to the cached prompt prefix — a
        plugin, a skill, a connector's toolset — invalidates every lane at once,
        and there is no cheap way to know which lanes composed which extension.
        """
        async with self._engine.begin() as conn:
            await conn.execute(delete(Session))

    async def reset_owned(self, owner_id: int, *, include_unowned: bool = False) -> None:
        """`reset_all` narrowed to one owner's lanes (Wave 4b, B-4).

        A skill one user edits changes the index in THEIR cached prefix and in
        nobody else's, so evicting every lane would throw away every other
        user's warm cache for nothing. `include_unowned` adds the `owner_id IS
        NULL` rows: a lane bound before the deploy was claimed wrote NULL (the
        runner passes `None` for owner `0`), and those lanes are the deploy
        owner's — only the caller that IS the owner may say so.
        """
        where = Session.owner_id == owner_id
        if include_unowned:
            where = where | Session.owner_id.is_(None)
        async with self._engine.begin() as conn:
            await conn.execute(delete(Session).where(where))


class MessageRepository:
    """`messages`: the cross-session recall corpus. Satisfies `MessageStore`."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def record(self, session_key: str, role: str, content: str, ts: str, *,
                     owner_id: int | None = None) -> None:
        """Append one message. `owner_id` is denormalised onto the row on purpose
        (D2.5): recall is one GIN query and it has to stay one query, so the owner
        has to be a column on `messages` rather than a join to `sessions`."""
        async with self._engine.begin() as conn:
            await conn.execute(insert(Message).values(
                session_key=session_key, role=role, content=content, ts=ts,
                owner_id=owner_id))

    async def last(self, session_key: str, role: str | None = None) -> dict | None:
        """Most recent message row for one session key. A plain column scan, not a
        text-search match — immune to whatever the model typed as a query."""
        stmt = (select(Message.role, Message.content, Message.ts)
                .where(Message.session_key == session_key))
        if role is not None:
            stmt = stmt.where(Message.role == role)
        # id breaks ts ties: two messages of the same turn share a timestamp, and
        # "last" has to mean the one written last.
        stmt = stmt.order_by(Message.ts.desc(), Message.id.desc()).limit(1)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def search(self, query: str, limit: int = 8, *,
                     owner_id: int | None = None) -> list[dict]:
        """Full-text recall, scoped to one owner when the caller names one.

        The owner predicate is part of the SAME statement as the text match, not a
        filter over its result: `LIMIT 8` is applied by PostgreSQL, so filtering
        afterwards would silently turn "eight of user A's matches" into "no
        matches" for user B — an isolation hole that looks like an empty index.
        `owner_id=None` searches every owner and is what the deploy-wide curator
        passes.
        """
        # plainto_tsquery, not to_tsquery: it treats the input as words, so no
        # amount of operator soup from the model can make this raise. The query is
        # punctuation-stripped the same way `content_tsv` is, because PostgreSQL's
        # parser would otherwise keep "report.pdf" or a URL as one indivisible token
        # and a search for "report" would miss it.
        q = func.plainto_tsquery(_TS_CONFIG, func.regexp_replace(
            query, "[^[:alnum:]]+", " ", "g"))
        stmt = (
            select(Message.session_key, Message.role, Message.ts,
                   func.ts_headline(_TS_CONFIG, Message.content, q,
                                    _HEADLINE_OPTS).label("snippet"))
            .where(Message.content_tsv.op("@@")(q))
            .order_by(func.ts_rank(Message.content_tsv, q).desc(), Message.id)
            .limit(limit))
        if owner_id is not None:
            stmt = stmt.where(Message.owner_id == owner_id)
        async with self._engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(stmt)).mappings().all()]

    async def last_ts(self, *, owner_id: int | None = None) -> str | None:
        stmt = select(func.max(Message.ts))
        if owner_id is not None:
            stmt = stmt.where(Message.owner_id == owner_id)
        async with self._engine.connect() as conn:
            return (await conn.execute(stmt)).scalar_one_or_none()


class ThreadRepository:
    """`threads`: the metadata that must outlive a session reset. Satisfies
    `ThreadStore`."""

    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    async def upsert(self, session_key: str, channel: str, owner_user_id: int | None,
                     now: str, *, talked: bool = False) -> None:
        """Create or bump a thread row. `talked=True` also records that somebody
        has taken a turn in this lane.

        The flag only ever goes up: the conflict branch ORs it with what is stored,
        so a later plain touch (a title fetch, a topic being provisioned) cannot
        un-say a turn that happened. Callers that merely create a lane — the web
        thread endpoint, job-topic creation — leave it false, which is what keeps
        an empty shell out of the sidebar.

        `owner_user_id` is `lane_owner(...)` at every 4a call site, and the
        conflict branch COALESCEs it: an existing owner is never overwritten (a
        lane does not change hands) and a legacy row that still has NULL — one the
        `0005` backfill could not derive, or one written by a caller that passes
        None, like the scheduler's delivery-event ensure — heals the first time
        somebody takes a turn in it. Without the COALESCE those rows would stay
        invisible to their own owner forever.
        """
        stmt = pg_insert(Thread).values(
            session_key=session_key, channel=channel, owner_user_id=owner_user_id,
            last_event_at=now, created_at=now, has_turns=talked)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Thread.session_key],
            set_={"last_event_at": stmt.excluded.last_event_at,
                  "has_turns": Thread.has_turns | stmt.excluded.has_turns,
                  "owner_user_id": func.coalesce(Thread.owner_user_id,
                                                 stmt.excluded.owner_user_id)})
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def get(self, session_key: str) -> dict | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(Thread).where(Thread.session_key == session_key))).mappings().first()
        return dict(row) if row else None

    async def list(self, *, include_archived: bool = False, before: str | None = None,
                   limit: int = 50) -> list[dict]:
        # A web thread is listed from the moment a person creates it. Any other
        # lane earns its row by being talked in (or named): the imported Telegram
        # lanes have a threads row, but those turns predate turn_events, and to
        # the sidebar such a lane is an empty shell.
        stmt = select(Thread).where(
            (Thread.channel == "web") | Thread.title.is_not(None) | Thread.has_turns)
        if not include_archived:
            stmt = stmt.where(Thread.archived_at.is_(None))
        if before is not None:
            stmt = stmt.where(Thread.last_event_at < before)
        stmt = stmt.order_by(Thread.last_event_at.desc()).limit(limit)
        async with self._engine.connect() as conn:
            return [dict(r) for r in (await conn.execute(stmt)).mappings().all()]

    async def set_title(self, session_key: str, title: str, manual: bool) -> None:
        # An automatic title never overwrites one the user typed.
        stmt = update(Thread).where(Thread.session_key == session_key)
        if manual:
            stmt = stmt.values(title=title, title_manual=1)
        else:
            stmt = stmt.where(Thread.title_manual == 0).values(title=title)
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def set_archived(self, session_key: str, archived_at: str | None) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(Thread)
                               .where(Thread.session_key == session_key)
                               .values(archived_at=archived_at))

    async def model_of(self, session_key: str) -> tuple[str | None, str | None] | None:
        """The lane's explicit (model, effort), or `None` when it has no thread row.

        Two columns rather than `get`: the resolver asks this on every turn, and
        `get` would pull the title, the channel and the timestamps to answer a
        question about two short strings — the same reason `owner_of` exists.
        `None` for "no row" is load-bearing: a lane nobody has spoken in yet
        cannot be given a model, and the caller turns that into its own reply.
        """
        async with self._engine.connect() as conn:
            row = (await conn.execute(
                select(Thread.model, Thread.effort)
                .where(Thread.session_key == session_key))).first()
        return (row[0], row[1]) if row is not None else None

    async def set_model(self, session_key: str, model: str | None,
                        effort: str | None) -> None:
        """Write the lane's explicit pair — on an EXISTING row only.

        An UPDATE and deliberately not the upsert the rest of this file favours:
        a thread row carries a channel and an owner this call is not told, and a
        lane invented here would be an unowned shell in everybody's sidebar.
        A key with no row is silently untouched; `SessionService.set_model` is
        what turns that into a `NotFound`.
        """
        async with self._engine.begin() as conn:
            await conn.execute(update(Thread)
                               .where(Thread.session_key == session_key)
                               .values(model=model, effort=effort))

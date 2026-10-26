"""Session and thread policy: expiry, the model a lane runs, visibility, titling.

`SessionRouter` decides whether a lane's SDK session is still the same
conversation. `SessionService` decides which (model, effort) that conversation
runs on — the one gatherer behind `core.model_catalog.resolve`. `ThreadService`
decides who may see which lane and keeps the sidebar's paging honest.
`ThreadTitleService` names an untitled thread in the background. All four talk to
ports, never to SQL.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from claude_hermes.core.config import Config
from claude_hermes.core.exceptions import NotFound
from claude_hermes.core.model_catalog import (
    Choice,
    lane_is_shared,
    lookup,
    normalize,
    offered,
    resolve,
    validate,
)

from ..domain.keys import web_session_key
from ..domain.policies import visible
from .ports import (
    EventPublisher,
    LaneActivity,
    OwnerDefaults,
    SessionStore,
    ThreadStore,
    TitleGenerator,
)

log = structlog.get_logger(__name__)


class SessionRouter:
    """The SDK session for a lane, or None when it has expired.

    Also the module's side of the turns module's `SessionLane` port: `reset`,
    `reset_all` and `get` live here so the runner stops reaching past the router
    into the store — the router is the only thing that knows a lane's session
    lifecycle.
    """

    def __init__(self, store: SessionStore, cfg: Config):
        self._store = store
        self._cfg = cfg

    async def route(self, session_key: str, now: datetime) -> dict | None:
        row = await self._store.get(session_key)
        if row is None:
            return None
        if not self.alive(row, now):
            await self._store.reset(session_key)
            return None
        return row

    def alive(self, row: dict, now: datetime) -> bool:
        """Is this session row still the same conversation — WITHOUT deleting it.

        `route` is the writer: it resets an expired lane, because the turn that
        asked is about to start a fresh session. The model resolver asks the same
        question as a reader (`SessionService.effective`), and a read that
        silently dropped a lane's session would turn opening a model menu into a
        lost conversation. One rule, two callers, and the rule is `_expired`.
        """
        last = datetime.fromisoformat(row["updated_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return not self._expired(last, now)

    def _expired(self, last: datetime, now: datetime) -> bool:
        idle = self._cfg.idle_reset_hours
        if idle and now - last > timedelta(hours=idle):
            return True
        hour = self._cfg.daily_reset_hour
        if hour is not None:
            boundary = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if boundary > now:
                boundary -= timedelta(days=1)
            if last < boundary:
                return True
        return False

    async def bind(
        self, session_key: str, sdk_session_id: str, system_append: str, now_iso: str,
        *, index_mtime: float | None = None, owner_id: int | None = None,
        model: str | None = None, effort: str | None = None,
        shell_enabled: bool | None = None,
    ) -> None:
        """Bind a lane to an SDK session, recording whose lane it is and what ran it.

        `owner_id` is `lane_owner(...)` decided by the caller (the turn runner).
        The router does not compute it: that rule needs the deploy owner, and
        `sessions` may not import identity.

        `model` is the model that actually ANSWERED (`AssistantMessage.model`),
        never the one requested — a fallback or a substitution must leave the row
        saying what the session really runs, because the resolver reads it to know
        what a live session's cached prefix is scoped to. Passing none leaves the
        columns alone.

        `shell_enabled` (0009) is whether the session was started with a shell,
        decided by the turn and only carried here — the turn reads it back at the
        next turn's start and resets the lane if the grant has moved since.
        `None` leaves it alone.
        """
        await self._store.upsert(session_key, sdk_session_id, system_append, now_iso,
                                 index_mtime=index_mtime, owner_id=owner_id,
                                 model=model, effort=effort,
                                 shell_enabled=shell_enabled)

    async def touch(self, session_key: str, now_iso: str, *,
                    index_mtime: float | None = None,
                    owner_id: int | None = None,
                    model: str | None = None, effort: str | None = None,
                    shell_enabled: bool | None = None) -> None:
        await self._store.touch(session_key, now_iso, index_mtime=index_mtime,
                                owner_id=owner_id, model=model, effort=effort,
                                shell_enabled=shell_enabled)

    async def owner_of(self, session_key: str) -> int | None:
        """Whose lane this is, off the row — for a caller that holds a key and no
        way to apply `lane_owner` (it has no deploy owner). `None` when the lane
        has never been bound, which is not the same as "nobody's": a turn resolves
        its workspace from the KEY, never from this."""
        return await self._store.owner_of(session_key)

    async def get(self, session_key: str) -> dict | None:
        return await self._store.get(session_key)

    async def reset(self, session_key: str) -> None:
        await self._store.reset(session_key)

    async def set_session_pair(self, session_key: str, model: str | None,
                               effort: str | None) -> None:
        """Redirect what the live session runs, without ending it. See the store."""
        await self._store.set_session_pair(session_key, model, effort)

    async def reset_all(self) -> None:
        await self._store.reset_all()

    async def reset_owned(self, owner_id: int, *, include_unowned: bool = False) -> None:
        """Drop every lane ONE user owns — the reset a change to their own prompt
        prefix (a skill edit) needs, where `reset_all` is for a change to
        everyone's (Wave 4b, B-4). `include_unowned` is the deploy owner's belt:
        a lane bound while `deploy_owner()` was still 0 wrote NULL, and nobody
        but the owner may claim those."""
        await self._store.reset_owned(owner_id, include_unowned=include_unowned)

    async def is_unbound(self, session_key: str) -> bool:
        """True while a lane has no session row — i.e. nobody has taken a turn in
        it yet. The titler asks this BEFORE the turn runs: once the turn has
        created the row the thread no longer looks new."""
        return await self._store.get(session_key) is None


class SessionService:
    """Which model this lane runs, and the one way to change it.

    Every channel asks the same question — the composer's picker, `/model` in
    Telegram, the turn about to call the CLI — and a question answered in three
    places is answered three different ways. So there is one gatherer here and
    one RULE in `core.model_catalog.resolve()`: this class collects the four
    inputs the rule takes and hands them over, and contains no ordering of its
    own.

    What it gathers, in the order `resolve` consumes it:

    1. the lane's explicit pair (`threads.model/effort`) — what a person chose;
    2. the pair its LIVE session runs (`sessions.model/effort`), where "live" is
       `SessionRouter`'s own expiry test applied WITHOUT deleting the row: a
       changed default must not move a conversation whose cached prefix is
       already scoped to another model (P1), and reading the menu must not end
       a conversation;
    3. the lane owner's personal default, through the `OwnerDefaults` port — and
       never on a shared lane (a group, a forum topic, a `cron:` job), which
       belongs to the room rather than to whoever spoke last;
    4. the deploy default, with no effort.

    A live session whose pair is NULL began before 0008 and therefore ran the
    deploy default: it counts as `(normalize(cfg.model), None)` rather than as
    nothing, or a personal default set today would silently move it.
    """

    def __init__(self, threads: ThreadStore, router: SessionRouter, cfg: Config, *,
                 defaults: OwnerDefaults | None = None,
                 events: EventPublisher | None = None):
        self._threads = threads
        self._router = router
        self._cfg = cfg
        self._defaults = defaults
        self._events = events
        self._offered_key: tuple | None = None
        self._offered: list = []

    def offered_models(self) -> list:
        """The `ModelInfo`s a user may pick from, in config order.

        Cached against the three config values it is computed from rather than
        recomputed per call: `offered()` warns when a model holds more context
        than the review fork can resume, and that warning is a boot-time fact,
        not one line per turn in the log.
        """
        key = (tuple(self._cfg.models or ()), self._cfg.model, self._cfg.review_model)
        if key != self._offered_key:
            self._offered_key = key
            self._offered = offered(self._cfg.models, self._cfg.model,
                                    self._cfg.review_model or self._cfg.model)
        return self._offered

    def offered_ids(self) -> list[str]:
        return [info.id for info in self.offered_models()]

    async def effective(self, session_key: str, owner_id: int | None = None, *,
                        now: datetime | None = None) -> Choice:
        """The (model, effort) this lane's next turn runs on. Never raises for a
        lane that does not exist yet: an unknown key simply has no pair of its
        own and falls through to the owner's default or the deploy's."""
        moment = now or datetime.now(timezone.utc)
        thread = await self._threads.model_of(session_key)
        row = await self._router.get(session_key)
        session = None
        if row is not None and self._router.alive(row, moment):
            session = (row.get("model"), row.get("effort"))
            if not session[0]:
                session = (normalize(self._cfg.model), None)
        return await self._resolve(session_key, owner_id, thread, session)

    async def _resolve(self, session_key: str, owner_id: int | None, thread, session) -> Choice:
        """`resolve` over this lane's owner default — the one place that asks for it."""
        shared = lane_is_shared(session_key)
        owner_default = None
        if not shared and owner_id and self._defaults is not None:
            owner_default = await self._defaults.default_of(owner_id)
        return resolve(thread, session, owner_default, shared=shared,
                       deploy_model=self._cfg.model, offered_ids=self.offered_ids())

    async def set_model(self, session_key: str, model: str | None,
                        effort: str | None) -> tuple[str | None, str | None]:
        """Write the lane's explicit pair and tell every open tab. Returns it.

        `model=None` clears the choice, and the effort goes with it — an effort
        without a model is a preference about nothing. Clearing is a switch to the
        lane's DEFAULT now, so the live session is redirected to it
        (`set_session_pair`): left alone it would keep running the model just
        cleared until it expired, and nulled it would read as a pre-0008 session —
        the deploy default — hiding the owner's own default until `/new`.

        Raises `NotFound` for a lane with no thread row (there is nothing to hang
        a choice on — the caller says "send a message here first") and
        `ValueError("bad_model" | "bad_effort")` from `validate` for a write the
        resolver would silently ignore. Both are answers the caller spells in its
        own channel's words.
        """
        if await self._threads.model_of(session_key) is None:
            raise NotFound("no such thread")
        validate(model, effort, self.offered_ids())
        # Through `lookup`: the caller may have named the model by an alias or by
        # its `[1m]` CLI spelling, and what is stored is always the catalog id.
        pair = (lookup(model).id, effort) if model else (None, None)
        await self._threads.set_model(session_key, *pair)
        if pair[0] is None:
            owner = (await self._threads.get(session_key) or {}).get("owner_user_id")
            default = await self._resolve(session_key, owner, None, None)
            await self._router.set_session_pair(session_key, default.model, default.effort)
        if self._events is not None:
            # `thread.title`'s pattern exactly (`turn_id=""`), and for the same
            # reason: this change did not come from a turn, and a tab that is open
            # on the lane has to see it without a reload. Only the EXPLICIT pair
            # travels — what the lane resolves to depends on the reader's own
            # owner default, so the pill re-reads `effective_*` for itself.
            await self._events.emit(session_key, "", "thread.model",
                                    {"model": pair[0], "effort": pair[1]})
        return pair


class ThreadService:
    """Thread metadata and the visibility rule around it.

    Satisfies the turns/scheduler `ThreadRegistry` port (`touch`) and the files
    module's `ThreadVisibility` port (`get_visible`).
    """

    def __init__(self, threads: ThreadStore, activity: LaneActivity | None = None):
        self._threads = threads
        self._activity = activity

    async def running_lanes(self, session_keys) -> set[str]:
        """Which of these lanes have a turn in flight.

        Batched, and asked once per page rather than once per row: the answer
        lives in the turns module's table, so per-row would be N round trips to
        render one sidebar. A deployment with no activity port (a test, a headless
        build) gets the empty set — every thread renders as idle, which is the
        honest answer when nothing can tell us otherwise.
        """
        keys = list(session_keys)
        if not keys or self._activity is None:
            return set()
        return await self._activity.active_lanes(keys)

    async def touch(self, session_key: str, channel: str, owner_user_id: int | None,
                    now: str, *, talked: bool = False) -> None:
        await self._threads.upsert(session_key, channel, owner_user_id, now, talked=talked)

    async def get(self, session_key: str) -> dict | None:
        return await self._threads.get(session_key)

    async def get_visible(self, session_key: str, user_id: int, is_owner: bool) -> dict:
        """The thread, or `NotFound` — never "it exists but is not yours". A 403
        would tell a stranger that a lane with that key exists."""
        thread = await self._threads.get(session_key)
        if thread is None or not visible(thread, user_id, is_owner):
            raise NotFound("no such thread")
        return thread

    async def create_web(self, user_id: int, now: str) -> dict:
        """A fresh browser lane, listed from the moment it is created (`talked`
        stays false: creating a thread is not taking a turn in it)."""
        key = web_session_key(user_id)
        await self._threads.upsert(key, "web", user_id, now)
        return await self._threads.get(key)

    async def list_visible(self, user_id: int, is_owner: bool, *,
                           include_archived: bool = False, before: str | None = None,
                           limit: int = 50) -> tuple[list[dict], str | None]:
        # `visible` is a Python predicate, so filtering AFTER a SQL LIMIT would page
        # over rows the caller cannot see: a user whose only lane sits behind 50
        # group lanes would get an empty page and a null cursor — an empty sidebar,
        # forever. Keep pulling batches until there are enough visible rows to
        # answer with, or the table runs out.
        mine: list[dict] = []
        cursor = before
        while len(mine) <= limit:
            rows = await self._threads.list(include_archived=include_archived,
                                            before=cursor, limit=limit + 1)
            mine.extend(t for t in rows if visible(t, user_id, is_owner))
            if len(rows) <= limit:                     # the table is exhausted
                break
            cursor = rows[-1]["last_event_at"]
        page, more = mine[:limit], len(mine) > limit
        return page, (page[-1]["last_event_at"] if more and page else None)

    async def set_title(self, session_key: str, title: str, *, manual: bool) -> None:
        await self._threads.set_title(session_key, title, manual)

    async def set_archived(self, session_key: str, archived_at: str | None) -> None:
        await self._threads.set_archived(session_key, archived_at)


class ThreadTitleService:
    """Names an untitled thread from its first message, in the background.

    The task set is held on the instance, not on the module: two apps in one
    process (a test suite is exactly that) must not share a task set, and a
    module-level one outlives the app it belongs to.
    """

    def __init__(self, threads: ThreadStore, generator: TitleGenerator | None = None,
                 events: EventPublisher | None = None):
        self._threads = threads
        self._generator = generator
        self._events = events
        self._tasks: set[asyncio.Task] = set()

    def autotitle(self, session_key: str, text: str) -> asyncio.Task | None:
        """Fire-and-forget: returns the task so a caller can await it in a test."""
        if self._generator is None:
            return None
        task = asyncio.create_task(self._autotitle(session_key, text))
        self._tasks.add(task)                      # keep a ref: the loop only weakly
        task.add_done_callback(self._tasks.discard)  # references tasks
        return task

    async def _autotitle(self, session_key: str, text: str) -> None:
        try:
            thread = await self._threads.get(session_key)
            if thread is None or thread["title"]:
                return
            title = await self._generator.generate(text)
            if not title:
                return
            await self.apply(session_key, title)
        except Exception:
            log.warning("auto title failed for %s", session_key, exc_info=True)

    async def apply(self, session_key: str, title: str) -> None:
        """Write a generated title and tell any open tab about it.

        `manual=False`: a title the person typed is never overwritten. The emit is
        what makes the sidebar row rename itself without a reload.
        """
        await self._threads.set_title(session_key, title, False)
        if self._events is not None:
            await self._events.emit(session_key, "", "thread.title", {"title": title})

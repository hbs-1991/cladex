"""The only place `content_items` SQL lives.

Every status change is ONE conditional statement — `UPDATE … WHERE status IN
(:expected) … RETURNING` — never a read followed by a write. That is what lets the
web routes, the agent tool and N ticks (redis mode runs `scheduler_tick` on every
worker) act on the same row without a lock of their own: PostgreSQL re-evaluates
the WHERE of a statement that waited on a row lock against the committed row, so
the second of two racing writers simply matches nothing.

Where a statement must also know what a row WAS (the card a content edit makes
stale), it reads the old value in a `FROM (SELECT … FOR UPDATE)` of the same
statement: PostgreSQL's `RETURNING` sees only the new row.

`meta_swap` is the one statement here on another table: the card topic's id is
written with a compare-and-set, which `core.meta.MetaStore` (get/set) cannot do.
"""
from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, case, delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from claude_hermes.core.db.models import Meta
from claude_hermes.modules.content.domain import cards as cd
from claude_hermes.modules.content.domain import status as st
from claude_hermes.modules.content.domain.models import ContentItem

# Columns a caller may write through `create`/`update`. `id`, `owner_id` and
# `created_at` are not in it: a request body forwarded whole cannot re-own a row.
_WRITABLE = frozenset({
    "channel", "format", "title", "body", "media", "publish_at", "status",
    "group_key", "source_path", "thread_id", "created_via", "approved_at",
    "approved_by", "attempts", "next_wake_at", "claimed_at", "published_url", "error",
    "theme_approved_at", "draft_attempts", "draft_wakes", "draft_next_wake_at",
    "redrafts", "draft_comment", "drafting_since", "card_state", "card_revision",
    "card_chat_id", "card_message_id", "card_attempts", "card_claimed_at",
})
PREVIOUS_CARD = cd.PREVIOUS_CARD


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pick(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if k in _WRITABLE}


def _due_wake(now_iso: str):
    """A queued item whose next wake is due. NULL counts as due: every writer that
    queues an item stamps it, but a row that somehow has none must not be stranded."""
    return or_(ContentItem.next_wake_at.is_(None), ContentItem.next_wake_at <= now_iso)


def _after_wake(after_wake: list[str], counter: Any = None):
    """`next_wake_at` for an item that has been woken `counter` times (`attempts`
    unless told otherwise): the i-th entry of the caller's backoff, the last one
    for everything past it. The caller computes the instants (the repository does
    no clock arithmetic)."""
    counter = ContentItem.attempts if counter is None else counter
    whens = [(counter == i, at) for i, at in enumerate(after_wake[:-1])]
    return case(*whens, else_=after_wake[-1]) if whens else after_wake[-1]


def _draft_wake_due(now_iso: str):
    return or_(ContentItem.draft_next_wake_at.is_(None),
               ContentItem.draft_next_wake_at <= now_iso)


def _due_for_drafting(now_iso: str, horizon_iso: str):
    """A `planned` item inside its drafting window, `publish_at - lead <= now <
    publish_at`, spelled `now < publish_at <= now + lead` (`horizon`)."""
    return and_(ContentItem.status == st.PLANNED, ContentItem.publish_at > now_iso,
                ContentItem.publish_at <= horizon_iso)


def _superseded_card():
    """`card_state` after a content edit: a live card is stale, a failed one is
    settled — the owner (or the agent) has now acted on the item itself, so the
    card that never arrived stops asking for attention — and any other stays."""
    return case((ContentItem.card_state.in_(cd.CARD_LIVE), cd.CARD_SUPERSEDED),
                (ContentItem.card_state == cd.CARD_FAILED, cd.CARD_NONE),
                else_=ContentItem.card_state)


def _rows(result) -> list[dict]:
    return [dict(r) for r in result.mappings().all()]


class ContentRepository:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    # --- rows -------------------------------------------------------------------
    async def create(self, owner_id: int, values: dict[str, Any]) -> dict:
        now = _now_iso()
        stmt = insert(ContentItem).values(
            owner_id=owner_id, created_at=now, updated_at=now, **_pick(values),
        ).returning(ContentItem)
        async with self._engine.begin() as conn:
            return dict((await conn.execute(stmt)).mappings().one())

    async def get(self, item_id: int, owner_id: int) -> dict | None:
        """One item of one owner, or None — somebody else's item and a missing one
        are the same answer, so the routes 404 rather than 403."""
        stmt = select(ContentItem).where(ContentItem.id == item_id,
                                         ContentItem.owner_id == owner_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def list_items(self, owner_id: int, *, start_iso: str | None = None,
                         end_iso: str | None = None, status: str | None = None,
                         channel: str | None = None, group_key: str | None = None,
                         limit: int = 500) -> list[dict]:
        """Dated items in `[start, end)` (either bound optional), soonest first.

        A range read excludes the backlog by construction: `publish_at` NULL never
        compares true. Without either bound, backlog rows come last.
        """
        stmt = select(ContentItem).where(ContentItem.owner_id == owner_id)
        if start_iso is not None:
            stmt = stmt.where(ContentItem.publish_at >= start_iso)
        if end_iso is not None:
            stmt = stmt.where(ContentItem.publish_at < end_iso)
        if status:
            stmt = stmt.where(ContentItem.status == status)
        if channel:
            stmt = stmt.where(ContentItem.channel == channel)
        if group_key:
            stmt = stmt.where(ContentItem.group_key == group_key)
        stmt = stmt.order_by(ContentItem.publish_at.asc().nulls_last(),
                             ContentItem.id).limit(limit)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def backlog(self, owner_id: int) -> list[dict]:
        """Undated items that still have a future: not published, not skipped."""
        stmt = (
            select(ContentItem)
            .where(ContentItem.owner_id == owner_id,
                   ContentItem.publish_at.is_(None),
                   ContentItem.status.not_in(st.BACKLOG_EXCLUDED))
            .order_by(ContentItem.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def update(self, item_id: int, owner_id: int, values: dict[str, Any], *,
                     expect: Collection[str] | None = None,
                     supersede: bool = False) -> dict | None:
        """Write `values` if the item is still in one of `expect` (any status when
        None). Returns the new row, or None when nothing matched — gone, not this
        owner's, or moved on by somebody else in the meantime.

        `supersede=True` marks a CONTENT edit: a live card (`pending`, `sending`,
        `sent`) becomes `superseded` and a `failed` one `none` in the same
        statement, and the row comes back with `previous_card_state` — the state it
        had — so the caller knows whether a card in Telegram now shows something
        that is no longer true.
        """
        values = {"updated_at": _now_iso(), **_pick(values)}
        if not supersede:
            stmt = (
                update(ContentItem)
                .where(ContentItem.id == item_id, ContentItem.owner_id == owner_id)
                .values(**values)
                .returning(ContentItem)
            )
        else:
            old = (select(ContentItem.id.label("old_id"),
                          ContentItem.card_state.label(PREVIOUS_CARD))
                   .where(ContentItem.id == item_id, ContentItem.owner_id == owner_id)
                   .with_for_update().subquery("old"))
            values.setdefault("card_state", _superseded_card())
            stmt = (
                update(ContentItem)
                .where(ContentItem.id == old.c.old_id)
                .values(**values)
                .returning(ContentItem, old.c[PREVIOUS_CARD])
            )
        if expect is not None:
            stmt = stmt.where(ContentItem.status.in_(tuple(expect)))
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def delete(self, item_id: int, owner_id: int, *,
                     forbid: Collection[str] = ()) -> bool:
        stmt = delete(ContentItem).where(ContentItem.id == item_id,
                                         ContentItem.owner_id == owner_id)
        if forbid:
            stmt = stmt.where(ContentItem.status.not_in(tuple(forbid)))
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt.returning(ContentItem.id))).first() is not None

    async def claim(self, item_id: int, owner_id: int, now_iso: str) -> dict | None:
        """`queued -> publishing`, once. Two publisher runs claiming the same item
        at the same instant serialise on the row lock and the second matches no
        `status='queued'` row — which is the whole "never post twice" guarantee on
        the plan's side."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == item_id, ContentItem.owner_id == owner_id,
                   ContentItem.status == st.QUEUED)
            .values(status=st.PUBLISHING, claimed_at=now_iso, next_wake_at=None,
                    error=None, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    # --- the tick ---------------------------------------------------------------
    # Deploy-wide on purpose (no owner filter where there is none): one clock
    # serves every owner's plan, as `claim_due` serves every owner's jobs.
    async def fail_late(self, cutoff_iso: str, now_iso: str, error: str) -> list[dict]:
        """`scheduled` items whose time passed before `cutoff` -> `failed`. Runs
        before `promote_due`, so a late item is never queued."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.status == st.SCHEDULED,
                   ContentItem.publish_at < cutoff_iso)
            .values(status=st.FAILED, error=error, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def fail_empty(self, now_iso: str, error: str) -> list[dict]:
        """`scheduled` items whose time has come with nothing to publish (a blank
        body) -> `failed`. The approval gate refuses a blank post, so only a row
        approved before that gate existed, or forced by hand, gets here."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.status == st.SCHEDULED,
                   ContentItem.publish_at <= now_iso,
                   func.btrim(ContentItem.body) == "")
            .values(status=st.FAILED, error=error, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            return _rows(await conn.execute(stmt))

    async def promote_due(self, now_iso: str, cutoff_iso: str) -> list[dict]:
        """`scheduled` items whose time has come (and not by more than the late
        window) -> `queued`, wake due now, no wakes counted yet. A blank body is
        never queued: `fail_empty` runs first, and this WHERE is the second lock."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.status == st.SCHEDULED,
                   ContentItem.publish_at <= now_iso,
                   ContentItem.publish_at >= cutoff_iso,
                   func.btrim(ContentItem.body) != "")
            .values(status=st.QUEUED, attempts=0, next_wake_at=now_iso, error=None,
                    updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def wake_owners(self, now_iso: str) -> list[int]:
        """Owners with at least one queued item whose wake is due."""
        stmt = select(ContentItem.owner_id).where(
            ContentItem.status == st.QUEUED, _due_wake(now_iso)).distinct()
        async with self._engine.connect() as conn:
            return [int(r[0]) for r in (await conn.execute(stmt)).all()]

    async def note_queued(self, owner_id: int, now_iso: str, error: str) -> int:
        """Explain why an owner's queued items are not moving (no publisher, a paused
        one). Written only where it differs, so a tick every 30 s does not rewrite
        `updated_at` on rows nothing happened to. Counts nothing: the backoff is for
        an idle publisher that ignores its wake, not for a missing one."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id, ContentItem.status == st.QUEUED,
                   or_(ContentItem.error.is_(None), ContentItem.error != error))
            .values(error=error, updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount or 0

    async def postpone_wakes(self, owner_id: int, now_iso: str,
                             after_wake: list[str]) -> int:
        """The publisher is busy: push every due wake forward by the item's current
        backoff step WITHOUT counting it. The backoff measures idle publisher time,
        and a run in progress (or a wake already pending) may well be the one that
        takes the item."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id, ContentItem.status == st.QUEUED,
                   _due_wake(now_iso))
            .values(next_wake_at=_after_wake([now_iso, *after_wake]), error=None,
                    updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount or 0

    async def take_wakes(self, owner_id: int, now_iso: str, after_wake: list[str],
                         max_wakes: int) -> list[int]:
        """The wake compare-and-set: count one wake on every due item that has
        wakes left, and move its next one out by the backoff. Returns the ids this
        call counted; an empty list means another tick got there first (or nothing
        was due) and the caller must NOT wake the publisher."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id, ContentItem.status == st.QUEUED,
                   _due_wake(now_iso), ContentItem.attempts < max_wakes)
            .values(attempts=ContentItem.attempts + 1,
                    next_wake_at=_after_wake(after_wake), error=None,
                    updated_at=now_iso)
            .returning(ContentItem.id)
        )
        async with self._engine.begin() as conn:
            return [int(r[0]) for r in (await conn.execute(stmt)).all()]

    async def release_wakes(self, item_ids: list[int], now_iso: str) -> None:
        """Undo `take_wakes` for a wake that did not happen (the publisher turned
        busy between the check and the wake): the count goes back and the item is
        due again on the next tick."""
        if not item_ids:
            return
        stmt = (
            update(ContentItem)
            .where(ContentItem.id.in_(item_ids), ContentItem.status == st.QUEUED,
                   ContentItem.attempts > 0)
            .values(attempts=ContentItem.attempts - 1, next_wake_at=now_iso,
                    updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def fail_unclaimed(self, owner_id: int, now_iso: str, max_wakes: int,
                             error: str) -> list[dict]:
        """Queued items that used every wake and waited out the last backoff ->
        `failed`. Never automatic re-posting: `failed` waits for the owner."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id, ContentItem.status == st.QUEUED,
                   _due_wake(now_iso), ContentItem.attempts >= max_wakes)
            .values(status=st.FAILED, error=error, next_wake_at=None,
                    updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    async def publishing_owners(self) -> list[int]:
        stmt = select(ContentItem.owner_id).where(
            ContentItem.status == st.PUBLISHING).distinct()
        async with self._engine.connect() as conn:
            return [int(r[0]) for r in (await conn.execute(stmt)).all()]

    async def mark_stuck(self, owner_id: int, cutoff_iso: str, now_iso: str,
                         error: str) -> list[dict]:
        """`publishing` items claimed before `cutoff` -> `stuck`. The publisher run
        that claimed them is long over; whether the post went out is a question
        only the owner can answer (retry: "re-post" or "it was published")."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id,
                   ContentItem.status == st.PUBLISHING,
                   or_(ContentItem.claimed_at.is_(None),
                       ContentItem.claimed_at < cutoff_iso))
            .values(status=st.STUCK, error=error, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            rows = (await conn.execute(stmt)).mappings().all()
        return [dict(r) for r in rows]

    # --- the owner's theme approval -----------------------------------------------
    async def approve_themes(self, owner_id: int, start_iso: str, end_iso: str,
                             now_iso: str) -> list[dict]:
        """Every `idea` of this owner with `start <= publish_at < end`, still in the
        future, -> `planned`, in one statement (the bulk "approve this week")."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id, ContentItem.status == st.IDEA,
                   ContentItem.publish_at >= start_iso,
                   ContentItem.publish_at < end_iso,
                   ContentItem.publish_at > now_iso)
            .values(status=st.PLANNED, theme_approved_at=now_iso, draft_wakes=0,
                    draft_attempts=0, draft_next_wake_at=None, error=None,
                    updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            rows = _rows(await conn.execute(stmt))
        return sorted(rows, key=lambda r: (r["publish_at"], r["id"]))

    # --- the drafter ----------------------------------------------------------------
    async def claim_draft(self, owner_id: int, now_iso: str,
                          horizon_iso: str) -> tuple[dict | None, dict | None]:
        """`planned -> drafting` for the earliest item inside its drafting window:
        `(claimed, None)`, `(None, the item already drafting)`, or `(None, None)`
        when nothing is due.

        One per owner: a transaction-scoped advisory lock on the owner serialises
        two claimers, so the "nothing is drafting" check and the claim cannot
        interleave. The row itself is picked `FOR UPDATE SKIP LOCKED` and moved by
        a conditional UPDATE, the rule every other state change here follows.
        """
        lock = select(func.pg_advisory_xact_lock(
            func.hashtextextended(f"content_draft:{int(owner_id)}", 0)))
        busy = (select(ContentItem)
                .where(ContentItem.owner_id == owner_id,
                       ContentItem.status == st.DRAFTING)
                .order_by(ContentItem.id).limit(1))
        head = (select(ContentItem.id)
                .where(ContentItem.owner_id == owner_id,
                       _due_for_drafting(now_iso, horizon_iso))
                .order_by(ContentItem.publish_at, ContentItem.id).limit(1)
                .with_for_update(skip_locked=True).scalar_subquery())
        claim = (
            update(ContentItem)
            .where(ContentItem.id == head, ContentItem.status == st.PLANNED)
            .values(status=st.DRAFTING, drafting_since=now_iso, draft_wakes=0,
                    draft_next_wake_at=None, error=None, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            await conn.execute(lock)
            held = (await conn.execute(busy)).mappings().first()
            if held is not None:
                return None, dict(held)
            row = (await conn.execute(claim)).mappings().first()
        return (dict(row) if row else None), None

    @staticmethod
    def _draft_returned(now_iso: str, max_attempts: int, error: str) -> dict:
        """The column values of a drafting run that ended without a submit."""
        last = ContentItem.draft_attempts + 1 >= max_attempts
        return {"status": case((last, st.FAILED), else_=st.PLANNED),
                "error": case((last, error), else_=None),
                "draft_attempts": ContentItem.draft_attempts + 1,
                "drafting_since": None, "draft_wakes": 0, "draft_next_wake_at": None,
                "updated_at": now_iso}

    async def return_draft(self, item_id: int, owner_id: int, now_iso: str,
                           max_attempts: int, error: str) -> dict | None:
        """The drafter's `draft_failed`: `drafting -> planned` with one attempt
        counted, or `failed` with `error` on the `max_attempts`-th. Its wakes start
        over, due now: the next idle drafter takes it again."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == item_id, ContentItem.owner_id == owner_id,
                   ContentItem.status == st.DRAFTING)
            .values(**self._draft_returned(now_iso, max_attempts, error))
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def drafting_owners(self) -> list[int]:
        stmt = select(ContentItem.owner_id).where(
            ContentItem.status == st.DRAFTING).distinct()
        async with self._engine.connect() as conn:
            return [int(r[0]) for r in (await conn.execute(stmt)).all()]

    async def sweep_drafting(self, owner_id: int, cutoff_iso: str, now_iso: str,
                             max_attempts: int, error: str) -> list[dict]:
        """`drafting` items claimed before `cutoff` (the drafter's timeout plus the
        grace) -> `planned`, one attempt counted, or `failed` on the last. The run
        that claimed them is over; a render it left running is not stopped."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id,
                   ContentItem.status == st.DRAFTING,
                   or_(ContentItem.drafting_since.is_(None),
                       ContentItem.drafting_since < cutoff_iso))
            .values(**self._draft_returned(now_iso, max_attempts, error))
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            return _rows(await conn.execute(stmt))

    async def draft_owners(self, now_iso: str, horizon_iso: str) -> list[int]:
        """Owners with at least one `planned` item inside its drafting window."""
        stmt = select(ContentItem.owner_id).where(
            _due_for_drafting(now_iso, horizon_iso)).distinct()
        async with self._engine.connect() as conn:
            return [int(r[0]) for r in (await conn.execute(stmt)).all()]

    async def note_planned(self, owner_id: int, now_iso: str, horizon_iso: str,
                           max_wakes: int, error: str) -> int:
        """Explain why due `planned` items are not being drafted (no drafter, a
        paused one). Written only where it differs; counts nothing."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id,
                   _due_for_drafting(now_iso, horizon_iso),
                   ContentItem.draft_wakes < max_wakes,
                   or_(ContentItem.error.is_(None), ContentItem.error != error))
            .values(error=error, updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount or 0

    async def clear_planned_notes(self, owner_id: int, now_iso: str,
                                  max_wakes: int) -> int:
        """The drafter is usable again: drop `note_planned`'s explanations. An item
        that used every wake keeps its error — only the owner's retry clears it."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id, ContentItem.status == st.PLANNED,
                   ContentItem.draft_wakes < max_wakes,
                   ContentItem.error.is_not(None))
            .values(error=None, updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount or 0

    async def postpone_draft_wakes(self, owner_id: int, now_iso: str,
                                   horizon_iso: str, after_wake: list[str],
                                   max_wakes: int) -> int:
        """The drafter is busy: push every due drafter wake forward by the item's
        current backoff step WITHOUT counting it — a drafter drafting another item
        is not ignoring this one."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id,
                   _due_for_drafting(now_iso, horizon_iso),
                   ContentItem.draft_wakes < max_wakes, _draft_wake_due(now_iso))
            .values(draft_next_wake_at=_after_wake([now_iso, *after_wake],
                                                   ContentItem.draft_wakes),
                    updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount or 0

    async def take_draft_wake(self, owner_id: int, now_iso: str, horizon_iso: str,
                              after_wake: list[str], max_wakes: int) -> int | None:
        """The drafter wake compare-and-set, on the ONE item the woken run will
        claim — the earliest due `planned` item with wakes left — and only when its
        wake is due. Returns its id, or None: another tick got there first, or its
        wake is not due yet, and the caller must NOT wake the drafter.

        One item, not every due one: the drafter drafts one item per run, and a
        wake that run could not have spent on an item must not count against it.
        """
        head = (select(ContentItem.id)
                .where(ContentItem.owner_id == owner_id,
                       _due_for_drafting(now_iso, horizon_iso),
                       ContentItem.draft_wakes < max_wakes)
                .order_by(ContentItem.publish_at, ContentItem.id).limit(1)
                .with_for_update().scalar_subquery())
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == head, ContentItem.status == st.PLANNED,
                   ContentItem.draft_wakes < max_wakes, _draft_wake_due(now_iso))
            .values(draft_wakes=ContentItem.draft_wakes + 1,
                    draft_next_wake_at=_after_wake(after_wake, ContentItem.draft_wakes),
                    error=None, updated_at=now_iso)
            .returning(ContentItem.id)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).first()
        return int(row[0]) if row else None

    async def release_draft_wake(self, item_id: int, now_iso: str) -> None:
        """Undo `take_draft_wake` for a wake that did not happen."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == item_id, ContentItem.status == st.PLANNED,
                   ContentItem.draft_wakes > 0)
            .values(draft_wakes=ContentItem.draft_wakes - 1,
                    draft_next_wake_at=now_iso, updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)

    async def exhaust_draft_wakes(self, owner_id: int, now_iso: str, horizon_iso: str,
                                  max_wakes: int, error: str) -> list[dict]:
        """Due `planned` items that used every drafter wake and waited out the last
        backoff: they STAY `planned`, with `error` (needs attention), and the tick
        wakes nothing for them until the owner's "Retry drafting"."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.owner_id == owner_id,
                   _due_for_drafting(now_iso, horizon_iso),
                   ContentItem.draft_wakes >= max_wakes, _draft_wake_due(now_iso),
                   ContentItem.error.is_(None))
            .values(error=error, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            return _rows(await conn.execute(stmt))

    async def redraft(self, item_id: int, owner_id: int, comment: str,
                      max_redrafts: int, now_iso: str) -> dict | None:
        """The owner's reply to a live card: `review -> planned` with the comment
        and `redrafts + 1`, the card `superseded` — only while the card is still
        `sent` and the redraft budget holds, in one statement."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == item_id, ContentItem.owner_id == owner_id,
                   ContentItem.status == st.REVIEW,
                   ContentItem.card_state == cd.CARD_SENT,
                   ContentItem.redrafts < max_redrafts)
            .values(status=st.PLANNED, draft_comment=comment,
                    redrafts=ContentItem.redrafts + 1, card_state=cd.CARD_SUPERSEDED,
                    draft_wakes=0, draft_next_wake_at=None, drafting_since=None,
                    error=None, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    # --- the approval card outbox -------------------------------------------------
    async def card_by_message(self, owner_id: int, chat_id: int,
                              message_id: int) -> dict | None:
        """The item whose card is exactly this Telegram message, or None."""
        stmt = select(ContentItem).where(ContentItem.owner_id == owner_id,
                                         ContentItem.card_chat_id == chat_id,
                                         ContentItem.card_message_id == message_id)
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def owns_items(self, owner_id: int) -> bool:
        stmt = select(ContentItem.id).where(ContentItem.owner_id == owner_id).limit(1)
        async with self._engine.connect() as conn:
            return (await conn.execute(stmt)).first() is not None

    async def recover_cards(self, stale_iso: str, now_iso: str) -> int:
        """`sending` cards claimed before `stale` -> `pending`: the worker that
        took them died. Counts nothing — nobody knows whether Telegram got it."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.card_state == cd.CARD_SENDING,
                   or_(ContentItem.card_claimed_at.is_(None),
                       ContentItem.card_claimed_at < stale_iso))
            .values(card_state=cd.CARD_PENDING, card_claimed_at=None,
                    updated_at=now_iso)
        )
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).rowcount or 0

    async def claim_card(self, now_iso: str) -> dict | None:
        """ONE pending card of an item still in `review` -> `sending`: the outbox's
        compare-and-set. N ticks pick different rows (`SKIP LOCKED`) or none."""
        head = (select(ContentItem.id)
                .where(ContentItem.card_state == cd.CARD_PENDING,
                       ContentItem.status == st.REVIEW)
                .order_by(ContentItem.publish_at.asc().nulls_last(), ContentItem.id)
                .limit(1).with_for_update(skip_locked=True).scalar_subquery())
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == head, ContentItem.card_state == cd.CARD_PENDING)
            .values(card_state=cd.CARD_SENDING, card_claimed_at=now_iso,
                    updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def card_sent(self, item_id: int, revision: str, chat_id: int,
                        message_id: int, now_iso: str) -> dict | None:
        """`sending -> sent` with where the card landed — unless the content moved
        meanwhile (`superseded`, or another revision): then None, and the card
        just sent is stale."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == item_id, ContentItem.card_state == cd.CARD_SENDING,
                   ContentItem.card_revision == revision)
            .values(card_state=cd.CARD_SENT, card_chat_id=chat_id,
                    card_message_id=message_id, card_claimed_at=None,
                    updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def card_unsent(self, item_id: int, now_iso: str, *, error: str | None,
                          max_attempts: int) -> dict | None:
        """A send that did not happen. `error=None`: nothing was tried (another
        tick is creating the topic) -> back to `pending`, nothing counted.
        Otherwise one attempt counts: `pending` again, or `failed` with the reason
        in the item's `error` on the `max_attempts`-th."""
        values: dict[str, Any] = {"card_claimed_at": None, "updated_at": now_iso}
        if error is None:
            values["card_state"] = cd.CARD_PENDING
        else:
            last = ContentItem.card_attempts + 1 >= max_attempts
            values.update(card_attempts=ContentItem.card_attempts + 1,
                          card_state=case((last, cd.CARD_FAILED),
                                          else_=cd.CARD_PENDING),
                          error=case((last, error), else_=ContentItem.error))
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == item_id, ContentItem.card_state == cd.CARD_SENDING)
            .values(**values)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def approve_card(self, item_id: int, owner_id: int, revision: str,
                           media: list[dict], now_iso: str) -> dict | None:
        """The Telegram approval, in ONE statement: `review -> scheduled` with the
        freshly taken hashes, only while the card is still `sent` with exactly
        this revision. A second tap, an edit meanwhile or another card's button
        matches nothing."""
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == item_id, ContentItem.owner_id == owner_id,
                   ContentItem.status == st.REVIEW,
                   ContentItem.card_state == cd.CARD_SENT,
                   ContentItem.card_revision == revision)
            .values(status=st.SCHEDULED, media=media, approved_at=now_iso,
                    approved_by=owner_id, error=None, attempts=0, next_wake_at=None,
                    claimed_at=None, updated_at=now_iso)
            .returning(ContentItem)
        )
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).mappings().first()
        return dict(row) if row else None

    async def supersede_missed(self, now_iso: str) -> list[dict]:
        """Cards of posts whose time came before anybody approved them (`planned`,
        `drafting` or `review`) -> `superseded`. Deploy-wide; each row comes back
        with `previous_card_state`, so a `sent` card can be told."""
        old = (select(ContentItem.id.label("old_id"),
                      ContentItem.card_state.label(PREVIOUS_CARD))
               .where(ContentItem.status.in_((st.PLANNED, st.DRAFTING, st.REVIEW)),
                      ContentItem.approved_at.is_(None),
                      ContentItem.publish_at <= now_iso,
                      ContentItem.card_state.in_(cd.CARD_LIVE))
               .with_for_update(skip_locked=True).subquery("old"))
        stmt = (
            update(ContentItem)
            .where(ContentItem.id == old.c.old_id,
                   ContentItem.card_state.in_(cd.CARD_LIVE))
            .values(card_state=cd.CARD_SUPERSEDED, updated_at=now_iso)
            .returning(ContentItem, old.c[PREVIOUS_CARD])
        )
        async with self._engine.begin() as conn:
            return _rows(await conn.execute(stmt))

    # --- meta: the card topic ------------------------------------------------------
    async def meta_swap(self, key: str, expected: str | None,
                        value: str | None) -> bool:
        """Compare-and-set one `meta` row: from `expected` (None = absent) to
        `value` (None = delete it). True only for the caller that changed it."""
        if expected is None:
            if value is None:
                return False
            stmt = (pg_insert(Meta).values(key=key, value=value)
                    .on_conflict_do_nothing(index_elements=[Meta.key])
                    .returning(Meta.key))
        elif value is None:
            stmt = (delete(Meta).where(Meta.key == key, Meta.value == expected)
                    .returning(Meta.key))
        else:
            stmt = (update(Meta).where(Meta.key == key, Meta.value == expected)
                    .values(value=value).returning(Meta.key))
        async with self._engine.begin() as conn:
            return (await conn.execute(stmt)).first() is not None

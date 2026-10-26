"""The `turns` table against a real PostgreSQL.

These are not CRUD tests. Every case below is one of the claims Wave 3b makes
about running turns in more than one process, and each one is only true because
of a specific piece of SQL: the unique partial index (not a check-then-insert),
`queued`-only claiming, the `worker_id` guard on every terminal write, and the
single-statement `request_cancel`. A fake store cannot check any of them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from claude_hermes.modules.turns.domain.exceptions import LaneBusy
from claude_hermes.modules.turns.infrastructure.turn_repository import TurnRepository

LANE = "web:1:x"
T0 = datetime(2026, 7, 12, 10, 0, tzinfo=timezone.utc)


def iso(offset_seconds: float = 0.0) -> str:
    return (T0 + timedelta(seconds=offset_seconds)).isoformat()


@pytest.fixture
async def store(test_database):
    engine = create_async_engine(test_database)
    try:
        yield TurnRepository(engine)
    finally:
        await engine.dispose()


async def create(store, turn_id="t1", lane=LANE, *, lease=300.0, text="hi",
                 attachment_ids=()):
    await store.create(turn_id, lane, "web", 1, text, list(attachment_ids),
                       iso(), iso(lease))
    return turn_id


# --- the index is the lock --------------------------------------------------


async def test_the_unique_index_is_what_refuses_the_second_turn(store):
    """No SELECT decides this: the second INSERT raises and the repository
    translates it. A check-then-insert would let two instances both pass the
    check on the same millisecond."""
    await create(store, "t1")
    with pytest.raises(LaneBusy):
        await create(store, "t2")
    # Another lane is untouched — the index is partial per session_key.
    await create(store, "t3", lane="web:1:y")


async def test_a_finished_turn_frees_the_lane_for_the_next_one(store):
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(61))
    assert await store.finish("t1", "w1", "ok", reply="done", now_iso=iso(2)) is True
    await create(store, "t2")                  # no LaneBusy: t1 is terminal
    assert (await store.active(LANE))["id"] == "t2"


# --- claim ------------------------------------------------------------------


async def test_claim_takes_a_queued_turn_once(store):
    await create(store, "t1")
    row = await store.claim("t1", "w1", iso(1), iso(61))
    assert row["status"] == "running" and row["worker_id"] == "w1"
    assert row["started_at"] == iso(1) and row["lease_until"] == iso(61)
    assert await store.claim("t1", "w2", iso(2), iso(62)) is None


async def test_claim_never_steals_a_running_row_whose_lease_lapsed(store):
    """A `running` row with an expired lease belongs to the reaper, never to a
    second execution: re-running an SDK loop that may have half-completed doubles
    its side effects and its cost."""
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(2))          # a lease already in the past
    assert (await store.expired(iso(600)))[0]["id"] == "t1"
    assert await store.claim("t1", "w2", iso(600), iso(660)) is None


async def test_a_turn_cancelled_before_pickup_is_not_claimable(store):
    await create(store, "t1")
    await store.request_cancel(LANE, iso(1))
    assert await store.claim("t1", "w1", iso(2), iso(62)) is None


# --- heartbeat and finish ---------------------------------------------------


async def test_heartbeat_extends_the_lease_and_reports_the_cancel_flag(store):
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(61))
    assert await store.heartbeat("t1", "w1", iso(81)) == {"cancel_requested_at": None}
    assert (await store.get("t1"))["lease_until"] == iso(81)
    await store.request_cancel(LANE, iso(30))
    assert await store.heartbeat("t1", "w1", iso(101)) == {"cancel_requested_at": iso(30)}


async def test_heartbeat_and_finish_refuse_a_foreign_worker(store):
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(61))
    assert await store.heartbeat("t1", "w2", iso(999)) is None
    assert await store.finish("t1", "w2", "ok", reply="not yours", now_iso=iso(2)) is False
    assert (await store.get("t1"))["status"] == "running"


async def test_finish_cannot_resurrect_a_reaped_turn(store):
    """The residual window this closes: the reaper won, and then the real worker
    finished. Its write is refused and returns False, which is what the caller
    logs — a completed turn recorded as `lost` must be visible somewhere."""
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(2))
    assert await store.mark_lost("t1", iso(600)) is True
    assert await store.finish("t1", "w1", "ok", reply="too late", now_iso=iso(601)) is False
    row = await store.get("t1")
    assert row["status"] == "lost" and row["reply"] is None
    assert row["error"] == "worker lost: lease expired"


# --- cancel -----------------------------------------------------------------


async def test_request_cancel_finishes_a_queued_turn_outright(store):
    """One statement with a CASE: a queued turn never ran, so cancelling it is
    also finishing it — and the lane is free again immediately."""
    await create(store, "t1")
    assert await store.request_cancel(LANE, iso(5)) == {"id": "t1", "status": "cancelled"}
    row = await store.get("t1")
    assert row["finished_at"] == iso(5) and row["cancel_requested_at"] == iso(5)
    assert await store.active(LANE) is None
    await create(store, "t2")                  # the lane is free


async def test_request_cancel_only_flags_a_running_turn(store):
    """A running turn is the worker's to end: the flag is what the heartbeat and
    the control nudge read, and the worker writes the terminal state itself."""
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(61))
    assert await store.request_cancel(LANE, iso(5)) == {"id": "t1", "status": "running"}
    row = await store.get("t1")
    assert row["status"] == "running" and row["cancel_requested_at"] == iso(5)
    assert row["finished_at"] is None


async def test_request_cancel_reports_nothing_on_an_idle_lane(store):
    assert await store.request_cancel(LANE, iso(5)) is None


async def test_request_cancel_can_name_the_turn_it_means(store):
    """Cancelling by lane can hit a newer turn if the previous one finished
    between the click and the UPDATE. The optional id is how a client that knows
    which turn it was looking at closes that race."""
    await create(store, "t1")
    assert await store.request_cancel(LANE, iso(5), turn_id="somebody-elses") is None
    assert (await store.get("t1"))["cancel_requested_at"] is None
    assert await store.request_cancel(LANE, iso(6), turn_id="t1") == {
        "id": "t1", "status": "cancelled"}


# --- reads ------------------------------------------------------------------


async def test_active_ignores_the_lease(store):
    """`is_busy` must never disagree with `create`. The index defines busy and the
    reaper is the only thing that frees a lane, so a lapsed lease here would
    report a lane free that the next INSERT refuses."""
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(2))
    assert (await store.active(LANE))["id"] == "t1"


async def test_active_lanes_answers_a_whole_page_in_one_query(store):
    await create(store, "t1", lane="web:1:a")
    await create(store, "t2", lane="web:1:b")
    await store.claim("t2", "w1", iso(1), iso(61))
    await create(store, "t3", lane="web:1:c")
    await store.finish("t3", "w9", "ok", now_iso=iso(2))       # never claimed: still queued
    keys = ["web:1:a", "web:1:b", "web:1:c", "web:1:nothing"]
    assert await store.active_lanes(keys) == {"web:1:a", "web:1:b", "web:1:c"}
    assert await store.active_lanes([]) == set()


async def test_expired_lists_queued_and_running_rows_past_their_lease(store):
    """A queued row is in here too: a queue message that vanished must not hold a
    lane forever."""
    await create(store, "queued-stale", lane="web:1:a", lease=10)
    await create(store, "queued-fresh", lane="web:1:b", lease=1000)
    await create(store, "running-stale", lane="web:1:c")
    await store.claim("running-stale", "w1", iso(1), iso(20))
    await create(store, "done", lane="web:1:d", lease=5)
    await store.claim("done", "w1", iso(1), iso(5))
    await store.finish("done", "w1", "ok", now_iso=iso(2))
    assert [r["id"] for r in await store.expired(iso(100))] == ["queued-stale",
                                                               "running-stale"]
    # Bounded, and ordered by lease so the longest-dead is reaped first.
    assert [r["id"] for r in await store.expired(iso(100), limit=1)] == ["queued-stale"]


async def test_mark_lost_is_refused_once_the_row_is_terminal(store):
    await create(store, "t1")
    await store.claim("t1", "w1", iso(1), iso(2))
    await store.finish("t1", "w1", "ok", reply="done", now_iso=iso(3))
    assert await store.mark_lost("t1", iso(600)) is False
    assert (await store.get("t1"))["status"] == "ok"


async def test_prune_drops_only_finished_rows_older_than_the_window(store):
    await create(store, "old", lane="web:1:a")
    await store.claim("old", "w1", iso(1), iso(61))
    await store.finish("old", "w1", "ok", reply="r", now_iso=iso(10))
    await create(store, "recent", lane="web:1:b")
    await store.claim("recent", "w1", iso(1), iso(61))
    await store.finish("recent", "w1", "ok", reply="r", now_iso=iso(900))
    await create(store, "live", lane="web:1:c")

    assert await store.prune(iso(100)) == 1
    assert await store.get("old") is None
    assert await store.get("recent") is not None
    assert await store.get("live") is not None
    assert await store.prune(iso(100)) == 0


async def test_the_row_carries_what_the_worker_needs_to_run_the_turn(store):
    await create(store, "t1", text="summarise this", attachment_ids=["f1", "f2"])
    row = await store.get("t1")
    assert row["text"] == "summarise this" and row["attachment_ids"] == ["f1", "f2"]
    assert row["channel"] == "web" and row["user_id"] == 1
    assert row["status"] == "queued" and row["created_at"] == iso()
    assert await store.get("no-such-turn") is None


async def test_fail_queued_ends_an_undispatched_turn_and_never_a_running_one(store):
    """The one terminal write with no `worker_id`, because a row whose dispatch
    raised was never handed to a worker. Narrowed to `queued` so it cannot end a
    turn that is already executing somewhere."""
    await create(store, "t1")
    assert await store.fail_queued("t1", "dispatch failed: no broker", iso(3)) is True
    row = await store.get("t1")
    assert row["status"] == "error" and row["worker_id"] is None
    assert row["error"] == "dispatch failed: no broker"
    # The lane is free again, which is the point of writing it at all.
    await create(store, "t2")

    # A claimed row is out of reach: the worker owns the ending from then on.
    await store.claim("t2", "w1", iso(4), iso(64))
    assert await store.fail_queued("t2", "too late", iso(5)) is False
    assert (await store.get("t2"))["status"] == "running"

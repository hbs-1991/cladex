"""`GET /api/routines/calendar`: a user's routines on a timeline.

Future fires of active cron/interval/once routines in the window (from
`max(from, now)`), anything firing hourly or more often once in `dense`, nothing
for `manual`, the recorded runs in the window — and all of it only for the asking
user. The service tests pin the arithmetic with a fixed clock; the route tests pin
the wire shape, the scope and `bad_range`.
"""
from datetime import datetime, timedelta, timezone

import pytest
from conftest import ensure_users

from claude_hermes.core.exceptions import ApplicationException
from claude_hermes.modules.scheduler.application.service import SchedulerService
from claude_hermes.modules.scheduler.domain.schedule import MAX_FIRES_PER_ROUTINE
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository

from tests.web_helpers import login, make_client

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
END = datetime(2026, 10, 4, tzinfo=timezone.utc)


async def add(jobs, name, kind, value, next_run_at, owner_id=1):
    return await jobs.create(name, kind, value, "p", owner_id, f"tg:private:{owner_id}",
                             next_run_at, owner_id=owner_id)


async def seeded(engine):
    await ensure_users(engine, 2, owner=1)
    jobs = JobRepository(engine)
    ids = {
        "cron": await add(jobs, "brief", "cron", "0 8 * * *",
                          "2026-09-29T08:00:00+00:00"),
        # Anchored on its own next_run_at, not on the window: 03:17, 09:17, …
        "interval": await add(jobs, "sweep", "interval", "21600",
                              "2026-10-01T03:17:00+00:00"),
        "once": await add(jobs, "launch", "once", "2026-10-02T12:00:00+00:00",
                          "2026-10-02T12:00:00+00:00"),
        "manual": await add(jobs, "publisher", "manual", "", None),
        "dense_interval": await add(jobs, "poll", "interval", "600",
                                    "2026-09-28T12:10:00+00:00"),
        "dense_cron": await add(jobs, "watch", "cron", "*/15 * * * *",
                                "2026-09-28T12:15:00+00:00"),
        "paused": await add(jobs, "paused", "cron", "0 9 * * *",
                            "2026-09-29T09:00:00+00:00"),
        "foreign": await add(jobs, "theirs", "cron", "0 7 * * *",
                             "2026-09-29T07:00:00+00:00", owner_id=2),
    }
    await jobs.set_state(ids["paused"], "paused")
    return jobs, ids


async def test_each_kind_expands_as_it_fires(engine):
    jobs, ids = await seeded(engine)
    cal = await SchedulerService(jobs).calendar(1, START, END, NOW)

    by = {}
    for f in cal["fires"]:
        by.setdefault(f["routine_id"], []).append(f["at"])
    assert by[ids["cron"]] == [f"2026-10-0{d}T08:00:00+00:00" for d in (1, 2, 3)]
    assert by[ids["interval"]] == [
        f"2026-10-0{d}T{h}:17:00+00:00" for d in (1, 2, 3) for h in ("03", "09", "15", "21")]
    assert by[ids["once"]] == ["2026-10-02T12:00:00+00:00"]
    # manual never fires on its own, a paused routine does not fire, a dense one is
    # a marker, and somebody else's routine is not here at all.
    assert set(by) == {ids["cron"], ids["interval"], ids["once"]}
    assert [f["at"] for f in cal["fires"]] == sorted(f["at"] for f in cal["fires"])
    assert {f["name"] for f in cal["fires"]} == {"brief", "sweep", "launch"}

    assert sorted((d["routine_id"], d["name"], d["every_seconds"])
                  for d in cal["dense"]) == [
        (ids["dense_interval"], "poll", 600), (ids["dense_cron"], "watch", 900)]


async def test_fires_start_at_now_when_the_window_began_earlier(engine):
    jobs, ids = await seeded(engine)
    now = datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)
    cal = await SchedulerService(jobs).calendar(1, START, END, now)
    cron = [f["at"] for f in cal["fires"] if f["routine_id"] == ids["cron"]]
    assert cron == ["2026-10-03T08:00:00+00:00"]
    sweep = [f["at"] for f in cal["fires"] if f["routine_id"] == ids["interval"]]
    assert sweep[0] == "2026-10-02T15:17:00+00:00"


async def test_an_hourly_routine_is_dense(engine):
    # The Tasks form's "every 1h" preset: 24 chips a day is the noise `dense` exists
    # to avoid, so exactly hourly is dense too.
    await ensure_users(engine, owner=1)
    jobs = JobRepository(engine)
    hourly = await add(jobs, "hourly", "interval", "3600", "2026-10-01T00:00:00+00:00")
    cal = await SchedulerService(jobs).calendar(
        1, START, START + timedelta(days=62), NOW)
    assert cal["fires"] == []
    assert cal["dense"] == [{"routine_id": hourly, "name": "hourly", "every_seconds": 3600}]


async def test_a_two_hourly_routine_fills_the_widest_window(engine):
    # Everything expanded fires at most every two hours, so the widest window is
    # 62 * 12 = 744 fires — under the cap, so a month view is never cut short.
    await ensure_users(engine, owner=1)
    jobs = JobRepository(engine)
    await add(jobs, "two-hourly", "interval", "7200", "2026-10-01T00:00:00+00:00")
    cal = await SchedulerService(jobs).calendar(
        1, START, START + timedelta(days=62), NOW)
    assert 740 <= len(cal["fires"]) < MAX_FIRES_PER_ROUTINE


async def test_runs_in_the_window_only_and_only_the_askers(engine):
    jobs, ids = await seeded(engine)
    await jobs.record_run(ids["cron"], "2026-09-30T23:59:59+00:00", "ok")   # before
    await jobs.record_run(ids["cron"], "2026-10-01T08:00:05+00:00", "ok")
    await jobs.record_run(ids["manual"], "2026-10-02T05:01:00+00:00", "error")
    await jobs.record_run(ids["cron"], "2026-10-04T00:00:00+00:00", "ok")   # `to` is open
    await jobs.record_run(ids["foreign"], "2026-10-02T07:00:00+00:00", "ok")
    service = SchedulerService(jobs)

    cal = await service.calendar(1, START, END, NOW)
    assert cal["runs"] == [
        {"routine_id": ids["cron"], "name": "brief",
         "at": "2026-10-01T08:00:05+00:00", "status": "ok"},
        {"routine_id": ids["manual"], "name": "publisher",
         "at": "2026-10-02T05:01:00+00:00", "status": "error"},
    ]
    theirs = await service.calendar(2, START, END, NOW)
    assert [r["routine_id"] for r in theirs["runs"]] == [ids["foreign"]]
    assert [f["routine_id"] for f in theirs["fires"]] == [ids["foreign"]] * 3


async def test_a_bad_window_is_refused(engine):
    service = SchedulerService(JobRepository(engine))
    for start, end in ((START, START + timedelta(days=62, seconds=1)),
                       (START, START), (END, START)):
        with pytest.raises(ApplicationException) as e:
            await service.calendar(1, start, end, NOW)
        assert (e.value.status, e.value.code) == (400, "bad_range")


# --- the route ----------------------------------------------------------------

def window(days: int = 7) -> dict:
    """A window starting now: the route reads the real clock."""
    start = datetime.now(timezone.utc).replace(microsecond=0)
    return {"from": start.isoformat(), "to": (start + timedelta(days=days)).isoformat()}


async def test_the_route_answers_the_wire_shape_scoped_to_the_asker(tmp_path, engine):
    client, _ = await make_client(tmp_path, owner=1, allowed=[2], engine=engine)
    jobs = JobRepository(engine)
    soon = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        minute=0, second=0, microsecond=0)
    mine = await add(jobs, "brief", "once", soon.isoformat(), soon.isoformat())
    await add(jobs, "publisher", "manual", "", None)
    theirs = await add(jobs, "theirs", "once", soon.isoformat(), soon.isoformat(),
                       owner_id=2)
    await jobs.record_run(theirs, datetime.now(timezone.utc).isoformat(), "ok")

    login(client, 1)
    r = await client.get("/api/routines/calendar", params=window())
    assert r.status_code == 200, r.text
    assert r.json() == {
        "fires": [{"routine_id": mine, "name": "brief", "at": soon.isoformat()}],
        "dense": [], "runs": []}

    # A non-owner may read, and reads only their own.
    login(client, 2)
    r = await client.get("/api/routines/calendar", params={
        "from": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "to": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()})
    assert r.status_code == 200
    body = r.json()
    assert [f["routine_id"] for f in body["fires"]] == [theirs]
    assert [(x["routine_id"], x["status"]) for x in body["runs"]] == [(theirs, "ok")]


async def test_the_route_refuses_a_bad_range(tmp_path):
    client, _ = await make_client(tmp_path)
    login(client, 1)
    start = datetime.now(timezone.utc)
    bad = [
        {"from": start.isoformat(), "to": (start + timedelta(days=63)).isoformat()},
        {"from": start.isoformat(), "to": start.isoformat()},
        {"from": "tomorrow", "to": start.isoformat()},
        {"to": start.isoformat()},
    ]
    for params in bad:
        r = await client.get("/api/routines/calendar", params=params)
        assert r.status_code == 400, (params, r.text)
        assert r.json()["error"]["code"] == "bad_range"
    # `Z` and a non-UTC offset are both fine; the window is the same instant.
    r = await client.get("/api/routines/calendar", params={
        "from": "2026-10-01T05:00:00+05:00", "to": "2026-10-08T00:00:00Z"})
    assert r.status_code == 200 and set(r.json()) == {"fires", "dense", "runs"}


async def test_the_route_needs_a_login(tmp_path):
    client, _ = await make_client(tmp_path)
    r = await client.get("/api/routines/calendar", params=window())
    assert r.status_code == 401

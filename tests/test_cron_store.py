"""`JobRepository` over the engine fixture: the three pins `tests/modules/
scheduler/test_scheduler_repository.py` and `test_scheduler_claiming.py` do not
carry — `due` + `reschedule`, a paused row staying out of `due`, and the run
history plus the `update` whitelist on the REAL table (the service test pins the
whitelist on a dict fake only)."""
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository

NOW = "2026-07-12T10:00:00+00:00"
LATER = "2026-07-12T11:00:00+00:00"


async def test_due_and_reschedule(engine):
    js = JobRepository(engine)
    jid = await js.create("j", "interval", "3600", "p", 1, "tg:private:1", NOW)
    assert [j["id"] for j in await js.due(LATER)] == [jid]
    await js.reschedule(jid, "2026-07-13T10:00:00+00:00")
    assert await js.due(LATER) == []
    await js.reschedule(jid, None)          # one-shot spent
    assert (await js.get(jid))["state"] == "done"


async def test_pause_excludes_from_due(engine):
    js = JobRepository(engine)
    jid = await js.create("j", "interval", "60", "p", 1, "tg:private:1", NOW)
    await js.set_state(jid, "paused")
    assert await js.due(LATER) == []
    assert await js.claim_due(LATER) == []


async def test_record_run_keeps_history_and_update_is_whitelisted(engine):
    js = JobRepository(engine)
    jid = await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                          "2026-07-12T10:00:00+00:00")
    await js.record_run(jid, "2026-07-12T10:00:00+00:00", "ok")
    await js.record_run(jid, "2026-07-12T11:00:00+00:00", "error")
    assert await js.runs(jid) == [{"ts": "2026-07-12T11:00:00+00:00", "status": "error"},
                                  {"ts": "2026-07-12T10:00:00+00:00", "status": "ok"}]
    await js.update(jid, name="k", id=99, state="hacked")
    job = await js.get(jid)
    assert job["name"] == "k" and job["state"] == "active"


async def test_the_pair_round_trips_and_clears(engine):
    """`jobs.model/effort` (0008): NULL means "inherit" — `cron_model`, then
    `model` — and the form clears a pair by writing NULL back, so `update` has to
    carry both columns as well as set them."""
    js = JobRepository(engine)
    jid = await js.create("j", "interval", "3600", "p", 1, "tg:private:1", NOW,
                          model="claude-opus-5-5", effort="high")
    job = await js.get(jid)
    assert (job["model"], job["effort"]) == ("claude-opus-5-5", "high")

    await js.update(jid, model=None, effort=None)
    job = await js.get(jid)
    assert (job["model"], job["effort"]) == (None, None)

    plain = await js.create("p", "interval", "3600", "p", 1, "tg:private:1", NOW)
    assert (await js.get(plain))["model"] is None

"""JobRepository over the engine fixture — and claims work."""
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository

NOW = "2026-07-12T10:00:00+00:00"
LATER = "2026-07-12T11:00:00+00:00"


async def test_repository_is_built_from_an_engine(engine):
    repo = JobRepository(engine)
    jid = await repo.create("digest", "cron", "0 8 * * *", "p", 42,
                            "tg:private:42", LATER)
    assert (await repo.get(jid))["name"] == "digest"


async def test_claim_due_is_ordered_bounded_and_state_aware(engine):
    repo = JobRepository(engine)
    late = await repo.create("late", "interval", "60", "p", 1, "tg:private:1", NOW)
    early = await repo.create("early", "interval", "60", "p", 1, "tg:private:1",
                              "2026-07-12T09:00:00+00:00")
    done = await repo.create("done", "interval", "60", "p", 1, "tg:private:1", NOW)
    await repo.set_state(done, "done")

    assert [j["id"] for j in await repo.claim_due(LATER)] == [early, late]
    # the lease each claim leaves is released before the next one, or these would
    # be three assertions about the same claim
    for jid in (early, late):
        await repo.clear_lease(jid)
    assert [j["id"] for j in await repo.claim_due(LATER, limit=1)] == [early]
    await repo.clear_lease(early)
    # a claimed row is a full job row, not an id: the tick reads kind/value/fire_at
    claimed = (await repo.claim_due(LATER))[0]
    assert claimed["kind"] == "interval" and claimed["next_run_at"]
    assert claimed["fire_at"] == claimed["next_run_at"]


async def test_by_thread_prefers_the_newest_reuse_of_a_topic(engine):
    repo = JobRepository(engine)
    await repo.create("old", "interval", "60", "p", 42, "tg:private:42", NOW,
                      deliver_thread_id=9)
    newer = await repo.create("new", "interval", "60", "p", 42, "tg:private:42", NOW,
                              deliver_thread_id=9)
    assert (await repo.by_thread(42, 9))["id"] == newer
    assert await repo.by_thread(42, 404) is None


# --- Wave 4a: jobs.owner_id --------------------------------------------------


async def make_owned(engine, *users: int):
    """A repository plus the `users` rows `jobs.owner_id` references."""
    from conftest import ensure_users
    await ensure_users(engine, *users)
    return JobRepository(engine)


async def test_create_records_the_owner_and_the_reads_scope_by_it(engine):
    repo = await make_owned(engine, 1, 2)
    mine = await repo.create("mine", "interval", "60", "p", 1, "tg:private:1", LATER,
                             owner_id=1)
    theirs = await repo.create("theirs", "interval", "60", "p", 2, "tg:private:2",
                               LATER, owner_id=2)

    assert (await repo.get(mine))["owner_id"] == 1
    assert [j["id"] for j in await repo.list_jobs(1)] == [mine]
    assert [j["id"] for j in await repo.list_jobs(2)] == [theirs]
    # No owner is "every routine" — the ticker and the executor, who run a claimed
    # fire rather than answering a request.
    assert [j["id"] for j in await repo.list_jobs()] == [mine, theirs]


async def test_get_of_somebody_elses_routine_is_indistinguishable_from_absent(engine):
    """A 404 and not a 403: the id must not confirm that a routine exists."""
    repo = await make_owned(engine, 1, 2)
    jid = await repo.create("mine", "interval", "60", "p", 1, "tg:private:1", LATER,
                            owner_id=1)
    assert await repo.get(jid, 2) is None
    assert await repo.get(jid, 1) is not None
    assert await repo.get(jid) is not None          # the executor still reads it


async def test_a_routine_with_no_owner_belongs_to_nobody_not_to_everybody(engine):
    repo = await make_owned(engine, 1)
    jid = await repo.create("legacy", "interval", "60", "p", 1, "tg:private:1", LATER)
    assert (await repo.get(jid))["owner_id"] is None
    assert await repo.get(jid, 1) is None
    assert await repo.list_jobs(1) == []


async def test_claiming_is_unaffected_by_the_owner_column(engine):
    """The tick is deploy-wide on purpose: one clock claims every due fire, whoever
    owns it, and the executor resolves the owner's workspace afterwards."""
    repo = await make_owned(engine, 1, 2)
    a = await repo.create("a", "interval", "60", "p", 1, "tg:private:1", NOW, owner_id=1)
    b = await repo.create("b", "interval", "60", "p", 2, "tg:private:2", NOW, owner_id=2)
    assert sorted(j["id"] for j in await repo.claim_due(LATER)) == sorted([a, b])

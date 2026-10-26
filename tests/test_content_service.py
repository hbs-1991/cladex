"""ContentService over a real PostgreSQL: the three actors' rules and the tick.

The publisher routine is a fake with the scheduler's `wake_if_idle` semantics (a
compare-and-set on `next_run_at`/`lease_until`) so these tests pin the content
side of the contract without depending on the scheduler module. The media resolver
is the real `WorkspaceMedia` over a temporary `agent_home`.

The helpers at the top are imported by `test_content_api.py` and
`test_content_tool.py`.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from claude_hermes.core.exceptions import ApplicationException, NotFound
from claude_hermes.core.meta import MetaStore
from claude_hermes.core.workspace import Workspace
from claude_hermes.modules.content.application.service import (
    PUBLISHER_KEY,
    ContentService,
)
from claude_hermes.modules.content.domain import status as st
from claude_hermes.modules.content.domain.exceptions import (
    ContentConflict,
    ContentRejected,
)
from claude_hermes.modules.content.infrastructure.media import WorkspaceMedia
from claude_hermes.modules.content.infrastructure.repository import ContentRepository

from conftest import ensure_users  # noqa: E402  (pytest puts tests/ on sys.path)

OWNER = 1
OTHER = 2
JOB = 7
NOW = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)


class FakeWorkspaces:
    """`WorkspaceResolver.for_user` by shape: the owner's root IS home."""

    def __init__(self, home):
        self.home = home

    async def for_user(self, user_id):
        ws = Workspace.for_user(self.home, "" if user_id == OWNER else f"users/{user_id}")
        ws.agent_home.mkdir(parents=True, exist_ok=True)
        return ws


class FakePublisher:
    """The `Publisher` port over an in-memory job table, with `wake_if_idle`'s
    one-statement semantics: active, manual, no pending wake, no live lease."""

    def __init__(self):
        self.jobs: dict[int, dict] = {}
        self.wakes: list[tuple[int, str]] = []
        self.calls = 0

    def add(self, job_id=JOB, owner_id=OWNER, **kw):
        self.jobs[job_id] = {"owner_id": owner_id, "name": "Content publisher",
                             "kind": "manual", "state": "active", "next_run_at": None,
                             "lease_until": None, "timeout_seconds": 1800, **kw}
        return self.jobs[job_id]

    async def describe(self, job_id, owner_id):
        job = self.jobs.get(job_id)
        if job is None or job["owner_id"] != owner_id:
            return None
        return {k: job[k] for k in ("name", "kind", "state", "next_run_at",
                                    "lease_until", "timeout_seconds")}

    async def wake_if_idle(self, job_id, now_iso):
        self.calls += 1
        job = self.jobs.get(job_id)
        if (job is None or job["state"] != "active" or job["kind"] != "manual"
                or job["next_run_at"] is not None
                or (job["lease_until"] is not None and job["lease_until"] >= now_iso)):
            return False
        job["next_run_at"] = now_iso
        self.wakes.append((job_id, now_iso))
        return True

    def run_finished(self, job_id=JOB):
        """The woken run came and went: the wake is consumed, the lease released."""
        self.jobs[job_id].update(next_run_at=None, lease_until=None)


def make_service(engine, home, publisher=None, *, tz="UTC", timeout=180.0):
    return ContentService(
        ContentRepository(engine), WorkspaceMedia(FakeWorkspaces(home)),
        publisher or FakePublisher(), MetaStore(engine),
        timezone=tz, publisher_timeout_seconds=timeout)


def write_media(home, rel="content/img/x.png", data=b"\x89PNG-one"):
    path = home / "agent_home" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


async def force(service, item_id, owner_id=OWNER, **values):
    """Put an item in a state no public path would reach in one step."""
    return await service._store.update(item_id, owner_id, values)


@pytest.fixture
async def users(engine):
    await ensure_users(engine, OTHER, owner=OWNER)


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
async def svc(engine, tmp_path, users, publisher):
    return make_service(engine, tmp_path, publisher)


async def scheduled_item(svc, home, publish_at=NOW, *, media=True):
    """An approved item due at `publish_at`. A `post`, whose media are optional, so
    the same helper serves the with- and the without-media cases."""
    body = {"channel": "instagram", "format": "post", "title": "t", "body": "b",
            "publish_at": publish_at.isoformat()}
    if media:
        body["media"] = [write_media(home)]
    item = await svc.create(OWNER, body)
    return await svc.approve(OWNER, item["id"], now=publish_at - timedelta(hours=1))


async def set_publisher(svc, publisher, **kw):
    publisher.add(**kw)
    await svc.set_publisher(OWNER, JOB)


async def refused(coro, code):
    with pytest.raises(ApplicationException) as e:
        await coro
    assert e.value.code == code, e.value.message
    return e.value


# --- create / validation ---------------------------------------------------------


async def test_create_validates_each_field_with_its_own_code(svc):
    await refused(svc.create(OWNER, {"channel": "myspace"}), "bad_channel")
    await refused(svc.create(OWNER, {"title": "no channel"}), "bad_channel")
    await refused(svc.create(OWNER, {"channel": "x", "media": [".claude/a.png"]}),
                  "bad_media")
    await refused(svc.create(OWNER, {"channel": "x", "media": ["notes.md"]}), "bad_media")
    await refused(svc.create(OWNER, {"channel": "x", "media": ["../../etc/x.png"]}),
                  "bad_media")
    await refused(svc.create(OWNER, {"channel": "x", "publish_at": "soon"}), "bad_time")
    await refused(svc.create(OWNER, {"channel": "x", "status": "scheduled"}), "bad_status")


async def test_create_normalizes_and_defaults(svc, tmp_path):
    item = await svc.create(OWNER, {
        "channel": " Instagram ", "media": ["content/img/x.png", "content\\img\\x.png"],
        "publish_at": "2026-10-01T10:00+05:00"})
    assert item["channel"] == "instagram"
    assert item["status"] == "draft" and item["created_via"] == "web"
    assert item["media"] == [{"path": "content/img/x.png", "sha256": None}]
    assert item["publish_at"] == "2026-10-01T05:00:00+00:00"


async def test_an_absolute_path_inside_agent_home_is_stored_relative(svc, tmp_path):
    inside = tmp_path / "agent_home" / "content" / "a.jpg"
    item = await svc.create(OWNER, {"channel": "x", "media": [str(inside)]})
    assert item["media"][0]["path"] == "content/a.jpg"
    outside = tmp_path / "elsewhere.jpg"
    await refused(svc.create(OWNER, {"channel": "x", "media": [str(outside)]}), "bad_media")


async def test_a_naive_time_is_read_in_the_deploy_zone(engine, tmp_path, users):
    svc = make_service(engine, tmp_path, tz="Asia/Tashkent")
    item = await svc.create(OWNER, {"channel": "x", "publish_at": "2026-10-01T10:00"})
    assert item["publish_at"] == "2026-10-01T05:00:00+00:00"


async def test_items_are_the_owners_own(svc):
    item = await svc.create(OTHER, {"channel": "x"})
    with pytest.raises(NotFound):
        await svc.require(item["id"], OWNER)
    with pytest.raises(NotFound):
        await svc.edit(OWNER, item["id"], {"title": "mine now"})


# --- the agent -----------------------------------------------------------------------


@pytest.mark.parametrize("current", st.STATUSES)
async def test_the_agent_status_table_row_by_row(svc, tmp_path, current):
    """Each row of the table through the service: from `current`, `review` is
    reachable exactly when the agent may leave `current`, `scheduled` never."""
    item = await svc.create(OWNER, {"channel": "x", "publish_at": NOW.isoformat()})
    await force(svc, item["id"], status=current)
    may_leave = current in st.AGENT_FROM
    await refused(svc.agent_set_status(OWNER, item["id"], "scheduled"), "bad_status")
    await refused(svc.agent_update(OWNER, item["id"], {"status": "scheduled"}),
                  "locked" if current in st.AGENT_LOCKED else "bad_status")
    if may_leave:
        moved = await svc.agent_set_status(OWNER, item["id"], "review")
        assert moved["status"] == "review"
    else:
        await refused(svc.agent_set_status(OWNER, item["id"], "review"), "bad_status")
        assert (await svc.require(item["id"], OWNER))["status"] == current


@pytest.mark.parametrize("locked", sorted(st.AGENT_LOCKED))
async def test_the_agent_never_edits_an_item_the_publisher_holds(svc, locked):
    item = await svc.create(OWNER, {"channel": "x", "body": "before"})
    await force(svc, item["id"], status=locked)
    await refused(svc.agent_update(OWNER, item["id"], {"body": "after"}), "locked")
    assert (await svc.require(item["id"], OWNER))["body"] == "before"


async def test_an_agent_content_edit_of_a_scheduled_item_lands_in_review(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    assert item["media"][0]["sha256"]
    same = await svc.agent_update(OWNER, item["id"], {"title": "new title"})
    assert same["status"] == "scheduled" and same["approved_at"] is not None
    moved = await svc.agent_update(OWNER, item["id"], {"body": "rewritten"})
    assert moved["status"] == "review"
    assert moved["approved_at"] is None and moved["approved_by"] is None
    assert moved["media"][0]["sha256"] is None


async def test_leaving_scheduled_clears_the_approval(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    moved = await svc.agent_set_status(OWNER, item["id"], "draft")
    assert moved["status"] == "draft" and moved["approved_at"] is None


async def test_the_agent_cannot_create_past_review(svc):
    await refused(svc.agent_create(OWNER, {"channel": "x", "status": "scheduled"},
                                   origin="chat", thread_id="web:1:a"), "bad_status")
    item = await svc.agent_create(OWNER, {"channel": "x"}, origin="cron:9", thread_id=None)
    assert item["created_via"] == "cron:9"


async def test_only_the_configured_publisher_is_the_publisher(svc, publisher):
    assert not await svc.is_publisher(f"cron:{JOB}")
    await set_publisher(svc, publisher)
    assert await svc.is_publisher(f"cron:{JOB}")
    assert not await svc.is_publisher("chat")
    assert not await svc.is_publisher("cron:8")


# --- the owner: approve, edit, retry, delete ------------------------------------------


async def test_approve_needs_a_time_and_every_file(svc, tmp_path):
    item = await svc.create(OWNER, {"channel": "x", "body": "text",
                                    "media": ["content/missing.png"]})
    await refused(svc.approve(OWNER, item["id"]), "no_publish_at")
    await svc.edit(OWNER, item["id"], {"publish_at": NOW.isoformat()})
    err = await refused(svc.approve(OWNER, item["id"]), "media_missing")
    assert "content/missing.png" in err.message
    write_media(tmp_path, "content/missing.png")
    approved = await svc.approve(OWNER, item["id"])
    assert approved["status"] == "scheduled" and approved["approved_by"] == OWNER
    assert len(approved["media"][0]["sha256"]) == 64
    await refused(svc.approve(OWNER, item["id"]), "not_approvable")


async def test_a_web_edit_keeps_a_scheduled_item_scheduled_and_rehashes(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    old = item["media"][0]["sha256"]
    rel = write_media(tmp_path, "content/img/y.png", b"other bytes")
    edited = await svc.edit(OWNER, item["id"], {"media": [rel], "body": "new"})
    assert edited["status"] == "scheduled"
    assert edited["media"][0]["sha256"] not in (None, old)
    await refused(svc.edit(OWNER, item["id"], {"media": ["content/gone.png"]}),
                  "media_missing")
    await refused(svc.edit(OWNER, item["id"], {"publish_at": None}), "no_publish_at")


@pytest.mark.parametrize("locked", sorted(st.WEB_LOCKED))
async def test_a_web_edit_of_a_held_item_is_a_conflict(svc, locked):
    item = await svc.create(OWNER, {"channel": "x"})
    await force(svc, item["id"], status=locked)
    with pytest.raises(ContentConflict) as e:
        await svc.edit(OWNER, item["id"], {"title": "x"})
    assert e.value.code == "locked" and e.value.status == 409


async def test_unapprove_only_from_scheduled(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    back = await svc.unapprove(OWNER, item["id"])
    assert back["status"] == "review" and back["approved_at"] is None
    await refused(svc.unapprove(OWNER, item["id"]), "not_approved")


async def test_retry_is_the_owners_two_way_choice(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    await refused(svc.retry(OWNER, item["id"], "repost"), "not_retryable")
    await force(svc, item["id"], status="stuck", error="x", attempts=3)
    await refused(svc.retry(OWNER, item["id"], "again"), "bad_mode")
    await refused(svc.retry(OWNER, item["id"], "published", "not a url"), "bad_url")
    again = await svc.retry(OWNER, item["id"], "repost", now=NOW)
    assert again["status"] == "queued" and again["attempts"] == 0 and again["error"] is None
    await force(svc, item["id"], status="publishing")
    done = await svc.retry(OWNER, item["id"], "published", "https://instagram.com/p/1")
    assert done["status"] == "published" and done["published_url"].endswith("/p/1")


async def test_a_published_item_cannot_be_deleted(svc):
    item = await svc.create(OWNER, {"channel": "x"})
    await force(svc, item["id"], status="published")
    with pytest.raises(ContentConflict) as e:
        await svc.delete(OWNER, item["id"])
    assert e.value.code == "published_item"
    other = await svc.create(OWNER, {"channel": "x"})
    await svc.delete(OWNER, other["id"])
    with pytest.raises(NotFound):
        await svc.require(other["id"], OWNER)


async def test_the_publisher_must_be_the_owners_manual_routine(svc, publisher):
    publisher.add(job_id=3, kind="cron")
    publisher.add(job_id=4, owner_id=OTHER)
    for job in (3, 4, 99, "x", None):
        await refused(svc.set_publisher(OWNER, job), "bad_publisher")
    publisher.add()
    assert await svc.set_publisher(OWNER, JOB) == {
        "job_id": JOB, "name": "Content publisher", "state": "active"}
    del publisher.jobs[JOB]
    assert await svc.publisher(OWNER) == {"job_id": JOB, "name": None, "state": None}


# --- the publisher: claim --------------------------------------------------------------


async def test_claim_moves_queued_to_publishing_once(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    await refused(svc.claim(OWNER, item["id"]), "not_queued")
    await force(svc, item["id"], status="queued")
    first, second = await asyncio.gather(
        svc.claim(OWNER, item["id"], now=NOW), svc.claim(OWNER, item["id"], now=NOW),
        return_exceptions=True)
    wins = [r for r in (first, second) if isinstance(r, dict)]
    losses = [r for r in (first, second) if isinstance(r, ContentRejected)]
    assert len(wins) == 1 and len(losses) == 1 and losses[0].code == "not_queued"
    assert wins[0]["status"] == "publishing"
    assert wins[0]["claimed_at"] == "2026-10-01T05:00:00+00:00"


async def test_a_file_changed_after_approval_sends_the_item_back_to_review(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    await force(svc, item["id"], status="queued")
    write_media(tmp_path, data=b"swapped after approval")
    err = await refused(svc.claim(OWNER, item["id"]), "media_changed")
    assert "content/img/x.png" in err.message
    back = await svc.require(item["id"], OWNER)
    assert back["status"] == "review" and back["approved_at"] is None
    assert "content/img/x.png" in back["error"]
    assert back["media"][0]["sha256"] is None


async def test_published_and_failed_only_from_publishing(svc, tmp_path):
    item = await scheduled_item(svc, tmp_path)
    await refused(svc.mark_published(OWNER, item["id"], "https://x.com/1"), "not_publishing")
    await force(svc, item["id"], status="publishing")
    await refused(svc.mark_published(OWNER, item["id"], ""), "bad_url")
    await refused(svc.mark_failed(OWNER, item["id"], " "), "no_error")
    failed = await svc.mark_failed(OWNER, item["id"], "API said no")
    assert failed["status"] == "failed" and failed["error"] == "API said no"


# --- the tick --------------------------------------------------------------------------


async def test_the_tick_queues_a_due_item_and_wakes_the_publisher_once(svc, tmp_path,
                                                                       publisher):
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, tmp_path, NOW)
    future = await scheduled_item(svc, tmp_path, NOW + timedelta(hours=1), media=False)
    await svc.tick(NOW - timedelta(minutes=1))
    assert (await svc.require(item["id"], OWNER))["status"] == "scheduled"
    assert publisher.wakes == []

    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "queued" and row["attempts"] == 1
    assert publisher.wakes == [(JOB, NOW.isoformat())]
    assert (await svc.require(future["id"], OWNER))["status"] == "scheduled"
    # A second tick while the wake is pending wakes nothing.
    await svc.tick(NOW + timedelta(seconds=30))
    assert len(publisher.wakes) == 1


async def test_an_item_more_than_two_hours_late_fails_instead(svc, tmp_path, publisher):
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, tmp_path, NOW)
    await svc.tick(NOW + timedelta(hours=2, minutes=1))
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "failed" and "window" in row["error"]
    assert publisher.wakes == []


async def test_a_busy_publisher_pauses_the_backoff(svc, tmp_path, publisher):
    await set_publisher(svc, publisher, lease_until=(NOW + timedelta(minutes=20)).isoformat())
    item = await scheduled_item(svc, tmp_path, NOW)
    for minute in (0, 5, 15):
        await svc.tick(NOW + timedelta(minutes=minute))
        row = await svc.require(item["id"], OWNER)
        assert row["status"] == "queued" and row["attempts"] == 0
    assert publisher.calls == 0
    # A pending wake counts as busy too.
    publisher.jobs[JOB].update(lease_until=None, next_run_at=NOW.isoformat())
    await svc.tick(NOW + timedelta(minutes=16))
    assert (await svc.require(item["id"], OWNER))["attempts"] == 0
    publisher.run_finished()
    await svc.tick(NOW + timedelta(minutes=17))
    assert (await svc.require(item["id"], OWNER))["attempts"] == 1
    assert len(publisher.wakes) == 1


async def test_an_ignored_item_is_rewoken_5_15_60_minutes_idle_then_fails(svc, tmp_path,
                                                                        publisher):
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, tmp_path, NOW)
    t = NOW
    await svc.tick(t)                                   # wake 1
    publisher.run_finished()
    for wait, wakes in ((5, 2), (15, 3)):
        await svc.tick(t + timedelta(minutes=wait) - timedelta(seconds=1))
        assert len(publisher.wakes) == wakes - 1        # not yet
        t += timedelta(minutes=wait)
        await svc.tick(t)
        assert len(publisher.wakes) == wakes
        assert (await svc.require(item["id"], OWNER))["attempts"] == wakes
        publisher.run_finished()
    await svc.tick(t + timedelta(minutes=59))
    assert (await svc.require(item["id"], OWNER))["status"] == "queued"
    await svc.tick(t + timedelta(minutes=60))
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "failed" and "never picked" in row["error"]
    assert len(publisher.wakes) == 3


async def test_two_concurrent_ticks_wake_the_publisher_once(svc, tmp_path, publisher):
    await set_publisher(svc, publisher)
    for _ in range(3):
        await scheduled_item(svc, tmp_path, NOW)
    await asyncio.gather(svc.tick(NOW), svc.tick(NOW), svc.tick(NOW))
    assert len(publisher.wakes) == 1
    # And the wake was counted once per item, not once per tick.
    items = await svc.due(OWNER)
    assert [i["attempts"] for i in items] == [1, 1, 1]
    assert publisher.calls == 1


async def test_a_missing_publisher_leaves_items_queued_with_a_reason(svc, tmp_path,
                                                                    publisher):
    item = await scheduled_item(svc, tmp_path, NOW)
    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "queued" and "No publisher" in row["error"]
    assert st.needs_attention(row["status"], row["error"])
    await svc._settings.set(PUBLISHER_KEY, str(JOB))       # set, but the job is gone
    await svc.tick(NOW + timedelta(minutes=1))
    assert "no longer exists" in (await svc.require(item["id"], OWNER))["error"]
    assert publisher.calls == 0


async def test_a_paused_publisher_wakes_nothing(svc, tmp_path, publisher):
    await set_publisher(svc, publisher)
    publisher.jobs[JOB]["state"] = "paused"
    item = await scheduled_item(svc, tmp_path, NOW)
    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "queued" and "paused" in row["error"]
    assert row["attempts"] == 0 and publisher.calls == 0
    publisher.jobs[JOB]["state"] = "active"
    await svc.tick(NOW + timedelta(minutes=1))
    row = await svc.require(item["id"], OWNER)
    assert row["attempts"] == 1 and row["error"] is None


async def test_a_publishing_item_past_timeout_and_grace_is_stuck(svc, tmp_path, publisher):
    await set_publisher(svc, publisher, timeout_seconds=600)
    item = await scheduled_item(svc, tmp_path, NOW)
    await force(svc, item["id"], status="queued")
    await svc.claim(OWNER, item["id"], now=NOW)
    await svc.tick(NOW + timedelta(minutes=19))
    assert (await svc.require(item["id"], OWNER))["status"] == "publishing"
    await svc.tick(NOW + timedelta(minutes=20, seconds=1))
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "stuck" and st.needs_attention(row["status"], row["error"])


async def test_the_stuck_timeout_falls_back_without_a_publisher(engine, tmp_path, users):
    svc = make_service(engine, tmp_path, timeout=60)
    item = await svc.create(OWNER, {"channel": "x"})
    await force(svc, item["id"], status="publishing", claimed_at=NOW.isoformat())
    await svc.tick(NOW + timedelta(minutes=10))
    assert (await svc.require(item["id"], OWNER))["status"] == "publishing"
    await svc.tick(NOW + timedelta(minutes=11, seconds=1))
    assert (await svc.require(item["id"], OWNER))["status"] == "stuck"


async def test_the_tick_never_touches_a_publishing_item_otherwise(svc, tmp_path, publisher):
    """No automatic re-post: nothing but `stuck` ever happens to `publishing`."""
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, tmp_path, NOW)
    await force(svc, item["id"], status="publishing", claimed_at=NOW.isoformat())
    await svc.tick(NOW + timedelta(minutes=1))
    assert (await svc.require(item["id"], OWNER))["status"] == "publishing"
    assert publisher.calls == 0


async def test_calendar_range_and_backlog(svc, tmp_path):
    inside = await svc.create(OWNER, {"channel": "x", "publish_at": NOW.isoformat()})
    await svc.create(OWNER, {"channel": "x",
                             "publish_at": (NOW + timedelta(days=3)).isoformat()})
    backlog = await svc.create(OWNER, {"channel": "x"})
    skipped = await svc.create(OWNER, {"channel": "x"})
    await force(svc, skipped["id"], status="skipped")
    await svc.create(OTHER, {"channel": "x", "publish_at": NOW.isoformat()})
    lo, hi = svc.parse_range(NOW.isoformat(), (NOW + timedelta(days=1)).isoformat())
    plan = await svc.calendar(OWNER, lo, hi)
    assert [i["id"] for i in plan["items"]] == [inside["id"]]
    assert [i["id"] for i in plan["backlog"]] == [backlog["id"]]
    for start, end in ((NOW, NOW), (NOW, NOW + timedelta(days=63)), (None, NOW)):
        with pytest.raises(ContentRejected) as e:
            svc.parse_range(start and start.isoformat(), end and end.isoformat())
        assert e.value.code == "bad_range"

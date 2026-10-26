"""The `content_plan` tool: its declaration, its actions and refusals, the timezone
of its answers, and the in-process server a deploy owner's routine run gets.

Over a real `ContentService` and database; the publisher routine is the fake from
`test_content_service`.
"""
from datetime import datetime, timedelta, timezone

import pytest

from claude_hermes.modules.content.presentation.agent_tool import (
    TOOL_NAME,
    TOOL_SCHEMA,
    ContentPlanTool,
)
from claude_hermes.modules.content.presentation.routine_server import ContentRoutineTools

from tests.test_content_service import (
    JOB,
    NOW,
    OWNER,
    FakePublisher,
    force,
    make_service,
    scheduled_item,
    set_publisher,
    users,  # noqa: F401  (fixture)
    write_media,
)


@pytest.fixture
def publisher():
    return FakePublisher()


@pytest.fixture
async def env(engine, tmp_path, users, publisher):  # noqa: F811
    svc = make_service(engine, tmp_path, publisher, tz="Asia/Tashkent")
    return ContentPlanTool(svc), svc, publisher, tmp_path


def text(result: dict) -> str:
    return result["content"][0]["text"]


async def call(tool, origin="chat", owner_id=OWNER, thread_id="web:1:abc", **args):
    return await tool.handle(args, owner_id=owner_id, origin=origin, thread_id=thread_id)


def test_the_declaration_is_the_contract():
    tool = ContentPlanTool(object())
    assert tool.name == TOOL_NAME == "content_plan"
    assert tool.schema == {
        "action": str, "id": int, "channel": str, "format": str, "title": str,
        "body": str, "media": str, "publish_at": str, "status": str, "group_key": str,
        "source_path": str, "url": str, "error": str, "from": str, "to": str}
    assert TOOL_SCHEMA is tool.schema
    for phrase in ("owner", "review", "timezone", "publisher routine", "planned",
                   "claim_draft", "draft_failed", "the drafter routine only"):
        assert phrase in tool.description


async def test_a_lane_without_an_owner_is_refused(env):
    tool, *_ = env
    for owner in (0, -1):
        r = await call(tool, owner_id=owner, action="list")
        assert r.get("is_error") is True and "Refused" in text(r)


async def test_create_list_get_in_the_configured_zone(env):
    tool, svc, *_ = env
    r = await call(tool, action="create", channel="linkedin", format="post",
                   title="Launch", body="We shipped.", publish_at="2026-10-01T10:00",
                   status="", media="", group_key="", source_path="")
    assert "is_error" not in r, text(r)
    assert "2026-10-01T10:00+05:00" in text(r)
    (item,) = await svc.list_items(OWNER)
    assert item["publish_at"] == "2026-10-01T05:00:00+00:00"
    assert item["created_via"] == "chat" and item["thread_id"] == "web:1:abc"
    listed = text(await call(tool, action="list"))
    assert "Asia/Tashkent" in listed and f"#{item['id']} [draft] linkedin/post" in listed
    got = text(await call(tool, action="get", id=item["id"]))
    assert "We shipped." in got and "+05:00" in got


async def test_update_leaves_empty_fields_alone_and_none_backlogs(env):
    tool, svc, *_ = env
    item = await svc.create(OWNER, {"channel": "x", "title": "keep",
                                    "publish_at": NOW.isoformat()})
    r = await call(tool, action="update", id=item["id"], title="", body="new body",
                   publish_at="")
    assert "is_error" not in r, text(r)
    row = await svc.require(item["id"], OWNER)
    assert row["title"] == "keep" and row["body"] == "new body"
    assert row["publish_at"] == NOW.isoformat()
    await call(tool, action="update", id=item["id"], publish_at="none")
    assert (await svc.require(item["id"], OWNER))["publish_at"] is None


async def test_the_agent_never_schedules(env):
    tool, svc, *_ = env
    item = await svc.create(OWNER, {"channel": "x"})
    for args in ({"action": "set_status", "status": "scheduled"},
                 {"action": "update", "status": "queued"},
                 {"action": "create", "channel": "x", "status": "scheduled"}):
        r = await call(tool, id=item["id"], **args)
        assert r.get("is_error") is True, args
    assert (await svc.require(item["id"], OWNER))["status"] == "draft"
    r = await call(tool, action="set_status", id=item["id"], status="review")
    assert "[review]" in text(r)


async def test_an_agent_edit_of_scheduled_content_goes_back_to_review(env):
    tool, svc, _pub, home = env
    item = await scheduled_item(svc, home)
    r = await call(tool, action="update", id=item["id"], body="changed")
    assert "[review]" in text(r)
    assert (await svc.require(item["id"], OWNER))["approved_at"] is None


@pytest.mark.parametrize("action", ["due", "claim", "published", "failed"])
async def test_publisher_actions_are_refused_outside_the_publisher(env, action):
    tool, svc, publisher, home = env
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, home)
    await force(svc, item["id"], status="queued")
    for origin in ("chat", "cron:8"):
        r = await call(tool, origin=origin, action=action, id=item["id"],
                       url="https://x.com/1", error="e")
        assert r.get("is_error") is True and "publisher routine" in text(r)
    assert (await svc.require(item["id"], OWNER))["status"] == "queued"


async def test_the_publisher_run_does_due_claim_published(env):
    tool, svc, publisher, home = env
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, home)
    origin = f"cron:{JOB}"
    assert text(await call(tool, origin=origin, action="due")) == "Nothing is due."
    await force(svc, item["id"], status="queued")
    due = text(await call(tool, origin=origin, action="due"))
    assert f"#{item['id']} [queued]" in due and "content/img/x.png" in due
    claimed = await call(tool, origin=origin, action="claim", id=item["id"])
    assert "is_error" not in claimed and "Claimed" in text(claimed)
    again = await call(tool, origin=origin, action="claim", id=item["id"])
    assert again.get("is_error") is True and "Do not publish" in text(again)
    bad = await call(tool, origin=origin, action="published", id=item["id"], url="")
    assert bad.get("is_error") is True
    done = await call(tool, origin=origin, action="published", id=item["id"],
                      url="https://www.instagram.com/reel/abc")
    assert "published" in text(done)
    assert (await svc.require(item["id"], OWNER))["status"] == "published"


async def test_a_claim_with_a_changed_file_is_refused(env):
    tool, svc, publisher, home = env
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, home)
    await force(svc, item["id"], status="queued")
    write_media(home, data=b"different")
    r = await call(tool, origin=f"cron:{JOB}", action="claim", id=item["id"])
    assert r.get("is_error") is True and "content/img/x.png" in text(r)
    assert (await svc.require(item["id"], OWNER))["status"] == "review"


async def test_a_publisher_failure_is_recorded(env):
    tool, svc, publisher, home = env
    await set_publisher(svc, publisher)
    item = await scheduled_item(svc, home)
    await force(svc, item["id"], status="publishing", claimed_at=NOW.isoformat())
    r = await call(tool, origin=f"cron:{JOB}", action="failed", id=item["id"],
                   error="no publisher for x/post")
    assert "failed" in text(r)
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "failed" and row["error"] == "no publisher for x/post"


async def test_list_filters_by_range_and_status(env):
    tool, svc, *_ = env
    await svc.create(OWNER, {"channel": "x", "title": "early",
                             "publish_at": NOW.isoformat()})
    await svc.create(OWNER, {"channel": "telegram", "title": "later",
                             "publish_at": (NOW + timedelta(days=5)).isoformat()})
    done = await svc.create(OWNER, {"channel": "x", "title": "done"})
    await force(svc, done["id"], status="published")
    everything = text(await call(tool, action="list"))
    assert "early" in everything and "later" in everything and "done" not in everything
    ranged = text(await call(tool, action="list", **{"from": "2026-10-03T00:00",
                                                    "to": "2026-10-10T00:00"}))
    assert "later" in ranged and "early" not in ranged
    assert "done" in text(await call(tool, action="list", status="published"))
    assert "later" in text(await call(tool, action="list", channel="telegram"))
    assert "early" not in text(await call(tool, action="list", channel="telegram"))


# --- the drafter's actions -------------------------------------------------------------

DRAFTER = 8


async def _with_drafter(svc, publisher):
    await set_publisher(svc, publisher)
    publisher.add(job_id=DRAFTER, name="Content drafter")
    await svc.set_drafter(OWNER, DRAFTER)


@pytest.mark.parametrize("action", ["claim_draft", "submit", "draft_failed"])
async def test_drafter_actions_are_refused_outside_the_drafter(env, action):
    tool, svc, publisher, _home = env
    await _with_drafter(svc, publisher)
    for origin in ("chat", f"cron:{JOB}", "cron:99"):
        r = await call(tool, origin=origin, action=action, id=1, body="b", error="e")
        assert r.get("is_error") is True and "drafter routine" in text(r), origin


async def test_the_drafter_run_claims_and_submits_one_item(env):
    tool, svc, publisher, home = env
    await _with_drafter(svc, publisher)
    origin = f"cron:{DRAFTER}"
    assert text(await call(tool, origin=origin, action="claim_draft")) \
        == "Nothing is due for drafting."
    # The tool runs on the real clock: a time two hours from now is inside the lead.
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    item = await svc.create(OWNER, {"channel": "x", "format": "post", "title": "Theme",
                                    "status": "idea", "publish_at": soon.isoformat()})
    await svc.approve_theme(OWNER, item["id"])
    await force(svc, item["id"], draft_comment="make it shorter")
    claimed = text(await call(tool, origin=origin, action="claim_draft"))
    assert "Claimed for drafting" in claimed and f"content/drafts/{item['id']}/1/" in claimed
    assert "make it shorter" in claimed and "[drafting]" in claimed
    busy = await call(tool, origin=origin, action="claim_draft")
    assert busy.get("is_error") is True and "one item per run" in text(busy)
    # The agent in chat cannot touch the item the drafter holds.
    locked = await call(tool, action="update", id=item["id"], body="mine")
    assert locked.get("is_error") is True and "drafter" in text(locked)
    rel = write_media(home, f"content/drafts/{item['id']}/1/cover.png")
    done = await call(tool, origin=origin, action="submit", id=item["id"],
                      body="The finished post.", media=rel, title="")
    assert "is_error" not in done, text(done)
    assert "[review]" in text(done) and "do not publish" in text(done)
    row = await svc.require(item["id"], OWNER)
    assert row["title"] == "Theme" and row["card_state"] == "pending"
    assert row["draft_comment"] is None
    late = await call(tool, origin=origin, action="draft_failed", id=item["id"], error="x")
    assert late.get("is_error") is True and "not drafting" in text(late)


async def test_a_failed_draft_is_reported(env):
    tool, svc, publisher, _home = env
    await _with_drafter(svc, publisher)
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    item = await svc.create(OWNER, {"channel": "x", "title": "Theme", "status": "idea",
                                    "publish_at": soon.isoformat()})
    await svc.approve_theme(OWNER, item["id"])
    origin = f"cron:{DRAFTER}"
    await call(tool, origin=origin, action="claim_draft")
    r = await call(tool, origin=origin, action="draft_failed", id=item["id"],
                   error="HyperFrames is not installed")
    assert "is_error" not in r and "[planned]" in text(r)
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "planned" and row["draft_attempts"] == 1


async def test_a_claim_shows_the_ideas_other_channels_and_their_media(env):
    tool, svc, publisher, home = env
    await _with_drafter(svc, publisher)
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    reel = await svc.create(OWNER, {"channel": "instagram", "format": "reel", "title": "Trap",
                                    "status": "idea", "group_key": "traps-02",
                                    "publish_at": soon.isoformat()})
    short = await svc.create(OWNER, {"channel": "youtube", "format": "short", "title": "Trap",
                                     "status": "idea", "group_key": "traps-02",
                                     "publish_at": (soon + timedelta(minutes=5)).isoformat()})
    await svc.create(OWNER, {"channel": "x", "title": "Unrelated", "status": "idea",
                             "group_key": "other", "publish_at": soon.isoformat()})
    for i in (reel, short):
        await svc.approve_theme(OWNER, i["id"])
    origin = f"cron:{DRAFTER}"
    first = text(await call(tool, origin=origin, action="claim_draft"))
    assert f"#{short['id']} [planned] youtube/short" in first and "Unrelated" not in first
    rel = write_media(home, f"content/drafts/{reel['id']}/1/reel.mp4")
    await call(tool, origin=origin, action="submit", id=reel["id"], body="IG caption", media=rel)
    second = text(await call(tool, origin=origin, action="claim_draft"))
    assert f"#{reel['id']} [review] instagram/reel" in second and f"  - {rel}" in second
    solo = await svc.create(OWNER, {"channel": "x", "title": "Solo", "status": "idea",
                                    "publish_at": soon.isoformat()})
    assert await svc.group_siblings(OWNER, solo) == []


# --- the routine server ---------------------------------------------------------------


def test_the_routine_server_is_the_deploy_owners_alone():
    tools = ContentRoutineTools(ContentPlanTool(object()))
    assert tools.for_owner(OWNER, is_deploy_owner=False, job_id=3) == ({}, [])
    assert tools.for_owner(0, is_deploy_owner=True, job_id=3) == ({}, [])
    servers, names = tools.for_owner(OWNER, is_deploy_owner=True, job_id=3)
    assert list(servers) == ["hermes"]
    assert names == ["mcp__hermes__content_plan"]


async def test_a_routine_write_records_its_origin(env, monkeypatch):
    """The server's one tool is the same port with `origin=cron:<job_id>` baked in.
    Called through the SDK tool the server was built from, so the wrapper is
    covered, not just the port."""
    from claude_hermes.modules.content.presentation import routine_server

    tool, svc, *_ = env
    built = {}
    real = routine_server.create_sdk_mcp_server

    def capture(name, version="1.0.0", tools=None):
        built.update(name=name, tools=list(tools or []))
        return real(name, version=version, tools=tools)

    monkeypatch.setattr(routine_server, "create_sdk_mcp_server", capture)
    ContentRoutineTools(tool).for_owner(OWNER, is_deploy_owner=True, job_id=11)
    assert built["name"] == "hermes"
    (registered,) = built["tools"]
    assert registered.name == "content_plan"
    assert registered.description == tool.description
    result = await registered.handler({"action": "create", "channel": "x", "title": "r"})
    assert "Created" in text(result)
    (item,) = await svc.list_items(OWNER)
    assert item["created_via"] == "cron:11" and item["thread_id"] is None

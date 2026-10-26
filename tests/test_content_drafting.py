"""The content plan v2 over a real PostgreSQL: theme approval, the drafter routine's
wakes and claims, the approval gate, the Telegram card outbox and the card's
decisions (approve, redraft, the card topic).

The scheduler is `FakePublisher` from `test_content_service` holding BOTH routines
(one adapter, the job id on every call, as in `main.py`); the Telegram side is
`FakeCards`, the `ApprovalCards` port by shape.
"""
import asyncio
from datetime import timedelta

import pytest

from claude_hermes.core.meta import MetaStore
from claude_hermes.modules.content.application.service import (
    DRAFTER_KEY,
    TOPIC_KEY,
    ContentService,
)
from claude_hermes.modules.content.domain import cards as cd
from claude_hermes.modules.content.domain import status as st
from claude_hermes.modules.content.infrastructure.media import WorkspaceMedia
from claude_hermes.modules.content.infrastructure.repository import ContentRepository
from claude_hermes.modules.content.presentation.decisions import (
    STALE_TEXT,
    ContentCardDecisions,
)
from claude_hermes.modules.content.presentation.schemas import to_wire
from claude_hermes.modules.content.wiring import ApprovalCardsSlot

from tests.test_content_service import (
    JOB,
    NOW,
    OTHER,
    OWNER,
    FakePublisher,
    FakeWorkspaces,
    force,
    refused,
    users,  # noqa: F401  (fixture)
    write_media,
)

DRAFTER = 8
# Due for drafting from NOW (lead 24 h), published 20 h later.
AT = NOW + timedelta(hours=20)


class FakeCards:
    """`ApprovalCards`: records every call; `answers` scripts `send` (None = ok)."""

    def __init__(self, topic=500):
        self.sent: list[dict] = []
        self.edits: list[tuple[int, int, str]] = []
        self.topics: list[tuple[int, str]] = []
        self.answers: list = []
        self.topic = topic
        self.next_message = 1000

    async def send(self, card):
        self.sent.append(card)
        await asyncio.sleep(0)
        answer = self.answers.pop(0) if self.answers else None
        if answer is not None:
            return answer
        self.next_message += 1
        return {"message_id": self.next_message, "thread_id": card["thread_id"]}

    async def edit(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))

    async def ensure_topic(self, chat_id, name):
        self.topics.append((chat_id, name))
        await asyncio.sleep(0.05)       # long enough for a concurrent tick to look
        if self.topic is None:
            return None
        self.topic += 1
        return self.topic


@pytest.fixture
def publisher():
    pub = FakePublisher()
    pub.add()
    pub.add(job_id=DRAFTER, name="Content drafter")
    return pub


@pytest.fixture
def cards():
    return FakeCards()


@pytest.fixture
async def svc(engine, tmp_path, users, publisher, cards):  # noqa: F811
    # The card decisions take no `now`: the service's clock is pinned instead.
    service = ContentService(
        ContentRepository(engine), WorkspaceMedia(FakeWorkspaces(tmp_path)), publisher,
        MetaStore(engine), drafter=publisher, cards=ApprovalCardsSlot(cards),
        draft_lead_hours=24, calendar_origin="https://hermes.example/",
        clock=lambda: NOW)
    await service.set_publisher(OWNER, JOB)
    await service.set_drafter(OWNER, DRAFTER)
    return service


async def idea(svc, publish_at=AT, fmt="post", title="Theme", owner=OWNER):
    return await svc.create(owner, {"channel": "instagram", "format": fmt,
                                    "title": title, "status": "idea",
                                    "publish_at": publish_at.isoformat()})


async def planned(svc, publish_at=AT, **kw):
    item = await idea(svc, publish_at, **kw)
    return await svc.approve_theme(OWNER, item["id"], now=NOW)


async def drafted(svc, home, publish_at=AT, *, body="The post."):
    """A planned item claimed and submitted: in review with a pending card."""
    item = await planned(svc, publish_at)
    claimed = await svc.claim_draft(OWNER, now=NOW)
    assert claimed["id"] == item["id"]
    rel = write_media(home, f"content/drafts/{item['id']}/1/a.png", b"img-" + bytes(
        str(item["id"]), "ascii"))
    return await svc.submit(OWNER, item["id"], {"body": body, "media": [rel]}, now=NOW)


async def sent_card(svc, home, publish_at=AT):
    item = await drafted(svc, home, publish_at)
    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert row["card_state"] == "sent", row
    return row


# --- theme approval ------------------------------------------------------------------


async def test_approving_a_theme_plans_it_and_nothing_else(svc):
    item = await idea(svc)
    draft = await svc.create(OWNER, {"channel": "x", "publish_at": AT.isoformat()})
    await refused(svc.approve_theme(OWNER, draft["id"], now=NOW), "not_an_idea")
    backlog = await svc.create(OWNER, {"channel": "x", "status": "idea"})
    await refused(svc.approve_theme(OWNER, backlog["id"], now=NOW), "no_publish_at")
    past = await idea(svc, NOW - timedelta(minutes=1))
    err = await refused(svc.approve_theme(OWNER, past["id"], now=NOW), "no_publish_at")
    assert "passed" in err.message

    row = await svc.approve_theme(OWNER, item["id"], now=NOW)
    assert row["status"] == "planned" and row["theme_approved_at"] == NOW.isoformat()
    assert row["approved_at"] is None                    # content is not approved
    await refused(svc.approve_theme(OWNER, item["id"], now=NOW), "not_an_idea")
    back = await svc.unapprove_theme(OWNER, item["id"])
    assert back["status"] == "idea" and back["theme_approved_at"] is None
    await refused(svc.unapprove_theme(OWNER, item["id"]), "not_planned")


async def test_approving_a_weeks_themes_takes_future_ideas_in_range(svc):
    inside = await idea(svc, NOW + timedelta(days=1))
    later = await idea(svc, NOW + timedelta(days=2))
    past = await idea(svc, NOW - timedelta(hours=1))
    outside = await idea(svc, NOW + timedelta(days=9))
    not_idea = await svc.create(OWNER, {"channel": "x",
                                        "publish_at": (NOW + timedelta(days=1)).isoformat()})
    theirs = await idea(svc, NOW + timedelta(days=1), owner=OTHER)
    items = await svc.approve_themes(OWNER, (NOW - timedelta(days=1)).isoformat(),
                                     (NOW + timedelta(days=6)).isoformat(), now=NOW)
    assert [i["id"] for i in items] == [inside["id"], later["id"]]
    for untouched, status in ((past, "idea"), (outside, "idea"), (not_idea, "draft")):
        assert (await svc.require(untouched["id"], OWNER))["status"] == status
    assert (await svc.require(theirs["id"], OTHER))["status"] == "idea"
    await refused(svc.approve_themes(OWNER, NOW.isoformat(),
                                     (NOW + timedelta(days=63)).isoformat()), "bad_range")


async def test_an_agent_edit_of_a_planned_theme_returns_it_to_idea(svc):
    item = await planned(svc)
    kept = await svc.agent_update(OWNER, item["id"], {"body": "notes for the drafter"})
    assert kept["status"] == "planned" and kept["theme_approved_at"] is not None
    for field, value in (("title", "Another"), ("format", "reel"), ("channel", "x"),
                         ("publish_at", (AT + timedelta(hours=1)).isoformat())):
        item = await svc.approve_theme(OWNER, item["id"], now=NOW) \
            if item["status"] == "idea" else item
        moved = await svc.agent_update(OWNER, item["id"], {field: value})
        assert moved["status"] == "idea", field
        assert moved["theme_approved_at"] is None
        item = moved
    item = await svc.approve_theme(OWNER, item["id"], now=NOW)
    skipped = await svc.agent_set_status(OWNER, item["id"], "skipped")
    assert skipped["status"] == "skipped" and skipped["theme_approved_at"] is None
    await refused(svc.agent_set_status(OWNER, item["id"], "planned"), "bad_status")


async def test_the_owners_edit_keeps_a_planned_item_planned(svc):
    item = await planned(svc)
    edited = await svc.edit(OWNER, item["id"], {"title": "Sharper"})
    assert edited["status"] == "planned" and edited["theme_approved_at"] is not None
    await refused(svc.edit(OWNER, item["id"], {"publish_at": None}), "no_publish_at")
    back = await svc.edit(OWNER, item["id"], {"status": "idea"})
    assert back["status"] == "idea" and back["theme_approved_at"] is None


# --- the two routines ---------------------------------------------------------------


async def test_the_drafter_is_another_manual_routine(svc, publisher):
    assert await svc.drafter(OWNER) == {"job_id": DRAFTER, "name": "Content drafter",
                                        "state": "active"}
    await refused(svc.set_drafter(OWNER, JOB), "bad_drafter")
    await refused(svc.set_publisher(OWNER, DRAFTER), "bad_publisher")
    publisher.add(job_id=9, kind="cron")
    await refused(svc.set_drafter(OWNER, 9), "bad_drafter")
    await refused(svc.set_drafter(OWNER, "x"), "bad_drafter")
    assert await svc.is_drafter(f"cron:{DRAFTER}")
    for origin in ("chat", f"cron:{JOB}", "cron:99"):
        assert not await svc.is_drafter(origin)


# --- the drafter's wakes -------------------------------------------------------------


async def test_the_drafter_is_woken_only_inside_the_lead_window(svc, publisher):
    item = await planned(svc, NOW + timedelta(hours=30))
    await svc.tick(NOW)
    assert publisher.wakes == []
    assert await svc.claim_draft(OWNER, now=NOW) is None      # not due yet
    later = NOW + timedelta(hours=6)
    await svc.tick(later)
    assert publisher.wakes == [(DRAFTER, later.isoformat())]
    assert (await svc.require(item["id"], OWNER))["draft_wakes"] == 1
    claimed = await svc.claim_draft(OWNER, now=later)
    assert claimed["id"] == item["id"] and claimed["status"] == "drafting"
    assert claimed["drafting_since"] == later.isoformat() and claimed["draft_wakes"] == 0


async def test_one_item_per_run_and_a_busy_drafter_costs_nothing(svc, publisher):
    first = await planned(svc, AT)
    second = await planned(svc, AT + timedelta(hours=1))
    third = await planned(svc, AT + timedelta(hours=2))
    await asyncio.gather(svc.tick(NOW), svc.tick(NOW), svc.tick(NOW))
    assert len(publisher.wakes) == 1                            # one wake, once
    # The wake counts on the one item the run will claim, not on every due one.
    assert [(await svc.require(i["id"], OWNER))["draft_wakes"]
            for i in (first, second, third)] == [1, 0, 0]
    claimed = await svc.claim_draft(OWNER, now=NOW)
    assert claimed["id"] == first["id"]
    err = await refused(svc.claim_draft(OWNER, now=NOW), "drafting_busy")
    assert f"#{first['id']}" in err.message
    # The run renders for a long time (within its 30-minute limit): every tick
    # meanwhile is free.
    publisher.jobs[DRAFTER].update(next_run_at=None,
                                   lease_until=(NOW + timedelta(minutes=30)).isoformat())
    for minutes in (5, 20, 29):
        await svc.tick(NOW + timedelta(minutes=minutes))
    assert len(publisher.wakes) == 1
    assert [(await svc.require(i["id"], OWNER))["draft_wakes"]
            for i in (second, third)] == [0, 0]
    await svc.draft_failed(OWNER, first["id"], "render crashed",
                           now=NOW + timedelta(minutes=29))
    publisher.run_finished(DRAFTER)
    t = NOW + timedelta(minutes=31)
    await svc.tick(t)
    assert len(publisher.wakes) == 2
    # The returned item is the earliest again, so the next run takes it first.
    assert (await svc.claim_draft(OWNER, now=t))["id"] == first["id"]


async def test_concurrent_claims_take_one_item(svc):
    for hours in (0, 1, 2):
        await planned(svc, AT + timedelta(hours=hours))
    results = await asyncio.gather(*(svc.claim_draft(OWNER, now=NOW) for _ in range(4)),
                                   return_exceptions=True)
    claimed = [r for r in results if isinstance(r, dict)]
    assert len(claimed) == 1
    assert all(getattr(r, "code", None) == "drafting_busy"
               for r in results if not isinstance(r, dict))
    assert len(await svc.list_items(OWNER, status="drafting")) == 1


async def test_an_ignored_wake_is_retried_5_15_60_then_needs_the_owner(svc, publisher):
    item = await planned(svc)
    t = NOW
    await svc.tick(t)
    publisher.run_finished(DRAFTER)
    for wait, wakes in ((5, 2), (15, 3)):
        await svc.tick(t + timedelta(minutes=wait) - timedelta(seconds=1))
        assert len(publisher.wakes) == wakes - 1
        t += timedelta(minutes=wait)
        await svc.tick(t)
        assert len(publisher.wakes) == wakes
        publisher.run_finished(DRAFTER)
    await svc.tick(t + timedelta(minutes=59))
    assert (await svc.require(item["id"], OWNER))["error"] is None
    await svc.tick(t + timedelta(minutes=60))
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "planned" and "3 times" in row["error"]
    assert to_wire(row, (t + timedelta(minutes=60)).isoformat())["needs_attention"]
    await svc.tick(t + timedelta(minutes=200))
    assert len(publisher.wakes) == 3                           # stopped waking
    retried = await svc.retry_drafting(OWNER, item["id"])
    assert retried["error"] is None and retried["draft_wakes"] == 0
    assert retried["theme_approved_at"] is not None
    await refused(svc.retry_drafting(OWNER, item["id"]), "not_retryable")
    await svc.tick(t + timedelta(minutes=201))
    assert len(publisher.wakes) == 4


async def test_a_missing_or_paused_drafter_is_explained_on_the_due_items(svc, publisher):
    item = await planned(svc)
    publisher.jobs[DRAFTER]["state"] = "paused"
    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert "drafter" in row["error"] and "paused" in row["error"]
    assert row["draft_wakes"] == 0 and publisher.calls == 0
    await svc._settings.set(DRAFTER_KEY, "99")
    await svc.tick(NOW + timedelta(minutes=1))
    assert "no longer exists" in (await svc.require(item["id"], OWNER))["error"]
    await svc.set_drafter(OWNER, DRAFTER)
    publisher.jobs[DRAFTER]["state"] = "active"
    await svc.tick(NOW + timedelta(minutes=2))
    row = await svc.require(item["id"], OWNER)
    assert row["error"] is None and row["draft_wakes"] == 1


async def test_a_past_item_is_never_claimed(svc):
    item = await planned(svc, NOW + timedelta(hours=1))
    assert await svc.claim_draft(OWNER, now=NOW + timedelta(hours=1)) is None
    assert (await svc.require(item["id"], OWNER))["status"] == "planned"


# --- submit, draft_failed, the timeout -----------------------------------------------


async def test_submit_hashes_the_media_and_queues_a_card(svc, tmp_path):
    item = await planned(svc, fmt="carousel")
    claimed = await svc.claim_draft(OWNER, now=NOW)
    folder = svc.draft_folder(claimed)
    assert folder == f"content/drafts/{item['id']}/1/"
    one = write_media(tmp_path, folder + "1.png", b"one")
    two = write_media(tmp_path, folder + "2.png", b"two")
    await refused(svc.submit(OWNER, item["id"], {"media": [one, two]}), "empty_body")
    await refused(svc.submit(OWNER, item["id"], {"body": "Slides", "media": [one]}),
                  "media_required")
    await refused(svc.submit(OWNER, item["id"], {
        "body": "Slides", "media": [one, folder + "missing.png"]}), "media_missing")
    await refused(svc.submit(OWNER, item["id"], {"body": "x", "media": ["../x.png"]}),
                  "bad_media")
    row = await svc.submit(OWNER, item["id"], {"body": "Slides", "media": [one, two],
                                               "title": "Final title"}, now=NOW)
    assert row["status"] == "review" and row["title"] == "Final title"
    assert all(len(m["sha256"]) == 64 for m in row["media"])
    assert row["card_state"] == "pending" and row["card_attempts"] == 0
    assert row["card_revision"] == cd.card_revision("Slides", row["media"])
    assert row["approved_at"] is None and row["drafting_since"] is None
    await refused(svc.submit(OWNER, item["id"], {"body": "again"}), "not_drafting")


async def test_a_submit_after_publish_at_queues_no_card(svc, tmp_path):
    item = await planned(svc, NOW + timedelta(hours=1))
    await svc.claim_draft(OWNER, now=NOW)
    row = await svc.submit(OWNER, item["id"], {"body": "late"},
                           now=NOW + timedelta(hours=2))
    assert row["status"] == "review" and row["card_state"] == "none"


async def test_three_failed_drafts_fail_the_item(svc):
    item = await planned(svc)
    folders = []
    for attempt in (1, 2, 3):
        claimed = await svc.claim_draft(OWNER, now=NOW)
        folders.append(svc.draft_folder(claimed))
        row = await svc.draft_failed(OWNER, item["id"], f"no tool, try {attempt}", now=NOW)
        assert row["draft_attempts"] == attempt
        assert row["status"] == ("failed" if attempt == 3 else "planned")
    assert row["error"] == "no tool, try 3" and st.needs_attention(row["status"],
                                                                   row["error"])
    assert len(set(folders)) == 3                  # each attempt has its own folder
    await refused(svc.draft_failed(OWNER, item["id"], "x"), "not_drafting")
    await refused(svc.draft_failed(OWNER, item["id"], " "), "no_error")
    # A draft that failed was never approved: there is nothing to re-post.
    await refused(svc.retry(OWNER, item["id"], "repost"), "not_retryable")
    # ...but it can be drafted again: planned, counters reset, theme kept.
    retried = await svc.retry_drafting(OWNER, item["id"])
    assert retried["status"] == "planned" and retried["error"] is None
    assert retried["draft_attempts"] == 0 and retried["theme_approved_at"] is not None
    await refused(svc.retry_drafting(OWNER, item["id"]), "not_retryable")


async def test_a_run_that_outlives_its_timeout_returns_the_item(svc, publisher):
    publisher.jobs[DRAFTER]["timeout_seconds"] = 1800
    item = await planned(svc)
    other = await planned(svc, AT + timedelta(hours=1))
    await svc.claim_draft(OWNER, now=NOW)
    # The run died without a word: its lease is gone, the item still drafting.
    # Waking the drafter now would only meet the one-per-run refusal.
    for minutes in (5, 25, 40):
        await svc.tick(NOW + timedelta(minutes=minutes))
    assert publisher.wakes == []
    assert (await svc.require(other["id"], OWNER))["draft_wakes"] == 0
    assert (await svc.require(item["id"], OWNER))["status"] == "drafting"
    t = NOW + timedelta(minutes=40, seconds=1)
    await svc.tick(t)
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "planned" and row["draft_attempts"] == 1
    assert row["error"] is None and row["drafting_since"] is None
    # Swept first, so the same tick wakes the drafter for it again.
    assert publisher.wakes == [(DRAFTER, t.isoformat())]
    assert row["draft_wakes"] == 1


# --- the approval gate ----------------------------------------------------------------


async def test_every_approve_needs_a_body_and_the_formats_media(svc, tmp_path):
    reel = await svc.create(OWNER, {"channel": "instagram", "format": "reel",
                                    "publish_at": AT.isoformat(), "status": "review"})
    await refused(svc.approve(OWNER, reel["id"]), "empty_body")
    await svc.edit(OWNER, reel["id"], {"body": "Watch", "media": [write_media(tmp_path)]})
    err = await refused(svc.approve(OWNER, reel["id"]), "media_required")
    assert "video" in err.message
    clip = write_media(tmp_path, "content/v.mp4", b"video")
    await svc.edit(OWNER, reel["id"], {"media": [clip]})
    assert (await svc.approve(OWNER, reel["id"]))["status"] == "scheduled"


async def test_a_blank_scheduled_post_is_never_queued(svc, publisher):
    item = await svc.create(OWNER, {"channel": "x", "publish_at": NOW.isoformat()})
    await force(svc, item["id"], status="scheduled", approved_at=NOW.isoformat())
    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert row["status"] == "failed" and "Nothing to publish" in row["error"]
    assert publisher.wakes == []


# --- the card outbox --------------------------------------------------------------------


async def test_a_card_goes_into_the_content_topic(svc, tmp_path, cards):
    item = await drafted(svc, tmp_path)
    await svc.tick(NOW)
    (card,) = cards.sent
    assert cards.topics == [(OWNER, "Контент")]
    assert await svc._settings.get(TOPIC_KEY) == "501"
    assert card["thread_id"] == 501 and card["chat_id"] == OWNER
    assert card["item_id"] == item["id"] and card["body"] == "The post."
    assert card["media_kind"] == "images" and len(card["media"]) == 1
    assert card["media"][0].endswith("a.png") and card["revision"] == item["card_revision"]
    assert card["publish_at_local"] == "02.10 01:00"
    assert card["calendar_url"] == f"https://hermes.example/calendar?item={item['id']}"
    row = await svc.require(item["id"], OWNER)
    assert row["card_state"] == "sent"
    assert (row["card_chat_id"], row["card_message_id"]) == (OWNER, 1001)
    await svc.tick(NOW)
    assert len(cards.sent) == 1                                # sent once


async def test_one_card_per_tick_and_concurrent_ticks_make_one_topic(svc, tmp_path, cards):
    items = [await drafted(svc, tmp_path, AT + timedelta(hours=h), body=f"Post {h}")
             for h in (0, 1, 2)]
    await asyncio.gather(svc.tick(NOW), svc.tick(NOW), svc.tick(NOW))
    assert len(cards.topics) == 1                               # never two topics
    for _ in range(4):
        await svc.tick(NOW)
    assert sorted(c["item_id"] for c in cards.sent) == sorted(i["id"] for i in items)
    assert {c["thread_id"] for c in cards.sent} == {501}
    rows = [await svc.require(i["id"], OWNER) for i in items]
    assert all(r["card_state"] == "sent" and r["card_attempts"] == 0 for r in rows)


async def test_a_refused_card_is_retried_three_times_then_needs_attention(svc, tmp_path,
                                                                        cards):
    item = await drafted(svc, tmp_path)
    cards.answers = [{"error": "file too large"}] * 3
    for n in (1, 2):
        await svc.tick(NOW)
        row = await svc.require(item["id"], OWNER)
        assert row["card_state"] == "pending" and row["card_attempts"] == n
    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert row["card_state"] == "failed" and row["error"] == "file too large"
    assert to_wire(row, NOW.isoformat())["needs_attention"] is True
    await svc.tick(NOW)
    assert len(cards.sent) == 3


async def test_approving_in_the_calendar_settles_a_failed_card(svc, tmp_path, cards):
    item = await drafted(svc, tmp_path)
    cards.answers = [{"error": "telegram unreachable"}] * 3
    for _ in range(3):
        await svc.tick(NOW)
    assert (await svc.require(item["id"], OWNER))["card_state"] == "failed"
    row = await svc.approve(OWNER, item["id"], now=NOW)
    assert row["status"] == "scheduled" and row["card_state"] == "none"
    assert row["error"] is None and cards.edits == []       # no card to tell
    assert to_wire(row, NOW.isoformat())["needs_attention"] is False
    row = await svc.unapprove(OWNER, item["id"])
    assert row["card_state"] == "none"
    assert to_wire(row, NOW.isoformat())["needs_attention"] is False


async def test_a_deleted_topic_is_recreated_once(svc, tmp_path, cards):
    first = await sent_card(svc, tmp_path)
    second = await drafted(svc, tmp_path, AT + timedelta(hours=1))
    cards.answers = [{"topic_gone": True}]
    await svc.tick(NOW)
    assert cards.topics == [(OWNER, "Контент")] * 2
    assert await svc._settings.get(TOPIC_KEY) == "502"
    assert [c["thread_id"] for c in cards.sent] == [501, 501, 502]
    assert (await svc.require(second["id"], OWNER))["card_state"] == "sent"
    third = await drafted(svc, tmp_path, AT + timedelta(hours=2))
    cards.answers = [{"topic_gone": True}, {"topic_gone": True}]
    await svc.tick(NOW)
    row = await svc.require(third["id"], OWNER)
    assert row["card_state"] == "failed" and "topic" in row["error"]
    assert first["card_state"] == "sent"


async def test_without_topics_the_card_goes_flat(svc, tmp_path, cards):
    cards.topic = None
    item = await drafted(svc, tmp_path)
    await svc.tick(NOW)
    assert cards.sent[0]["thread_id"] is None
    assert await svc._settings.get(TOPIC_KEY) is None
    assert (await svc.require(item["id"], OWNER))["card_state"] == "sent"


async def test_a_process_without_a_bot_leaves_the_card_pending(engine, tmp_path, users,  # noqa: F811
                                                              publisher):
    service = ContentService(
        ContentRepository(engine), WorkspaceMedia(FakeWorkspaces(tmp_path)), publisher,
        MetaStore(engine), cards=ApprovalCardsSlot())
    await service.set_drafter(OWNER, DRAFTER)
    item = await drafted(service, tmp_path)
    await service.tick(NOW)
    row = await service.require(item["id"], OWNER)
    assert row["card_state"] == "pending" and row["card_attempts"] == 0


async def test_a_dead_workers_send_is_taken_again(svc, tmp_path, cards):
    item = await drafted(svc, tmp_path)
    await force(svc, item["id"], card_state="sending", card_claimed_at=NOW.isoformat())
    await svc.tick(NOW + timedelta(minutes=4))
    assert cards.sent == []
    await svc.tick(NOW + timedelta(minutes=5, seconds=1))
    assert len(cards.sent) == 1
    assert (await svc.require(item["id"], OWNER))["card_state"] == "sent"


# --- the card's decisions ----------------------------------------------------------------


async def test_the_approve_button_approves_exactly_its_revision(svc, tmp_path, cards):
    decisions = ContentCardDecisions(svc)
    row = await sent_card(svc, tmp_path)
    rev = row["card_revision"]
    assert await decisions.approve_from_card(row["id"], "0" * 12, OWNER) == STALE_TEXT
    assert await decisions.approve_from_card(row["id"], rev, OTHER) == STALE_TEXT
    assert (await svc.require(row["id"], OWNER))["status"] == "review"
    answer = await decisions.approve_from_card(row["id"], rev, OWNER)
    assert answer == "✅ Одобрено — выйдет ≈ 02.10 01:00"
    approved = await svc.require(row["id"], OWNER)
    assert approved["status"] == "scheduled" and approved["approved_by"] == OWNER
    assert approved["media"][0]["sha256"] == row["media"][0]["sha256"]
    assert cards.edits[-1] == (OWNER, row["card_message_id"], answer)
    assert await decisions.approve_from_card(row["id"], rev, OWNER) == STALE_TEXT


async def test_a_file_changed_after_the_card_is_never_approved(svc, tmp_path):
    decisions = ContentCardDecisions(svc)
    row = await sent_card(svc, tmp_path)
    write_media(tmp_path, row["media"][0]["path"], b"swapped")
    assert await decisions.approve_from_card(row["id"], row["card_revision"],
                                             OWNER) == STALE_TEXT
    assert (await svc.require(row["id"], OWNER))["status"] == "review"


async def test_a_failed_card_lookup_raises_instead_of_letting_the_reply_through(
        svc, monkeypatch):
    # `None` means "not a card" and the reply would become a turn; a raise is what
    # the gateway's `intercept_card_reply` answers with its failure text.
    async def down(*_a, **_k):
        raise ConnectionError("database down")

    monkeypatch.setattr(svc, "live_card", down)
    with pytest.raises(ConnectionError):
        await ContentCardDecisions(svc).redraft_from_reply(OWNER, 42, OWNER, "shorter")


@pytest.mark.parametrize("how", ["web", "agent"])
async def test_an_edit_supersedes_the_card(svc, tmp_path, cards, how):
    decisions = ContentCardDecisions(svc)
    row = await sent_card(svc, tmp_path)
    if how == "web":
        await svc.edit(OWNER, row["id"], {"body": "Rewritten by hand"})
    else:
        await svc.agent_update(OWNER, row["id"], {"title": "Renamed"})
    edited = await svc.require(row["id"], OWNER)
    assert edited["card_state"] == "superseded"
    assert cards.edits == [(OWNER, row["card_message_id"], cd.CHANGED_TEXT)]
    assert await decisions.approve_from_card(row["id"], row["card_revision"],
                                             OWNER) == STALE_TEXT
    assert (await svc.require(row["id"], OWNER))["status"] == "review"


async def test_an_edit_while_the_card_is_on_its_way_marks_it_stale(svc, tmp_path, cards):
    item = await drafted(svc, tmp_path)
    real_send = cards.send

    async def send_and_edit(card):
        await svc.edit(OWNER, item["id"], {"body": "changed mid-flight"})
        return await real_send(card)

    cards.send = send_and_edit
    await svc.tick(NOW)
    row = await svc.require(item["id"], OWNER)
    assert row["card_state"] == "superseded"
    assert cards.edits == [(OWNER, 1001, cd.CHANGED_TEXT)]


async def test_unapproving_a_telegram_approval_tells_the_card(svc, tmp_path, cards):
    decisions = ContentCardDecisions(svc)
    row = await sent_card(svc, tmp_path)
    await decisions.approve_from_card(row["id"], row["card_revision"], OWNER)
    back = await svc.unapprove(OWNER, row["id"])
    assert back["status"] == "review" and back["card_state"] == "superseded"
    assert cards.edits[-1][2] == cd.CHANGED_TEXT


async def test_a_reply_to_a_card_redrafts_it(svc, tmp_path, cards, publisher):
    decisions = ContentCardDecisions(svc)
    row = await sent_card(svc, tmp_path)
    msg = row["card_message_id"]
    assert await decisions.redraft_from_reply(OWNER, msg + 1, OWNER, "shorter") is None
    assert await decisions.redraft_from_reply(OWNER, msg, OTHER, "shorter") is None
    assert await decisions.redraft_from_reply(OTHER, msg, OWNER, "shorter") is None
    assert "что переделать" in await decisions.redraft_from_reply(OWNER, msg, OWNER, " ")
    publisher.run_finished(DRAFTER)
    answer = await decisions.redraft_from_reply(OWNER, msg, OWNER, "Shorter, please " * 200)
    assert answer.startswith("🔁")
    redone = await svc.require(row["id"], OWNER)
    assert redone["status"] == "planned" and redone["redrafts"] == 1
    assert redone["card_state"] == "superseded"
    assert redone["draft_comment"].startswith("Shorter") and len(redone["draft_comment"]) == 2000
    assert cards.edits[-1] == (OWNER, msg, cd.REDRAFTING_TEXT)
    assert publisher.wakes[-1][0] == DRAFTER                   # the drafter is woken
    # The card is no longer live: another reply is an ordinary message.
    assert await decisions.redraft_from_reply(OWNER, msg, OWNER, "again") is None
    # The drafter sees the comment on its next claim, and the new attempt its folder.
    claimed = await svc.claim_draft(OWNER, now=NOW)
    assert claimed["draft_comment"].startswith("Shorter")
    assert svc.draft_folder(claimed).endswith("/2/")


async def test_the_fourth_redraft_points_to_the_calendar(svc, tmp_path):
    decisions = ContentCardDecisions(svc)
    row = await sent_card(svc, tmp_path)
    await force(svc, row["id"], redrafts=3)
    answer = await decisions.redraft_from_reply(OWNER, row["card_message_id"], OWNER, "x")
    assert "календар" in answer and f"calendar?item={row['id']}" in answer
    assert (await svc.require(row["id"], OWNER))["status"] == "review"


async def test_the_card_topic_is_recognised_in_the_owners_chat_only(svc, tmp_path):
    decisions = ContentCardDecisions(svc)
    assert await decisions.is_card_topic(OWNER, 501) is False     # no topic yet
    await sent_card(svc, tmp_path)
    assert await decisions.is_card_topic(OWNER, 501) is True
    assert await decisions.is_card_topic(OWNER, 502) is False
    assert await decisions.is_card_topic(OWNER, None) is False
    assert await decisions.is_card_topic(OTHER, 501) is False


# --- a missed publish time ------------------------------------------------------------------


async def test_a_post_nobody_approved_in_time_is_never_published(svc, tmp_path, cards,
                                                                 publisher):
    row = await sent_card(svc, tmp_path)
    left = await planned(svc, AT + timedelta(minutes=30))
    wakes = len(publisher.wakes)
    after = AT + timedelta(minutes=31)
    await svc.tick(after)
    missed = await svc.require(row["id"], OWNER)
    assert missed["status"] == "review" and missed["card_state"] == "superseded"
    assert cards.edits[-1] == (OWNER, row["card_message_id"], cd.MISSED_TEXT)
    assert to_wire(missed, after.isoformat())["needs_attention"] is True
    still = await svc.require(left["id"], OWNER)
    assert still["status"] == "planned"
    assert to_wire(still, after.isoformat())["needs_attention"] is True
    assert all(job == DRAFTER for job, _ in publisher.wakes[wakes:])
    assert await svc.list_items(OWNER, status="queued") == []

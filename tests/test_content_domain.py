"""The content plan's pure rules: who may move an item where, what needs the owner's
attention, how a time is read and written, what a media path may look like, what
each format must carry before approval, and the approval card's revision.

The agent transition table is asserted row by row over EVERY (from, to) pair, so a
status added to `STATUSES` without a decision about it fails here.
"""
from datetime import datetime, timezone

import pytest

from claude_hermes.modules.content.domain import cards as cd
from claude_hermes.modules.content.domain import status as st
from claude_hermes.modules.content.domain.formats import (
    MEDIA_RULES,
    approval_refusal,
    card_media_kind,
    media_kind,
)
from claude_hermes.modules.content.domain.media import (
    MEDIA_EXTENSIONS,
    media_problem,
    split_paths,
    stored_media,
)
from claude_hermes.modules.content.domain.time import iso_utc, local_iso, parse_instant

# The agent may leave these...
AGENT_MAY_LEAVE = {"idea", "planned", "draft", "review", "scheduled", "failed"}
# ...only for these.
AGENT_MAY_ENTER = {"idea", "draft", "review", "skipped"}


def test_the_status_set_is_exactly_the_locked_one():
    assert st.STATUSES == ("idea", "planned", "drafting", "draft", "review",
                           "scheduled", "queued", "publishing", "published", "failed",
                           "stuck", "skipped")
    assert st.AGENT_LOCKED == {"drafting", "queued", "publishing", "stuck", "published"}
    # The agent never enters either approval, nor the drafter's own status.
    assert not {"planned", "drafting", "scheduled"} & st.AGENT_TO
    assert st.THEME_FIELDS == ("title", "channel", "format", "publish_at")


@pytest.mark.parametrize("current", st.STATUSES)
@pytest.mark.parametrize("target", st.STATUSES)
def test_agent_transition_table(current, target):
    allowed = current in AGENT_MAY_LEAVE and target in AGENT_MAY_ENTER
    refusal = st.agent_refusal(current, target)
    assert (refusal is None) is allowed, (current, target, refusal)


def test_the_agent_is_told_how_to_get_an_item_scheduled():
    """Refusing `scheduled` names the way forward, since the agent cannot take it."""
    assert "review" in st.agent_refusal("draft", "scheduled")
    assert "owner" in st.agent_refusal("draft", "scheduled")


def test_the_agent_is_told_who_moves_planned_and_drafting():
    assert "theme" in st.agent_refusal("idea", "planned")
    assert "drafter" in st.agent_refusal("drafting", "review")


def test_an_unknown_status_is_refused():
    assert "Unknown status" in st.agent_refusal("draft", "live")


@pytest.mark.parametrize(("status", "error", "expected"), [
    ("failed", None, True), ("stuck", None, True), ("failed", "x", True),
    ("queued", "no publisher", True), ("queued", None, False),
    ("scheduled", "x", False), ("draft", None, False), ("published", None, False),
])
def test_needs_attention(status, error, expected):
    assert st.needs_attention(status, error) is expected


PAST = "2026-10-01T04:00:00+00:00"
NOW_ISO = "2026-10-01T05:00:00+00:00"
FUTURE = "2026-10-02T05:00:00+00:00"


@pytest.mark.parametrize(("status", "error", "publish_at", "approved", "card", "expected"), [
    # A planned item the drafter could not take.
    ("planned", "no drafter", FUTURE, False, "none", True),
    ("planned", None, FUTURE, False, "none", False),
    # A post whose time came before anybody approved it.
    ("review", None, PAST, False, "sent", True),
    ("review", None, FUTURE, False, "sent", False),
    ("planned", None, PAST, False, "none", True),
    ("drafting", None, PAST, False, "none", True),
    ("scheduled", None, PAST, True, "sent", False),
    ("idea", None, PAST, False, "none", False),
    # A card Telegram refused three times — only while the post still waits for
    # the approval that card was meant to carry.
    ("review", None, FUTURE, False, "failed", True),
    ("scheduled", None, FUTURE, True, "failed", False),
    ("published", None, PAST, True, "failed", False),
    ("skipped", None, FUTURE, False, "failed", False),
])
def test_needs_attention_v2(status, error, publish_at, approved, card, expected):
    assert st.needs_attention(status, error, publish_at=publish_at, approved=approved,
                              card_state=card, now_iso=NOW_ISO) is expected
    row = {"status": status, "error": error, "publish_at": publish_at,
           "approved_at": NOW_ISO if approved else None, "card_state": card}
    assert st.item_needs_attention(row, NOW_ISO) is expected


def test_the_owner_sets_approve_and_retry_from_the_contract():
    assert st.APPROVABLE == {"idea", "draft", "review", "failed"}
    assert st.RETRYABLE == {"failed", "stuck", "publishing"}
    assert st.WEB_LOCKED == {"drafting", "queued", "publishing", "published"}
    assert st.CREATE_STATUSES == {"idea", "draft", "review"}


# --- time ----------------------------------------------------------------------


def test_a_naive_time_is_read_in_the_configured_zone():
    moment = parse_instant("2026-10-01T10:00", "Asia/Tashkent")
    assert moment == datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)
    assert iso_utc(moment) == "2026-10-01T05:00:00+00:00"


def test_an_offset_is_taken_at_its_word():
    moment = parse_instant("2026-10-01T10:00+02:00", "Asia/Tashkent")
    assert iso_utc(moment) == "2026-10-01T08:00:00+00:00"
    assert iso_utc(parse_instant("2026-10-01T10:00Z", "Asia/Tashkent")) \
        == "2026-10-01T10:00:00+00:00"


def test_an_unreadable_time_says_what_to_write():
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_instant("tomorrow at ten", "UTC")


def test_answers_are_written_in_the_zone_with_their_offset():
    assert local_iso("2026-10-01T05:00:00+00:00", "Asia/Tashkent") == "2026-10-01T10:00+05:00"
    assert local_iso(None, "Asia/Tashkent") is None


# --- media ---------------------------------------------------------------------


@pytest.mark.parametrize("path", ["content/img/x.mp4", "a.PNG", "reels\\cut.mov"])
def test_an_ordinary_media_path_passes(path):
    assert media_problem(path) is None


@pytest.mark.parametrize(("path", "fragment"), [
    ("", "empty"),
    (".claude/skills/x.png", "dot-directory"),
    ("content/../x.png", "dot-directory"),
    ("content/.hidden.png", "dot-directory"),
    ("secrets.env", "only"),
    ("notes/plan.md", "only"),
    ("video", "only"),
])
def test_a_bad_media_path_is_refused(path, fragment):
    assert fragment in media_problem(path)


def test_the_media_extensions_are_images_and_video_only():
    assert MEDIA_EXTENSIONS == {"png", "jpg", "jpeg", "webp", "gif", "mp4", "mov", "webm"}


def test_the_tool_media_argument_is_one_path_per_line():
    assert split_paths("a.png\n\n  b.mp4 \r\n") == ["a.png", "b.mp4"]
    assert split_paths(None) == []


def test_stored_media_tolerates_whatever_a_row_holds():
    assert stored_media([{"path": "a.png"}, {"nope": 1}, "x", {"path": "b.png",
                                                               "sha256": "ab"}]) == [
        {"path": "a.png", "sha256": None}, {"path": "b.png", "sha256": "ab"}]
    assert stored_media(None) == []


# --- the approval gate ---------------------------------------------------------


def test_the_format_table_is_the_locked_one():
    assert MEDIA_RULES == {"carousel": ("image", 2, 10), "reel": ("video", 1, 1),
                           "short": ("video", 1, 1), "video": ("video", 1, 1),
                           "photo": ("image", 1, 1)}
    assert media_kind("a/b.PNG") == "image" and media_kind("c.mov") == "video"
    assert media_kind("notes.md") is None


def _entries(*names):
    return [{"path": n, "sha256": None} for n in names]


IMGS = [f"s{i}.png" for i in range(11)]


@pytest.mark.parametrize(("fmt", "media", "code"), [
    ("carousel", IMGS[:2], None), ("carousel", IMGS[:10], None),
    ("carousel", IMGS[:1], "media_required"), ("carousel", IMGS[:11], "media_required"),
    ("carousel", ["a.png", "b.mp4"], "media_required"),
    ("reel", ["r.mp4"], None), ("reel", ["r.png"], "media_required"),
    ("reel", [], "media_required"), ("reel", ["a.mp4", "b.mp4"], "media_required"),
    ("short", ["r.webm"], None), ("video", ["v.mov"], None),
    ("video", [], "media_required"),
    ("photo", ["p.jpg"], None), ("photo", ["p.mp4"], "media_required"),
    ("photo", ["a.jpg", "b.jpg"], "media_required"),
    ("post", [], None), ("post", ["x.mp4", "y.png"], None), ("", [], None),
    ("Carousel", IMGS[:3], None),
])
def test_approval_needs_the_formats_media(fmt, media, code):
    refusal = approval_refusal("text", fmt, _entries(*media))
    assert (refusal[0] if refusal else None) == code, refusal
    # Plain strings are read the same way as stored entries.
    assert approval_refusal("text", fmt, media) == refusal


def test_a_blank_body_is_refused_first_and_the_message_names_the_need():
    assert approval_refusal("  \n", "post", [])[0] == "empty_body"
    assert approval_refusal("", "reel", [])[0] == "empty_body"
    code, message = approval_refusal("text", "carousel", _entries("one.png"))
    assert code == "media_required" and "2-10 images" in message and "1 file" in message
    _code, message = approval_refusal("text", "reel", _entries("x.png"))
    assert "exactly 1 video" in message and "x.png" in message


def test_the_card_media_kind():
    assert card_media_kind([]) == "none"
    assert card_media_kind(_entries("v.mp4")) == "video"
    assert card_media_kind(_entries("p.png")) == "images"
    assert card_media_kind(_entries("a.png", "b.png")) == "images"


# --- the card revision ---------------------------------------------------------


def test_the_revision_binds_body_paths_and_hashes():
    media = [{"path": "a.png", "sha256": "11"}, {"path": "b.png", "sha256": "22"}]
    rev = cd.card_revision("body", media)
    assert len(rev) == 12 and int(rev, 16) >= 0
    assert cd.card_revision("body", [dict(m) for m in media]) == rev
    assert cd.card_revision("body!", media) != rev
    assert cd.card_revision("body", media[::-1]) != rev
    assert cd.card_revision("body", [media[0], {"path": "b.png", "sha256": "23"}]) != rev
    assert cd.card_revision("body", [media[0], {"path": "c.png", "sha256": "22"}]) != rev
    # callback_data `cnt:a:<id>:<rev>` stays far under Telegram's 64 bytes.
    assert len(f"cnt:a:{2**63 - 1}:{rev}".encode()) < 64

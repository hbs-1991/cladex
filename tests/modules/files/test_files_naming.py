"""The pure naming invariants. Moved from tests/test_attachments.py — the rule
belongs to files now, the aiogram half of that test file stays with telegram."""
from claude_hermes.modules.files.domain.naming import lane_token, safe_name
from claude_hermes.modules.files.infrastructure.layout import (
    inbox_dir,
    lane_dir,
    outbox_dir,
)


def test_safe_name_cannot_escape_its_directory(tmp_path):
    name = safe_name(42, "../../etc/passwd")
    assert "/" not in name and "\\" not in name and ".." not in name
    assert (tmp_path / name).parent == tmp_path


def test_safe_name_keeps_a_readable_name_and_prefixes_the_message_id():
    # The prefix does two jobs: two `report.pdf` from different messages cannot
    # collide, and a message id never changes, so the path stays stable.
    assert safe_name(42, "Q3 report.pdf") == "42_Q3_report.pdf"


def test_safe_name_falls_back_when_nothing_survives_sanitizing():
    assert safe_name(7, "...") == "7_file"
    assert safe_name(7, "") == "7_file"


def test_lane_token_flattens_the_session_key():
    # The same string becomes the object-key prefix in Wave 3, so it is frozen.
    assert lane_token("tg:private:1:t9") == "tg-private-1-t9"
    assert lane_token("") == "lane"


def test_lane_dir_is_under_agent_home_and_flattens_the_session_key(tmp_path):
    assert lane_dir(tmp_path, "tg:private:1:t9") == (
        tmp_path / "agent_home" / "inbox" / "tg-private-1-t9"
    )
    assert inbox_dir is lane_dir
    assert outbox_dir(tmp_path, "tg:private:1:t9") == (
        tmp_path / "agent_home" / "outbox" / "tg-private-1-t9"
    )

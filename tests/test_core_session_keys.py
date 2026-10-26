"""The session-key format is a cross-module contract: sessions, messages, threads,
turn events, costs and files are all keyed by it, and the importer's backfill parses
it with a regex. One test per function, asserted against the producer where possible
so the pair cannot drift."""
from claude_hermes.core.session_keys import (
    build_session_key,
    chat_id_from_key,
    chat_type_from_key,
    lane_owner,
    thread_id_from_key,
    web_session_key,
)


def test_build_session_key_private_and_group():
    assert build_session_key("private", 42) == "tg:private:42"
    assert build_session_key("supergroup", -100, user_id=7) == "tg:supergroup:-100:7"


def test_build_session_key_with_thread():
    assert build_session_key("private", 42, thread_id=9) == "tg:private:42:t9"
    assert (build_session_key("supergroup", -100, user_id=7, thread_id=3)
            == "tg:supergroup:-100:7:t3")


def test_absent_thread_keeps_the_legacy_key():
    # Backward-compatibility guard: rows written before forum topics existed are
    # keyed by the old string. A zero/None thread must reproduce it byte-for-byte.
    assert build_session_key("private", 42, thread_id=None) == "tg:private:42"
    assert build_session_key("private", 42, thread_id=0) == "tg:private:42"


def test_chat_id_from_key():
    assert chat_id_from_key("tg:private:42") == 42
    assert chat_id_from_key("tg:supergroup:-5:7") == -5
    assert chat_id_from_key("tg:private:42:t9") == 42
    assert chat_id_from_key("tg:supergroup:-5:7:t3") == -5


def test_chat_id_from_key_is_none_for_a_non_tg_key():
    assert chat_id_from_key("cron:3") is None
    assert chat_id_from_key("web:1:abc") is None
    assert chat_id_from_key("tg:private:notanumber") is None


def test_thread_id_from_key():
    assert thread_id_from_key(build_session_key("private", 42, thread_id=9)) == 9
    assert thread_id_from_key("tg:private:42") is None
    # a trailing segment that merely starts with t is not a topic id
    assert thread_id_from_key("tg:private:42:talk") is None


def test_chat_type_from_key_matches_what_build_session_key_produces():
    assert chat_type_from_key(build_session_key("private", 1)) == "private"
    assert chat_type_from_key(build_session_key("group", -5, user_id=7)) == "group"
    assert chat_type_from_key(
        build_session_key("supergroup", -5, user_id=7, thread_id=9)) == "supergroup"


def test_a_web_lane_reads_as_private():
    """The ask_user gate and every other private-only rule apply to a browser
    thread unchanged: one person, alone with the agent."""
    assert chat_type_from_key("web:1:abc") == "private"


def test_chat_type_from_key_is_empty_for_a_non_tg_key():
    assert chat_type_from_key("cron:12") == ""
    assert chat_type_from_key("") == ""


def test_web_session_key_is_unique_per_thread_and_carries_the_user():
    a, b = web_session_key(7), web_session_key(7)
    assert a != b
    assert a.startswith("web:7:")
    assert chat_type_from_key(a) == "private"


# --- lane_owner: the one rule (D3) ------------------------------------------
# `sessions.owner_id`, `threads.owner_user_id`, the 0005 backfill and the workspace
# resolver all go through this (or through its spelling in SQL), which is what makes
# it impossible for them to disagree about a lane.

OWNER = 1


def test_lane_owner_web_lane_belongs_to_the_id_in_the_key():
    """Not to whoever is asking: user B opening user A's lane must not make B its
    owner — that would move the running turn into B's workspace."""
    assert lane_owner("web:42:abc", "web", 42, OWNER) == 42
    assert lane_owner("web:42:abc", "web", 99, OWNER) == 42


def test_lane_owner_private_chat_is_the_chat_id():
    assert lane_owner("tg:private:42", "telegram", 42, OWNER) == 42
    assert lane_owner("tg:private:42:t9", "telegram", 42, OWNER) == 42
    # chat id == user id in a private chat, so the key is the authority.
    assert lane_owner("tg:private:42", "telegram", 999, OWNER) == 42


def test_lane_owner_group_lanes_belong_to_the_deploy_owner():
    """Where `thread_owner` answered None. One lane, one workspace — and the deploy
    owner is the only person in a group chat the agent has a workspace for. The
    sender is still the sender; they just do not own the room."""
    assert lane_owner("tg:supergroup:-100:7", "telegram", 7, OWNER) == OWNER
    assert lane_owner("tg:group:-5:7:t3", "telegram", 7, OWNER) == OWNER


def test_lane_owner_cron_takes_the_job_owner_the_caller_passes():
    """Nothing in `cron:<job_id>` names a person, so the scheduler passes the job's
    `owner_id` through — which is what makes a routine run in ITS owner's tree."""
    assert lane_owner("cron:7", "cron", 42, OWNER) == 42
    assert lane_owner("cron:7", "cron", 0, OWNER) == OWNER


def test_lane_owner_never_invents_an_owner_for_a_key_it_cannot_read():
    """A grammar this function has never seen is not a licence to guess a
    stranger's workspace: the deploy owner is the only safe answer."""
    assert lane_owner("web:notanumber:x", "web", 42, OWNER) == 42
    assert lane_owner("nonsense", "telegram", 42, OWNER) == OWNER
    assert lane_owner("", "telegram", 42, OWNER) == OWNER


def test_lane_owner_always_answers_an_int():
    """`thread_owner` could answer None and its callers wrote NULL; every caller of
    this one writes an owner, and a NULL row is visible to nobody."""
    for key, channel in (("web:1:a", "web"), ("tg:private:1", "telegram"),
                         ("tg:group:-5:2", "telegram"), ("cron:1", "cron")):
        assert isinstance(lane_owner(key, channel, 1, OWNER), int)


# --- is_group_lane: tool presence is a property of the LANE (4b, D6) --------


def test_is_group_lane_is_true_for_the_two_room_grammars_only():
    """`skill_admin`, `session_search` and `cost_report` are withheld from a room
    — from everybody in it, the owner included — because the room's library,
    recall and spend are the deploy owner's, and a per-sender rule would flip the
    MCP schema list between two turns of one session (a cached-prefix miss)."""
    from claude_hermes.core.session_keys import is_group_lane

    assert is_group_lane(build_session_key("group", -5, user_id=7))
    assert is_group_lane(build_session_key("supergroup", -5, user_id=7, thread_id=9))
    assert not is_group_lane(build_session_key("private", 1))
    assert not is_group_lane(build_session_key("private", 1, thread_id=9))
    assert not is_group_lane("web:1:abc")
    assert not is_group_lane("cron:12")
    assert not is_group_lane("")

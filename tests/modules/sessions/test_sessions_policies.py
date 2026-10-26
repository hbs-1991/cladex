"""The visibility rule and the key grammar the module re-exports."""
from claude_hermes.core import session_keys
from claude_hermes.modules.sessions.domain import keys
from claude_hermes.modules.sessions.domain.constants import (
    TITLE_MAX_CHARS,
    TITLE_MAX_WORDS,
)
from claude_hermes.modules.sessions.domain.dto import SEARCH_FIELDS, MessageHit
from claude_hermes.modules.sessions.domain.policies import visible


def test_keys_are_the_core_functions_not_copies():
    """A copy would drift: the key format is one contract, shared with turns,
    telegram, the scheduler and the web."""
    for name in ("build_session_key", "chat_id_from_key", "chat_type_from_key",
                 "thread_id_from_key", "web_session_key"):
        assert getattr(keys, name) is getattr(session_keys, name)


def test_a_web_lane_belongs_to_the_user_who_opened_it():
    mine = {"session_key": "web:1:a", "channel": "web", "owner_user_id": 1}
    theirs = {"session_key": "web:2:a", "channel": "web", "owner_user_id": 2}
    assert visible(mine, 1, False)
    assert not visible(theirs, 1, False)
    assert not visible(theirs, 1, True)      # not even the owner reads another's web lane


def test_a_cron_lane_is_never_listed():
    """Job lanes are the agent talking to itself; a person has no business in one."""
    job = {"session_key": "cron:7", "channel": "telegram", "owner_user_id": None}
    assert not visible(job, 1, False) and not visible(job, 1, True)


def test_a_group_lane_is_the_owners_alone():
    group = {"session_key": "tg:supergroup:-5:1:t9", "channel": "telegram",
             "owner_user_id": None}
    assert not visible(group, 1, False) and visible(group, 1, True)


def test_a_private_lane_belongs_to_that_chat():
    private = {"session_key": "tg:private:1", "channel": "telegram", "owner_user_id": 1}
    assert visible(private, 1, False) and not visible(private, 2, False)


def test_the_title_cap_is_the_telegram_topic_name_limit():
    """The same number `gateway.topics.TOPIC_NAME_LIMIT` carries: one title is
    written to threads.title AND used as a forum-topic name, so shortening it
    here would silently change what Telegram shows."""
    assert TITLE_MAX_CHARS == 128
    assert TITLE_MAX_WORDS == 10


def test_the_recall_wire_shape_is_frozen():
    row = {"session_key": "web:1:a", "role": "user", "ts": "2026-07-12T10:00:00+00:00",
           "snippet": "a [match] here", "extra": "ignored"}
    hit = MessageHit.from_row(row)
    assert SEARCH_FIELDS == ("session_key", "role", "ts", "snippet")
    assert hit.snippet == "a [match] here" and hit.role == "user"


# --- Wave 4a (D3): the rule reads the row's owner, with the is_owner belt -----


def test_the_owner_column_is_what_decides_not_the_key():
    """Every lane writer goes through `lane_owner` now, so the column is the one
    rule applied once — the same one `sessions.owner_id`, the `0005` backfill and
    the workspace resolver use. Re-deriving it here is how they would drift."""
    private = {"session_key": "tg:private:42", "channel": "telegram",
               "owner_user_id": 42}
    assert visible(private, 42, False)
    assert not visible(private, 7, False)


def test_a_group_lane_belongs_to_the_deploy_owner_by_its_column():
    """Where `thread_owner` wrote NULL, `lane_owner` writes the deploy owner. The
    owner therefore sees it as their own lane, not through the belt."""
    group = {"session_key": "tg:supergroup:-100:7:t3", "channel": "telegram",
             "owner_user_id": 1}
    assert visible(group, 1, False)              # no is_owner needed
    assert not visible(group, 7, False)          # the sender does not own the room


def test_the_is_owner_belt_only_covers_a_lane_the_backfill_missed():
    """A group thread with no owner must not disappear from the owner's sidebar —
    silently, and with no way to get it back. The belt is deliberately NOT extended
    to a `web:` lane: the owner does not read another person's browser thread."""
    orphan_group = {"session_key": "tg:group:-5:9", "channel": "telegram",
                    "owner_user_id": None}
    orphan_web = {"session_key": "web:2:abc", "channel": "web", "owner_user_id": None}
    assert visible(orphan_group, 1, True) and not visible(orphan_group, 9, False)
    assert not visible(orphan_web, 1, True)


def test_a_cron_lane_is_hidden_before_anything_else_is_asked():
    """Cron is checked first, so even a job lane that names an owner stays out of
    every sidebar — the Routines page reads the delivery lane instead."""
    job = {"session_key": "cron:7", "channel": "telegram", "owner_user_id": 1}
    assert not visible(job, 1, True) and not visible(job, 1, False)


def test_user_a_never_sees_user_bs_web_lane_even_as_owner():
    b = {"session_key": "web:2:abc", "channel": "web", "owner_user_id": 2}
    assert visible(b, 2, False)
    assert not visible(b, 1, False) and not visible(b, 1, True)


def test_the_owner_does_not_see_another_users_private_telegram_lane():
    """Advisor blocker B-2 (code pass): the belt is for group lanes only. Before
    4a the owner could not open an allowlisted user's private chat; a fall-through
    `return is_owner` had quietly reopened it, in `list_visible` and `get_visible`
    alike."""
    private_of_b = {"session_key": "tg:private:2", "channel": "telegram",
                    "owner_user_id": 2}
    assert visible(private_of_b, 2, False)
    assert not visible(private_of_b, 1, True)
    # and an orphan private lane (no owner column) is nobody's, not the owner's
    orphan = {"session_key": "tg:private:2", "channel": "telegram", "owner_user_id": None}
    assert not visible(orphan, 1, True)
    supergroup = {"session_key": "tg:supergroup:-5:9:t3", "channel": "telegram",
                  "owner_user_id": None}
    assert visible(supergroup, 1, True) and not visible(supergroup, 9, False)

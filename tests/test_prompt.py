from datetime import date

from claude_hermes.modules.turns.domain.prompt import TURN_RULES, WIKI_RULES, build_system_append


def test_append_carries_the_rules_the_index_and_the_date():
    out = build_system_append("# Wiki index\n- user/profile.md — who they are\n", date(2026, 7, 14))
    assert WIKI_RULES in out                      # the schema is code-owned, not in CLAUDE.md
    assert TURN_RULES in out
    assert "user/profile.md" in out
    assert "2026-07-14" in out
    assert ":" not in out.splitlines()[-1]        # date-only stamp, no clock time


def test_rules_are_present_even_with_an_empty_wiki():
    out = build_system_append("", date(2026, 7, 14))
    assert WIKI_RULES in out
    assert "Wiki index (snapshot" not in out      # no empty section header


def test_rules_tell_the_model_the_index_may_be_stale():
    assert "index.md" in WIKI_RULES
    assert "raw/" in WIKI_RULES


def test_rules_pin_the_link_format_to_a_namespaced_path():
    """A bare [[name]] is indistinguishable from a link to a page that does not
    exist, so lint would 'fix' a live link by deleting it."""
    assert "[[user/nutrition]]" in WIKI_RULES
    assert "bare" in WIKI_RULES


def test_turn_rules_kill_the_two_beliefs_that_stalled_a_live_task():
    """15.08.2026: the agent ended three turns in a row on "I'll wait for this to
    finish — I'll be notified", then told the user to change a permission mode that
    was already bypassPermissions. Nothing here notifies, and nothing here asks."""
    assert "background-task notifications" in TURN_RULES
    assert "run_in_background" in TURN_RULES
    assert "misdiagnosis" in TURN_RULES


def test_append_stable_for_same_inputs():
    a = build_system_append("idx", date(2026, 7, 14))
    b = build_system_append("idx", date(2026, 7, 14))
    assert a == b                                  # byte-stable → the prompt cache stays warm

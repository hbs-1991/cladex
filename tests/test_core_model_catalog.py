"""The model catalog and the one rule for which model a lane runs."""
import logging

import pytest

from claude_hermes.core.model_catalog import (
    CATALOG,
    EFFORTS,
    Choice,
    cli_id,
    is_model_command,
    lane_is_shared,
    lookup,
    normalize,
    offered,
    resolve,
    validate,
)

OFFERED = ["claude-sonnet-5", "claude-opus-5-5", "claude-fable-5-1"]


def test_every_spelling_finds_the_same_entry():
    for info in CATALOG:
        for spelling in (info.id, info.cli_id, f"{info.id}[1m]", *info.aliases):
            assert lookup(spelling) is info
    assert normalize("claude-opus-5[1m]") == "claude-opus-5"
    assert cli_id("claude-opus-5[1m]") == lookup("claude-opus-5").cli_id


def test_an_unknown_id_resolves_to_an_entry_that_knows_nothing():
    info = lookup("claude-opus-4-8[1m]")
    assert (info.id, info.cli_id, info.label) == ("claude-opus-4-8", "claude-opus-4-8[1m]",
                                                  "claude-opus-4-8")
    # no window, so a picker never disables it; no efforts, so no effort row
    assert info.window == 0 and info.efforts == () and info.price_in == 0


def test_offered_puts_a_missing_deploy_default_first_and_drops_duplicates():
    ids = [m.id for m in offered(["claude-opus-5-5", "claude-opus-5-5[1m]"], "claude-opus-5")]
    assert ids == ["claude-opus-5", "claude-opus-5-5"]
    # present already: its position is kept
    ids = [m.id for m in offered(OFFERED, "claude-opus-5-5")]
    assert ids == ["claude-sonnet-5", "claude-opus-5-5", "claude-fable-5-1"]


def test_offered_warns_when_a_model_outgrows_the_review_fork(caplog):
    with caplog.at_level(logging.WARNING):
        offered(["claude-sonnet-5"], "claude-sonnet-5", fork_model="claude-sonnet-5")
    assert not caplog.records
    small = lookup("claude-sonnet-5")
    import dataclasses

    from claude_hermes.core import model_catalog
    tiny = dataclasses.replace(small, id="tiny", cli_id="tiny", window=1000)
    model_catalog._BY_SPELLING["tiny"] = tiny
    try:
        with caplog.at_level(logging.WARNING):
            offered(["claude-sonnet-5"], "claude-sonnet-5", fork_model="tiny")
        assert "review fork" in caplog.text
    finally:
        del model_catalog._BY_SPELLING["tiny"]


@pytest.mark.parametrize("key,shared", [
    ("tg:private:11", False),
    ("tg:private:11:t7", False),          # a private chat's topic is that user's own
    ("web:11:abc", False),
    ("tg:supergroup:-100123", True),
    ("tg:supergroup:-100123:t9", True),   # a forum topic is a room
    ("cron:4", True),
])
def test_which_lanes_are_shared(key, shared):
    assert lane_is_shared(key) is shared


def _resolve(thread=None, session=None, default=None, *, shared=False,
             deploy="claude-opus-5", offered_ids=OFFERED):
    return resolve(thread, session, default, shared=shared, deploy_model=deploy,
                   offered_ids=offered_ids)


def test_nothing_chosen_anywhere_is_the_deploy_default_with_no_effort():
    assert _resolve() == Choice("claude-opus-5", cli_id("claude-opus-5"), None)


def test_the_order_is_thread_then_live_session_then_default():
    thread, session = ("claude-fable-5-1", "max"), ("claude-opus-5-5", "low")
    default = ("claude-sonnet-5", "high")
    assert _resolve(thread, session, default).model == "claude-fable-5-1"
    assert _resolve(None, session, default) == Choice(
        "claude-opus-5-5", cli_id("claude-opus-5-5"), "low")
    assert _resolve(None, None, default).model == "claude-sonnet-5"


def test_a_shared_lane_never_takes_a_personal_default():
    assert _resolve(None, None, ("claude-fable-5-1", None), shared=True).model == "claude-opus-5"
    # an explicit choice on the lane still counts
    assert _resolve(("claude-fable-5-1", None), None, None, shared=True).model == "claude-fable-5-1"


def test_a_model_no_longer_offered_is_skipped_and_a_foreign_effort_dropped():
    gone = ("claude-opus-4-8", "high")
    assert _resolve(gone, None, ("claude-sonnet-5", "turbo")) == Choice(
        "claude-sonnet-5", cli_id("claude-sonnet-5"), None)


def test_the_deploy_default_is_always_usable_even_when_not_listed():
    session = ("claude-opus-5[1m]", None)          # what a pre-feature session ran
    assert _resolve(None, session, None, offered_ids=["claude-sonnet-5"]).model == "claude-opus-5"


def test_validate_refuses_what_resolve_would_ignore():
    validate(None, "high", OFFERED)                 # clearing is always allowed
    validate("claude-opus-5-5[1m]", "xhigh", OFFERED)
    with pytest.raises(ValueError, match="bad_model"):
        validate("claude-opus-4-8", None, OFFERED)
    with pytest.raises(ValueError, match="bad_effort"):
        validate("claude-sonnet-5", "turbo", OFFERED)


@pytest.mark.parametrize("text,hit", [
    ("/model claude-fable-5-1", True), ("  /model", True), ("/model@your_bot x", True),
    ("/effort max", True), ("/models", False), ("tell me about /model", False),
    ("/modelling", False), ("", False), (None, False),
])
def test_is_model_command(text, hit):
    assert is_model_command(text) is hit


def test_every_callback_shape_fits_telegrams_64_bytes():
    for info in CATALOG:
        for data in (f"mdl:t:m:{info.id}", f"mdl:d:m:{info.id}"):
            assert len(data.encode()) <= 64, data
    for effort in (*EFFORTS, "auto"):
        assert len(f"mdl:t:e:{effort}".encode()) <= 64
    assert len(b"mdl:a:x:-") <= 64

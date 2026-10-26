"""Which model a turn runs on, what it says when that model is not available,
and what every event names afterwards.

The same fake ports `test_turns_service.py` drives the turn envelope with — no
database, no SDK — because all of this is decided in the application layer: the
executor is handed a pair and reports what answered, and everything else here is
the runner's arithmetic on those two facts.

`FakeExecutor` answers as whatever it was ASKED for, which is what the live
matrix saw every model do (story Dev Notes). A fake that answered as something
else is a SUBSTITUTION, and the tests that want one say so.
"""
from __future__ import annotations

import pytest

from claude_hermes.core.model_catalog import Choice, cli_id, lookup
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.domain.policies import _MODEL_COMMAND_REPLY

from tests.modules.turns.test_turns_service import (
    FakeExecutor,
    FakeSessions,
    make_service,
)

# The deployment's own model, and one a lane may be switched to.
DEPLOY = "claude-sonnet-5"
OTHER = "claude-opus-5-5"

SETTINGS = TurnSettings(model=DEPLOY, models=(DEPLOY, OTHER))


class FakeLaneModels:
    """`LaneModels` by shape only: one answer, and what it was asked."""

    def __init__(self, choice: Choice | None = None, *, boom: bool = False):
        self.calls: list[tuple[str, int]] = []
        self._choice = choice
        self._boom = boom

    async def effective(self, session_key, owner_id):
        self.calls.append((session_key, owner_id))
        if self._boom:
            raise RuntimeError("sessions is away")
        return self._choice


def service(**kw):
    kw.setdefault("settings", SETTINGS)
    return make_service(**kw)


def ends(parts) -> dict:
    return parts["bus"].events[-1][1]


def starts(parts) -> dict:
    return parts["bus"].events[0][1]


# --- resolution -------------------------------------------------------------


async def test_a_lane_that_chose_nothing_runs_the_deploy_default_with_no_effort():
    """AC #2. No port wired at all is a fork, or a composition from before this
    feature: the deploy default is what every lane ran then and runs now."""
    svc, parts = service()
    await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")

    [call] = parts["executor"].calls
    assert (call["model"], call["cli_model"], call["effort"]) == (
        DEPLOY, cli_id(DEPLOY), None)
    assert starts(parts)["model"] == DEPLOY and starts(parts)["effort"] is None
    assert ends(parts)["model"] == DEPLOY
    assert ends(parts)["effort"] is None and ends(parts)["fallback_from"] is None


async def test_the_resolved_pair_reaches_the_cli_and_both_events():
    """AC #4: the picker's choice is what the CLI is handed and what the events
    name — `turn.start` the request, `turn.end` the answer."""
    models = FakeLaneModels(Choice(OTHER, cli_id(OTHER), "high"))
    svc, parts = service(models=models)
    await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")

    assert models.calls == [("web:1:x", 1)]      # asked once, with the lane's owner
    [call] = parts["executor"].calls
    assert (call["model"], call["cli_model"], call["effort"]) == (
        OTHER, cli_id(OTHER), "high")
    assert starts(parts)["model"] == OTHER and starts(parts)["effort"] == "high"
    assert ends(parts)["model"] == OTHER and ends(parts)["effort"] == "high"


async def test_a_resolver_failure_costs_the_choice_and_not_the_reply():
    """A lane that cannot be resolved still answers — on the deploy default, which
    is where it was before anybody chose anything."""
    svc, parts = service(models=FakeLaneModels(boom=True))
    assert await svc.run_turn("web:1:x", "hi", user_id=1, channel="web") == "ok"
    assert parts["executor"].calls[0]["model"] == DEPLOY


async def test_a_resolver_that_answers_nothing_is_the_deploy_default_too():
    svc, parts = service(models=FakeLaneModels(None))
    await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")
    assert parts["executor"].calls[0]["model"] == DEPLOY


async def test_the_session_records_the_pair_that_actually_ran():
    """`sessions.model/effort` is what the resolver reads back as "what this live
    session runs", so it is the ANSWER, never the request."""
    models = FakeLaneModels(Choice(OTHER, cli_id(OTHER), "low"))
    svc, parts = service(models=models)
    await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")
    assert parts["sessions"].pairs == [(OTHER, "low")]


async def test_a_resumed_lane_records_the_pair_on_the_touch():
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01"})
    svc, parts = service(sessions=sessions,
                         models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), None)))
    await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")
    assert sessions.touched == ["web:1:x"] and sessions.pairs == [(OTHER, None)]


# --- every end names a model ------------------------------------------------


async def test_a_failed_turn_still_names_the_model_it_was_running():
    class Boom(FakeExecutor):
        async def execute(self, **kw):
            raise ValueError("something else went wrong")

    svc, parts = service(executor=Boom(),
                         models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), "max")))
    with pytest.raises(ValueError):
        await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")

    end = ends(parts)
    assert end["status"] == "error"
    # What it was RUNNING: the CLI never said what answered, and the requested
    # pair is the honest answer to "which model was this turn".
    assert (end["model"], end["effort"], end["fallback_from"]) == (OTHER, "max", None)


async def test_a_cancelled_turn_carries_the_same_keys():
    import asyncio

    started = asyncio.Event()

    class Slow(FakeExecutor):
        async def execute(self, **kw):
            started.set()
            await asyncio.sleep(10)

    svc, parts = service(executor=Slow(),
                         models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), None)))
    task = asyncio.create_task(svc.run_turn("web:1:x", "hi", channel="web"))
    await started.wait()
    svc.cancel("web:1:x")
    with pytest.raises(asyncio.CancelledError):
        await task

    end = ends(parts)
    assert end["status"] == "cancelled"
    assert (end["model"], end["effort"], end["fallback_from"]) == (OTHER, None, None)


# --- the /model guard -------------------------------------------------------


@pytest.mark.parametrize("text", ["/model", "/model claude-opus-5", "  /effort high",
                                  "/model@hermes_bot"])
async def test_a_model_command_never_reaches_the_cli(text):
    """AC #15. The CLI runs `/model` and `/effort` as its OWN local commands in
    non-interactive mode: it would move the session's model and record it nowhere.
    Every channel refuses one earlier; this is the backstop."""
    svc, parts = service()
    assert await svc.run_turn("web:1:x", text, user_id=1, channel="web") == \
        _MODEL_COMMAND_REPLY
    assert parts["executor"].calls == []
    # The web thread renders `text.block`s, so the answer has to be one.
    assert ("text.block", {"text": _MODEL_COMMAND_REPLY}) in parts["bus"].events
    end = ends(parts)
    assert end["status"] == "ok"
    # no model answered the canned reply, so the event names none (P5) and the
    # web renders no "which model answered" label under it
    assert (end["model"], end["effort"], end["fallback_from"]) == (None, None, None)


async def test_models_is_an_ordinary_message():
    """`/models` is not `/model`: the guard is anchored on a word boundary."""
    svc, parts = service()
    assert await svc.run_turn("web:1:x", "/models", user_id=1, channel="web") == "ok"
    assert parts["executor"].calls[0]["text"] == "/models"


# --- the fallback -----------------------------------------------------------


def notice_for(rejected: str, answered: str) -> str:
    return (f"{lookup(rejected).label} is unavailable right now"
            f" — {lookup(answered).label} answered instead.")


async def test_a_rejected_model_is_retried_once_on_the_deploy_default():
    """AC #12. The rejected call never reached a model, so the session is exactly
    where it was: the retry resumes the SAME id and nothing is reset."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01"})
    ex = FakeExecutor(reply="Done.", reject={OTHER})
    svc, parts = service(sessions=sessions, executor=ex,
                         models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), "high")))

    reply = await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")

    assert [c["model"] for c in ex.calls] == [OTHER, DEPLOY]
    assert [c["resume"] for c in ex.calls] == ["sdk-old", "sdk-old"]
    assert sessions.resets == []                     # a reset cannot fix a model
    # The retry drops the effort with the model: it was chosen for the other one.
    assert ex.calls[1]["effort"] is None
    notice = notice_for(OTHER, DEPLOY)
    assert reply == f"{notice}\n\nDone."
    assert ("text.block", {"text": notice}) in parts["bus"].events
    end = ends(parts)
    assert (end["status"], end["model"], end["effort"]) == ("ok", DEPLOY, None)
    assert end["fallback_from"] == OTHER
    assert sessions.pairs == [(DEPLOY, None)]


async def test_a_rejection_on_a_lane_with_no_session_retries_without_one():
    ex = FakeExecutor(reject={OTHER})
    svc, _ = service(executor=ex,
                     models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), None)))
    await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")
    assert [c["resume"] for c in ex.calls] == [None, None]


async def test_a_retry_that_is_rejected_too_ends_the_turn():
    """At most two executions per turn, and still no reset — the CLI's own
    sentence is what `turn.end.error` carries."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01"})
    ex = FakeExecutor(reject={OTHER, DEPLOY})
    svc, parts = service(sessions=sessions, executor=ex,
                         models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), None)))

    with pytest.raises(Exception, match=f"no access to {DEPLOY}"):
        await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")

    assert len(ex.calls) == 2 and sessions.resets == []
    end = ends(parts)
    assert end["status"] == "error" and end["error"] == f"no access to {DEPLOY}"
    assert end["fallback_from"] == OTHER


async def test_the_deploy_default_being_rejected_fails_without_a_reset():
    """There is nothing left to fall back to, and a reset cannot make a refused
    model exist — so the lane keeps its session and the user sees the reason."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01"})
    ex = FakeExecutor(reject={DEPLOY})
    svc, parts = service(sessions=sessions, executor=ex)

    with pytest.raises(Exception, match=f"no access to {DEPLOY}"):
        await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")

    assert len(ex.calls) == 1 and sessions.resets == []
    assert ends(parts)["error"] == f"no access to {DEPLOY}"


async def test_a_silent_substitution_is_announced_like_a_fallback():
    """`CLAUDE_CODE_NO_MODEL_FALLBACK=1` should make this impossible; if the CLI
    swaps a model anyway, P5 says the label names what ANSWERED and the reply says
    the swap happened."""
    ex = FakeExecutor(reply="Done.", ran_model=DEPLOY)
    svc, parts = service(executor=ex,
                         models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), "high")))

    reply = await svc.run_turn("web:1:x", "hi", user_id=1, channel="web")

    assert reply.startswith(notice_for(OTHER, DEPLOY))
    end = ends(parts)
    assert end["model"] == DEPLOY and end["fallback_from"] == OTHER


async def test_another_spelling_of_the_same_model_is_not_a_substitution():
    """`claude-opus-5[1m]` is how some CLI versions ask for Opus 5's full window;
    the API answers as `claude-opus-5`. Every comparison goes through `lookup`, so
    the two are one model and nothing is announced."""
    ex = FakeExecutor(reply="Done.", ran_model="claude-opus-5[1m]")
    svc, parts = service(executor=ex,
                         models=FakeLaneModels(Choice("claude-opus-5",
                                                      cli_id("claude-opus-5"), None)))

    assert await svc.run_turn("web:1:x", "hi", user_id=1, channel="web") == "Done."
    end = ends(parts)
    assert end["model"] == "claude-opus-5" and end["fallback_from"] is None


async def test_a_dead_cli_still_takes_the_reset_path():
    """The rejection branch must not swallow the failure that IS about the
    session: a CLI death on a resume resets once and retries from scratch, as it
    did before this feature."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01"})
    ex = FakeExecutor(fail_on_resume="sdk-old")
    svc, _ = service(sessions=sessions, executor=ex,
                     models=FakeLaneModels(Choice(OTHER, cli_id(OTHER), "high")))

    assert await svc.run_turn("web:1:x", "hi", user_id=1, channel="web") == "ok"
    assert [c["resume"] for c in ex.calls] == ["sdk-old", None]
    assert sessions.resets == ["web:1:x"]
    # the lane's model is unchanged by a reset: only the session was stale
    assert [c["model"] for c in ex.calls] == [OTHER, OTHER]

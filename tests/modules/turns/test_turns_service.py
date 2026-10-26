"""`TurnService` against fake ports — no database, no SDK, no wiki on disk.

This is the payoff of constructor injection: the whole turn envelope (events,
session binding, the resume retry, the post-turn reset) is exercised in-process.
"""
import asyncio

import pytest

from claude_hermes.core.events import TurnStats
from claude_hermes.core.model_catalog import Choice, lookup
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.application.turn_service import TurnService
from claude_hermes.modules.turns.domain.exceptions import ModelRejected
from claude_hermes.modules.turns.domain.policies import (
    _FALLBACK_REPLY,
    SHELL_CHANGED_NOTICE,
)


class FakeSessions:
    def __init__(self, row=None):
        self.row = row
        self.bound = []
        self.touched = []
        # What the lane was told to remember about the wiki index. A per-LANE
        # fact since Wave 3b, because the next turn on this lane may run in a
        # different process.
        self.index_mtimes = []
        # The lane owner each write carried (Wave 4a). `None` is "unscoped", which
        # is what a service built without a `deploy_owner` still answers.
        self.owners = []
        # The (model, effort) each write carried — the pair that actually RAN, so
        # the resolver can read it back as "what this live session runs".
        self.pairs = []
        # The `has_shell` each write stamped the session with (0009) — what the
        # next turn compares with the lane's grant.
        self.shells = []
        self.resets = []
        self.reset_alls = 0
        self.reset_owneds = []

    async def route(self, session_key, now):
        return self.row

    async def bind(self, session_key, sdk_session_id, system_append, now_iso, *,
                   index_mtime=None, owner_id=None, model=None, effort=None,
                   shell_enabled=None):
        self.bound.append((session_key, sdk_session_id, system_append))
        self.index_mtimes.append(index_mtime)
        self.owners.append(owner_id)
        self.pairs.append((model, effort))
        self.shells.append(shell_enabled)

    async def touch(self, session_key, now_iso, *, index_mtime=None, owner_id=None,
                    model=None, effort=None, shell_enabled=None):
        self.touched.append(session_key)
        self.index_mtimes.append(index_mtime)
        self.owners.append(owner_id)
        self.pairs.append((model, effort))
        self.shells.append(shell_enabled)

    async def reset(self, session_key):
        self.resets.append(session_key)
        self.row = None

    async def reset_all(self):
        self.reset_alls += 1

    async def reset_owned(self, owner_id, *, include_unowned=False):
        self.reset_owneds.append((owner_id, include_unowned))


class FakeMessages:
    def __init__(self, last=None):
        self.recorded = []
        self.owners = []
        self._last = last

    async def record(self, session_key, role, content, ts, *, owner_id=None):
        self.recorded.append((session_key, role, content))
        self.owners.append(owner_id)

    async def last(self, session_key, role=None):
        return self._last


class FakeThreads:
    def __init__(self):
        self.calls = []

    async def touch(self, session_key, channel, owner_user_id, now_iso, *, talked=False):
        self.calls.append((session_key, channel, owner_user_id, talked))


class FakeWiki:
    root = None

    def index(self):
        return "# Wiki index\n"

    def index_mtime(self):
        return 42.0


class FakeBus:
    def __init__(self):
        self.events = []
        self.turn_ids = []

    async def emit(self, session_key, turn_id, type, payload):
        self.events.append((type, payload))
        self.turn_ids.append(turn_id)
        return None

    def subscribe(self, session_key, listener):
        return lambda: None

    def snapshot(self, session_key):
        return []

    def is_running(self, session_key):
        return False


class FakeExecutor:
    """Returns the tuple the SDK loop returns:
    (text, sdk_session_id, stats, cost, usage, ran_model).

    `ran_model` defaults to whatever the call was ASKED for (`ctx.model`), which
    is what the live matrix saw every model do. A fake that answered as something
    else would be a substitution, and the runner announces those.
    """

    def __init__(self, *, reply="ok", session_id="sdk-1", fail_on_resume=None,
                 ran_model=None, reject=None):
        self.calls = []
        self._reply = reply
        self._session_id = session_id
        self._fail_on_resume = fail_on_resume
        self._ran_model = ran_model
        # Models this fake's CLI refuses, in the shape `SdkTurnExecutor` raises.
        self._reject = set(reject or ())

    def is_cli_death(self, error):
        return isinstance(error, RuntimeError)

    async def execute(self, *, user_text, resume_id, append, session_key, ctx,
                      attachments, index_seen, on_slash_commands, on_session_id=None):
        self.calls.append({"text": user_text, "resume": resume_id, "append": append,
                           "model": ctx.model, "cli_model": ctx.cli_model,
                           "effort": ctx.effort, "shell": ctx.has_shell,
                           "learning": ctx.learning_enabled})
        if ctx.model in self._reject:
            raise ModelRejected(ctx.model, f"no access to {ctx.model}")
        if self._fail_on_resume is not None and resume_id == self._fail_on_resume:
            raise RuntimeError("the CLI died")
        on_slash_commands(["compact"])
        ran = self._ran_model if self._ran_model is not None else ctx.model
        return self._reply, self._session_id, TurnStats(), 0.5, None, ran


def make_service(**kw):
    parts = {
        "settings": TurnSettings(),
        "sessions": kw.pop("sessions", FakeSessions()),
        "messages": kw.pop("messages", FakeMessages()),
        "threads": kw.pop("threads", FakeThreads()),
        "wiki": FakeWiki(),
        "bus": kw.pop("bus", FakeBus()),
        "executor": kw.pop("executor", FakeExecutor()),
    }
    parts.update(kw)
    return TurnService(**parts), parts


async def test_a_fresh_lane_binds_records_and_emits_the_envelope():
    svc, parts = make_service()
    reply = await svc.run_turn("web:1:x", "hello", user_id=1, channel="web")

    assert reply == "ok"
    assert [t for t, _ in parts["bus"].events] == [
        "turn.start", "user.message", "turn.end"]
    assert parts["bus"].events[-1][1]["status"] == "ok"
    assert parts["bus"].events[-1][1]["cost_usd"] == 0.5
    # a fresh lane is BOUND, not touched, and the append it was bound with is the
    # one the wiki index produced
    assert parts["sessions"].bound[0][1] == "sdk-1"
    assert "Wiki index" in parts["sessions"].bound[0][2]
    assert svc._index_seen["web:1:x"] == 42.0
    assert parts["threads"].calls == [("web:1:x", "web", 1, True)]
    assert [r[1] for r in parts["messages"].recorded] == ["user", "assistant"]
    assert svc.slash_commands == ["compact"]


async def test_a_resumed_lane_reuses_the_stored_append_and_only_touches():
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "FROZEN",
                                 "updated_at": "2026-01-01"})
    svc, parts = make_service(sessions=sessions)
    await svc.run_turn("tg:private:1", "hi")
    assert parts["executor"].calls[0]["resume"] == "sdk-old"
    assert parts["executor"].calls[0]["append"] == "FROZEN"
    assert sessions.bound == [] and sessions.touched == ["tg:private:1"]


async def test_a_dead_cli_on_a_resume_resets_once_and_retries_fresh():
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "FROZEN",
                                 "updated_at": "2026-01-01"})
    ex = FakeExecutor(fail_on_resume="sdk-old")
    svc, _ = make_service(sessions=sessions, executor=ex)
    assert await svc.run_turn("tg:private:1", "hi") == "ok"
    assert [c["resume"] for c in ex.calls] == ["sdk-old", None]
    assert sessions.resets == ["tg:private:1"]


async def test_a_dead_cli_without_a_session_to_blame_propagates():
    ex = FakeExecutor(fail_on_resume=None)

    class Boom(FakeExecutor):
        async def execute(self, **kw):
            raise RuntimeError("the CLI died")

    svc, parts = make_service(executor=Boom())
    with pytest.raises(RuntimeError):
        await svc.run_turn("tg:private:1", "hi")
    assert parts["bus"].events[-1][1]["status"] == "error"
    assert ex.calls == []


async def test_an_empty_reply_falls_back_rather_than_sending_a_blank_message():
    svc, _ = make_service(executor=FakeExecutor(reply=None))
    assert await svc.run_turn("tg:private:1", "hi") == _FALLBACK_REPLY


async def test_a_tool_that_raised_reset_lane_resets_after_the_bind():
    """Order matters: the session row is written back first, so a reset before the
    bind would simply be undone."""
    sessions = FakeSessions()

    class Flagging(FakeExecutor):
        async def execute(self, *, ctx, **kw):
            ctx.flags.reset_lane = True
            return await super().execute(ctx=ctx, **kw)

    svc, _ = make_service(sessions=sessions, executor=Flagging())
    await svc.run_turn("tg:private:1", "hi")
    assert sessions.bound and sessions.resets == ["tg:private:1"]


async def test_reset_all_does_not_take_the_lane_lock():
    sessions = FakeSessions()
    svc, _ = make_service(sessions=sessions)
    async with svc._locks["tg:private:1"]:
        await asyncio.wait_for(svc.reset_all(), timeout=2)
    assert sessions.reset_alls == 1


async def test_reset_owned_passes_the_owner_through_and_takes_no_lane_lock():
    """Wave 4b, B-4: one user's skill edit evicts THAT user's lanes, so the
    service forwards the owner and the NULL belt untouched — and, like
    `reset_all`, does not wait behind a running turn."""
    sessions = FakeSessions()
    svc, _ = make_service(sessions=sessions)
    async with svc._locks["web:2:x"]:
        await asyncio.wait_for(svc.reset_owned(2), timeout=2)
        await asyncio.wait_for(svc.reset_owned(1, include_unowned=True), timeout=2)
    assert sessions.reset_owneds == [(2, False), (1, True)]
    assert sessions.reset_alls == 0


async def test_cancel_reports_whether_there_was_a_turn():
    started = asyncio.Event()

    class Slow(FakeExecutor):
        async def execute(self, **kw):
            started.set()
            await asyncio.sleep(10)

    svc, parts = make_service(executor=Slow())
    task = asyncio.create_task(svc.run_turn("web:1:x", "hi", channel="web"))
    await started.wait()
    assert svc.is_busy("web:1:x") is True
    assert svc.cancel("web:1:x") is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert parts["bus"].events[-1][1]["status"] == "cancelled"
    assert svc.cancel("web:1:x") is False


async def test_a_cancelled_first_turn_keeps_its_session():
    """Stop mid-turn, then write again: the next turn resumes, not restarts."""
    started = asyncio.Event()

    class Slow(FakeExecutor):
        async def execute(self, *, on_session_id, **kw):
            on_session_id("sid-interrupted")
            started.set()
            await asyncio.sleep(10)

    sessions = FakeSessions()
    svc, _ = make_service(executor=Slow(), sessions=sessions)
    task = asyncio.create_task(svc.run_turn("web:1:x", "hi", channel="web"))
    await started.wait()
    svc.cancel("web:1:x")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [b[1] for b in sessions.bound] == ["sid-interrupted"]


async def test_a_bus_failure_never_kills_the_turn():
    class BadBus(FakeBus):
        async def emit(self, *a, **kw):
            raise RuntimeError("bus down")

    svc, _ = make_service(bus=BadBus())
    assert await svc.run_turn("tg:private:1", "hi") == "ok"


async def test_an_observer_failure_never_kills_the_reply():
    class Boom:
        async def after_turn(self, session_key, stats, *, owner_id=0):
            raise RuntimeError("learning down")

    svc, _ = make_service(observer=Boom())
    assert await svc.run_turn("tg:private:1", "hi") == "ok"


async def test_the_cron_report_is_quoted_into_the_first_reply_after_a_delivery():
    class Jobs:
        async def job_for_thread(self, chat_id, thread_id):
            return {"id": 7, "name": "content-radar"}

    messages = FakeMessages(last={"ts": "2026-09-01T00:00:00+00:00",
                                  "content": "1. topic one"})
    svc, parts = make_service(messages=messages, jobs=Jobs())
    await svc.run_turn("tg:private:1:t57074", "1")
    sent = parts["executor"].calls[0]["text"]
    assert 'scheduled job "content-radar"' in sent and "topic one" in sent
    assert sent.rstrip().endswith("1")


async def test_a_lane_that_already_replied_after_the_delivery_is_not_reinjected():
    class Jobs:
        async def job_for_thread(self, chat_id, thread_id):
            return {"id": 7, "name": "content-radar"}

    messages = FakeMessages(last={"ts": "2026-09-01T00:00:00+00:00", "content": "brief"})
    sessions = FakeSessions(row={"sdk_session_id": "s", "system_append": "A",
                                 "updated_at": "2026-09-02T00:00:00+00:00"})
    svc, parts = make_service(messages=messages, sessions=sessions, jobs=Jobs())
    await svc.run_turn("tg:private:1:t57074", "1")
    assert parts["executor"].calls[0]["text"] == "1"


async def test_a_job_lookup_failure_still_runs_the_turn():
    class Jobs:
        async def job_for_thread(self, chat_id, thread_id):
            raise RuntimeError("db went away")

    svc, parts = make_service(jobs=Jobs())
    assert await svc.run_turn("tg:private:1:t1", "3") == "ok"
    assert parts["executor"].calls[0]["text"] == "3"


# --- the turn id, the index mtime and the slash-command list ----------------
# Three things Wave 3b moved off this object, because the process that runs a
# turn is no longer the process that started it or the one that will answer a
# question about it afterwards.


class FakeCommands:
    def __init__(self):
        self.saved = []

    async def sdk_commands(self):
        return list(self.saved[-1]) if self.saved else []

    async def save(self, names):
        self.saved.append(list(names))


async def test_the_turn_id_from_the_row_is_the_one_every_event_carries():
    """The 202 hands a client a turn id; the events it then streams have to name
    the same turn, or the SPA cannot match them up."""
    svc, parts = make_service()
    await svc.run_turn("web:1:x", "hi", turn_id="row-1")
    assert parts["bus"].turn_ids == ["row-1", "row-1", "row-1"]


async def test_an_omitted_turn_id_is_generated_so_legacy_callers_are_unchanged():
    svc, parts = make_service()
    await svc.run_turn("web:1:x", "hi")
    assert len(set(parts["bus"].turn_ids)) == 1
    assert parts["bus"].turn_ids[0]


async def test_a_fresh_lane_writes_the_index_mtime_it_was_bound_with():
    """`sessions.index_mtime` is the lane's own record of which wiki index its
    cached prefix was built from. Left in this process it would be re-injected on
    every turn that lands on a worker that has not seen the lane."""
    svc, parts = make_service()
    await svc.run_turn("web:1:x", "hi")
    assert parts["sessions"].index_mtimes == [42.0]


async def test_a_resumed_lane_seeds_the_index_mtime_from_its_row():
    """The whole point of the column: this process has never seen the lane, and
    without the seed the hook would inject the entire index into the user message
    — 8000 characters of uncached tokens on a turn that needed none."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01", "index_mtime": 41.0})
    svc, parts = make_service(sessions=sessions)
    await svc.run_turn("tg:private:1", "hi")

    assert svc._index_seen["tg:private:1"] == 41.0
    assert sessions.index_mtimes == [41.0]          # written back by `touch`


async def test_a_newer_row_wins_over_what_this_process_remembers():
    """The row, not the local dict, is the truth. Another worker can show the lane
    a newer index and write the column back while this process still holds an
    older mtime; seeding "only when we have no entry" would then re-inject the
    whole index once per worker after every index change."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01", "index_mtime": 41.0})
    svc, _ = make_service(sessions=sessions)
    svc._index_seen["tg:private:1"] = 30.0          # what this worker last saw

    await svc.run_turn("tg:private:1", "hi")

    assert svc._index_seen["tg:private:1"] == 41.0


async def test_an_older_row_never_drags_this_process_backwards():
    """`max`, not "the row wins": a turn running here may have bumped the local
    value mid-flight, and re-injecting on the next turn would undo that."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01", "index_mtime": 41.0})
    svc, _ = make_service(sessions=sessions)
    svc._index_seen["tg:private:1"] = 50.0

    await svc.run_turn("tg:private:1", "hi")

    assert svc._index_seen["tg:private:1"] == 50.0


async def test_a_row_with_no_index_mtime_yet_leaves_the_column_alone():
    """`None` means "nothing to write" — a lane bound before the column existed
    must not be told it has seen an index it has not."""
    sessions = FakeSessions(row={"sdk_session_id": "sdk-old", "system_append": "A",
                                 "updated_at": "2026-01-01", "index_mtime": None})
    svc, _ = make_service(sessions=sessions)
    await svc.run_turn("tg:private:1", "hi")

    assert "tg:private:1" not in svc._index_seen
    assert sessions.index_mtimes == [None]


async def test_the_reported_slash_commands_are_saved_when_they_change():
    commands = FakeCommands()
    svc, _ = make_service(commands_store=commands)

    await svc.run_turn("web:1:x", "hi")
    assert commands.saved == [["compact"]]

    # The second turn reports the same list: comparing against the in-process
    # copy is what keeps this from being a meta write per turn.
    await svc.run_turn("web:1:x", "again")
    assert commands.saved == [["compact"]]


async def test_a_commands_store_failure_never_costs_the_reply():
    class Broken(FakeCommands):
        async def save(self, names):
            raise RuntimeError("meta is away")

    svc, _ = make_service(commands_store=Broken())
    assert await svc.run_turn("web:1:x", "hi") == "ok"



# --- per-user grants and the lazy reset (0009) --------------------------------


class Grant:
    """What identity's `Grants` looks like from here: two attributes."""

    def __init__(self, shell=False, learning=False):
        self.shell, self.learning = shell, learning


class FakeGrants:
    """`LaneGrants` by shape: one mutable answer, the config default, and a record
    of every question — the owner's lanes must never ask, and a non-owner lane
    asks once per turn, never from a cache."""

    def __init__(self, *, shell=False, learning=False, default_shell=False,
                 boom=False):
        self.answer = Grant(shell, learning)
        self.default = Grant(default_shell, False)
        self.asked: list[int] = []
        self.defaults_asked = 0
        self._boom = boom

    async def grants(self, user_id):
        self.asked.append(user_id)
        if self._boom:
            raise RuntimeError("identity is away")
        return self.answer

    async def grant_defaults(self):
        self.defaults_asked += 1
        return self.default


class UserWorkspace:
    """A non-owner's tree, as far as `_is_owner_lane` looks: its root is not home."""
    root = "users/2"
    home = "home"


USER_LANE = "web:2:x"                      # `lane_owner` reads 2 out of the key


def resumable(**extra) -> dict:
    """A live session row, as `SessionRouter.route` hands it back."""
    return {"sdk_session_id": "sdk-old", "system_append": "FROZEN",
            "updated_at": "2026-01-01", **extra}


async def user_turn(svc, text="hi"):
    return await svc.run_turn(USER_LANE, text, user_id=2, channel="web",
                              workspace=UserWorkspace())


def blocks(parts) -> list[str]:
    return [p["text"] for t, p in parts["bus"].events if t == "text.block"]


class Watching(FakeExecutor):
    """Remembers which events had been emitted when the CLI was called — the
    notice has to be on the wire before the answer starts."""

    def __init__(self, bus, **kw):
        super().__init__(**kw)
        self._bus = bus
        self.seen_before: list[list[str]] = []

    async def execute(self, **kw):
        self.seen_before.append([t for t, _ in self._bus.events])
        return await super().execute(**kw)


async def test_grants_are_resolved_per_turn_so_a_change_applies_on_the_next():
    """No rebuild, no restart: the port is asked on every non-owner turn, with the
    lane's owner, and what it says is what the tools and the stamp are built on."""
    grants = FakeGrants(shell=True, learning=True)
    svc, parts = make_service(grants=grants)
    await user_turn(svc)
    grants.answer = Grant(shell=False, learning=False)       # the owner flips both
    await user_turn(svc)

    calls = parts["executor"].calls
    assert [(c["shell"], c["learning"]) for c in calls] == [(True, True), (False, False)]
    assert grants.asked == [2, 2]
    assert parts["sessions"].shells == [True, False]         # each bind stamps has_shell


async def test_an_owner_lane_never_asks_for_grants():
    """`is_owner_lane` already gives the deploy owner's lanes everything; an unstamped
    session of theirs is compared with `True` and resumed without a query."""
    grants = FakeGrants(shell=False)
    sessions = FakeSessions(row=resumable())                  # stamp is NULL
    svc, parts = make_service(grants=grants, sessions=sessions)
    await svc.run_turn("tg:private:1", "hi", user_id=1)
    assert grants.asked == [] and grants.defaults_asked == 0
    assert parts["executor"].calls[0]["shell"] is True
    assert parts["executor"].calls[0]["resume"] == "sdk-old"
    assert sessions.resets == [] and sessions.shells == [True]


async def test_a_failing_grants_lookup_ends_the_turn_as_an_error_not_a_guess():
    """A default substituted for a failed lookup could reset the lane under a notice
    that lies. The turn has started, so it ends — with the failure named."""
    grants = FakeGrants(boom=True)
    sessions = FakeSessions(row=resumable(shell_enabled=True))
    svc, parts = make_service(grants=grants, sessions=sessions)
    with pytest.raises(RuntimeError, match="identity is away"):
        await user_turn(svc)
    events = parts["bus"].events
    assert [t for t, _ in events] == ["turn.start", "user.message", "turn.end"]
    assert events[-1][1]["status"] == "error"
    assert "identity is away" in events[-1][1]["error"]
    assert parts["executor"].calls == []
    assert sessions.resets == [] and sessions.bound == [] and sessions.touched == []


async def test_a_moved_shell_grant_resets_the_lane_and_says_so_first():
    """P1 and P5 together: the old session's toolset had no shell, so it cannot be
    resumed — the store's `reset` (the lane lock is already held), a fresh append,
    the notice on the wire before the answer, the reply prefixed for Telegram, and
    the new session stamped with what it now runs."""
    bus = FakeBus()
    sessions = FakeSessions(row=resumable(shell_enabled=False))
    ex = Watching(bus)
    svc, parts = make_service(grants=FakeGrants(shell=True), sessions=sessions,
                              bus=bus, executor=ex)
    reply = await user_turn(svc)

    assert sessions.resets == [USER_LANE]
    assert ex.calls[0]["resume"] is None
    assert "Wiki index" in ex.calls[0]["append"]              # built fresh, not FROZEN
    assert ex.seen_before[0][-1] == "text.block"              # emitted before the CLI ran
    assert blocks(parts) == [SHELL_CHANGED_NOTICE]
    assert reply == f"{SHELL_CHANGED_NOTICE}\n\nok"
    assert sessions.bound[0][1] == "sdk-1" and sessions.touched == []
    assert sessions.shells == [True]
    assert parts["messages"].recorded[-1][2] == reply
    assert parts["bus"].events[-1][1]["status"] == "ok"


async def test_withdrawing_the_shell_resets_the_lane_too():
    sessions = FakeSessions(row=resumable(shell_enabled=True))
    svc, parts = make_service(grants=FakeGrants(shell=False), sessions=sessions)
    reply = await user_turn(svc)
    assert sessions.resets == [USER_LANE] and sessions.shells == [False]
    assert parts["executor"].calls[0]["shell"] is False
    assert reply.startswith(SHELL_CHANGED_NOTICE)


async def test_a_matching_stamp_resumes_without_a_word():
    grants = FakeGrants(shell=True)
    sessions = FakeSessions(row=resumable(shell_enabled=True))
    svc, parts = make_service(grants=grants, sessions=sessions)
    reply = await user_turn(svc)
    assert reply == "ok" and blocks(parts) == []
    assert parts["executor"].calls[0]["resume"] == "sdk-old"
    assert sessions.resets == [] and sessions.touched == [USER_LANE]
    assert sessions.shells == [True]
    assert grants.defaults_asked == 0                        # a stamp needs no default


@pytest.mark.parametrize("default_shell", [False, True])
async def test_an_unstamped_session_equal_to_the_default_is_resumed_and_stamped(
        default_shell):
    """A session from before 0009 ran under `is_owner_lane or users_shell_enabled`.
    A user who still inherits that default has the same toolset now: resume, and
    stamp it so the next turn compares against a real value."""
    grants = FakeGrants(shell=default_shell, default_shell=default_shell)
    sessions = FakeSessions(row=resumable())                  # no shell_enabled key
    svc, parts = make_service(grants=grants, sessions=sessions)
    reply = await user_turn(svc)
    assert reply == "ok" and blocks(parts) == []
    assert parts["executor"].calls[0]["resume"] == "sdk-old"
    assert sessions.resets == [] and sessions.shells == [default_shell]
    assert grants.defaults_asked == 1

    sessions.row = resumable(shell_enabled=None)              # a NULL column reads the same
    await user_turn(svc)
    assert sessions.resets == [] and grants.defaults_asked == 2


async def test_an_unstamped_session_with_a_differing_explicit_grant_is_reset():
    """The owner granted a shell after the upgrade: the pre-0009 session was built
    without one, so it starts over — with the notice."""
    grants = FakeGrants(shell=True, default_shell=False)
    sessions = FakeSessions(row=resumable())
    svc, parts = make_service(grants=grants, sessions=sessions)
    reply = await user_turn(svc)
    assert sessions.resets == [USER_LANE]
    assert parts["executor"].calls[0]["resume"] is None
    assert blocks(parts) == [SHELL_CHANGED_NOTICE]
    assert reply == f"{SHELL_CHANGED_NOTICE}\n\nok"
    assert sessions.shells == [True]


async def test_the_shell_notice_and_a_model_fallback_notice_share_the_reply():
    """Both lines, in the order they were emitted, a blank line apart."""
    settings = TurnSettings(model="claude-sonnet-5")
    ex = FakeExecutor(ran_model="claude-opus-5-5")            # a substitution
    sessions = FakeSessions(row=resumable(shell_enabled=False))
    svc, parts = make_service(settings=settings, executor=ex, sessions=sessions,
                              grants=FakeGrants(shell=True))
    reply = await user_turn(svc)
    shown = blocks(parts)
    assert len(shown) == 2 and shown[0] == SHELL_CHANGED_NOTICE
    assert "answered instead" in shown[1]
    assert reply == "\n\n".join([*shown, "ok"])


async def test_a_model_retry_keeps_the_grants_without_asking_again():
    """The `ModelRejected` retry replaces the model on the context, not the grants:
    one lookup per turn, whatever the executor does."""
    other, deploy = lookup("claude-opus-5-5"), lookup("claude-sonnet-5")

    class Models:
        async def effective(self, session_key, owner_id):
            return Choice(other.id, other.cli_id, None)

    grants = FakeGrants(shell=True, learning=True)
    ex = FakeExecutor(reject={other.id})
    svc, _ = make_service(settings=TurnSettings(model=deploy.id), models=Models(),
                          executor=ex, grants=grants)
    await user_turn(svc)
    assert [c["model"] for c in ex.calls] == [other.id, deploy.id]
    assert [(c["shell"], c["learning"]) for c in ex.calls] == [(True, True)] * 2
    assert grants.asked == [2]


async def test_a_service_without_the_port_gives_a_user_lane_nothing():
    """A fork or a test built without `grants=`: fail closed, and an unstamped
    session compares with the same False, so nothing is reset."""
    sessions = FakeSessions(row=resumable())
    svc, parts = make_service(sessions=sessions)
    await user_turn(svc)
    assert parts["executor"].calls[0]["shell"] is False
    assert parts["executor"].calls[0]["resume"] == "sdk-old"
    assert sessions.resets == [] and sessions.shells == [False]

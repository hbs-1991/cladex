"""`LearningLoop.after_turn`: bump the sidecar, feed the tracker, dispatch a review.

Wave 3a retargeted this file. The loop no longer holds the review — it holds a
`ReviewDispatcher` (where the pass goes) and reads a `ReviewGate` (whether one is
already in flight), so the fakes here are those two ports rather than a reviewer.
That is the whole point of the change: the single-flight rule used to be an
`asyncio.Task` handle only this process could see, and it is now a lease anybody
can read.

Wave 4b retargeted it again: the loop is a router over `LearningScopes`.
`after_turn(owner_id=)` picks THAT user's sidecar, tracker and gate, and the
dispatch carries the id so the worker runs the review in the same scope. The
scopes here are fakes with exactly the four fields the loop reads; the registry
itself is `tests/modules/learning/test_learning_scopes.py`. The deprecated
`reviewer=` constructor is gone with the constructor it hung off (plan R-3).
"""
from types import SimpleNamespace

from claude_hermes.core.config import Config
from claude_hermes.core.events import TurnStats
from claude_hermes.core.meta import MetaStore
from claude_hermes.modules.learning.application.services.loop import LearningLoop
from claude_hermes.modules.learning.application.services.nudges import NudgeTracker
from claude_hermes.modules.learning.infrastructure.scoped_meta import ScopedMeta
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar


class FakeGate:
    """`ReviewGate` as a boolean. The real one is an expiry in `meta`; what the
    loop cares about is only whether somebody is reviewing right now."""

    def __init__(self, running=False):
        self.claimed = running

    async def running(self):
        return self.claimed

    async def claim(self):
        self.claimed = True

    async def release(self):
        self.claimed = False


class FakeDispatcher:
    """`ReviewDispatcher`. Records exactly what it was handed — the port promises
    primitives, and a `set` would not survive the trip to a worker.

    It does NOT claim the gate: in production `ReviewService` does that when the
    pass actually starts, possibly in another process, and pretending otherwise
    here would hide the window these tests are about.
    """

    def __init__(self):
        self.calls = []

    async def dispatch(self, session_key, kinds, owner_id=0):
        self.calls.append((session_key, kinds, owner_id))


class FakeScopes:
    """`LearningScopes` over a dict: `0` is the owner, like the real registry."""

    def __init__(self, owner: int, scopes: dict):
        self._owner = owner
        self._scopes = scopes
        self.asked: list[int] = []

    async def for_user(self, user_id):
        self.asked.append(user_id)
        return self._scopes[user_id or self._owner]


def scope_for(tmp_path, uid, meta, cfg, *, gate=None):
    """One scope's four loop-facing fields over its own tree and meta suffix."""
    skills = tmp_path / ("agent_home" if uid == 1 else f"users/{uid}/agent_home") \
        / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    scoped = ScopedMeta(meta, "" if uid == 1 else f":{uid}")
    return SimpleNamespace(user_id=uid, sidecar=UsageSidecar(skills),
                           nudges=NudgeTracker(scoped, cfg), gate=gate or FakeGate())


def make(tmp_path, engine, *, gate=None, dispatcher=None, users=(), **cfg_kw):
    cfg = Config(**{"wiki_nudge_interval": 2, "skill_nudge_interval": 100, **cfg_kw})
    meta = MetaStore(engine)
    gate = gate or FakeGate()
    dispatcher = dispatcher or FakeDispatcher()
    owner = scope_for(tmp_path, 1, meta, cfg, gate=gate)
    scopes = FakeScopes(1, {1: owner, **{u: scope_for(tmp_path, u, meta, cfg)
                                         for u in users}})
    loop = LearningLoop(scopes, dispatcher)
    return loop, owner.sidecar, dispatcher, gate, scopes


async def test_after_turn_dispatches_a_review_on_a_nudge(tmp_path, engine):
    ll, _, dispatcher, _, _ = make(tmp_path, engine)
    await ll.after_turn("tg:private:1", TurnStats())
    assert dispatcher.calls == []                    # 1 of 2
    await ll.after_turn("tg:private:1", TurnStats())
    assert dispatcher.calls == [("tg:private:1", ["wiki"], 1)]


async def test_after_turn_routes_to_the_lane_owners_scope(tmp_path, engine):
    """User 7's turn bumps USER 7's sidecar and counters, and the dispatch
    carries 7 so the worker resumes their session in their tree. The owner's
    sidecar and counter are untouched: nobody learns from anybody else."""
    ll, owner_sidecar, dispatcher, _, scopes = make(tmp_path, engine, users=(7,))
    seven = (await scopes.for_user(7)).sidecar
    await ll.after_turn("tg:private:7", TurnStats(skill_invocations=["deploy"]),
                        owner_id=7)
    await ll.after_turn("tg:private:7", TurnStats(), owner_id=7)
    assert seven.get("deploy")["use_count"] == 1
    assert owner_sidecar.get("deploy") is None
    assert dispatcher.calls == [("tg:private:7", ["wiki"], 7)]
    # The owner's counter never moved: their next turn is still "1 of 2".
    await ll.after_turn("tg:private:1", TurnStats())
    assert len(dispatcher.calls) == 1


async def test_a_gate_is_per_user(tmp_path, engine):
    """User 7's review in flight must not drop the owner's nudge, and vice
    versa — that was the one-lease world, and it is what the deploy-wide slot
    (not the gate) bounds now."""
    ll, _, dispatcher, owner_gate, scopes = make(tmp_path, engine, users=(7,))
    await owner_gate.claim()
    await ll.after_turn("tg:private:7", TurnStats(), owner_id=7)
    await ll.after_turn("tg:private:7", TurnStats(), owner_id=7)
    assert dispatcher.calls == [("tg:private:7", ["wiki"], 7)]
    assert await ll.review_in_flight(7) is True         # claimed at dispatch
    assert await ll.review_in_flight() is True          # the owner's, from before


async def test_kinds_travel_as_a_sorted_list_not_a_set(tmp_path, engine):
    """The argument has to survive being JSON on a queue, and the same nudge has
    to produce the same payload every time."""
    ll, _, dispatcher, _, _ = make(tmp_path, engine, skill_nudge_interval=2)
    await ll.after_turn("k", TurnStats(tool_uses=1))
    await ll.after_turn("k", TurnStats(tool_uses=1))
    _, kinds, _ = dispatcher.calls[0]
    assert kinds == ["skills", "wiki"]
    assert isinstance(kinds, list)


async def test_a_nudge_while_the_gate_is_claimed_is_dropped(tmp_path, engine):
    """Dropped, not queued: two forks writing one skill tree is the failure the
    etag precondition in the browser exists to make visible."""
    gate = FakeGate(running=True)
    ll, _, dispatcher, _, _ = make(tmp_path, engine, gate=gate)
    await ll.after_turn("k", TurnStats())
    await ll.after_turn("k", TurnStats())            # due, but a review is running
    assert dispatcher.calls == []


async def test_the_next_nudge_after_the_gate_clears_dispatches(tmp_path, engine):
    gate = FakeGate(running=True)
    ll, _, dispatcher, _, _ = make(tmp_path, engine, gate=gate)
    await ll.after_turn("k", TurnStats())
    await ll.after_turn("k", TurnStats())            # dropped
    await gate.release()
    await ll.after_turn("k", TurnStats())
    await ll.after_turn("k", TurnStats())            # due again, and free
    assert len(dispatcher.calls) == 1


async def test_review_in_flight_reads_the_gate(tmp_path, engine):
    gate = FakeGate()
    ll, _, _, _, _ = make(tmp_path, engine, gate=gate)
    assert await ll.review_in_flight() is False
    await gate.claim()
    assert await ll.review_in_flight() is True


async def test_after_turn_bumps_skill_usage(tmp_path, engine):
    ll, sidecar, _, _, _ = make(tmp_path, engine)
    await ll.after_turn("k", TurnStats(skill_invocations=["deploy"]))
    assert sidecar.get("deploy")["use_count"] == 1


async def test_after_turn_bumps_skill_patches(tmp_path, engine):
    ll, sidecar, _, _, _ = make(tmp_path, engine)
    await ll.after_turn("k", TurnStats(skill_patches=["deploy"]))
    assert sidecar.get("deploy")["patch_count"] == 1
    assert sidecar.get("deploy")["last_patched"] is not None

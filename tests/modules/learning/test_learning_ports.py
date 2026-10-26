"""The ports learning consumes, exercised with fakes.

The point of these tests is what they do NOT import: no `Database`, no wiki
directory, no `claude_agent_sdk`. If one of them ever needs a real collaborator
again, a module boundary has leaked.
"""
from __future__ import annotations

from types import SimpleNamespace

from claude_hermes.core.config import Config
from claude_hermes.core.events import TurnObservation, TurnStats
from claude_hermes.modules.learning.application.services.loop import LearningLoop
from claude_hermes.modules.learning.application.services.nudges import NudgeTracker
from claude_hermes.modules.learning.application.services.review import ReviewService
from claude_hermes.modules.learning.domain.constants import (
    SKILL_NUDGE_META_KEY,
    WIKI_NUDGE_META_KEY,
)
from claude_hermes.modules.learning.infrastructure.usage_sidecar import UsageSidecar


class FakeMeta:
    def __init__(self, **rows):
        self.rows = dict(rows)

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


class FakeSessions:
    """`SessionSnapshotPort`: two fields out of the sessions module's row, and
    nothing else. Learning never selects from the `sessions` table itself."""

    def __init__(self, row=None):
        self.row = row
        self.asked = []

    async def get(self, session_key):
        self.asked.append(session_key)
        return self.row


class FakeFork:
    def __init__(self, final="done", creates=()):
        self.final = final
        self.creates = creates
        self.kwargs = None

    async def run_review(self, **kwargs):
        self.kwargs = kwargs
        for name in self.creates:
            # what the write guard does for a skill the fork actually wrote
            kwargs["touched"].add(name)
            d = kwargs["skills_dir"] / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: X.\n---\n")
        return self.final

    async def run_consolidation(self, **kwargs):  # pragma: no cover
        raise AssertionError("not this pass")


def sidecar_at(tmp_path):
    skills = tmp_path / "agent_home" / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    return skills, UsageSidecar(skills)


def service(tmp_path, fork, row):
    skills, sidecar = sidecar_at(tmp_path)
    svc = ReviewService(sessions=FakeSessions(row), usage=sidecar, fork=fork,
                        skills_dir=skills, wiki_root=tmp_path / "agent_home" / "wiki")
    return svc, sidecar, skills


# ------------------------------------------------------------ review -------

async def test_review_resumes_the_lanes_own_session(tmp_path):
    fork = FakeFork()
    svc, _, skills = service(
        tmp_path, fork, {"sdk_session_id": "sdk-abc", "system_append": "APPEND"})

    assert await svc.run("tg:private:1", {"wiki", "skills"}) == "done"
    # byte-identical append → the fork hits the warm cache the turn built
    assert fork.kwargs["system_append"] == "APPEND"
    assert fork.kwargs["resume"] == "sdk-abc"
    assert "wiki/index.md" in fork.kwargs["prompt"]
    assert "SKILL.md" in fork.kwargs["prompt"]
    assert fork.kwargs["workspace"] is None           # no scope → the owner's tree


async def test_review_hands_its_scopes_workspace_to_the_fork(tmp_path):
    """The fork runs in the SCOPE's tree (D4): a non-owner's review resumes
    their session in their `CLAUDE_CONFIG_DIR`, not the owner's."""
    fork = FakeFork()
    skills, sidecar = sidecar_at(tmp_path)
    ws = object()
    svc = ReviewService(sessions=FakeSessions({"sdk_session_id": "s", "system_append": ""}),
                        usage=sidecar, fork=fork, skills_dir=skills,
                        wiki_root=tmp_path / "wiki", workspace=ws)
    await svc.run("k", {"wiki"})
    assert fork.kwargs["workspace"] is ws
    assert fork.kwargs["owner_id"] == 0        # no id wired → nothing is indexed


async def test_review_hands_its_scopes_user_id_to_the_fork(tmp_path):
    """The fork flushes the pages it wrote into the wiki index, and the rows are
    keyed `(owner_id, path)`. The workspace does not name its owner — `users/7`
    is a directory — so the SCOPE's id is what travels."""
    fork = FakeFork()
    skills, sidecar = sidecar_at(tmp_path)
    svc = ReviewService(sessions=FakeSessions({"sdk_session_id": "s", "system_append": ""}),
                        usage=sidecar, fork=fork, skills_dir=skills,
                        wiki_root=tmp_path / "wiki", workspace=object(), owner_id=7)
    await svc.run("k", {"wiki"})
    assert fork.kwargs["owner_id"] == 7


async def test_review_skips_a_lane_that_never_ran_a_turn(tmp_path):
    fork = FakeFork()
    svc, _, _ = service(tmp_path, fork, None)
    assert await svc.run("tg:private:404", {"wiki"}) is None
    assert fork.kwargs is None

    svc, _, _ = service(tmp_path, fork, {"sdk_session_id": "", "system_append": "x"})
    assert await svc.run("tg:private:1", {"wiki"}) is None
    assert fork.kwargs is None


async def test_only_guard_recorded_dirs_are_claimed_as_agent_made(tmp_path):
    """A concurrent foreground /learn creates a directory too; claiming it would
    hand the curator permission to age away something the user made by hand."""
    fork = FakeFork(creates=["release-flow"])
    svc, sidecar, skills = service(
        tmp_path, fork, {"sdk_session_id": "sdk", "system_append": ""})

    async def concurrent_learn():
        d = skills / "user-made"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: user-made\ndescription: U.\n---\n")

    await concurrent_learn()
    await svc.run("k", {"skills"})

    assert sidecar.get("release-flow")["created_by"] == "agent"
    assert sidecar.get("user-made") is None


async def test_provenance_is_marked_even_when_the_fork_dies(tmp_path):
    class Boom(FakeFork):
        async def run_review(self, **kwargs):
            kwargs["touched"].add("half-written")
            d = kwargs["skills_dir"] / "half-written"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("---\nname: h\ndescription: H.\n---\n")
            raise RuntimeError("fork died mid-write")

    svc, sidecar, _ = service(tmp_path, Boom(),
                              {"sdk_session_id": "sdk", "system_append": ""})
    try:
        await svc.run("k", {"skills"})
    except RuntimeError:
        pass
    assert sidecar.get("half-written")["created_by"] == "agent"


# -------------------------------------------------------------- nudges -----

async def test_nudges_count_in_meta_and_reset_on_fire():
    meta = FakeMeta()
    nt = NudgeTracker(meta, Config(wiki_nudge_interval=3, skill_nudge_interval=100))
    assert await nt.observe_turn(False, False, 0) == set()
    assert await nt.observe_turn(False, False, 0) == set()
    assert await nt.observe_turn(False, False, 0) == {"wiki"}
    assert meta.rows[WIKI_NUDGE_META_KEY] == "0"
    assert SKILL_NUDGE_META_KEY in meta.rows


# ---------------------------------------------------------------- loop -----

class FakeGate:
    """`ReviewGate`. In production `MetaReviewGate` — an expiry in `meta`, so the
    answer survives the process that started the review."""

    def __init__(self, running=False):
        self.claimed = running

    async def running(self):
        return self.claimed

    async def claim(self):
        self.claimed = True

    async def release(self):
        self.claimed = False


class FakeDispatcher:
    """`ReviewDispatcher`. Only primitives cross it — the owner id included."""

    def __init__(self):
        self.calls = []

    async def dispatch(self, session_key, kinds, owner_id=0):
        self.calls.append((session_key, kinds, owner_id))


class OneScope:
    """`LearningScopes` with one scope, the owner's (id 1): every id resolves to
    it, which is what the real registry does for `0`."""

    def __init__(self, scope):
        self.scope = scope

    async def for_user(self, user_id):
        return self.scope


def make_loop(sidecar, *, gate, dispatcher, wiki=2):
    scope = SimpleNamespace(
        user_id=1, sidecar=sidecar, gate=gate,
        nudges=NudgeTracker(FakeMeta(), Config(wiki_nudge_interval=wiki,
                                               skill_nudge_interval=100)))
    return LearningLoop(OneScope(scope), dispatcher)


async def test_after_turn_bumps_usage_then_nudges(tmp_path):
    _, sidecar = sidecar_at(tmp_path)
    dispatcher = FakeDispatcher()
    loop = make_loop(sidecar, gate=FakeGate(), dispatcher=dispatcher)
    await loop.after_turn("k", TurnStats(skill_invocations=["deploy"],
                                         skill_patches=["deploy"]))
    assert sidecar.get("deploy")["use_count"] == 1
    assert sidecar.get("deploy")["patch_count"] == 1
    assert dispatcher.calls == []                     # 1 of 2
    await loop.after_turn("k", TurnObservation())
    assert dispatcher.calls == [("k", ["wiki"], 1)]   # the scope's id travels


async def test_a_second_nudge_while_a_review_is_in_flight_is_dropped(tmp_path):
    _, sidecar = sidecar_at(tmp_path)
    gate, dispatcher = FakeGate(), FakeDispatcher()
    loop = make_loop(sidecar, gate=gate, dispatcher=dispatcher, wiki=1)
    await loop.after_turn("k", TurnStats())
    assert len(dispatcher.calls) == 1
    await gate.claim()                                # the pass has started
    assert await loop.review_in_flight() is True
    await loop.after_turn("k", TurnStats())
    assert len(dispatcher.calls) == 1                 # dropped, not queued
    await gate.release()
    await loop.after_turn("k", TurnStats())
    assert len(dispatcher.calls) == 2

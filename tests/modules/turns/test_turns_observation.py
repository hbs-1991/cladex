"""`observe_block`: what a turn did, read off the SDK's tool-use blocks.

Moved here from `tests/test_learning_usage.py` in Wave 2 — the parser lives with
the runner loop now (`modules.turns.application.observation`); only the finished
`TurnStats` crosses into `learning`.
"""
from claude_hermes.core.events import TurnStats
from claude_hermes.modules.turns.application.observation import observe_block


class _Block:
    def __init__(self, name, **inp):
        self.name = name
        self.input = inp


def test_a_write_under_wiki_counts_as_a_wiki_write():
    stats = TurnStats()
    observe_block(stats, _Block("Write", file_path="/data/agent_home/wiki/user/profile.md"))
    assert stats.wrote_wiki is True
    assert stats.wrote_skill is False


def test_a_windows_path_under_wiki_counts_too():
    stats = TurnStats()
    observe_block(stats, _Block("Edit", file_path=r"C:\data\agent_home\wiki\topics\x.md"))
    assert stats.wrote_wiki is True


def test_a_skill_write_is_still_a_skill_write():
    stats = TurnStats()
    observe_block(stats, _Block("Write",
                                file_path="/data/agent_home/.claude/skills/foo/SKILL.md"))
    assert stats.wrote_skill is True
    assert stats.wrote_wiki is False


# --- who reaches the observer at all (0009) -----------------------------------


class _Observer:
    def __init__(self):
        self.seen: list[tuple[str, int]] = []

    async def after_turn(self, session_key, stats, *, owner_id=0):
        self.seen.append((session_key, owner_id))


async def test_a_user_lane_is_observed_iff_its_owner_holds_a_learning_grant():
    """The finished `TurnStats` crosses into `learning` for the owner's lanes always
    and for anybody else's only with their learning grant — resolved per turn, so
    a withdrawal stops the next turn's review, and stamped with the LANE's owner so
    the counters bumped are theirs."""
    from tests.modules.turns.test_turns_service import (
        FakeGrants,
        Grant,
        make_service,
        user_turn,
    )

    observer, grants = _Observer(), FakeGrants(learning=True)
    svc, _ = make_service(observer=observer, grants=grants)
    await user_turn(svc)
    assert observer.seen == [("web:2:x", 2)]

    grants.answer = Grant(learning=False)                    # withdrawn
    await user_turn(svc)
    assert observer.seen == [("web:2:x", 2)]

    # A shell grant is not a learning grant.
    grants.answer = Grant(shell=True, learning=False)
    await user_turn(svc)
    assert observer.seen == [("web:2:x", 2)]

    # The owner's lane never asks and is always observed.
    asked = list(grants.asked)
    await svc.run_turn("tg:private:1", "hi", user_id=1)
    assert observer.seen[-1] == ("tg:private:1", 1)
    assert grants.asked == asked


async def test_without_the_port_a_user_lane_is_not_observed():
    from tests.modules.turns.test_turns_service import make_service, user_turn

    observer = _Observer()
    svc, _ = make_service(observer=observer)
    await user_turn(svc)
    assert observer.seen == []

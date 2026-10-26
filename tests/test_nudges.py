from claude_hermes.core.config import Config
from claude_hermes.core.meta import MetaStore
from claude_hermes.modules.learning.application.services.nudges import NudgeTracker


def make(engine, **kw):
    """The tracker takes a `MetaStore` over the test engine."""
    return NudgeTracker(MetaStore(engine), Config(**kw))


async def test_wiki_nudge_fires_and_resets(engine):
    nt = make(engine, wiki_nudge_interval=3, skill_nudge_interval=100)
    assert await nt.observe_turn(False, False, 0) == set()
    assert await nt.observe_turn(False, False, 0) == set()
    assert await nt.observe_turn(False, False, 0) == {"wiki"}
    assert await nt.observe_turn(False, False, 0) == set()  # counter reset on fire


async def test_an_organic_wiki_write_resets_the_counter(engine):
    nt = make(engine, wiki_nudge_interval=3, skill_nudge_interval=100)
    await nt.observe_turn(False, False, 0)
    await nt.observe_turn(False, False, 0)
    assert await nt.observe_turn(True, False, 0) == set()   # wrote a page → reset
    assert await nt.observe_turn(False, False, 0) == set()  # 1 of 3 again


async def test_skill_nudge_counts_tool_iterations(engine):
    nt = make(engine, wiki_nudge_interval=100, skill_nudge_interval=10)
    assert await nt.observe_turn(False, False, 6) == set()
    assert await nt.observe_turn(False, False, 6) == {"skills"}   # 12 ≥ 10
    assert await nt.observe_turn(False, False, 4) == set()        # reset on fire


async def test_counters_survive_restart(engine):
    nt = make(engine, wiki_nudge_interval=3, skill_nudge_interval=100)
    await nt.observe_turn(False, False, 0)
    await nt.observe_turn(False, False, 0)
    # a fresh tracker over the same store: the counters live in `meta`, not in memory
    nt2 = NudgeTracker(MetaStore(engine),
                       Config(wiki_nudge_interval=3, skill_nudge_interval=100))
    assert await nt2.observe_turn(False, False, 0) == {"wiki"}

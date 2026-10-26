"""Topic naming: the Telegram-shaped half of titling, over the moved `Titler`.

The model call itself (`SdkTitleGenerator`: the prompt, `tools=[]`, the
"answered instead of naming" filter) is pinned in `tests/modules/sessions/
test_sessions_title_generator.py`, so the generator here is a fake with the
port's one method. What this file pins is what `Titler` does around it: when a
thread counts as new, the `topics.rename`, the web thread's title, and that a
generator that fails or produces nothing leaves the topic alone.
"""
import asyncio

from conftest import ensure_users

from claude_hermes.core.config import Config
from claude_hermes.modules.sessions.application.services import (
    SessionRouter,
    ThreadTitleService,
)
from claude_hermes.modules.sessions.infrastructure.repositories import (
    SessionRepository,
    ThreadRepository,
)
from claude_hermes.modules.telegram.infrastructure.titler import Titler


class FakeGenerator:
    """`TitleGeneratorPort`: the title the model would have produced, or the
    exception the SDK call would have raised."""

    def __init__(self, produced="Ansible deploy flow", error=None):
        self._produced = produced
        self._error = error

    async def generate(self, text):
        if self._error is not None:
            raise self._error
        return self._produced


def titler(engine, topics, *, generator=None, cfg=None, bus=None):
    """The lane router and the title writer are built from the sessions module's
    repositories, as the composition root does."""
    cfg = cfg if cfg is not None else Config()
    return Titler(
        topics,
        generator=generator if generator is not None else FakeGenerator(),
        sessions=SessionRouter(SessionRepository(engine), cfg),
        titles=ThreadTitleService(ThreadRepository(engine), None, bus),
    )


class SpyTopics:
    def __init__(self):
        self.renamed = []

    async def rename(self, chat_id, thread_id, name):
        self.renamed.append((chat_id, thread_id, name))


class SpyBus:
    def __init__(self):
        self.emitted = []

    async def emit(self, session_key, turn_id, type, payload):
        self.emitted.append((session_key, type, payload))


async def test_thread_without_a_session_row_is_new(engine):
    t = titler(engine, SpyTopics())
    assert await t.is_new_thread("tg:private:1:t9", 9) is True
    await SessionRepository(engine).upsert("tg:private:1:t9", "sdk-1", "APPEND",
                                           "2026-07-13T10:00:00+00:00")
    assert await t.is_new_thread("tg:private:1:t9", 9) is False
    assert await t.is_new_thread("tg:private:1", None) is False    # no thread → nothing to name


async def test_spawn_titles_and_renames_the_topic(engine):
    topics = SpyTopics()
    t = titler(engine, topics)
    t.spawn(42, 9, "how do I deploy with ansible?")
    await asyncio.sleep(0)                       # let the fire-and-forget task run
    await asyncio.gather(*t._tasks)
    assert topics.renamed == [(42, 9, "Ansible deploy flow")]


async def test_the_topic_name_is_also_the_web_threads_title(engine):
    """One thread, one name: the sidebar row must not say "Без названия" while the
    Telegram topic already carries the title. A title the person typed stays."""
    bus = SpyBus()
    threads = ThreadRepository(engine)
    t = titler(engine, SpyTopics(), bus=bus)
    # `threads.owner_user_id` references `users(id)` since 0005.
    await ensure_users(engine, 42)
    await threads.upsert("tg:private:42:t9", "telegram", 42, "2026-07-13T10:00:00+00:00")
    t.spawn(42, 9, "how do I deploy with ansible?", session_key="tg:private:42:t9")
    await asyncio.gather(*t._tasks)
    assert (await threads.get("tg:private:42:t9"))["title"] == "Ansible deploy flow"
    assert bus.emitted == [("tg:private:42:t9", "thread.title", {"title": "Ansible deploy flow"})]

    await threads.set_title("tg:private:42:t9", "Mine", True)
    t.spawn(42, 9, "another first message", session_key="tg:private:42:t9")
    await asyncio.gather(*t._tasks)
    assert (await threads.get("tg:private:42:t9"))["title"] == "Mine"


async def test_an_answer_instead_of_a_title_leaves_the_topic_alone(engine):
    """A model that answers rather than titles must not name the topic with the first
    128 characters of its answer. No name beats a garbage name. (`SdkTitleGenerator`
    turns such an answer into "" — pinned in `test_sessions_title_generator.py`;
    what is pinned here is that "" renames nothing.)"""
    topics = SpyTopics()
    t = titler(engine, topics, generator=FakeGenerator(produced=""))
    t.spawn(42, 9, "how do I deploy with ansible?")
    await asyncio.sleep(0)
    await asyncio.gather(*t._tasks)
    assert topics.renamed == []


async def test_a_failing_title_call_is_swallowed(engine):
    topics = SpyTopics()
    t = titler(engine, topics, generator=FakeGenerator(error=RuntimeError("model down")))
    t.spawn(42, 9, "hi")
    await asyncio.sleep(0)
    await asyncio.gather(*t._tasks)              # must not raise
    assert topics.renamed == []

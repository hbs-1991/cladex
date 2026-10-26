"""The two `cron_manage` create-path pins that `tests/modules/scheduler` does not
carry: the reply text when no timeout was asked for, and that a rejected timeout
opens no topic (validation runs BEFORE the topic call — a fake topics port that
records what was created is the only way to see that ordering).

Everything else this file used to hold is pinned by `test_scheduler_tool.py`,
`test_scheduler_service.py` and `test_scheduler_policy.py`.
"""
from conftest import ensure_users

from claude_hermes.modules.scheduler.application.service import SchedulerService
from claude_hermes.modules.scheduler.infrastructure.repository import JobRepository
from claude_hermes.modules.scheduler.presentation.tool import build_cron_tool


class Ctx:
    session_key = "tg:private:42"
    chat_id = 42


class FakeTopics:
    def __init__(self, thread_id=77, fail=False):
        self.created = []
        self.closed = []
        self._thread_id = thread_id
        self._fail = fail

    async def create(self, chat_id, name):
        self.created.append((chat_id, name))
        return None if self._fail else self._thread_id

    async def close(self, chat_id, thread_id):
        self.closed.append((chat_id, thread_id))


async def make(engine):
    # The cron tool ensures a delivery thread, and `threads.owner_user_id`
    # references `users(id)` since 0005.
    await ensure_users(engine, 42)
    topics = FakeTopics()
    service = SchedulerService(JobRepository(engine), topics=topics)
    handler, _ = build_cron_tool(service, Ctx())
    return service, handler, topics


async def test_create_without_a_timeout_inherits_the_global_one(engine):
    service, h, _ = await make(engine)
    out = await h({"action": "create", "name": "quick",
                   "schedule": "30m", "prompt": "check something"})
    assert "limit" not in out["content"][0]["text"]
    assert (await service.get(1))["timeout_seconds"] is None


async def test_create_rejects_an_out_of_range_timeout(engine):
    service, h, topics = await make(engine)
    for bad in (5, 3600, "soon"):
        out = await h({"action": "create", "name": "x",
                       "schedule": "30m", "prompt": "ok",
                       "timeout_seconds": bad})
        assert "timeout_seconds" in out["content"][0]["text"], bad
    assert await service.list_jobs() == []     # nothing was created, and no topic opened
    assert topics.created == []

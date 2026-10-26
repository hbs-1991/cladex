
from claude_hermes.modules.telegram.domain.constants import TOPIC_NAME_LIMIT
from claude_hermes.modules.telegram.infrastructure.topics import TopicManager


class FakeCall:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result or {}
        self._error = error

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        if self._error:
            raise self._error
        return self._result


async def test_create_returns_thread_id():
    call = FakeCall(result={"message_thread_id": 77})
    assert await TopicManager(call).create(42, "⏰ digest") == 77
    method, payload = call.calls[0]
    assert method == "createForumTopic"
    assert payload == {"chat_id": 42, "name": "⏰ digest"}


async def test_create_truncates_long_names():
    call = FakeCall(result={"message_thread_id": 1})
    await TopicManager(call).create(42, "x" * 300)
    assert len(call.calls[0][1]["name"]) == TOPIC_NAME_LIMIT


async def test_create_failure_degrades_to_none():
    call = FakeCall(error=RuntimeError("no rights"))
    assert await TopicManager(call).create(42, "job") is None    # never raises


async def test_rename_and_close_swallow_failures():
    call = FakeCall(error=RuntimeError("chat does not support renaming"))
    tm = TopicManager(call)
    await tm.rename(42, 7, "new name")      # must not raise
    await tm.close(42, 7)                   # must not raise
    assert [m for m, _ in call.calls] == ["editForumTopic", "closeForumTopic"]

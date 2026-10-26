"""`AskService` — the ask_user sequencing that lived as a closure in `main.py`
(which has no unit tests) until Wave 2 gave it a home.
"""
import asyncio

from claude_hermes.modules.turns.application.ask_service import AskService
from claude_hermes.modules.turns.domain.value_objects import CANCELLED
from claude_hermes.modules.turns.infrastructure.ask_broker import AskBroker


class RecordingPresenter:
    def __init__(self, fail=False):
        self.presented = []
        self.dismissed = []
        self._fail = fail

    async def present(self, chat_id, thread_id, qid, question, options):
        if self._fail:
            raise RuntimeError("telegram down")
        self.presented.append((chat_id, thread_id, qid, question, list(options)))
        return {"message_id": 11}

    async def dismiss(self, handle):
        self.dismissed.append(handle)


def collector():
    seen = []

    async def emit(type, payload):
        seen.append((type, payload))

    return seen, emit


async def test_a_tap_resolves_the_question_and_clears_the_keyboard():
    broker, presenter = AskBroker(), RecordingPresenter()
    svc = AskService(broker, presenter)
    seen, emit = collector()

    task = asyncio.create_task(svc.ask(1, None, "Which?", ["Redis", "memory"], 7, 5.0,
                                       emit=emit))
    await asyncio.sleep(0)
    qid = presenter.presented[0][2]
    assert await broker.resolve(qid, "0", user_id=7) is True
    assert await task == "Redis"

    assert [t for t, _ in seen] == ["ask.open", "ask.answer"]
    assert seen[0][1] == {"qid": qid, "question": "Which?",
                          "options": ["Redis", "memory"]}
    assert seen[1][1] == {"qid": qid, "source": "user", "answer": "Redis"}
    assert presenter.dismissed == [{"message_id": 11}]
    assert await broker.options_for(qid) is None          # the entry is gone


async def test_a_timeout_reports_itself_and_still_clears_the_entry():
    broker, presenter = AskBroker(), RecordingPresenter()
    seen, emit = collector()
    answer = await AskService(broker, presenter).ask(1, None, "q", ["a", "b"], 7, 0.01,
                                                     emit=emit)
    assert answer is None
    assert seen[1][1] == {"qid": seen[0][1]["qid"], "source": "timeout", "answer": None}
    assert await broker.options_for(seen[0][1]["qid"]) is None


async def test_a_cancel_is_reported_as_cancelled_not_as_an_answer():
    broker, presenter = AskBroker(), RecordingPresenter()
    svc = AskService(broker, presenter)
    seen, emit = collector()
    task = asyncio.create_task(svc.ask(1, None, "q", ["a", "b"], 7, 5.0, emit=emit))
    await asyncio.sleep(0)
    await broker.resolve(presenter.presented[0][2], "c", user_id=7)
    assert await task is CANCELLED
    assert seen[1][1]["source"] == "cancelled" and seen[1][1]["answer"] is None


async def test_a_web_lane_has_no_keyboard_but_still_gets_the_events():
    """chat_id 0 is a browser thread: nothing to present, the ask.open event IS the
    presentation."""
    broker, presenter = AskBroker(), RecordingPresenter()
    svc = AskService(broker, presenter)
    seen, emit = collector()
    task = asyncio.create_task(svc.ask(0, None, "q", ["a", "b"], 1, 5.0, emit=emit))
    await asyncio.sleep(0)
    assert presenter.presented == []
    await broker.resolve(seen[0][1]["qid"], "1", user_id=1)
    assert await task == "b"
    assert presenter.dismissed == []


async def test_a_presenter_failure_does_not_leak_the_pending_entry():
    broker, presenter = AskBroker(), RecordingPresenter(fail=True)
    seen, emit = collector()
    try:
        await AskService(broker, presenter).ask(1, None, "q", ["a", "b"], 7, 5.0,
                                                emit=emit)
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    assert await broker.options_for(seen[0][1]["qid"]) is None


async def test_the_service_is_the_callable_the_tool_expects():
    broker, presenter = AskBroker(), RecordingPresenter()
    svc = AskService(broker, presenter)
    task = asyncio.create_task(svc(1, None, "q", ["a", "b"], 7, 5.0))
    await asyncio.sleep(0)
    await broker.resolve(presenter.presented[0][2], "0", user_id=7)
    assert await task == "a"

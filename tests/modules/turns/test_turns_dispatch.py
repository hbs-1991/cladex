"""How a created turn reaches the process that runs it.

Three lines of code, and two properties worth pinning: the argument list is one
primitive (everything else is on the row, where a worker in another process can
read it), and the task name is resolved at the CALL site, so a typo fails in the
agent instead of vanishing into a worker's log.
"""
from __future__ import annotations

import pytest

from claude_hermes.core.tasks import InlineQueue, TaskRegistry, UnknownTask
from claude_hermes.modules.turns.infrastructure.dispatch import (
    RUN_TURN_TASK,
    QueueTurnDispatcher,
)


async def test_dispatch_enqueues_the_turn_id_and_nothing_else():
    registry = TaskRegistry()
    seen = []

    @registry.task(RUN_TURN_TASK)
    async def run_turn(*, turn_id: str):
        seen.append(turn_id)

    queue = InlineQueue(registry)
    await QueueTurnDispatcher(queue).dispatch("deadbeef")
    await queue.drain()
    assert seen == ["deadbeef"]


async def test_an_unregistered_task_fails_at_the_caller():
    """Inline mode is not a degraded mode, and neither is it a different contract:
    the name has to exist in the registry in both, so a deploy that renamed the
    task breaks where it can be seen."""
    with pytest.raises(UnknownTask):
        await QueueTurnDispatcher(InlineQueue(TaskRegistry())).dispatch("t1")

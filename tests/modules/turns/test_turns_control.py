"""The control channel: cancelling a turn that runs somewhere else, delivering an
ask answer to the process that is parked on it, and the slash-command list that
has to outlive the process that learned it.

Everything here is about the cross-process half. The durable side (the
`cancel_requested_at` flag, the answer key) is what makes these correct; the
channel is only the latency, so what is worth pinning down is that a lost or
duplicated nudge is harmless.
"""
from __future__ import annotations

import asyncio

from claude_hermes.core.eventbus import CONTROL_CHANNEL, InProcessEventBus
from claude_hermes.modules.turns.application.control import (
    SDK_SLASH_COMMANDS_KEY,
    CancelRegistry,
    MetaSlashCommands,
    TurnControl,
)
from claude_hermes.modules.turns.infrastructure.ask_broker import AskBroker


async def sleeper() -> asyncio.Task:
    task = asyncio.create_task(asyncio.sleep(30))
    await asyncio.sleep(0)
    return task


# --- CancelRegistry ---------------------------------------------------------


async def test_cancel_reaches_the_task_registered_here():
    registry = CancelRegistry()
    task = await sleeper()
    registry.register("t1", task)
    assert registry.cancel("t1") is True
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


async def test_cancel_is_idempotent_per_turn():
    """The second cancel is the bug this guards: after the first one the heartbeat
    keeps seeing `cancel_requested_at` on the row and would call `task.cancel()`
    again a third of a lease later — landing on the terminal write's `await` and
    aborting it, so a cancelled turn ends with no row state at all."""
    registry = CancelRegistry()
    cancels = []

    class Spy:
        def cancel(self):
            cancels.append(1)

    registry.register("t1", Spy())
    assert registry.cancel("t1") is True
    assert registry.cancel("t1") is False
    assert registry.cancel("t1") is False
    assert cancels == [1]


async def test_cancelling_a_turn_that_runs_elsewhere_is_a_no_op():
    """Every process gets the nudge; exactly one of them is running the turn."""
    registry = CancelRegistry()
    assert registry.cancel("not-here") is False


async def test_unregister_lets_the_id_be_used_again():
    registry = CancelRegistry()
    first, second = await sleeper(), await sleeper()
    registry.register("t1", first)
    registry.cancel("t1")
    registry.unregister("t1")
    registry.register("t1", second)
    assert registry.cancel("t1") is True           # not stuck on the earlier cancel
    for task in (first, second):
        task.cancel()


# --- TurnControl ------------------------------------------------------------


async def test_a_cancel_nudge_crosses_the_bus_to_the_registry():
    bus = InProcessEventBus()
    registry = CancelRegistry()
    control = TurnControl(bus, registry)
    await control.start()
    task = await sleeper()
    registry.register("t1", task)

    # Published from "another process" — the same channel either way.
    await bus.publish(CONTROL_CHANNEL, {"op": "cancel", "turn_id": "t1"})
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()

    await control.aclose()
    other = await sleeper()
    registry.register("t2", other)
    await bus.publish(CONTROL_CHANNEL, {"op": "cancel", "turn_id": "t2"})
    await asyncio.sleep(0)
    assert not other.cancelled()                  # unsubscribed
    other.cancel()


async def test_an_ask_nudge_is_delivered_to_the_broker_that_holds_it():
    bus = InProcessEventBus()
    broker = AskBroker()
    control = TurnControl(bus, CancelRegistry(), broker)
    await control.start()
    qid, fut = await broker.open(["Redis", "memory"], user_id=7)

    await bus.publish(CONTROL_CHANNEL, {"op": "ask", "qid": qid, "answer": "memory"})
    assert await broker.wait(qid, fut, timeout=1.0) == "memory"

    # A second nudge for the same question, and one for a question this process
    # never held, are both harmless: the nudge is broadcast and the answer key is
    # what makes a lost one recoverable.
    await bus.publish(CONTROL_CHANNEL, {"op": "ask", "qid": qid, "answer": "memory"})
    await bus.publish(CONTROL_CHANNEL, {"op": "ask", "qid": "nobodys", "answer": "x"})


async def test_an_unknown_op_and_a_missing_broker_are_dropped_not_raised():
    """This runs on the bus's reader task: an exception here would cost every
    other subscriber in the process its events."""
    bus = InProcessEventBus()
    control = TurnControl(bus, CancelRegistry(), None)
    await control.start()
    await bus.publish(CONTROL_CHANNEL, {"op": "ask", "qid": "q", "answer": "a"})
    await bus.publish(CONTROL_CHANNEL, {"op": "explode"})
    await bus.publish(CONTROL_CHANNEL, {})


async def test_the_publishing_side_speaks_the_shape_the_handler_reads():
    """One object owns both ends of the wire format, so they cannot drift."""
    bus = InProcessEventBus()
    registry = CancelRegistry()
    broker = AskBroker()
    control = TurnControl(bus, registry, broker)
    await control.start()

    task = await sleeper()
    registry.register("t1", task)
    await control.nudge_cancel("t1")
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()

    qid, fut = await broker.open(["a"], user_id=1)
    await control.nudge_answer(qid, "a")
    assert await broker.wait(qid, fut, timeout=1.0) == "a"


async def test_start_is_idempotent():
    bus = InProcessEventBus()
    registry = CancelRegistry()
    control = TurnControl(bus, registry)
    await control.start()
    await control.start()
    calls = []

    class Spy:
        def cancel(self):
            calls.append(1)

    registry.register("t1", Spy())
    await control.nudge_cancel("t1")
    assert calls == [1]                            # one subscription, not two


# --- MetaSlashCommands ------------------------------------------------------


class FakeMeta:
    def __init__(self, **rows):
        self.rows = dict(rows)

    async def get(self, key):
        return self.rows.get(key)

    async def set(self, key, value):
        self.rows[key] = value


async def test_the_slash_commands_survive_the_process_that_learned_them():
    """The worker that ran the last turn is not the process serving
    `GET /api/commands`."""
    meta = FakeMeta()
    writer = MetaSlashCommands(meta)
    assert await writer.sdk_commands() == []       # before the first turn
    await writer.save(["compact", "cost"])
    assert meta.rows[SDK_SLASH_COMMANDS_KEY] == '["compact", "cost"]'
    assert await MetaSlashCommands(meta).sdk_commands() == ["compact", "cost"]


async def test_a_malformed_value_reads_as_nothing_reported():
    """The `/` menu falls back to skills and plugins, which is exactly what a
    deployment before its first turn shows — a 500 on the composer is not."""
    assert await MetaSlashCommands(FakeMeta(sdk_slash_commands="{not json")
                                   ).sdk_commands() == []
    assert await MetaSlashCommands(FakeMeta(sdk_slash_commands='"a string"')
                                   ).sdk_commands() == []
    assert await MetaSlashCommands(FakeMeta(sdk_slash_commands='["ok", 7, null]')
                                   ).sdk_commands() == ["ok"]

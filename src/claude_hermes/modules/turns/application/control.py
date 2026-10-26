"""Cross-process control: cancelling a turn, delivering an ask answer, and the
slash-command list that has to outlive the process that learned it.

Everything here exists because Wave 3b broke one assumption: the process that
*runs* a turn is no longer the one that receives the click. A cancel arrives at an
API instance and has to reach a worker; an `ask_user` answer arrives at whichever
instance served the request and has to reach the worker parked on the question;
the CLI reports its slash commands to a worker and a browser asks a different
process for them.

The shape is the same in all three: **the durable write is the truth, the channel
message is only the latency.** Cancel is a flag on the `turns` row plus a nudge on
`hermes:ctl`; the heartbeat sees the flag within a third of a lease even if the
nudge is lost. An answer is a Redis key plus a nudge; `wait` polls the key. Losing
a pub/sub message costs a few seconds, never correctness.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import structlog

from claude_hermes.core.eventbus import CONTROL_CHANNEL, EventBus

from .ports import AskBrokerPort

log = structlog.get_logger(__name__)

__all__ = ["CancelRegistry", "TurnControl", "MetaSlashCommands",
           "SDK_SLASH_COMMANDS_KEY", "CONTROL_CHANNEL"]

# A `meta` key, so it is frozen like every other one: the worker writes it and an
# API instance reads it.
SDK_SLASH_COMMANDS_KEY = "sdk_slash_commands"


class CancelRegistry:
    """The turns running **in this process**, so a nudge can reach their task.

    `cancel` is **idempotent per turn**, and that is not tidiness: after the first
    cancel the heartbeat keeps seeing `cancel_requested_at` on the row and would
    call `task.cancel()` again a third of a lease later — landing on the terminal
    write's `await` and aborting it, which is how a cancelled turn ends up with no
    row state at all. The first cancel wins; later ones are no-ops.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()

    def register(self, turn_id: str, task: asyncio.Task) -> None:
        self._tasks[turn_id] = task

    def unregister(self, turn_id: str) -> None:
        self._tasks.pop(turn_id, None)
        self._cancelled.discard(turn_id)

    def cancel(self, turn_id: str) -> bool:
        """True when this call is the one that cancelled the task. False means the
        turn is not running here, or somebody already cancelled it."""
        task = self._tasks.get(turn_id)
        if task is None or turn_id in self._cancelled:
            return False
        self._cancelled.add(turn_id)
        task.cancel()
        return True


class TurnControl:
    """This process's subscription to `hermes:ctl`.

    Every process starts one, **after the secrets are popped from the
    environment** — nothing here spawns, but "the pops are the first thing that
    happens after the objects hold their values" is an invariant that is only
    worth having if it is monotone.

    An agent in redis mode runs no turns and holds no asks, so its registry is
    empty and its broker has nothing pending: the handler simply never matches,
    which is cheaper than deciding per deployment who should subscribe.
    """

    def __init__(self, bus: EventBus, registry: CancelRegistry,
                 asks: AskBrokerPort | None = None) -> None:
        self._bus = bus
        self._registry = registry
        self._asks = asks
        self._unsubscribe = None

    async def start(self) -> None:
        if self._unsubscribe is not None:
            return
        self._unsubscribe = self._bus.subscribe(CONTROL_CHANNEL, self.handle)
        await self._bus.ready(CONTROL_CHANNEL)

    async def aclose(self) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()

    async def handle(self, payload: dict) -> None:
        """Dispatch one control message. Never raises: this runs on the bus's
        reader, and an exception there would cost every other subscriber."""
        op = payload.get("op")
        if op == "cancel":
            self._registry.cancel(str(payload.get("turn_id") or ""))
        elif op == "ask":
            if self._asks is not None:
                # A no-op when the qid is not pending here, which is the normal
                # case: the nudge is broadcast and exactly one process holds it.
                self._asks.deliver(str(payload.get("qid") or ""),
                                   payload.get("answer"))
        else:
            log.warning("unknown control op", op=op)

    # --- the publishing side -------------------------------------------------

    async def nudge_cancel(self, turn_id: str) -> None:
        await self._bus.publish(CONTROL_CHANNEL, {"op": "cancel", "turn_id": turn_id})

    async def nudge_answer(self, qid: str, answer: str | None) -> None:
        await self._bus.publish(CONTROL_CHANNEL,
                                {"op": "ask", "qid": qid, "answer": answer})


class MetaSlashCommands:
    """The names the CLI reported, in `meta` rather than on an object.

    Satisfies both `SlashCommandsPort` (the read the commands router does) and
    `SlashCommandsStore` (the write a finished turn does). It is here rather than
    in `infrastructure/` because it holds no SQL: it is handed a `MetaStore` and
    does JSON, which is the whole adapter.

    A malformed value reads as "nothing reported" rather than raising — the `/`
    menu falls back to skills and plugins, which is exactly what a deployment
    before its first turn shows.
    """

    def __init__(self, meta: Any, *, key: str = SDK_SLASH_COMMANDS_KEY) -> None:
        self._meta = meta
        self._key = key

    async def sdk_commands(self) -> list[str]:
        raw = await self._meta.get(self._key)
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except ValueError:
            log.warning("ignoring a malformed %s meta value", self._key)
            return []
        return [n for n in data if isinstance(n, str)] if isinstance(data, list) else []

    async def save(self, names: list[str]) -> None:
        await self._meta.set(self._key, json.dumps([str(n) for n in names]))

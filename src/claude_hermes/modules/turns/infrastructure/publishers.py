"""Turn events on a channel: `EventPublisher` over `core.eventbus.EventBus`.

Wave 3b replaced the listener list this module used to be with a named channel per
lane — `hermes:turn:<session_key>` — so that the process running a turn and the
process streaming it to a browser no longer have to be the same one. The bus
underneath is either `InProcessEventBus` (inline mode, the old behaviour byte for
byte) or `RedisEventBus`; nothing above this file can tell.

The wire form is `TurnEvent.to_wire()` out and `TurnEvent(**wire)` in: `id` is
`None` for the transient deltas, and `ts`/`seq` travel as they are, because the
SSE watermark on the other side is written against that seq.

`subscribe` is synchronous and `ready` is the seam — a subscriber that must not
miss the first event awaits `ready(lane)` before reading any state. See
`core/eventbus.py` and `application/bus.py` for the ordering that depends on it.
"""
from __future__ import annotations

from typing import Callable

from claude_hermes.core.eventbus import EventBus, InProcessEventBus
from claude_hermes.modules.turns.domain.events import Listener, TurnEvent

# The channel namespace. A wire value like a queue name: an agent and a worker
# from two different deploys have to agree on it, so it is frozen.
TURN_CHANNEL_PREFIX = "hermes:turn:"


def turn_channel(session_key: str) -> str:
    return TURN_CHANNEL_PREFIX + session_key


class ChannelPublisher:
    """`EventPublisher` over one `EventBus` channel per lane."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def subscribe(self, session_key: str, listener: Listener) -> Callable[[], None]:
        async def handler(payload: dict) -> None:
            await listener(TurnEvent(**payload))

        return self._bus.subscribe(turn_channel(session_key), handler)

    async def ready(self, session_key: str) -> None:
        await self._bus.ready(turn_channel(session_key))

    async def publish(self, event: TurnEvent) -> None:
        await self._bus.publish(turn_channel(event.session_key), event.to_wire())


class InProcessPublisher(ChannelPublisher):
    """`ChannelPublisher` over a bus that never leaves this process.

    The name survives the rewrite because the composition root, `web_helpers.py`
    and the bus tests all construct it, and because "inline mode" is a deployment
    shape rather than a second code path: this is the same class over a different
    bus.
    """

    def __init__(self, bus: EventBus | None = None) -> None:
        super().__init__(bus if bus is not None else InProcessEventBus())

"""The channel bus every turn event and every control nudge rides on.

Wave 3b takes the turn off the request path: the process that *runs* a turn is no
longer the process that is streaming it to a browser or drafting a status line into
a Telegram chat. So fan-out stops being a listener list inside one object and
becomes a named channel that either stays in this process (`InProcessEventBus`,
inline mode) or crosses Redis pub/sub (`RedisEventBus`, redis mode). Nothing above
this file knows which one it got.

Two shapes in the Protocol are deliberate and load-bearing:

* **`subscribe` is synchronous, `ready` is the seam.** A caller that must not miss
  the first event on a channel subscribes (cheap, never fails) and then awaits
  `ready(channel)`, which resolves once the subscription is actually live — in
  redis mode that is the server's SUBSCRIBE confirmation, in-process it is
  immediate. `TurnBus.snapshot` awaits it before reading the snapshot store and the
  emitter writes the snapshot before it publishes, so after `snapshot()` returns
  every event is either in what it returned or still on its way to the
  subscription. That is at-least-once; de-duplicating is the SSE stream's job
  (`modules/turns/presentation/sse.py` and its seq watermark).
* **A handler that raises is logged and never stops the others.** One browser tab
  whose queue blew up must not cost every other subscriber its events, and it must
  never take the turn down.

`core` may not import a module, so payloads are plain dicts here: the turn event
is serialized by `modules/turns/infrastructure/publishers.py` on the way out and
rebuilt on the way in.
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any, Awaitable, Callable, Protocol

import structlog

log = structlog.get_logger(__name__)

__all__ = ["EventBus", "Handler", "InProcessEventBus", "RedisEventBus",
           "CONTROL_CHANNEL"]

# The one control channel: cancel nudges and ask answers. A single channel rather
# than one per turn because every process subscribes to it exactly once, at boot,
# and a nudge for a turn this process never heard of is a dict that matches
# nothing — cheaper than a subscription per in-flight turn.
CONTROL_CHANNEL = "hermes:ctl"

Handler = Callable[[dict], Awaitable[None]]


class EventBus(Protocol):
    async def publish(self, channel: str, payload: dict) -> None: ...

    def subscribe(self, channel: str, handler: Handler) -> Callable[[], None]: ...

    async def ready(self, channel: str) -> None: ...

    async def start(self) -> None: ...

    async def aclose(self) -> None: ...


async def _fan_out(channel: str, handlers, payload: dict) -> None:
    """Deliver to every handler, in order, isolating each one's failure."""
    for handler in handlers:
        try:
            await handler(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("event handler failed on %s", channel)


class InProcessEventBus:
    """Inline mode: a listener list per channel, and nothing else.

    `start`/`aclose`/`ready` exist so the composition root and every caller can be
    written once against `EventBus` — there is no second code path for "we have no
    Redis", which is the whole point of inline mode.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    async def publish(self, channel: str, payload: dict) -> None:
        # A copy of the list: a handler may unsubscribe itself while being called.
        await _fan_out(channel, list(self._handlers.get(channel, ())), payload)

    def subscribe(self, channel: str, handler: Handler) -> Callable[[], None]:
        self._handlers[channel].append(handler)

        def unsubscribe() -> None:
            try:
                self._handlers[channel].remove(handler)
            except ValueError:
                pass

        return unsubscribe

    async def ready(self, channel: str) -> None:
        """Immediate: `subscribe` above already put the handler in the list."""
        return None

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        self._handlers.clear()


class RedisEventBus:
    """Redis pub/sub: one `PubSub` and one reader task for the whole process.

    Three decisions are worth the words:

    * **SUBSCRIBE/UNSUBSCRIBE are issued by the reader task, never by the caller.**
      `subscribe()` is synchronous and only appends a handler and (for the first
      handler on a channel) queues the command; the reader drains that queue
      between reads. redis-py's `PubSub` is one connection and one parser, and
      issuing a command from another task while the reader is mid-read is exactly
      the kind of interleaving that yields a dropped confirmation nobody can
      reproduce. Serializing on the reader removes the question.
    * **`ready(channel)` is the SERVER's confirmation, read off the wire.** Sending
      SUBSCRIBE is not the same as the server having processed it: redis-py's
      `PubSub.subscribe` only writes the command and never parses a reply, so
      resolving `ready` there would promise an ordering the socket has not
      delivered — and the snapshot read that follows goes out on a *different*
      pooled connection, so an event published in between would be in neither the
      snapshot nor the subscription. The reader therefore consumes the
      `type == "subscribe"` frame and sets the event then, counting outstanding
      SUBSCRIBEs per channel so an unsubscribe/resubscribe pair cannot let an
      older confirmation resolve a newer subscription.
    * **Handlers run on a per-channel delivery task, not on the reader.** Fan-out
      inline on the socket read meant one slow handler — the Telegram presenter
      doing a network call per status change, an SSE queue whose client stopped
      reading — stalled *every* channel in the process: other lanes' events,
      pending `ready()` confirmations, and the control nudges that carry cancel
      and ask answers. So the reader only parses and enqueues. The queue is
      per channel because the SSE seq watermark depends on per-lane order, and
      nothing weaker than per-channel FIFO preserves it.
    * **The reader survives a dead socket.** A pub/sub connection that dies
      silently freezes every SSE stream in the process, which looks like "the agent
      stopped answering" and not like an outage. So an error rebuilds the `PubSub`,
      re-subscribes every channel that still has a handler, and continues after a
      capped backoff.

    An UNSUBSCRIBE is fire-and-forget: a message that arrives for a channel with no
    handler left is dropped, which is the same answer the handler list would give.
    """

    #: How long one read may block before the reader looks at the command queue.
    #: Short enough that `ready()` is not a visible delay when a browser opens a
    #: stream, long enough that an idle process is not a spin loop.
    POLL_SECONDS = 0.05
    #: Backoff after a connection error, doubled up to this cap.
    BACKOFF_SECONDS = 0.5
    MAX_BACKOFF_SECONDS = 5.0
    #: Every this many payloads waiting on one channel's delivery queue, say so.
    #: A wedged handler is otherwise invisible: the reader keeps up, the socket
    #: looks healthy, and one lane silently lags further and further behind.
    SLOW_HANDLER_DEPTH = 100

    def __init__(self, client: Any) -> None:
        self._client = client
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._ready: dict[str, asyncio.Event] = {}
        self._commands: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        # SUBSCRIBEs sent but not yet confirmed, per channel. `ready` resolves on
        # the confirmation that brings this back to zero, never on an earlier one.
        self._pending_subs: dict[str, int] = {}
        # One delivery queue and one consumer task per channel: the reader parses
        # and enqueues, these await the handlers.
        self._queues: dict[str, asyncio.Queue[dict]] = {}
        self._deliverers: dict[str, asyncio.Task] = {}
        self._pubsub: Any = None
        self._reader: asyncio.Task | None = None
        self._closed = False

    # --- the EventBus surface ----------------------------------------------

    async def publish(self, channel: str, payload: dict) -> None:
        await self._client.publish(channel, json.dumps(payload, ensure_ascii=False))

    def subscribe(self, channel: str, handler: Handler) -> Callable[[], None]:
        first = not self._handlers[channel]
        self._handlers[channel].append(handler)
        if first:
            self._ready.setdefault(channel, asyncio.Event())
            self._commands.put_nowait(("subscribe", channel))

        def unsubscribe() -> None:
            try:
                self._handlers[channel].remove(handler)
            except ValueError:
                return
            if not self._handlers[channel]:
                self._ready.pop(channel, None)
                self._commands.put_nowait(("unsubscribe", channel))

        return unsubscribe

    async def ready(self, channel: str) -> None:
        """Resolve once this channel's SUBSCRIBE has been confirmed by the server.

        INVARIANT: this returns only after the reader has read the server's
        `subscribe` frame for this channel — not after the command was written.
        Everything that reads state right afterwards (`TurnBus.snapshot`,
        `TurnLauncher.wait`, the Telegram dispatcher) depends on the server
        already routing this channel to us by then.

        A channel nobody subscribed to is "ready" immediately: there is nothing to
        wait for, and making this raise would turn a harmless call into an outage.
        """
        event = self._ready.get(channel)
        if event is not None:
            await event.wait()

    async def start(self) -> None:
        if self._reader is None:
            self._closed = False
            self._reader = asyncio.create_task(self._read_loop(), name="eventbus:reader")

    async def aclose(self) -> None:
        self._closed = True
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
        # The deliverers after the reader: nothing can enqueue once it is gone.
        deliverers = list(self._deliverers.values())
        self._deliverers.clear()
        self._queues.clear()
        for task in deliverers:
            task.cancel()
        for task in deliverers:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                log.debug("an event bus deliverer failed on its way out", exc_info=True)
        await self._drop_pubsub()
        self._handlers.clear()
        self._ready.clear()

    # --- the reader --------------------------------------------------------

    async def _read_loop(self) -> None:
        backoff = self.BACKOFF_SECONDS
        while not self._closed:
            try:
                await self._pump()
                backoff = self.BACKOFF_SECONDS      # a clean pass resets the wait
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("event bus reader lost its connection; resubscribing",
                            exc_info=True)
                await self._drop_pubsub()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF_SECONDS)
                # Every channel that still has a handler is queued again, so the
                # recovered connection carries the same subscriptions as the dead
                # one. `ready` events stay set: the subscription was confirmed once
                # and a caller blocked on the gap would never be woken again.
                self._requeue_subscriptions()

    async def _pump(self) -> None:
        """One pass: apply pending commands, then read at most one message.

        With no subscription at all there is no connection to read from, so the
        pass blocks on the command queue instead — an idle web-only process costs
        nothing.
        """
        if self._pubsub is None:
            self._pubsub = self._client.pubsub()
        while not self._commands.empty():
            await self._apply(*self._commands.get_nowait())
        if not any(self._handlers.values()):
            await self._apply(*await self._commands.get())
            return
        # `ignore_subscribe_messages=False`: the confirmation frames are not noise
        # here, they are what `ready()` means.
        message = await self._pubsub.get_message(ignore_subscribe_messages=False,
                                                 timeout=self.POLL_SECONDS)
        if message is None:
            return
        kind = message.get("type")
        channel = _text(message.get("channel"))
        if kind in ("subscribe", "psubscribe"):
            self._confirm(channel)
            return
        if kind != "message":
            return                                    # unsubscribe frames and the like
        try:
            payload = json.loads(_text(message.get("data")))
        except (TypeError, ValueError):
            log.warning("dropping an unparseable event bus payload on %s", channel)
            return
        if isinstance(payload, dict) and self._handlers.get(channel):
            # Enqueue, never await a handler here: the reader is the whole
            # process's socket and a slow handler on one lane must not hold it.
            self._enqueue(channel, payload)

    def _confirm(self, channel: str) -> None:
        """One SUBSCRIBE confirmation landed. `ready` resolves on the one that
        clears the outstanding count — an older confirmation belonging to a
        subscription that was already dropped must not resolve a newer one."""
        left = self._pending_subs.get(channel, 0) - 1
        if left > 0:
            self._pending_subs[channel] = left
            return
        self._pending_subs.pop(channel, None)
        event = self._ready.get(channel)
        if event is not None:
            event.set()

    async def _apply(self, op: str, channel: str) -> None:
        if op == "subscribe":
            if not self._handlers.get(channel):
                return                                # unsubscribed before we got here
            await self._pubsub.subscribe(channel)
            # Counted, not set: the server has been WRITTEN to, not heard from.
            self._pending_subs[channel] = self._pending_subs.get(channel, 0) + 1
            return
        self._stop_delivery(channel)
        if self._pubsub is not None and self._pubsub.subscribed:
            # Fire-and-forget by design: a message for a channel with no handler is
            # dropped anyway, so an UNSUBSCRIBE that fails costs nothing.
            await self._pubsub.unsubscribe(channel)

    # --- delivery ----------------------------------------------------------

    def _enqueue(self, channel: str, payload: dict) -> None:
        queue = self._queues.get(channel)
        if queue is None:
            queue = self._queues[channel] = asyncio.Queue()
            self._deliverers[channel] = asyncio.create_task(
                self._deliver(channel, queue), name=f"eventbus:deliver:{channel}")
        queue.put_nowait(payload)
        depth = queue.qsize()
        if depth and depth % self.SLOW_HANDLER_DEPTH == 0:
            log.warning("event bus handlers are falling behind on %s (%d pending)",
                        channel, depth)

    async def _deliver(self, channel: str, queue: asyncio.Queue) -> None:
        """One channel's handlers, in the order the events arrived. Per channel
        rather than per process: the SSE watermark reads a per-lane `seq`, so the
        order within a lane is a contract and the order between lanes is not."""
        while True:
            payload = await queue.get()
            await _fan_out(channel, list(self._handlers.get(channel, ())), payload)

    def _stop_delivery(self, channel: str) -> None:
        """Drop a channel's queue with its last handler.

        Anything still queued goes with it — the same answer an empty handler
        list would give — and a handler that is mid-await when the last
        subscriber leaves is cancelled rather than left to finish. Draining
        instead would mean a wedged handler on a channel nobody listens to any
        more keeps a task alive for the life of the process.
        """
        self._queues.pop(channel, None)
        task = self._deliverers.pop(channel, None)
        if task is not None:
            task.cancel()

    def _requeue_subscriptions(self) -> None:
        for channel, handlers in self._handlers.items():
            if handlers:
                self._commands.put_nowait(("subscribe", channel))

    async def _drop_pubsub(self) -> None:
        pubsub, self._pubsub = self._pubsub, None
        # The confirmations still owed to us died with the socket; the requeued
        # SUBSCRIBEs start the count over. Leaving stale counts here would mean a
        # channel whose `ready` never resolves again.
        self._pending_subs.clear()
        if pubsub is None:
            return
        try:
            await pubsub.aclose()
        except Exception:
            log.debug("closing the pubsub connection failed", exc_info=True)


def _text(value: Any) -> str:
    """Channel names and payloads arrive as `str` with `decode_responses=True` and
    as `bytes` without it; this bus does not care which client it was handed."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)

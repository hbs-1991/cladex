"""The in-flight snapshot store: the turn as a late subscriber needs to see it.

A browser that reloads mid-turn, or a Telegram presenter that attaches after
`turn.start`, has to be handed the persisted events of every turn in flight plus
the text and reasoning that have not been flushed into a block yet. In one process
that is a dict (`MemorySnapshots`); across processes it has to be somewhere both of
them can read, which is what `RedisSnapshots` is: the worker running the turn
mirrors it into Redis and any API instance reads it back.

`MemorySnapshots` is the inline-mode store and the default `TurnBus` builds. Its
body lives in `application/bus.py` — `application` may not import
`infrastructure` (import-linter, "turns layers") and the two-argument
`TurnBus(events, publisher)` constructor has to keep working — and this module is
where it is *named*, next to the Redis implementation that satisfies the same
port. Everything outside the bus imports it from here.

The two stores are held to one contract by one parametrized test file
(`tests/modules/turns/test_turns_snapshots.py`): `read` must return the same
events in the same order with the same `seq`, because the SSE watermark in
`presentation/sse.py` de-duplicates against exactly that number and a store that
reported a different one would double every delta on a reloaded tab.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from claude_hermes.modules.turns.application.bus import MemorySnapshots
from claude_hermes.modules.turns.domain.events import TurnEvent, _now

log = structlog.get_logger(__name__)

__all__ = ["MemorySnapshots", "RedisSnapshots", "SNAPSHOT_PREFIX"]

# The key namespace. A wire value like a channel name: a worker and an API
# instance from two different deploys have to agree on it, so it is frozen.
SNAPSHOT_PREFIX = "hermes:snap:"


class RedisSnapshots:
    """`MemorySnapshots` in Redis keys, so another process can read the turn.

    Key layout, per lane and per turn in flight:

    * ``hermes:snap:<lane>``                  — a list of active turn ids, in start
      order (RPUSH on `turn.start`, LREM on `turn.end`). Redis drops an empty list,
      so the key's existence *is* "this lane is busy" — no empty-lane bookkeeping.
    * ``hermes:snap:<lane>:<turn>``           — a hash carrying `seq`, the turn's
      latest sequence number. This key's existence is what `record` treats as "the
      turn is registered here": an event for a turn whose `turn.start` this store
      never saw is dropped, exactly as the memory store drops it.
    * ``hermes:snap:<lane>:<turn>:events``    — the persisted events, JSON per entry.
    * ``hermes:snap:<lane>:<turn>:text`` / ``:thinking`` — the unflushed buffers
      (APPEND on a delta, DEL when the matching block flushes).

    INVARIANT (the TTL is the garbage collector): every write is one pipeline that
    also EXPIREs every key it touched, including the lane list. A worker killed
    between a heartbeat and the reaper leaves keys behind by definition; they age
    out on their own, and the reaper's `clear` removes them sooner. Nothing here
    ever needs a sweep.

    The one non-obvious ordering is in `record`: for anything but `turn.start` the
    registration check is a separate EXISTS *before* the pipeline. Redis has no
    conditional write without Lua, and inventing the turn instead would leave a
    lane reporting itself busy forever. The check is safe because a turn has
    exactly one emitter — the only concurrent writer is the reaper's `clear`, and
    losing that race costs a few stale keys that the TTL collects.
    """

    def __init__(self, client: Any, ttl_seconds: int = 3600) -> None:
        self._client = client
        self._ttl = int(ttl_seconds)

    # --- keys ---------------------------------------------------------------

    def _lane_key(self, session_key: str) -> str:
        return SNAPSHOT_PREFIX + session_key

    def _turn_key(self, session_key: str, turn_id: str) -> str:
        return f"{SNAPSHOT_PREFIX}{session_key}:{turn_id}"

    # --- the SnapshotStore surface -----------------------------------------

    async def record(self, ev: TurnEvent) -> None:
        lane_key = self._lane_key(ev.session_key)
        base = self._turn_key(ev.session_key, ev.turn_id)
        events_key, text_key, thinking_key = f"{base}:events", f"{base}:text", f"{base}:thinking"

        if ev.type == "turn.start":
            async with self._client.pipeline(transaction=True) as pipe:
                # LREM before RPUSH, and a DEL of the four keys: a second
                # `turn.start` for the same id RESTARTS the turn — the same answer
                # the emitter's counter gives — and must not leave the id in the
                # list twice or carry the previous attempt's text into this one.
                pipe.lrem(lane_key, 0, ev.turn_id)
                pipe.rpush(lane_key, ev.turn_id)
                pipe.delete(base, events_key, text_key, thinking_key)
                pipe.hset(base, "seq", ev.seq)
                pipe.rpush(events_key, json.dumps(ev.to_wire(), ensure_ascii=False))
                for key in (lane_key, base, events_key):
                    pipe.expire(key, self._ttl)
                await pipe.execute()
            return

        if not await self._client.exists(base):
            # An event for a turn this store never saw start — a cron delivery's
            # `turn.end` after a restart, say. The emitter still sequences and
            # publishes it; there is simply nothing here to catch up on.
            return

        if ev.type == "turn.end":
            await self.clear(ev.session_key, ev.turn_id)
            return

        async with self._client.pipeline(transaction=True) as pipe:
            touched = [lane_key, base]
            # Every record, transient or not: the synthetic deltas `read` produces
            # carry this seq, and the SSE watermark is written against it.
            pipe.hset(base, "seq", ev.seq)
            if ev.type == "text.delta":
                pipe.append(text_key, str(ev.payload.get("delta", "")))
                touched.append(text_key)
            elif ev.type == "thinking.delta":
                pipe.append(thinking_key, str(ev.payload.get("delta", "")))
                touched.append(thinking_key)
            else:
                pipe.rpush(events_key, json.dumps(ev.to_wire(), ensure_ascii=False))
                touched.append(events_key)
                if ev.type == "text.block":
                    pipe.delete(text_key)
                elif ev.type == "thinking":
                    pipe.delete(thinking_key)
            for key in touched:
                pipe.expire(key, self._ttl)
            await pipe.execute()

    async def read(self, session_key: str) -> list[TurnEvent]:
        """Everything a late subscriber needs: the persisted events of every turn
        in flight, each followed by one synthetic thinking.delta and one text.delta
        carrying its not-yet-flushed reasoning and text, at the turn's latest seq.
        Turns come in the order they started; every event names its own turn."""
        lane_key = self._lane_key(session_key)
        turn_ids = [_text(t) for t in await self._client.lrange(lane_key, 0, -1)]
        if not turn_ids:
            return []
        async with self._client.pipeline(transaction=False) as pipe:
            for turn_id in turn_ids:
                base = self._turn_key(session_key, turn_id)
                pipe.hget(base, "seq")
                pipe.lrange(f"{base}:events", 0, -1)
                pipe.get(f"{base}:thinking")
                pipe.get(f"{base}:text")
            rows = await pipe.execute()

        out: list[TurnEvent] = []
        for i, turn_id in enumerate(turn_ids):
            raw_seq, raw_events, thinking, text = rows[i * 4:i * 4 + 4]
            if raw_seq is None:
                # Listed but gone: the turn's keys aged out while the lane list
                # survived (a killed worker). Nothing to replay for it.
                continue
            seq = int(_text(raw_seq) or 0)
            for raw in raw_events:
                event = _event(raw)
                if event is not None:
                    out.append(event)
            for kind, buffered in (("thinking.delta", thinking), ("text.delta", text)):
                buffered = _text(buffered)
                if buffered:
                    out.append(TurnEvent(session_key, turn_id, seq, _now(), kind,
                                         {"delta": buffered}))
        return out

    async def is_running(self, session_key: str) -> bool:
        """True while at least one listed turn still has state.

        The listed-and-gone case above is why this is not a bare EXISTS on the
        lane list: a killed worker can leave an id in the list whose keys have
        expired, and reporting that lane busy would be a lie a reader cannot
        check.
        """
        turn_ids = [_text(t) for t in await self._client.lrange(
            self._lane_key(session_key), 0, -1)]
        if not turn_ids:
            return False
        async with self._client.pipeline(transaction=False) as pipe:
            for turn_id in turn_ids:
                pipe.exists(self._turn_key(session_key, turn_id))
            return any(await pipe.execute())

    async def clear(self, session_key: str, turn_id: str) -> None:
        """Drop one turn: what `turn.end` does, and what the reaper calls."""
        lane_key = self._lane_key(session_key)
        base = self._turn_key(session_key, turn_id)
        async with self._client.pipeline(transaction=True) as pipe:
            pipe.lrem(lane_key, 0, turn_id)
            pipe.delete(base, f"{base}:events", f"{base}:text", f"{base}:thinking")
            # Redis deletes a list that goes empty, so the lane key disappears with
            # its last turn and no separate "is the lane empty" write is needed.
            pipe.expire(lane_key, self._ttl)
            await pipe.execute()


def _event(raw: Any) -> TurnEvent | None:
    """One stored event back into a `TurnEvent`. A frame this store cannot parse
    is dropped rather than raised: a snapshot read serves an SSE stream, and one
    bad key must not turn a reload into a 500."""
    try:
        return TurnEvent(**json.loads(_text(raw)))
    except (TypeError, ValueError):
        log.warning("dropping an unparseable snapshot event")
        return None


def _text(value: Any) -> str:
    """Values arrive as `str` with `decode_responses=True` (what
    `core.redis.create_client` builds) and as `bytes` without it; this store does
    not care which client it was handed."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return "" if value is None else str(value)

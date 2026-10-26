"""The SSE body for one lane.

The frame format is frozen (the browser and the e2e tests parse it): an `id:` line
carrying the persisted event id, `event: turn`, then the JSON payload.

`event_stream`'s `deps` argument is frozen too: `tests/test_web_turns.py` hands it the
whole `WebDeps` and iterates the generator without HTTP. The route hands it a
`dependencies.StreamDeps` instead — the same two attributes (`bus`, `turn_events`),
built from the route's own providers so an override reaches the stream as well.
"""
from __future__ import annotations

import asyncio
import json

from claude_hermes.modules.turns.presentation.dependencies import events_after

HEARTBEAT_SECONDS = 15


def _frame(ev_wire: dict) -> str:
    eid = ev_wire.get("id")
    return (f"id: {eid if eid is not None else ''}\nevent: turn\n"
            f"data: {json.dumps(ev_wire, ensure_ascii=False)}\n\n")


async def event_stream(deps, session_key: str, after: int):
    """Persisted events after the cursor, the active-turn snapshot, then the live
    tail with heartbeats. A plain async generator so tests can iterate it without
    HTTP (httpx's ASGITransport buffers whole responses and cannot consume an
    endless stream)."""
    queue: asyncio.Queue = asyncio.Queue()

    async def listener(ev):
        await queue.put(ev.to_wire())

    unsubscribe = deps.bus.subscribe(session_key, listener)
    try:
        # `cursor` is the highest persisted id already sent. The snapshot of the
        # turn in flight repeats the rows the replay just yielded, and an event
        # emitted while the replay ran reaches the queue as well, so both later
        # stages are filtered against it — only the synthetic text.delta of the
        # snapshot (id None, never persisted) is always new.
        cursor = after
        for row in await events_after(deps, session_key, after_id=after, limit=2000):
            cursor = max(cursor, row["id"])
            yield _frame(row)
        # The order of the next four statements is the whole de-duplication story,
        # and it is an invariant:
        #
        #   subscribe -> DB replay -> await snapshot -> drain -> yield snapshot
        #
        # The replay above suspends on every yield, so the bus may have emitted
        # while it ran: those events are on the queue AND (for a turn still in
        # flight) in the snapshot. A persisted event de-duplicates by id, but a
        # delta has none — it would be sent twice, once coalesced in the snapshot
        # and once raw from the queue, and the browser would show the text
        # doubled.
        #
        # `snapshot` is a coroutine since Wave 3b (the state may be in Redis), so
        # "drain with no await between the drain and the snapshot" is no longer
        # available: taking the snapshot IS an await. Hence the seq watermark.
        # `snapshot()` returns each turn's events up to some seq; everything at or
        # below that seq for that turn is already represented in what we are about
        # to yield, so any LATER arrival of it — still in the queue, or still in
        # flight to this process from another one — is a duplicate. Persisted
        # events keep the id/cursor/replayed filters (a reaper's synthetic
        # `turn.end` starts a fresh seq at 1 in a foreign process, so seq-dropping
        # must apply to deltas only).
        snap = await deps.bus.snapshot(session_key)
        pending = []
        while not queue.empty():
            pending.append(queue.get_nowait())
        replayed: set[int] = set()
        watermark: dict[str, int] = {}
        for ev in snap:
            if ev.id is not None:
                replayed.add(ev.id)
            watermark[ev.turn_id] = max(watermark.get(ev.turn_id, 0), ev.seq)

        def covered(item: dict) -> bool:
            """True when this frame is already on the wire — by id, or by seq."""
            eid = item.get("id")
            if eid is not None:
                return eid <= cursor or eid in replayed
            mark = watermark.get(item.get("turn_id"))
            return mark is not None and int(item.get("seq") or 0) <= mark

        for ev in snap:
            if ev.id is None or ev.id > cursor:
                yield _frame(ev.to_wire())
        for item in pending:
            if covered(item):
                continue
            yield _frame(item)
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                if covered(item):
                    continue
                yield _frame(item)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        unsubscribe()

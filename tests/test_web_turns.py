"""The turns router: start a turn, read its history, tail it, cancel it, answer an ask."""
import asyncio
import json

import pytest

from claude_hermes.modules.sessions.infrastructure.repositories import ThreadRepository
from claude_hermes.modules.turns.infrastructure.ask_broker import AskBroker
from claude_hermes.modules.turns.presentation.sse import event_stream

from conftest import ensure_users
from tests.web_helpers import ORIGIN, json_headers, login, make_client

NOW = "2026-07-12T10:00:00+00:00"


async def read_sse(deps, session_key, after: int, want: int, timeout=3.0):
    """Collect `want` turn events from the SSE generator, then stop. The generator
    is iterated directly: httpx's ASGITransport buffers whole responses, so an
    endless stream cannot be read over it."""
    events = []

    async def pump():
        async for frame in event_stream(deps, session_key, after):
            if frame.startswith(":"):
                continue                                   # heartbeat
            data = [x[5:].strip() for x in frame.splitlines() if x.startswith("data:")]
            events.append(json.loads(data[0]))
            if len(events) >= want:
                return

    await asyncio.wait_for(pump(), timeout)
    return events


async def test_post_message_runs_a_turn_and_events_are_readable(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    up = (await client.post("/api/uploads", data={"thread_id": tid},
                            files={"file": ("notes.md", b"# n", "text/markdown")},
                            headers={"Origin": ORIGIN})).json()
    r = await client.post(f"/api/threads/{tid}/messages",
                          json={"text": "hi", "attachment_ids": [up["file_id"]]},
                          headers=json_headers())
    assert r.status_code == 202
    body = r.json()
    # `turn_id` is the one additive change to this body in Wave 3b (spec req. 16):
    # the SPA can name the turn it is watching. Popped so the rest stays pinned by
    # equality, which is what makes a silent key addition a failing test.
    assert body.pop("turn_id")
    assert body == {"accepted": True, "thread_id": tid}
    await asyncio.sleep(0.05)                       # the fake runner finishes instantly
    assert deps.runner.calls[0][:3] == (tid, "hi", 1) and deps.runner.calls[0][4] == "web"
    assert deps.runner.calls[0][3][0].file_id == up["file_id"]
    r = await client.get(f"/api/threads/{tid}/events")
    types = [e["type"] for e in r.json()["events"]]
    assert types == ["turn.start", "user.message", "text.block", "turn.end"]
    # the browser opens the file it just sent straight from the thread
    assert r.json()["events"][1]["payload"]["attachments"] == [
        {"file_id": up["file_id"], "name": "notes.md", "media_type": "text/markdown",
         "size": 3, "url": f"/api/files/{up['file_id']}"},
    ]
    last = r.json()["events"][-1]["id"]
    r = await client.get(f"/api/threads/{tid}/events?after={last}")
    assert r.json()["events"] == []


async def test_busy_lane_is_409_and_cancel_hits_the_launcher(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    deps.runner.busy.add(tid)
    r = await client.post(f"/api/threads/{tid}/messages", json={"text": "hi"},
                          headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    r = await client.post(f"/api/threads/{tid}/cancel", headers=json_headers())
    assert r.status_code == 204 and deps.runner.cancelled == [tid]
    deps.runner.busy.clear()
    r = await client.post(f"/api/threads/{tid}/cancel", headers=json_headers())
    assert r.status_code == 409


async def test_stream_replays_snapshot_then_tails(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    await deps.bus.emit(tid, "t1", "turn.start", {"channel": "web", "user_id": 1})
    await deps.bus.emit(tid, "t1", "text.delta", {"delta": "Hel"})

    async def later():
        await asyncio.sleep(0.05)
        await deps.bus.emit(tid, "t1", "text.block", {"text": "Hello"})
        await deps.bus.emit(tid, "t1", "turn.end", {"status": "ok", "cost_usd": None,
                                                   "sdk_session_id": None, "error": None})

    asyncio.create_task(later())
    events = await read_sse(deps, tid, after=0, want=4)
    assert [e["type"] for e in events] == ["turn.start", "text.delta", "text.block", "turn.end"]
    assert events[1]["payload"] == {"delta": "Hel"}


async def test_stream_endpoint_is_guarded(tmp_path):
    client, deps = await make_client(tmp_path)
    assert (await client.get("/api/threads/web:1:x/stream")).status_code == 401
    login(client, 1)
    assert (await client.get("/api/threads/web:2:zzz/stream")).status_code == 404


async def test_stream_after_cursor_skips_old_persisted_events(tmp_path):
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    a = await deps.bus.emit(tid, "t0", "turn.start", {"channel": "web", "user_id": 1})
    await deps.bus.emit(tid, "t0", "turn.end", {"status": "ok", "cost_usd": None,
                                               "sdk_session_id": None, "error": None})

    async def later():
        await asyncio.sleep(0.05)
        await deps.bus.emit(tid, "t1", "turn.start", {"channel": "web", "user_id": 1})

    asyncio.create_task(later())
    events = await read_sse(deps, tid, after=a.id, want=2)
    assert [e["turn_id"] for e in events] == ["t0", "t1"]   # the t0 turn.end, then live


async def test_answering_an_ask_resolves_the_broker(tmp_path):
    client, deps = await make_client(tmp_path)
    deps.broker = AskBroker()
    login(client, 1)
    qid, fut = await deps.broker.open(["A", "B"], user_id=1)
    r = await client.post(f"/api/asks/{qid}", json={"answer": "B"}, headers=json_headers())
    assert r.status_code == 204
    assert fut.result() == "B"
    r = await client.post(f"/api/asks/{qid}", json={"answer": "B"}, headers=json_headers())
    assert r.status_code == 410                                     # already answered
    qid2, _ = await deps.broker.open(["A", "B"], user_id=2)                 # someone else's question
    r = await client.post(f"/api/asks/{qid2}", json={"answer": "A"}, headers=json_headers())
    assert r.status_code == 410


async def test_foreign_thread_events_are_404(tmp_path, engine):
    client, deps = await make_client(tmp_path, engine=engine)
    login(client, 1)
    # `threads.owner_user_id` references `users(id)` since 0005.
    await ensure_users(engine, 2)
    await ThreadRepository(engine).upsert("web:2:zzz", "web", 2, NOW)
    assert (await client.get("/api/threads/web:2:zzz/events")).status_code == 404


async def test_stream_does_not_double_deltas_emitted_during_the_replay(tmp_path):
    """The listener is subscribed before the DB replay, so an event emitted while
    the replay runs arrives twice: once on the queue, once in the snapshot. A
    text.delta has no id to de-duplicate it, so the queue copy must be dropped."""
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    await deps.bus.emit(tid, "t1", "turn.start", {"channel": "web", "user_id": 1})

    gen = event_stream(deps, tid, 0)
    first = await gen.__anext__()                       # the persisted turn.start
    await deps.bus.emit(tid, "t1", "text.delta", {"delta": "Hel"})
    frames = [first, await gen.__anext__()]
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(gen.__anext__(), 0.2)     # nothing repeats the delta
    await gen.aclose()
    payloads = [json.loads(f.split("data: ", 1)[1]) for f in frames]
    assert [e["type"] for e in payloads] == ["turn.start", "text.delta"]
    assert payloads[1]["payload"] == {"delta": "Hel"}


async def test_stream_still_delivers_a_turn_that_finished_during_the_replay(tmp_path):
    """Dropping the queue must not drop a turn the snapshot no longer knows about:
    a cron delivery that starts and ends while the replay runs is still persisted,
    so it is forwarded from the queue by id."""
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    await deps.bus.emit(tid, "t1", "turn.start", {"channel": "web", "user_id": 1})

    gen = event_stream(deps, tid, 0)
    first = await gen.__anext__()
    await deps.bus.emit(tid, "cron", "turn.start", {"channel": "cron", "user_id": 0})
    await deps.bus.emit(tid, "cron", "text.block", {"text": "report"})
    await deps.bus.emit(tid, "cron", "turn.end", {"status": "ok"})
    frames = [first]
    for _ in range(3):
        frames.append(await asyncio.wait_for(gen.__anext__(), 1.0))
    await gen.aclose()
    payloads = [json.loads(f.split("data: ", 1)[1]) for f in frames if not f.startswith(":")]
    assert [(e["turn_id"], e["type"]) for e in payloads] == [
        ("t1", "turn.start"), ("cron", "turn.start"), ("cron", "text.block"),
        ("cron", "turn.end"),
    ]


class WatermarkBus:
    """A bus whose snapshot is fixed and which can push events afterwards.

    The one thing a real in-process bus cannot stage: an event that is BOTH in the
    snapshot the stream just yielded and still in flight to this process. In redis
    mode that is the normal case — the emitter mirrors the snapshot before it
    publishes, so a subscriber that snapshots in between gets the delta twice.
    """

    def __init__(self, snap):
        self._snap = list(snap)
        self._listeners = []

    def subscribe(self, key, listener):
        self._listeners.append(listener)
        return lambda: None

    async def ready(self, key):
        return None

    async def snapshot(self, key):
        return list(self._snap)

    async def is_running(self, key):
        return bool(self._snap)

    async def push(self, ev):
        for listener in list(self._listeners):
            await listener(ev)


class NoEvents:
    async def after(self, session_key, after_id=0, limit=1000):
        return []


async def test_the_stream_drops_a_delta_the_snapshot_already_carried():
    """The seq watermark. `snapshot()` is a coroutine now, so the old "drain the
    queue with no await between it and the snapshot" trick is gone: the snapshot
    IS an await. Instead the stream records the highest seq per turn that the
    snapshot spoke for and drops anything at or below it that has no persisted id
    — the raw deltas whose text the coalesced snapshot already contains."""
    from types import SimpleNamespace

    from claude_hermes.modules.turns.domain.events import TurnEvent

    lane = "web:1:x"
    snap = [TurnEvent(lane, "t1", 1, NOW, "turn.start", {"channel": "web", "user_id": 1},
                      id=1),
            TurnEvent(lane, "t1", 4, NOW, "text.delta", {"delta": "Hello"})]
    bus = WatermarkBus(snap)
    gen = event_stream(SimpleNamespace(bus=bus, turn_events=NoEvents()), lane, 0)

    def payload(frame):
        data = [x[5:].strip() for x in frame.splitlines() if x.startswith("data:")]
        return json.loads(data[0])

    # The two snapshot frames. After the second the generator is suspended past
    # its drain, which is exactly the window a live delta arrives in.
    assert payload(await gen.__anext__())["type"] == "turn.start"
    assert payload(await gen.__anext__())["payload"] == {"delta": "Hello"}

    # Already spoken for by the coalesced snapshot (seq 4 <= watermark 4).
    await bus.push(TurnEvent(lane, "t1", 3, NOW, "text.delta", {"delta": "Hel"}))
    await bus.push(TurnEvent(lane, "t1", 4, NOW, "text.delta", {"delta": "lo"}))
    # Past the watermark: genuinely new.
    await bus.push(TurnEvent(lane, "t1", 5, NOW, "text.delta", {"delta": "!"}))
    # A different turn has its own watermark, so nothing of its is dropped.
    await bus.push(TurnEvent(lane, "t2", 1, NOW, "text.delta", {"delta": "other"}))

    assert payload(await gen.__anext__()) == {
        "id": None, "session_key": lane, "turn_id": "t1", "seq": 5, "ts": NOW,
        "type": "text.delta", "payload": {"delta": "!"}}
    assert payload(await gen.__anext__())["turn_id"] == "t2"
    await gen.aclose()

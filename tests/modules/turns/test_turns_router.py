"""The turns routers' seams: the collaborators they ask for, and the checks an
override must NOT reach.

`routes(deps)` closed over the bundle, so a test that wanted a different runner had to
build a different bundle and a different app. Each provider is now a named callable,
and `app.dependency_overrides[get_turns]` swaps one collaborator for one app without
touching the database, the bus or the other nine routers.

`tests/test_web_turns.py`, `test_web_commands.py` and `test_web_voice.py` still own the
wire contract end to end; what is pinned here is that the seams exist and that the
lenient body models did not turn a hand-written 400 into a Pydantic 422.
"""
import httpx

from claude_hermes.integrations.openai.voice import VoiceError
from claude_hermes.modules.turns.presentation.dependencies import (
    StreamDeps,
    get_bus,
    get_events,
    get_sdk_commands,
    get_turns,
    get_voice,
)
from claude_hermes.modules.turns.presentation.sse import event_stream
from claude_hermes.http.app import build_web_app

from tests.web_helpers import ORIGIN, json_headers, login, make_client


class FakeLauncher:
    """A `TurnLauncher` by shape only: the three methods a route may call.

    `cancel` returns "there was something to cancel", which for a fake is the
    inverse of "the lane is free" — the real one returns whether `request_cancel`
    matched an active row.
    """

    def __init__(self, busy: bool = False):
        self.busy = busy
        self.calls = []
        self.cancelled = []

    async def is_busy(self, key):
        return self.busy

    async def cancel(self, key, turn_id=None):
        self.cancelled.append(key)
        return not self.busy

    async def start(self, session_key, text, *, user_id=0, attachments=None,
                    channel="telegram"):
        self.calls.append((session_key, text, user_id, list(attachments or []), channel))
        return "turn-" + str(len(self.calls))


class FakeBus:
    """The SSE half of `TurnEventBusPort`. No `is_running`: the busy check moved to
    the `turns` row in Wave 3b, and a bus that still answered it would hide a route
    that had not."""

    def subscribe(self, key, listener):
        return lambda: None

    async def ready(self, key):
        return None

    async def snapshot(self, key):
        return []


class FakeSdkCommands:
    """A `SlashCommandsPort` by shape only — the shared store the worker wrote to."""

    def __init__(self, names):
        self.names = list(names)

    async def sdk_commands(self):
        return list(self.names)


class FakeEvents:
    """A `TurnEventRepository` by shape only."""

    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    async def after(self, session_key, after_id=0, limit=1000):
        self.calls.append((session_key, after_id, limit))
        return self.rows


class FakeVoice:
    def __init__(self, error: str | None = None):
        self.error = error
        self.heard = []

    async def transcribe(self, audio, *, filename="audio.ogg"):
        if self.error:
            raise VoiceError(self.error)
        self.heard.append((audio, filename))
        return "fake transcript"

    async def synthesize(self, text):
        return b"OGG" + text.encode()


async def thread_on(tmp_path):
    """A logged-in owner, a thread of theirs, and a SECOND app over the same bundle —
    the one whose providers the test overrides."""
    client, deps = await make_client(tmp_path)
    login(client, 1)
    tid = (await client.post("/api/threads", json={}, headers=json_headers())).json()["id"]
    app = build_web_app(deps)
    faked = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)
    login(faked, 1)
    return app, faked, deps, tid


async def test_the_launcher_is_swappable_and_the_body_model_stays_lenient(tmp_path):
    app, client, _deps, tid = await thread_on(tmp_path)
    turns = FakeLauncher()
    app.dependency_overrides[get_turns] = lambda: turns
    app.dependency_overrides[get_bus] = FakeBus

    # A non-string `text` is stringified by the handler, exactly as `str(body.get(...))`
    # did — the model does not refuse it, so there is no 422 where a turn used to run.
    r = await client.post(f"/api/threads/{tid}/messages", json={"text": 12, "nope": "x"},
                          headers=json_headers())
    assert r.status_code == 202
    body = r.json()
    assert body.pop("turn_id") == "turn-1"          # additive: the id of the queued row
    assert body == {"accepted": True, "thread_id": tid}
    assert turns.calls == [(tid, "12", 1, [], "web")]

    # And the empty body is still the handler's own 400, not a schema's.
    r = await client.post(f"/api/threads/{tid}/messages", json={}, headers=json_headers())
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "bad_request",
                                  "message": "text or attachments required"}}


async def test_a_busy_lane_and_a_missing_turn_are_the_launchers_answer(tmp_path):
    """One collaborator answers both now. Before Wave 3b the route asked the runner
    AND the bus, which could disagree across processes; the `turns` row cannot."""
    app, client, _deps, tid = await thread_on(tmp_path)
    app.dependency_overrides[get_turns] = lambda: FakeLauncher(busy=True)
    app.dependency_overrides[get_bus] = FakeBus
    r = await client.post(f"/api/threads/{tid}/messages", json={"text": "hi"},
                          headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    assert r.json()["error"]["message"] == "the agent is busy in this thread"

    # A busy lane has a turn to cancel; a free one answers 409 not_running.
    r = await client.post(f"/api/threads/{tid}/cancel", headers=json_headers())
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_running"

    app.dependency_overrides[get_turns] = FakeLauncher
    r = await client.post(f"/api/threads/{tid}/cancel", headers=json_headers())
    assert r.status_code == 204


async def test_a_lane_lost_between_the_check_and_the_insert_is_still_409(tmp_path):
    """The `is_busy` call is a courtesy; the unique partial index is the guarantee.
    A launcher whose `start` raises `LaneBusy` renders the same 409 body, so the
    route may not catch it into something else."""
    from claude_hermes.modules.turns.domain.exceptions import LaneBusy

    app, client, _deps, tid = await thread_on(tmp_path)

    class RacingLauncher(FakeLauncher):
        async def start(self, *a, **kw):
            raise LaneBusy("the agent is busy in this thread")

    app.dependency_overrides[get_turns] = RacingLauncher
    r = await client.post(f"/api/threads/{tid}/messages", json={"text": "hi"},
                          headers=json_headers())
    assert r.status_code == 409
    assert r.json() == {"error": {"code": "busy",
                                  "message": "the agent is busy in this thread"}}


async def test_a_bundle_without_a_launcher_is_503_not_a_500(tmp_path):
    """`get_turns` is `required=True`: a shell that mounted the router without wiring
    `deps.turns` is a composition bug, and saying so beats an AttributeError."""
    app, client, deps, tid = await thread_on(tmp_path)
    deps.turns = None
    try:
        r = await client.post(f"/api/threads/{tid}/messages", json={"text": "hi"},
                              headers=json_headers())
        assert r.status_code == 503 and r.json()["error"]["code"] == "unavailable"
    finally:
        deps.turns = deps.runner


async def test_the_event_repository_is_the_seam_and_after_is_a_typed_cursor(tmp_path):
    app, client, _deps, tid = await thread_on(tmp_path)
    events = FakeEvents([{"id": 7, "type": "turn.start", "payload": {}}])
    app.dependency_overrides[get_events] = lambda: events

    r = await client.get(f"/api/threads/{tid}/events", params={"after": 3})
    assert r.status_code == 200
    assert r.json() == {"events": [{"id": 7, "type": "turn.start", "payload": {}}]}
    assert events.calls == [(tid, 3, 2000)]

    # `after` is an int parameter now, so a non-numeric cursor is the shell's 400
    # rather than the ValueError the hand-parsed `int(query_params[...])` raised.
    r = await client.get(f"/api/threads/{tid}/events", params={"after": "twelve"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"
    assert r.json()["error"]["message"].startswith("query.after: ")


async def test_faking_every_collaborator_does_not_open_someone_elses_lane(tmp_path):
    """`owned_thread` is its own dependency: a test that fakes the runner, the bus and
    the event store still only reaches the lanes it owns."""
    app, client, _deps, _tid = await thread_on(tmp_path)
    app.dependency_overrides[get_turns] = FakeLauncher
    app.dependency_overrides[get_bus] = FakeBus
    app.dependency_overrides[get_events] = FakeEvents
    assert (await client.get("/api/threads/web:2:zzz/events")).status_code == 404
    r = await client.post("/api/threads/web:2:zzz/messages", json={"text": "hi"},
                          headers=json_headers())
    assert r.status_code == 404


async def test_the_voice_service_is_swappable_in_both_directions(tmp_path):
    app, client, _deps, _tid = await thread_on(tmp_path)
    voice = FakeVoice()
    app.dependency_overrides[get_voice] = lambda: voice
    r = await client.post("/api/voice/transcribe", content=b"abc",
                          headers={"Origin": ORIGIN, "Content-Type": "audio/mp4"})
    assert r.status_code == 200 and r.json() == {"text": "fake transcript"}
    # the extension follows the Content-Type: the transcription endpoint sniffs it
    assert voice.heard == [(b"abc", "audio.m4a")]

    app.dependency_overrides[get_voice] = lambda: FakeVoice(error="unreadable")
    r = await client.post("/api/voice/transcribe", content=b"abc",
                          headers={"Origin": ORIGIN, "Content-Type": "audio/webm"})
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "bad_audio", "message": "unreadable"}}


async def test_the_sdk_command_list_comes_from_the_port_alone(tmp_path):
    """The `/` menu's third source is the names the CLI reported in the last turn.
    The worker that heard them is not this process, so the router reads the port
    (`MetaSlashCommands` in a deployment) and never the local `TurnService` —
    `__`-prefixed plumbing dropped by the service, not by the route. Nothing
    recorded yet is the empty list, the same answer a deployment that never ran a
    turn gives."""
    app, client, _deps, _tid = await thread_on(tmp_path)
    app.dependency_overrides[get_sdk_commands] = lambda: None
    r = await client.get("/api/commands")
    assert r.status_code == 200
    assert r.json() == {"commands": []}

    app.dependency_overrides[get_sdk_commands] = lambda: FakeSdkCommands(
        ["resume", "__plumbing"])
    r = await client.get("/api/commands")
    assert r.json() == {"commands": [{"name": "resume", "description": "", "source": "sdk"}]}


async def test_stream_deps_is_all_event_stream_reads_off_a_bundle():
    """The SSE route hands `event_stream` two fields instead of the whole bundle, so an
    override of `get_bus` / `get_events` reaches the stream too. This is that shape."""
    events = FakeEvents([{"id": 7, "type": "turn.start", "payload": {}}])
    gen = event_stream(StreamDeps(bus=FakeBus(), turn_events=events), "web:1:x", 2)
    frame = await gen.__anext__()
    await gen.aclose()
    assert frame.startswith("id: 7\nevent: turn\ndata: ")
    assert events.calls == [("web:1:x", 2, 2000)]

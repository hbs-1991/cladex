"""`send_document`: bound to the turn's chat, confined to the agent's home.

Since Wave 4b (D7) the containment root is `agent_home` — the turn's cwd — and a
relative `path` is resolved against it. `agent_home(tmp_path)` below is that
directory; a test that wants a file the tool may send writes it there.

The registration gate lives in `tests/modules/turns/test_turns_tool_gates.py::
test_send_document_needs_a_turn_and_somewhere_to_send`; this file is the body,
built straight from `make_send_document` — every refusal, the caption trim, the
web lane's file store, and the lane workspace's boundary.
"""
from claude_hermes.core.workspace import Workspace
from claude_hermes.modules.files.application.service import FileService
from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore
from claude_hermes.modules.files.infrastructure.repository import FileRepository
from claude_hermes.modules.turns.application.bus import TurnBus
from claude_hermes.modules.turns.domain.value_objects import TurnContext
from claude_hermes.modules.turns.infrastructure.hermes_server import make_handlers
from claude_hermes.modules.turns.infrastructure.publishers import InProcessPublisher
from claude_hermes.modules.turns.infrastructure.repositories import TurnEventRepository
from claude_hermes.modules.turns.infrastructure.tool_handlers.documents import (
    make_send_document,
)


def _ctx():
    return TurnContext(session_key="tg:private:1", chat_id=1, thread_id=None)


def agent_home(home):
    """`<home>/agent_home`, created: the tool's containment root for a `home`."""
    directory = home / "agent_home"
    directory.mkdir(exist_ok=True)
    return directory


class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, chat_id, thread_id, path, caption=None):
        self.calls.append((chat_id, thread_id, path, caption))


def _send(tmp_path, deliver, ctx=None, home=None, **kw):
    """The handler as `make_handlers(home=...)` builds it: contained by
    `<home>/agent_home`, delivering into the turn's chat."""
    root = home if home is not None else tmp_path
    return make_send_document(ctx=ctx if ctx is not None else _ctx(),
                              agent_home=root / "agent_home",
                              deliver_document=deliver, **kw)


def _file_store(engine, home):
    return FileService(FileRepository(engine), LocalBlobStore(home))


async def test_sends_a_real_file(tmp_path):
    rec = _Recorder()
    send = _send(tmp_path, rec)
    f = agent_home(tmp_path) / "transcript.md"
    f.write_text("hello", encoding="utf-8")
    result = await send({"path": str(f), "caption": " note "})
    assert "Sent transcript.md" in result["content"][0]["text"]
    assert rec.calls == [(1, None, str(f.resolve()), "note")]  # bound to ctx chat, trimmed caption


async def test_refuses_missing_file(tmp_path):
    rec = _Recorder()
    send = _send(tmp_path, rec)
    result = await send({"path": str(agent_home(tmp_path) / "nope.md")})
    assert "No such file" in result["content"][0]["text"]
    assert rec.calls == []


async def test_refuses_outside_home(tmp_path):
    rec = _Recorder()
    home = tmp_path / "home"
    home.mkdir()
    send = _send(tmp_path, rec, home=home)
    outsider = tmp_path / "secret.txt"
    outsider.write_text("x", encoding="utf-8")
    result = await send({"path": str(outsider)})
    assert "outside the agent's home" in result["content"][0]["text"]
    assert rec.calls == []


async def test_refuses_a_path_under_home_but_outside_agent_home(tmp_path):
    """The boundary is `agent_home`, not the workspace root (D7, S-5): for the
    deploy owner `root == CH_HOME`, so a root boundary would let a group member
    on the owner's lane name `/data/users/7/…` — or `secrets.env` — by absolute
    path and have it sent."""
    rec = _Recorder()
    send = _send(tmp_path, rec)
    (tmp_path / "users" / "7" / "agent_home").mkdir(parents=True)
    theirs = tmp_path / "users" / "7" / "agent_home" / "notes.md"
    theirs.write_text("x", encoding="utf-8")
    result = await send({"path": str(theirs)})
    assert "outside the agent's home" in result["content"][0]["text"]
    assert str(tmp_path / "agent_home") in result["content"][0]["text"]
    assert rec.calls == []


async def test_a_relative_path_resolves_against_agent_home(tmp_path):
    """The seeded skills name their outputs cwd-relative (`outbox/x.png`, D13)
    and the turn's cwd is `agent_home` — so the tool resolves a relative path
    there, never against the worker process's cwd, which is wherever the
    container happened to start."""
    rec = _Recorder()
    send = _send(tmp_path, rec)
    outbox = agent_home(tmp_path) / "outbox"
    outbox.mkdir()
    (outbox / "chart.png").write_bytes(b"png")
    result = await send({"path": "outbox/chart.png"})
    assert "Sent chart.png" in result["content"][0]["text"]
    assert rec.calls == [(1, None, str((outbox / "chart.png").resolve()), None)]
    # `..` is judged after resolution, so it cannot climb out either.
    (tmp_path / "escape.md").write_text("x", encoding="utf-8")
    result = await send({"path": "../escape.md"})
    assert "outside the agent's home" in result["content"][0]["text"]
    assert len(rec.calls) == 1


async def test_a_lane_workspace_confines_to_its_own_agent_home(tmp_path):
    """Through `make_handlers(workspace=...)`, the path a lane's turn takes: the
    user's `logs/` is under their root and still outside their `agent_home`."""
    ws = Workspace.for_user(tmp_path, "users/7")
    ws.agent_home.mkdir(parents=True)
    ws.logs_dir.mkdir(parents=True)
    (ws.logs_dir / "run.log").write_text("x", encoding="utf-8")
    (ws.agent_home / "ok.md").write_text("x", encoding="utf-8")
    rec = _Recorder()
    ctx = TurnContext(session_key="tg:private:7", chat_id=7, thread_id=None)
    handlers = make_handlers(search=None, costs=None, ctx=ctx, home=tmp_path,
                             workspace=ws, deliver_document=rec)
    refused = await handlers["send_document"]({"path": str(ws.logs_dir / "run.log")})
    assert "outside the agent's home" in refused["content"][0]["text"]
    assert str(ws.agent_home) in refused["content"][0]["text"]
    sent = await handlers["send_document"]({"path": "ok.md"})
    assert "Sent ok.md" in sent["content"][0]["text"]
    assert rec.calls == [(7, None, str((ws.agent_home / "ok.md").resolve()), None)]


async def test_refuses_secrets_env(tmp_path):
    rec = _Recorder()
    send = _send(tmp_path, rec)
    f = agent_home(tmp_path) / "secrets.env"
    f.write_text("TOKEN=abc", encoding="utf-8")
    result = await send({"path": str(f)})
    assert "secrets.env is not sendable" in result["content"][0]["text"]
    assert rec.calls == []


async def test_refuses_empty_file(tmp_path):
    rec = _Recorder()
    send = _send(tmp_path, rec)
    f = agent_home(tmp_path) / "empty.md"
    f.write_text("", encoding="utf-8")
    result = await send({"path": str(f)})
    assert "empty" in result["content"][0]["text"]
    assert rec.calls == []


async def test_refuses_oversize_file(tmp_path):
    rec = _Recorder()
    # `max_bytes` is the composition root's provider; four bytes stands in for the
    # 50MB Telegram ceiling the reply still names.
    send = _send(tmp_path, rec, max_bytes=lambda: 4)
    f = agent_home(tmp_path) / "big.md"
    f.write_text("way too big", encoding="utf-8")
    result = await send({"path": str(f)})
    assert "50MB" in result["content"][0]["text"]
    assert rec.calls == []


async def test_delivery_failure_is_reported(tmp_path):
    async def boom(chat_id, thread_id, path, caption=None):
        raise RuntimeError("telegram down")

    send = _send(tmp_path, boom)
    f = agent_home(tmp_path) / "x.md"
    f.write_text("hi", encoding="utf-8")
    result = await send({"path": str(f)})
    assert "Delivery failed" in result["content"][0]["text"]
    assert "telegram down" in result["content"][0]["text"]


async def test_missing_path_arg(tmp_path):
    rec = _Recorder()
    send = _send(tmp_path, rec)
    result = await send({})
    assert "needs a 'path'" in result["content"][0]["text"]
    assert rec.calls == []


async def test_web_lane_publishes_and_emits_without_telegram(tmp_path, engine):
    doc = agent_home(tmp_path) / "r.md"
    doc.write_text("x", encoding="utf-8")
    bus, events = TurnBus(TurnEventRepository(engine), InProcessPublisher()), []

    async def listener(ev):
        events.append(ev)

    bus.subscribe("web:1:x", listener)

    async def emit(type, payload):
        await bus.emit("web:1:x", "t1", type, payload)

    ctx = TurnContext(session_key="web:1:x", chat_id=0, thread_id=None, turn_id="t1", emit=emit)
    rec = _Recorder()
    send = _send(tmp_path, rec, ctx=ctx, file_store=_file_store(engine, tmp_path))
    out = await send({"path": str(doc)})
    assert rec.calls == []                                   # no Telegram on a web lane
    assert events[0].type == "file"
    assert events[0].payload["name"] == "r.md" and events[0].payload["telegram_sent"] is False
    assert events[0].payload["url"].startswith("/api/files/")
    assert "r.md" in out["content"][0]["text"]


async def test_telegram_lane_still_delivers_and_also_emits(tmp_path, engine):
    doc = agent_home(tmp_path) / "r.md"
    doc.write_text("x", encoding="utf-8")
    events = []

    async def emit(type, payload):
        events.append((type, payload))

    ctx = TurnContext(session_key="tg:private:1", chat_id=1, thread_id=None, turn_id="t1", emit=emit)
    rec = _Recorder()
    send = _send(tmp_path, rec, ctx=ctx, file_store=_file_store(engine, tmp_path))
    await send({"path": str(doc)})
    assert len(rec.calls) == 1
    assert events == [("file", events[0][1])] and events[0][1]["telegram_sent"] is True

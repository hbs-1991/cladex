"""The runner end to end: `TurnService` over the real `SdkTurnExecutor`, the
real repositories on the test engine and the real `TurnBus`, with only the SDK's
`query` faked.

What `tests/modules/turns/test_turns_service.py` pins with a two-line fake
executor is not repeated here; what IS here is the SDK loop's half — the message
stream becoming events, the usage arithmetic, the option composition, the tool
server's ports — driven through the same object the composition root builds.
"""
from dataclasses import dataclass

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultError,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from conftest import ensure_users

from claude_hermes.core.app_context import WikiHooksProvider
from claude_hermes.core.config import Config
from claude_hermes.core.events import TurnStats
from claude_hermes.core.model_catalog import Choice, cli_id
from claude_hermes.modules.memory.infrastructure.turn_hooks import WikiTurnState, wiki_hooks
from claude_hermes.modules.memory.infrastructure.wiki_store import WikiStore
from claude_hermes.modules.sessions.application.services import SessionRouter, ThreadService
from claude_hermes.modules.sessions.infrastructure.repositories import (
    MessageRepository,
    SessionRepository,
    ThreadRepository,
)
from claude_hermes.modules.turns.application.bus import TurnBus
from claude_hermes.modules.turns.application.observation import observe_block
from claude_hermes.modules.turns.application.ports import TurnSettings
from claude_hermes.modules.turns.application.turn_service import TurnService
# The SDK seam moved with the loop in Wave 2: `query` is read off the module
# that calls it, so this is where it has to be patched.
from claude_hermes.modules.turns.infrastructure import sdk_runner
from claude_hermes.modules.turns.infrastructure.publishers import InProcessPublisher
from claude_hermes.modules.turns.infrastructure.repositories import (
    TurnCostRepository,
    TurnEventRepository,
)

# The model every fake below answers as. `Config().model` is the deployment's
# default, so a fake that answered as anything else would be a SUBSTITUTION —
# the runner announces those, and every reply here would grow a notice.
MODEL = Config().model


class FakeResult:
    """Duck-typed ResultMessage stand-in (runner matches on attributes)."""

    def __init__(self, session_id, result, model_usage=None):
        self.session_id = session_id
        self.result = result
        self.is_error = False
        self.model_usage = model_usage


def make_fake_query(captured, session_id="sdk-xyz", reply="hello from agent",
                    model=None):
    async def fake_query(*, prompt, options):
        captured.append(options)
        yield AssistantMessage(content=[TextBlock(text=reply)], model=model or MODEL)
        yield FakeResult(session_id, reply)

    return fake_query


@dataclass
class Stack:
    """One runner and the stores it writes, so a test can read back what a turn
    left behind the way the legacy facade let it."""
    runner: TurnService
    sessions: SessionRepository
    messages: MessageRepository
    threads: ThreadRepository
    events: TurnEventRepository
    bus: TurnBus
    wiki: WikiStore


async def make_stack(engine, tmp_path, *, cfg=None, observer=None, jobs=None,
                     hooks=None, models=None, **executor_kw) -> Stack:
    """`main.build_app`'s runner slice on the test engine: the same objects, minus
    the channels. `executor_kw` reaches `SdkTurnExecutor` (a file store, a
    composer) so a test can wire exactly the port it is about."""
    cfg = cfg if cfg is not None else Config()
    # The lanes below are `tg:private:{1,2,9}` and `web:1:…`, whose owners are
    # those ids (`lane_owner`), and `sessions.owner_id` / `threads.owner_user_id`
    # reference `users(id)` since 0005. One line here rather than in each test.
    await ensure_users(engine, 1, 2, 9)
    wiki = WikiStore(tmp_path)
    wiki.seed()
    settings = TurnSettings.from_config(cfg)
    sessions = SessionRepository(engine)
    messages = MessageRepository(engine)
    threads = ThreadRepository(engine)
    events = TurnEventRepository(engine)
    executor = sdk_runner.SdkTurnExecutor(
        settings=settings, home=tmp_path, search=messages,
        costs=TurnCostRepository(engine), hooks=hooks, **executor_kw)
    bus = TurnBus(events, InProcessPublisher())
    runner = TurnService(
        settings=settings, sessions=SessionRouter(sessions, cfg), messages=messages,
        threads=ThreadService(threads), wiki=wiki, bus=bus, executor=executor,
        observer=observer, jobs=jobs, models=models)
    return Stack(runner, sessions, messages, threads, events, bus, wiki)


async def test_sdk_replaced_cli_error_retries_like_a_process_error(engine, tmp_path,
                                                                    monkeypatch):
    """claude-agent-sdk >= 0.2.126 swallows the CLI's non-zero-exit ProcessError and
    re-raises a generic Exception('Claude Code returned an error result: <subtype>')
    instead. That is the same 'the CLI died, the stored session may be stale' signal,
    and it must take the same reset-and-retry-once path — otherwise a thread with a
    broken transcript errors on every message until the daily reset (seen live
    23.07.2026: 'error result: success')."""
    stack = await make_stack(engine, tmp_path)
    captured = []
    monkeypatch.setattr(sdk_runner, "query", make_fake_query(captured))
    await stack.runner.run_turn("tg:private:1", "first")

    calls = []

    async def flaky_query(*, prompt, options):
        calls.append(options)
        if options.resume is not None:
            raise Exception("Claude Code returned an error result: error_during_execution")
        captured.append(options)
        yield AssistantMessage(content=[TextBlock(text="fresh reply")], model=MODEL)
        yield FakeResult("sdk-new", "fresh reply")

    monkeypatch.setattr(sdk_runner, "query", flaky_query)
    reply = await stack.runner.run_turn("tg:private:1", "second")

    assert reply == "fresh reply"
    assert [c.resume for c in calls] == ["sdk-xyz", None]


async def test_an_unrelated_exception_still_propagates_and_keeps_the_session(
        engine, tmp_path, monkeypatch):
    """Only CLI-death shapes may trigger the retry: an arbitrary error must not
    silently throw away a healthy thread's context."""
    stack = await make_stack(engine, tmp_path)
    captured = []
    monkeypatch.setattr(sdk_runner, "query", make_fake_query(captured))
    await stack.runner.run_turn("tg:private:1", "first")

    async def always_fails(*, prompt, options):
        raise Exception("connection reset by peer")
        yield  # pragma: no cover - unreachable, keeps this an async generator

    monkeypatch.setattr(sdk_runner, "query", always_fails)
    try:
        await stack.runner.run_turn("tg:private:1", "second")
        raised = False
    except Exception as e:
        raised = "connection reset" in str(e)
    assert raised
    row = await stack.sessions.get("tg:private:1")
    assert row is not None and row["sdk_session_id"] == "sdk-xyz"   # session kept


def test_observe_block_classifies():
    stats = TurnStats()
    observe_block(stats, ToolUseBlock(id="1", name="Bash", input={"command": "ls"}))
    observe_block(stats, ToolUseBlock(id="3", name="Write",
                                      input={"file_path": "/x/.claude/skills/deploy/SKILL.md"}))
    observe_block(stats, ToolUseBlock(id="4", name="Skill", input={"skill": "deploy"}))
    observe_block(stats, ToolUseBlock(id="5", name="Edit",
                                      input={"file_path": "C:\\x\\.claude\\skills\\deploy\\references\\a.md"}))
    observe_block(stats, ToolUseBlock(id="6", name="Write",
                                      input={"file_path": "/x/.claude/skills/README.md"}))
    assert stats.tool_uses == 5
    assert stats.wrote_wiki is False
    assert stats.wrote_skill is True
    assert stats.skill_invocations == ["deploy"]
    assert stats.skill_patches == ["deploy", "deploy"]


async def test_runner_reports_stats_to_learning(engine, tmp_path, monkeypatch):
    class FakeLearning:
        def __init__(self):
            self.calls = []

        async def after_turn(self, session_key, stats, *, owner_id=0):
            self.calls.append((session_key, stats))

    # The observer is a constructor argument now: `runner._learning = x` is gone.
    learning = FakeLearning()
    stack = await make_stack(engine, tmp_path, observer=learning)

    async def fake_query(*, prompt, options):
        yield AssistantMessage(
            content=[
                ToolUseBlock(id="1", name="Bash", input={"command": "ls"}),
                TextBlock(text="done"),
            ],
            model=MODEL,
        )
        yield FakeResult("sdk-1", "done")

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    await stack.runner.run_turn("tg:private:1", "hi")
    assert len(learning.calls) == 1
    key, stats = learning.calls[0]
    assert key == "tg:private:1" and stats.tool_uses == 1


def test_agent_tool_is_allowed_so_refuter_is_reachable():
    """The refuter subagent is useless if the model cannot spawn it. The tool was
    renamed Task -> Agent in CLI 2.1.63; both names stay allowed."""
    from claude_hermes.modules.turns.domain.policies import _BUILTIN_TOOLS

    assert "Agent" in _BUILTIN_TOOLS
    assert "Task" in _BUILTIN_TOOLS


async def test_turn_wires_all_four_wiki_hook_events(engine, tmp_path, monkeypatch):
    wiki = WikiStore(tmp_path)
    stack = await make_stack(
        engine, tmp_path,
        hooks=WikiHooksProvider(wiki, wiki_hooks=wiki_hooks, new_state=WikiTurnState))
    captured = []
    monkeypatch.setattr(sdk_runner, "query", make_fake_query(captured))
    await stack.runner.run_turn("tg:private:1", "hi")

    hooks = captured[0].hooks
    assert set(hooks) == {"PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit"}
    pre = [m.matcher for m in hooks["PreToolUse"]]
    assert "Bash" in pre and "Write|Edit" in pre        # the bash guard is still there
    post = [m.matcher for m in hooks["PostToolUse"]]
    assert "Write|Edit" in post and "WebFetch" in post


def test_review_fork_cannot_spawn_subagents():
    """The review fork is restricted to skills/memory work — it must not be able
    to fan out subagents of its own."""
    from claude_hermes.modules.learning.domain.constants import REVIEW_TOOLS

    assert "Agent" not in REVIEW_TOOLS
    assert "Task" not in REVIEW_TOOLS


def streaming_query(captured):
    async def fake_query(*, prompt, options):
        captured.append(options)
        yield StreamEvent(uuid="u0", session_id="s", event={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "Need to read a.md"}})
        yield AssistantMessage(content=[ThinkingBlock(thinking="Need to read a.md",
                                                      signature="sig")], model=MODEL)
        yield StreamEvent(uuid="u1", session_id="s", event={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Let me "}})
        yield StreamEvent(uuid="u2", session_id="s", event={
            "type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "look."}})
        yield AssistantMessage(content=[TextBlock(text="Let me look.")], model=MODEL)
        yield AssistantMessage(content=[ToolUseBlock(id="c1", name="Read",
                                                     input={"file_path": "a.md"})], model=MODEL)
        yield UserMessage(content=[ToolResultBlock(tool_use_id="c1", content="file body",
                                                   is_error=False)])
        yield AssistantMessage(content=[TextBlock(text="Done.")], model=MODEL)
        yield FakeResult("sdk-1", "Done.")
    return fake_query


async def test_turn_emits_the_event_sequence(engine, tmp_path, monkeypatch):
    stack = await make_stack(engine, tmp_path)
    captured = []
    monkeypatch.setattr(sdk_runner, "query", streaming_query(captured))
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    reply = await stack.runner.run_turn("web:1:x", "hello", user_id=1, channel="web")
    assert reply == "Done."
    assert captured[0].include_partial_messages is True
    assert captured[0].thinking == {"type": "adaptive", "display": "summarized"}
    assert [e.type for e in seen] == [
        "turn.start", "user.message", "thinking.delta", "thinking", "text.delta",
        "text.delta", "text.block", "tool.call", "tool.result", "text.block", "turn.end",
    ]
    # `turn.start` names what the turn ASKED for: nothing has chosen anything
    # here, so it is the deploy default with no effort (AC #2).
    assert seen[0].payload == {"channel": "web", "user_id": 1,
                               "model": MODEL, "effort": None}
    assert seen[1].payload == {"text": "hello", "attachments": []}
    assert seen[2].payload == {"delta": "Need to read a.md"}
    assert seen[3].payload == {"text": "Need to read a.md"}
    assert seen[7].payload == {"call_id": "c1", "name": "Read", "input": {"file_path": "a.md"}}
    assert seen[8].payload["call_id"] == "c1" and seen[8].payload["output"] == "file body"
    assert seen[8].payload["is_error"] is False and seen[8].payload["duration_ms"] >= 0
    assert seen[-1].payload["status"] == "ok" and seen[-1].payload["sdk_session_id"] == "sdk-1"
    assert seen[-1].payload["usage"] is None          # the SDK reported no usage
    assert all(e.turn_id == seen[0].turn_id for e in seen)
    stored = await stack.events.after("web:1:x")
    assert not {"text.delta", "thinking.delta"} & {r["type"] for r in stored}
    t = await stack.threads.get("web:1:x")
    assert t["channel"] == "web" and t["owner_user_id"] == 1


async def test_tool_output_is_truncated_to_config(engine, tmp_path, monkeypatch):
    stack = await make_stack(engine, tmp_path, cfg=Config(web_tool_output_max_chars=5))

    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[ToolUseBlock(id="c1", name="Bash", input={})], model=MODEL)
        yield UserMessage(content=[ToolResultBlock(tool_use_id="c1", content="0123456789")])
        yield FakeResult("sdk-1", "ok")

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    await stack.runner.run_turn("web:1:x", "hi", user_id=1, channel="web")
    result = next(e for e in seen if e.type == "tool.result")
    assert result.payload["output"] == "0123…"


async def test_tool_call_input_is_truncated_per_string(engine, tmp_path, monkeypatch):
    """The arguments are the bigger half of a tool's pair — a Write carries the whole
    file — and they were stored uncapped. `turn_events` is never pruned, so every one
    of them is shipped again on every open of that thread.

    Per string, not over the serialized whole: the browser renders the arguments as a
    named structure and the Telegram presenter reads keys out of them, so the keys and
    the nesting survive and only an over-long value loses its tail.
    """
    stack = await make_stack(engine, tmp_path, cfg=Config(web_tool_output_max_chars=5))
    args = {"file_path": "a.md", "content": "0123456789",
            "edits": [{"old": "0123456789", "new": "x"}], "count": 10}

    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[ToolUseBlock(id="c1", name="Write", input=args)],
                               model=MODEL)
        yield FakeResult("sdk-1", "ok")

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    await stack.runner.run_turn("web:1:x", "hi", user_id=1, channel="web")
    call = next(e for e in seen if e.type == "tool.call")
    assert call.payload["input"] == {
        "file_path": "a.md",                      # short enough to survive whole
        "content": "0123…",
        "edits": [{"old": "0123…", "new": "x"}],  # nested, and still a structure
        "count": 10,                              # not a string, not touched
    }


async def test_web_lane_send_document_publishes_and_emits_file(engine, tmp_path,
                                                                monkeypatch):
    """The runner must hand its file store to the tool server, or `send_document`
    is dead on a web lane: no chat to deliver to and no store to publish into."""
    from claude_hermes.modules.files.application.service import FileService
    from claude_hermes.modules.files.infrastructure.local_blob_store import LocalBlobStore
    from claude_hermes.modules.files.infrastructure.repository import FileRepository

    stack = await make_stack(
        engine, tmp_path,
        file_store=FileService(FileRepository(engine), LocalBlobStore(tmp_path)))
    # Under `agent_home`, the turn's cwd: since Wave 4b (D7) that directory is the
    # tool's containment root, on the legacy head as on a lane's workspace.
    (tmp_path / "agent_home").mkdir(exist_ok=True)
    report = tmp_path / "agent_home" / "report.md"
    report.write_text("weekly numbers", encoding="utf-8")

    handlers: list[dict] = []
    real_build = sdk_runner.build_hermes_server

    def build(built):
        handlers.append(built)
        return real_build(built)

    monkeypatch.setattr(sdk_runner, "build_hermes_server", build)
    said = []

    async def fake_query(*, prompt, options):
        said.append((await handlers[0]["send_document"](
            {"path": str(report)}))["content"][0]["text"])
        yield AssistantMessage(content=[TextBlock(text="sent")], model=MODEL)
        yield FakeResult("sdk-1", "sent")

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    await stack.runner.run_turn("web:1:x", "send me the report", user_id=1, channel="web")
    assert "to the conversation" in said[0]
    ev = next(e for e in seen if e.type == "file")
    assert ev.payload["name"] == "report.md" and ev.payload["telegram_sent"] is False
    assert ev.payload["url"] == f"/api/files/{ev.payload['file_id']}"


def usage_query(captured):
    """Two main-loop API calls plus a subagent's: the result's model_usage is the
    SUM the SDK reports, so the turn.end must carry the LAST call's prompt instead."""
    first = {"input_tokens": 10, "cache_read_input_tokens": 1000,
             "cache_creation_input_tokens": 200, "output_tokens": 1}
    last = {"input_tokens": 12, "cache_read_input_tokens": 1300,
            "cache_creation_input_tokens": 50, "output_tokens": 1}
    summed = {"inputTokens": 22, "cacheReadInputTokens": 2300, "cacheCreationInputTokens": 250,
              "outputTokens": 40, "costUSD": 0.2, "contextWindow": 200000,
              "maxOutputTokens": 32000, "webSearchRequests": 0}

    async def fake_query(*, prompt, options):
        captured.append(options)
        yield AssistantMessage(content=[ToolUseBlock(id="c1", name="Read",
                                                     input={"file_path": "a.md"})],
                               model=MODEL, usage=first)
        yield UserMessage(content=[ToolResultBlock(tool_use_id="c1", content="body",
                                                   is_error=False)])
        # a subagent's call: not the main loop's window
        yield AssistantMessage(content=[TextBlock(text="sub")], model="haiku",
                               parent_tool_use_id="c1",
                               usage={"input_tokens": 99999, "cache_read_input_tokens": 0,
                                      "cache_creation_input_tokens": 0, "output_tokens": 1})
        yield AssistantMessage(content=[TextBlock(text="Done.")], model=MODEL, usage=last)
        yield FakeResult("sdk-2", "Done.", model_usage={MODEL: summed})
    return fake_query


async def test_turn_end_carries_the_last_calls_context_usage(engine, tmp_path, monkeypatch):
    stack = await make_stack(engine, tmp_path)
    captured = []
    monkeypatch.setattr(sdk_runner, "query", usage_query(captured))
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    await stack.runner.run_turn("web:1:x", "hello", user_id=1, channel="web")
    end = seen[-1]
    assert end.type == "turn.end"
    assert end.payload["usage"] == {MODEL: {
        "context_window": 200000,
        "input_tokens": 12, "cache_read_input_tokens": 1300, "cache_creation_input_tokens": 50,
        "output_tokens": 40,                             # the turn's total, from model_usage
    }}
    stored = await stack.events.after("web:1:x")
    assert [r for r in stored if r["type"] == "turn.end"][0]["payload"]["usage"][MODEL]["input_tokens"] == 12


async def test_usage_without_a_window_still_carries_the_tokens(engine, tmp_path, monkeypatch):
    stack = await make_stack(engine, tmp_path)
    captured = []

    async def fake_query(*, prompt, options):
        captured.append(options)
        yield AssistantMessage(content=[TextBlock(text="hi")], model=MODEL,
                               usage={"input_tokens": 5, "cache_read_input_tokens": 7,
                                      "cache_creation_input_tokens": 0, "output_tokens": 3})
        yield FakeResult("sdk-3", "hi")                  # no model_usage at all
    monkeypatch.setattr(sdk_runner, "query", fake_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:y", listener)
    await stack.runner.run_turn("web:1:y", "hello", user_id=1, channel="web")
    assert seen[-1].payload["usage"] == {MODEL: {
        "context_window": None, "input_tokens": 5, "cache_read_input_tokens": 7,
        "cache_creation_input_tokens": 0, "output_tokens": 3,
    }}


async def test_a_1m_model_reports_its_window_under_the_suffixed_key(engine, tmp_path, monkeypatch):
    """`model: claude-opus-5[1m]` is how the CLI gives Opus 5 its 1M window; the
    CLI then keys `model_usage` by the suffixed id while the API names the model
    without it. The shape below is what CLI 2.1.218 reported — including its own
    Haiku side call, which must never stand in for the main loop's window."""
    stack = await make_stack(engine, tmp_path, cfg=Config(model="claude-opus-5"))

    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[TextBlock(text="ok")], model="claude-opus-5",
                               usage={"input_tokens": 169, "cache_read_input_tokens": 0,
                                      "cache_creation_input_tokens": 0, "output_tokens": 1})
        yield FakeResult("sdk-4", "ok", model_usage={
            "claude-haiku-4-5-20251001": {
                "inputTokens": 523, "outputTokens": 12, "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0, "costUSD": 0.0006, "contextWindow": 200000,
                "maxOutputTokens": 32000, "webSearchRequests": 0},
            "claude-opus-5[1m]": {
                "inputTokens": 169, "outputTokens": 4, "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0, "costUSD": 0.05, "contextWindow": 1000000,
                "maxOutputTokens": 64000, "webSearchRequests": 0},
        })
    monkeypatch.setattr(sdk_runner, "query", fake_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:z", listener)
    await stack.runner.run_turn("web:1:z", "hello", user_id=1, channel="web")
    assert seen[-1].payload["usage"] == {"claude-opus-5": {
        "context_window": 1000000, "input_tokens": 169, "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0, "output_tokens": 4,
    }}



# --- which model the CLI is actually handed ---------------------------------
# The application layer's arithmetic lives in
# `tests/modules/turns/test_turns_models.py`; what is pinned here is the half
# only the real SDK loop can answer — the options it builds, and what it makes of
# the message stream that comes back.


class FakeLaneModels:
    """`LaneModels` by shape only: one pair for every lane."""

    def __init__(self, choice):
        self._choice = choice

    async def effective(self, session_key, owner_id):
        return self._choice


async def test_a_lane_that_chose_nothing_gets_the_deploy_default_and_no_effort(
        engine, tmp_path, monkeypatch):
    """AC #2: `cli_id(cfg.model)`, no `effort` option, and the env that stops the
    CLI substituting a model behind our back."""
    stack = await make_stack(engine, tmp_path)
    captured = []
    monkeypatch.setattr(sdk_runner, "query", make_fake_query(captured))
    await stack.runner.run_turn("tg:private:1", "hi")

    assert captured[0].model == cli_id(MODEL)
    assert captured[0].effort is None
    # One line in `workspace_env`, and this is the assertion that it reaches the
    # foreground turn: without it the CLI's own availability chain would answer
    # with a different model and say so nowhere.
    assert captured[0].env["CLAUDE_CODE_NO_MODEL_FALLBACK"] == "1"


async def test_the_lanes_pair_is_what_the_cli_receives(engine, tmp_path, monkeypatch):
    """AC #4. `cli_id` is the only spelling that reaches the CLI; the catalog id is
    what the row, the wire and the events use."""
    stack = await make_stack(
        engine, tmp_path,
        models=FakeLaneModels(Choice("claude-opus-5-5",
                                     cli_id("claude-opus-5-5"), "xhigh")))
    captured = []
    monkeypatch.setattr(sdk_runner, "query",
                        make_fake_query(captured, model="claude-opus-5-5"))
    await stack.runner.run_turn("tg:private:1", "hi")

    assert captured[0].model == cli_id("claude-opus-5-5")
    assert captured[0].effort == "xhigh"


async def test_the_model_that_answered_is_what_turn_end_names(engine, tmp_path,
                                                              monkeypatch):
    """AC #5, P5: the label comes from `AssistantMessage.model`, never from the id
    the turn asked for — and every comparison goes through the catalog, so the
    suffixed spelling some CLI versions answer with is the same model."""
    stack = await make_stack(engine, tmp_path, cfg=Config(model="claude-opus-5"))

    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[TextBlock(text="ok")],
                               model="claude-opus-5[1m]")
        yield FakeResult("sdk-1", "ok")

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    await stack.runner.run_turn("web:1:x", "hi", user_id=1, channel="web")
    assert seen[-1].payload["model"] == "claude-opus-5"
    assert seen[-1].payload["fallback_from"] is None


async def test_the_clis_own_side_call_is_never_the_model_that_ran(engine, tmp_path,
                                                                  monkeypatch):
    """Every run also shows a Haiku entry — the CLI's own side call. It is not a
    main-loop message, and nothing that reads "the model that ran" may see it."""
    stack = await make_stack(engine, tmp_path)

    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[ToolUseBlock(id="c1", name="Read", input={})],
                               model=MODEL)
        yield UserMessage(content=[ToolResultBlock(tool_use_id="c1", content="x")])
        yield AssistantMessage(content=[TextBlock(text="sub")],
                               model="claude-haiku-4-5-20251001",
                               parent_tool_use_id="c1")
        yield AssistantMessage(content=[TextBlock(text="done")], model=MODEL)
        yield FakeResult("sdk-1", "done")

    monkeypatch.setattr(sdk_runner, "query", fake_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    await stack.runner.run_turn("web:1:x", "hi", user_id=1, channel="web")
    assert seen[-1].payload["model"] == MODEL
    assert seen[-1].payload["fallback_from"] is None


def _refusal_text(model_id: str) -> str:
    return (f"There's an issue with the selected model ({model_id}). It may not "
            "exist or you may not have access to it.")


def _rejection(model_id: str) -> ResultError:
    """The exception the SDK raises when the API refuses a model id — the shape
    `scripts/model_matrix.py` recorded live (story Dev Notes). Note the subtype is
    `success`: nothing may classify a rejection from the exception's text."""
    return ResultError("Claude Code returned an error result: success", {
        "subtype": "success", "errors": [], "result": _refusal_text(model_id),
        "api_error_status": 404, "terminal_reason": "api_error",
    }, 1)


async def test_a_refused_model_falls_back_once_and_says_so(engine, tmp_path,
                                                           monkeypatch):
    """AC #12 end to end, through the real SDK loop: the errored message's text
    never becomes a `text.block`, the retry runs on the deploy default with the
    same resume id, and the session is not reset."""
    stack = await make_stack(
        engine, tmp_path,
        models=FakeLaneModels(Choice("claude-fable-5-1",
                                     cli_id("claude-fable-5-1"), "high")))
    captured = []
    monkeypatch.setattr(sdk_runner, "query", make_fake_query(captured, model=MODEL))
    await stack.runner.run_turn("web:1:x", "first", user_id=1, channel="web")
    captured.clear()

    async def rejecting_query(*, prompt, options):
        captured.append(options)
        if options.model == cli_id("claude-fable-5-1"):
            # Exactly what the CLI does: an errored assistant message whose model
            # is the synthetic stand-in, then the call raises.
            yield AssistantMessage(
                content=[TextBlock(text=_refusal_text("claude-fable-5-1"))],
                model="<synthetic>", error="model_not_found")
            raise _rejection("claude-fable-5-1")
        yield AssistantMessage(content=[TextBlock(text="Done.")], model=MODEL)
        yield FakeResult("sdk-xyz", "Done.")

    monkeypatch.setattr(sdk_runner, "query", rejecting_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:x", listener)
    reply = await stack.runner.run_turn("web:1:x", "second", user_id=1, channel="web")

    assert [o.model for o in captured] == [cli_id("claude-fable-5-1"), cli_id(MODEL)]
    assert [o.resume for o in captured] == ["sdk-xyz", "sdk-xyz"]   # no reset
    assert captured[1].effort is None
    blocks = [e.payload["text"] for e in seen if e.type == "text.block"]
    assert not any("issue with the selected model" in b for b in blocks)
    assert reply.startswith("Fable 5.1 is unavailable right now")
    end = seen[-1].payload
    assert end["status"] == "ok" and end["model"] == MODEL
    assert end["fallback_from"] == "claude-fable-5-1"
    row = await stack.sessions.get("web:1:x")
    assert row is not None and row["sdk_session_id"] == "sdk-xyz"
    assert row["model"] == MODEL                  # the pair that actually ran


async def test_a_refusal_after_a_tool_call_is_not_a_rejection(engine, tmp_path,
                                                              monkeypatch):
    """A 4xx once a tool has run is an ordinary API failure of a model that was
    working a moment ago — re-running the whole turn would repeat the tool's side
    effects. Today's path takes it, and the CLI's own sentence reaches
    `turn.end.error` rather than the result subtype."""
    stack = await make_stack(engine, tmp_path)

    async def failing_query(*, prompt, options):
        yield AssistantMessage(content=[ToolUseBlock(id="c1", name="Bash", input={})],
                               model=MODEL)
        yield UserMessage(content=[ToolResultBlock(tool_use_id="c1", content="ok")])
        yield AssistantMessage(content=[TextBlock(text="API Error: 429 overloaded")],
                               model="<synthetic>", error="rate_limit")
        raise ResultError("Claude Code returned an error result: success", {
            "subtype": "success", "errors": [], "result": "API Error: 429 overloaded",
            "api_error_status": 429, "terminal_reason": "api_error"}, 1)

    monkeypatch.setattr(sdk_runner, "query", failing_query)
    seen = []

    async def listener(ev):
        seen.append(ev)

    stack.bus.subscribe("web:1:y", listener)
    with pytest.raises(Exception):
        await stack.runner.run_turn("web:1:y", "hi", user_id=1, channel="web")

    blocks = [e.payload["text"] for e in seen if e.type == "text.block"]
    assert blocks == []                       # an errored message is not the reply
    end = seen[-1].payload
    assert end["status"] == "error" and end["error"] == "API Error: 429 overloaded"
    assert end["model"] == MODEL and end["fallback_from"] is None

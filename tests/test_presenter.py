import asyncio

from claude_hermes.modules.telegram.presentation.formatting import status_for
from claude_hermes.modules.telegram.presentation.presenter import Presenter
from claude_hermes.modules.turns.domain.events import TurnEvent


class SpySender:
    def __init__(self):
        self.drafts = []
        self.finals = []

    async def send_draft(self, chat_id, thread_id, status, draft_id=1):
        self.drafts.append((chat_id, thread_id, status))

    async def send(self, chat_id, thread_id, text):
        self.finals.append((chat_id, thread_id, text))


def test_status_for_maps_tools_to_one_line_statuses():
    assert "web" in status_for("WebSearch").lower()
    assert "web" in status_for("WebFetch").lower()
    assert "read" in status_for("Grep").lower()
    assert "writ" in status_for("Edit").lower()
    assert "command" in status_for("Bash").lower()
    assert "subagent" in status_for("Agent").lower()
    assert "memory" in status_for("mcp__hermes__session_search").lower()
    assert "schedul" in status_for("mcp__hermes__cron_manage").lower()
    assert "writ" in status_for("").lower()          # "" → the model is writing prose
    assert status_for("SomeUnknownTool")             # unknown tools still get a status


def test_status_for_names_the_skill_being_used():
    assert status_for("Skill", {"skill": "gpt-image-2"}) == "🧩 Using skill: gpt-image-2"
    assert status_for("Skill", {"name": "deploy"}) == "🧩 Using skill: deploy"
    assert "skill" in status_for("Skill").lower()    # no input → generic skill line


def test_status_for_image_generation_bash_is_dedicated():
    cmd = {"command": "bash .claude/skills/gpt-image-2/scripts/gen.sh --prompt x --out y"}
    assert "generat" in status_for("Bash", cmd).lower()
    # an ordinary Bash command keeps the generic status
    assert "command" in status_for("Bash", {"command": "ls -la"}).lower()


async def test_identical_statuses_push_one_draft():
    sender = SpySender()
    p = Presenter(sender, 42, 9, chat_action=_noop)
    await p.on_event("WebSearch")
    await p.on_event("WebFetch")      # same status string → no second draft
    await p.on_event("Bash")
    assert [s for _, _, s in sender.drafts] == [status_for("WebSearch"), status_for("Bash")]
    assert all(thread == 9 for _, thread, _ in sender.drafts)


async def test_finalize_sends_the_reply():
    sender = SpySender()
    await Presenter(sender, 42, None, chat_action=_noop).finalize("done")
    assert sender.finals == [(42, None, "done")]


async def test_typing_loop_ticks_and_stops():
    sender = SpySender()
    ticks = []

    async def chat_action():
        ticks.append(1)

    async with Presenter(sender, 42, 9, chat_action=chat_action, typing_interval=0.01):
        await asyncio.sleep(0.05)
    before = len(ticks)
    assert before >= 2                     # it ticked while the turn ran
    await asyncio.sleep(0.05)
    assert len(ticks) == before            # and stopped on exit


async def test_typing_failure_does_not_escape():
    sender = SpySender()

    async def bad_action():
        raise RuntimeError("telegram down")

    async with Presenter(sender, 42, 9, chat_action=bad_action, typing_interval=0.01):
        await asyncio.sleep(0.03)          # must not raise


async def _noop():
    return None


def _ev(type, payload, turn_id="t1"):
    return TurnEvent("tg:private:42", turn_id, 1, "2026-07-12T10:00:00+00:00", type, payload)


async def test_bus_events_drive_the_same_status_drafts(tmp_path):
    sender = SpySender()
    p = Presenter(sender, 42, 9, chat_action=_noop)
    await p.on_turn_event(_ev("turn.start", {"channel": "telegram", "user_id": 42}))
    await p.on_turn_event(_ev("tool.call", {"call_id": "c1", "name": "WebSearch", "input": {}}))
    await p.on_turn_event(_ev("tool.result", {"call_id": "c1", "output": "", "is_error": False,
                                              "duration_ms": 3}))
    await p.on_turn_event(_ev("text.delta", {"delta": "He"}))
    await p.on_turn_event(_ev("text.delta", {"delta": "llo"}))
    await p.on_turn_event(_ev("tool.call", {"call_id": "c2", "name": "Bash",
                                            "input": {"command": "ls"}}))
    assert [s for _, _, s in sender.drafts] == [
        status_for("WebSearch"), status_for(""), status_for("Bash", {"command": "ls"}),
    ]


async def test_web_turns_on_a_telegram_lane_are_ignored(tmp_path):
    sender = SpySender()
    p = Presenter(sender, 42, 9, chat_action=_noop)
    await p.on_turn_event(_ev("turn.start", {"channel": "web", "user_id": 42}))
    await p.on_turn_event(_ev("tool.call", {"call_id": "c1", "name": "Bash", "input": {}}))
    assert sender.drafts == []

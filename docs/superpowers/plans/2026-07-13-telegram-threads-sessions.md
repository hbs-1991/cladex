# Telegram Threads as Session Lanes + Rich Messages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Telegram thread becomes the agent's session lane — the user opens a topic and gets an isolated, eternal agent context; replies stream as a live status draft and land as a formatted rich message; every cron job gets its own topic.

**Architecture:** The session key gains a `:t<thread_id>` suffix (absent thread → the legacy key, byte-for-byte, so nothing migrates). Idle/daily resets are disabled by default; `/new` is the manual reset. Two new gateway modules own the entire Bot API 10.x surface — `gateway/rich.py` (rich messages + streaming drafts) and `gateway/topics.py` (create/rename/close topics) — both driven by an injected `call(method, payload) -> dict` so unit tests never touch the network. `AgentRunner` stays Telegram-unaware: it gains one optional `progress` callback fed from the block loop it already runs for learning stats.

**Tech Stack:** Python 3.12, `claude-agent-sdk`, `aiogram` (long-polling), `aiosqlite`, `pytest` (`asyncio_mode=auto`). One new dependency: `httpx` (raw Bot API 10.x calls).

**Spec:** `docs/superpowers/specs/2026-07-13-telegram-threads-sessions-design.md`

## Global Constraints

- **All Bot API 10.x calls live in `gateway/rich.py` and `gateway/topics.py` only.** No other module may name `sendRichMessage`, `sendRichMessageDraft`, `createForumTopic`, `editForumTopic`, or `closeForumTopic`.
- **The legacy session key must not change.** `build_session_key("private", 42)` must still return exactly `tg:private:42` when there is no thread. A test guards this.
- **No migration of `sessions`, `messages_fts`, `turn_costs`.** The only schema change is one nullable column: `jobs.deliver_thread_id INTEGER`.
- **The progress UI is best-effort.** A failing draft, chat action, or topic rename must never abort a turn — swallow and log.
- **Drafts carry a status line only**, never the reply body, and are pushed only when the status string changes.
- **Final-send degradation chain:** `sendRichMessage(markdown)` → `sendRichMessage(html)` → plain `sendMessage` + existing `chunk_text`.
- **Tests never hit the network.** Every Telegram call goes through an injected `call` coroutine; tests pass a fake.
- Run tests with `.venv/Scripts/python -m pytest` (Windows dev box).

---

### Task 1: Verify the live Bot API 10.x surface (spike, no production code)

Bot API 10.0 shipped a bug where `sendMessage` with `message_thread_id` failed for private-chat topics (`400: message thread not found`, tdlib/telegram-bot-api#847). Spec Open Question #1 says: verify before building on it. This task produces a written verdict, not code.

**Files:**
- Create: `docs/superpowers/notes/2026-07-13-bot-api-10-verification.md`

- [ ] **Step 1: Enable Threaded Mode**

In @BotFather → your bot → Bot Settings (Mini App) → enable **Threaded Mode**. Then open the private chat with the bot and create a topic by hand. Note its `message_thread_id` (it appears in the next update the bot receives; print it with the snippet below).

- [ ] **Step 2: Capture a real thread id**

```bash
# .env already holds TELEGRAM_BOT_TOKEN
TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)
curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | python -m json.tool | grep -E "message_thread_id|is_topic_message|chat\"|\"id\""
```
Expected: a `message_thread_id` on the message you sent inside the topic. Record `CHAT_ID` and `THREAD_ID`.

- [ ] **Step 3: Probe the four methods that the design depends on**

```bash
TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)
CHAT_ID=<your chat id>; THREAD_ID=<the topic id from step 2>

# 3a. rich message into a private-chat topic (the tdlib#847 risk)
curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendRichMessage" \
  -H 'Content-Type: application/json' \
  -d "{\"chat_id\":$CHAT_ID,\"message_thread_id\":$THREAD_ID,\"rich_message\":{\"markdown\":\"**bold** and a table\\n\\n| a | b |\\n|---|---|\\n| 1 | 2 |\"}}"

# 3b. streaming draft
curl -s -X POST "https://api.telegram.org/bot$TOKEN/sendRichMessageDraft" \
  -H 'Content-Type: application/json' \
  -d "{\"chat_id\":$CHAT_ID,\"message_thread_id\":$THREAD_ID,\"draft_id\":1,\"rich_message\":{\"markdown\":\"🔎 Searching the web…\"}}"

# 3c. bot creates a topic in the PRIVATE chat (Bot API 10.0 allows this)
curl -s -X POST "https://api.telegram.org/bot$TOKEN/createForumTopic" \
  -H 'Content-Type: application/json' \
  -d "{\"chat_id\":$CHAT_ID,\"name\":\"⏰ test job\"}"

# 3d. close the topic created in 3c (use the message_thread_id it returned)
curl -s -X POST "https://api.telegram.org/bot$TOKEN/closeForumTopic" \
  -H 'Content-Type: application/json' \
  -d "{\"chat_id\":$CHAT_ID,\"message_thread_id\":<id from 3c>}"
```

Expected: `{"ok":true,...}` for all four. The draft appears for ~30s and then vanishes (it is ephemeral — that is correct behaviour, not a failure).

- [ ] **Step 4: Write the verdict**

Create `docs/superpowers/notes/2026-07-13-bot-api-10-verification.md` recording, for each of 3a–3d: the exact request, the exact response, and OK / BROKEN. If **3a is BROKEN**, the fallback is defined in the spec: keep threads, ship plain `sendMessage`, and set `rich_enabled: false` — Task 6 still builds the module, Task 9 just wires the flag off. If **3c is BROKEN**, cron jobs deliver flat (`deliver_thread_id = NULL`), which Task 10 already handles as its failure path.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/notes/2026-07-13-bot-api-10-verification.md
git commit -m "docs: verify Bot API 10.x rich messages and private-chat topics against the live bot"
```

---

### Task 2: Thread id in the session key

**Files:**
- Modify: `src/claude_hermes/sessions.py:19-29`
- Modify: `src/claude_hermes/gateway/events.py:4-10`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces: `build_session_key(chat_type: str, chat_id: int | str, user_id: int | str | None = None, thread_id: int | None = None) -> str`
- Produces: `MessageEvent(chat_type, chat_id, user_id, text, mentioned, thread_id: int | None = None)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sessions.py`:

```python
def test_build_session_key_with_thread():
    assert build_session_key("private", 42, thread_id=9) == "tg:private:42:t9"
    assert build_session_key("supergroup", -100, user_id=7, thread_id=3) == "tg:supergroup:-100:7:t3"


def test_absent_thread_keeps_the_legacy_key():
    # Backward-compatibility guard: existing rows in sessions/messages_fts/turn_costs
    # are keyed by the old string. A zero/None thread must reproduce it byte-for-byte.
    assert build_session_key("private", 42, thread_id=None) == "tg:private:42"
    assert build_session_key("private", 42, thread_id=0) == "tg:private:42"


def test_chat_id_from_threaded_key():
    assert chat_id_from_key("tg:private:42:t9") == 42
    assert chat_id_from_key("tg:supergroup:-5:7:t3") == -5
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_sessions.py -k thread -v`
Expected: FAIL — `build_session_key() got an unexpected keyword argument 'thread_id'`

- [ ] **Step 3: Implement**

Replace `build_session_key` in `src/claude_hermes/sessions.py`:

```python
def build_session_key(
    chat_type: str,
    chat_id: int | str,
    user_id: int | str | None = None,
    thread_id: int | None = None,
) -> str:
    """Deterministic routing/isolation primitive (Hermes pattern).

    A Telegram thread is a session lane. A chat with no threads (thread_id 0/None)
    produces the pre-threads key verbatim, so existing rows keep resolving.
    """
    key = f"tg:{chat_type}:{chat_id}"
    if user_id is not None and chat_type != "private":
        key += f":{user_id}"
    if thread_id:
        key += f":t{thread_id}"
    return key
```

Add the field to `src/claude_hermes/gateway/events.py`:

```python
@dataclass
class MessageEvent:
    chat_type: str
    chat_id: int
    user_id: int
    text: str
    mentioned: bool
    thread_id: int | None = None
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS (existing tests unaffected — the new parameter defaults to `None`).

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/sessions.py src/claude_hermes/gateway/events.py tests/test_sessions.py
git commit -m "feat(sessions): thread id in the session key, legacy key preserved when absent"
```

---

### Task 3: Eternal sessions (timers off by default)

**Files:**
- Modify: `src/claude_hermes/config.py:19-20`
- Modify: `src/claude_hermes/sessions.py:49-59`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Produces: `Config.idle_reset_hours: float | None = None`, `Config.daily_reset_hour: int | None = None`
- Produces: `SessionRouter._expired(last, now) -> bool` returns `False` when both settings are unset.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sessions.py`:

```python
async def test_sessions_are_eternal_by_default(tmp_path):
    db, router = await make_router(tmp_path)          # Config() → both timers None
    await router.bind("tg:private:1:t9", "sdk-1", "APPEND", "2026-01-01T10:00:00+00:00")
    row = await router.route("tg:private:1:t9", dt("2026-12-31T23:59:00"))
    assert row is not None and row["sdk_session_id"] == "sdk-1"   # a year later, still alive
    await db.close()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_sessions.py::test_sessions_are_eternal_by_default -v`
Expected: FAIL — the row is purged (`idle_reset_hours` currently defaults to 24.0, `daily_reset_hour` to 4).

- [ ] **Step 3: Implement**

In `src/claude_hermes/config.py`, change the two defaults (a thread is the session boundary now; the timers stay available for anyone who wants them back):

```python
    idle_reset_hours: float | None = None   # None → sessions never idle-expire
    daily_reset_hour: int | None = None     # None → no daily boundary reset
```

In `src/claude_hermes/sessions.py`, make `_expired` tolerate `None`:

```python
    def _expired(self, last: datetime, now: datetime) -> bool:
        idle = self._cfg.idle_reset_hours
        if idle and now - last > timedelta(hours=idle):
            return True
        hour = self._cfg.daily_reset_hour
        if hour is not None:
            boundary = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if boundary > now:
                boundary -= timedelta(days=1)
            if last < boundary:
                return True
        return False
```

- [ ] **Step 4: Run the suite**

Run: `.venv/Scripts/python -m pytest tests/test_sessions.py -v`
Expected: PASS — including `test_route_idle_reset` and `test_route_daily_reset_at_4am`, which pass the settings explicitly and therefore still exercise the timers.

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/config.py src/claude_hermes/sessions.py tests/test_sessions.py
git commit -m "feat(sessions): eternal sessions within a thread; reset timers off by default"
```

---

### Task 4: `/new` and the `progress` callback plumbed through `handle_event`

`handle_event` stops chunking and starts returning the final reply text — chunking moves to the delivery layer (Task 6/9), which needs the whole text to decide between rich and plain.

**Files:**
- Modify: `src/claude_hermes/gateway/telegram.py:15-29`
- Modify: `src/claude_hermes/runner.py:41-60`
- Test: `tests/test_gateway.py`, `tests/test_runner.py`

**Interfaces:**
- Consumes: `build_session_key(..., thread_id=...)` (Task 2)
- Produces: `handle_event(runner, cfg, event, *, acl, progress=None) -> str | None` — `None` means "ignore this update"; a string is the final reply text (unchunked).
- Produces: `AgentRunner.reset(session_key: str) -> None`
- Produces: `AgentRunner.run_turn(session_key: str, user_text: str, progress=None) -> str`

- [ ] **Step 1: Write the failing tests**

Rewrite the assertions in `tests/test_gateway.py` that expect chunk lists, and add the new cases. `FakeRunner` grows a `reset` method and a `progress` kwarg:

```python
class FakeRunner:
    def __init__(self, reply="ok"):
        self.calls = []
        self.resets = []
        self.reply = reply

    async def run_turn(self, session_key, text, progress=None):
        self.calls.append((session_key, text))
        return self.reply

    async def reset(self, session_key):
        self.resets.append(session_key)


async def test_private_message_routes_to_private_key():
    runner = FakeRunner()
    reply = await handle_event(runner, Config(), ev(), acl=AllowAcl())
    assert reply == "ok"
    assert runner.calls[0][0] == "tg:private:1"


async def test_thread_id_becomes_part_of_the_key():
    runner = FakeRunner()
    await handle_event(runner, Config(), ev(thread_id=9), acl=AllowAcl())
    assert runner.calls[0][0] == "tg:private:1:t9"


async def test_new_command_resets_only_this_thread():
    runner = FakeRunner()
    reply = await handle_event(runner, Config(), ev(text="/new", thread_id=9), acl=AllowAcl())
    assert runner.resets == ["tg:private:1:t9"]
    assert runner.calls == []                 # no turn is run
    assert reply is not None and "fresh" in reply.lower()


async def test_new_command_tolerates_botname_suffix():
    runner = FakeRunner()
    await handle_event(runner, Config(), ev(text="/new@hermes_bot"), acl=AllowAcl())
    assert runner.resets == ["tg:private:1"]
```

Also update the other existing tests in the file that assert on `chunks`: `test_group_without_mention_is_ignored` and `test_denied_group_user_gets_silence` now assert `reply is None`; `test_denied_private_user_gets_refusal` asserts `"not" in reply.lower()`; `test_group_with_mention_uses_per_user_key`, `test_learn_*` keep asserting on `runner.calls`. The two `chunk_text` tests are unchanged — the function stays.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_gateway.py -v`
Expected: FAIL — `handle_event` still returns a list; `/new` is not handled.

- [ ] **Step 3: Implement `handle_event`**

Replace the body in `src/claude_hermes/gateway/telegram.py`:

```python
_REFUSAL = "Sorry, this agent is private and you're not on its allowlist."
_RESET_ACK = "Context cleared — this thread starts fresh on your next message."


async def handle_event(runner, cfg: Config, event: MessageEvent, *, acl,
                       progress=None) -> str | None:
    """Route one Telegram update. Returns the final reply text, or None to stay silent."""
    if event.chat_type != "private" and cfg.group_require_mention and not event.mentioned:
        return None
    verdict = await acl.check(event.user_id, event.chat_type)
    if verdict == "deny":
        return _REFUSAL if event.chat_type == "private" else None

    text = event.text
    command, _, rest = text.partition(" ")
    command = command.split("@", 1)[0]          # tolerate /cmd@botname
    key = build_session_key(
        event.chat_type, event.chat_id, user_id=event.user_id, thread_id=event.thread_id
    )
    if command == "/new":
        await runner.reset(key)
        return _RESET_ACK
    if command == "/learn":
        text = build_learn_prompt(rest.strip())

    return await runner.run_turn(key, text, progress=progress)
```

- [ ] **Step 4: Implement `AgentRunner.reset` and the `progress` hook**

In `src/claude_hermes/runner.py`, add the method and thread the callback down. `run_turn` / `_run_turn_locked` / `_execute` each take `progress`:

```python
    async def reset(self, session_key: str) -> None:
        """Manual context reset (/new). The next turn starts a fresh SDK session."""
        async with self._locks[session_key]:
            await self._db.reset_session(session_key)

    async def run_turn(self, session_key: str, user_text: str, progress=None) -> str:
        async with self._locks[session_key]:
            return await self._run_turn_locked(session_key, user_text, progress)
```

`_run_turn_locked(self, session_key, user_text, progress=None)` passes `progress` into both `self._execute(...)` calls (the first attempt and the post-`ProcessError` retry). In `_execute(self, user_text, resume_id, append, session_key, progress=None)`, emit inside the existing block loop:

```python
        async for msg in query(prompt=user_text, options=options):
            if isinstance(msg, AssistantMessage):
                parts = []
                for b in msg.content:
                    if isinstance(b, TextBlock):
                        parts.append(b.text)
                        if progress is not None:
                            await progress("")           # "" → the model is writing prose
                    elif isinstance(b, ToolUseBlock):
                        observe_block(stats, b)
                        if progress is not None:
                            await progress(getattr(b, "name", ""))
                if parts:
                    final_text = "\n".join(parts)
```

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python -m pytest tests/test_gateway.py tests/test_runner.py -v`
Expected: PASS. (`tests/test_runner.py` calls `run_turn(key, text)` without `progress`; the default `None` keeps it green.)

- [ ] **Step 6: Commit**

```bash
git add src/claude_hermes/gateway/telegram.py src/claude_hermes/runner.py tests/test_gateway.py
git commit -m "feat(gateway): /new resets a thread; handle_event returns the reply and forwards a progress callback"
```

---

### Task 5: `gateway/topics.py` — create, rename, close (fail-safe)

**Files:**
- Create: `src/claude_hermes/gateway/topics.py`
- Test: `tests/test_topics.py`

**Interfaces:**
- Produces: `TopicManager(call)` where `call: Callable[[str, dict], Awaitable[dict]]` — takes a Bot API method name and a payload, returns the parsed `result` object.
- Produces: `async TopicManager.create(chat_id: int, name: str) -> int | None` (the `message_thread_id`, or `None` on any failure)
- Produces: `async TopicManager.rename(chat_id: int, thread_id: int, name: str) -> None`
- Produces: `async TopicManager.close(chat_id: int, thread_id: int) -> None`
- Produces: `TOPIC_NAME_LIMIT = 128`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_topics.py`:

```python
import pytest

from claude_hermes.gateway.topics import TOPIC_NAME_LIMIT, TopicManager


class FakeCall:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result or {}
        self._error = error

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        if self._error:
            raise self._error
        return self._result


async def test_create_returns_thread_id():
    call = FakeCall(result={"message_thread_id": 77})
    assert await TopicManager(call).create(42, "⏰ digest") == 77
    method, payload = call.calls[0]
    assert method == "createForumTopic"
    assert payload == {"chat_id": 42, "name": "⏰ digest"}


async def test_create_truncates_long_names():
    call = FakeCall(result={"message_thread_id": 1})
    await TopicManager(call).create(42, "x" * 300)
    assert len(call.calls[0][1]["name"]) == TOPIC_NAME_LIMIT


async def test_create_failure_degrades_to_none():
    call = FakeCall(error=RuntimeError("no rights"))
    assert await TopicManager(call).create(42, "job") is None    # never raises


async def test_rename_and_close_swallow_failures():
    call = FakeCall(error=RuntimeError("chat does not support renaming"))
    tm = TopicManager(call)
    await tm.rename(42, 7, "new name")      # must not raise
    await tm.close(42, 7)                   # must not raise
    assert [m for m, _ in call.calls] == ["editForumTopic", "closeForumTopic"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_topics.py -v`
Expected: FAIL — `ModuleNotFoundError: claude_hermes.gateway.topics`

- [ ] **Step 3: Implement**

Create `src/claude_hermes/gateway/topics.py`:

```python
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

TOPIC_NAME_LIMIT = 128   # Telegram's limit on a forum-topic name


class TopicManager:
    """Forum topics, including topics in private chats (Bot API 10.0).

    Every operation is fail-safe: a chat that is not threaded, a missing right,
    or a rate limit degrades to `None` / no-op, never to a raised exception.
    A `None` thread id means "deliver flat", which is what the bot did before
    threads existed.
    """

    def __init__(self, call):
        self._call = call

    async def create(self, chat_id: int, name: str) -> int | None:
        try:
            result = await self._call(
                "createForumTopic", {"chat_id": chat_id, "name": name[:TOPIC_NAME_LIMIT]}
            )
            return int(result["message_thread_id"])
        except Exception:
            log.warning("createForumTopic failed for chat=%s; delivering flat",
                        chat_id, exc_info=True)
            return None

    async def rename(self, chat_id: int, thread_id: int, name: str) -> None:
        try:
            await self._call("editForumTopic", {
                "chat_id": chat_id,
                "message_thread_id": thread_id,
                "name": name[:TOPIC_NAME_LIMIT],
            })
        except Exception:
            log.warning("editForumTopic failed for chat=%s thread=%s",
                        chat_id, thread_id, exc_info=True)

    async def close(self, chat_id: int, thread_id: int) -> None:
        try:
            await self._call("closeForumTopic", {
                "chat_id": chat_id, "message_thread_id": thread_id,
            })
        except Exception:
            log.warning("closeForumTopic failed for chat=%s thread=%s",
                        chat_id, thread_id, exc_info=True)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_topics.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/gateway/topics.py tests/test_topics.py
git commit -m "feat(gateway): fail-safe forum topic manager (create/rename/close)"
```

---

### Task 6: `gateway/rich.py` — rich sends, drafts, and the degradation chain

**Files:**
- Create: `src/claude_hermes/gateway/rich.py`
- Test: `tests/test_rich.py`

**Interfaces:**
- Consumes: `chunk_text` from `gateway/telegram.py`
- Produces: `render_html(text: str) -> str`
- Produces: `RichSender(call, enabled: bool = True)`
- Produces: `async RichSender.send(chat_id: int, thread_id: int | None, text: str) -> None`
- Produces: `async RichSender.send_draft(chat_id: int, thread_id: int | None, status: str, draft_id: int = 1) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rich.py`:

```python
from claude_hermes.gateway.rich import RichSender, render_html


class FakeCall:
    """Records calls; raises for the methods named in `fail`."""

    def __init__(self, fail=(), fail_markdown=False):
        self.calls = []
        self._fail = set(fail)
        self._fail_markdown = fail_markdown

    async def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method in self._fail:
            raise RuntimeError(f"{method} rejected")
        if self._fail_markdown and "markdown" in payload.get("rich_message", {}):
            raise RuntimeError("malformed markdown")
        return {}


async def test_send_uses_markdown_and_thread():
    call = FakeCall()
    await RichSender(call).send(42, 9, "**bold**")
    assert call.calls == [("sendRichMessage", {
        "chat_id": 42, "message_thread_id": 9,
        "rich_message": {"markdown": "**bold**"},
    })]


async def test_thread_is_omitted_when_absent():
    call = FakeCall()
    await RichSender(call).send(42, None, "hi")
    assert "message_thread_id" not in call.calls[0][1]
    await RichSender(call).send(42, 0, "hi")
    assert "message_thread_id" not in call.calls[1][1]


async def test_markdown_failure_falls_back_to_html():
    call = FakeCall(fail_markdown=True)
    await RichSender(call).send(42, None, "**bold**")
    methods = [m for m, _ in call.calls]
    assert methods == ["sendRichMessage", "sendRichMessage"]
    assert "html" in call.calls[1][1]["rich_message"]


async def test_html_failure_falls_back_to_plain_chunks():
    call = FakeCall(fail=("sendRichMessage",))
    await RichSender(call).send(42, 9, "y" * 9000)
    methods = [m for m, _ in call.calls]
    assert methods[:2] == ["sendRichMessage", "sendRichMessage"]
    plain = [p for m, p in call.calls if m == "sendMessage"]
    assert len(plain) == 3                       # 9000 chars → 4000/4000/1000
    assert all(p["message_thread_id"] == 9 for p in plain)


async def test_disabled_sender_goes_straight_to_plain():
    call = FakeCall()
    await RichSender(call, enabled=False).send(42, None, "hi")
    assert [m for m, _ in call.calls] == ["sendMessage"]


async def test_draft_carries_draft_id_and_never_raises():
    call = FakeCall(fail=("sendRichMessageDraft",))
    await RichSender(call).send_draft(42, 9, "🔎 Searching the web…")   # must not raise
    method, payload = call.calls[0]
    assert method == "sendRichMessageDraft"
    assert payload["draft_id"] == 1 and payload["message_thread_id"] == 9


async def test_disabled_sender_sends_no_drafts():
    call = FakeCall()
    await RichSender(call, enabled=False).send_draft(42, 9, "💭 Thinking…")
    assert call.calls == []


def test_render_html_escapes_and_paragraphs():
    assert render_html("a < b\n\nsecond") == "<p>a &lt; b</p><p>second</p>"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_rich.py -v`
Expected: FAIL — `ModuleNotFoundError: claude_hermes.gateway.rich`

- [ ] **Step 3: Implement**

Create `src/claude_hermes/gateway/rich.py`:

```python
from __future__ import annotations

import html
import logging

from .telegram import chunk_text

log = logging.getLogger(__name__)


def render_html(text: str) -> str:
    """Escaped HTML fallback for text the markdown renderer rejected."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{html.escape(p)}</p>" for p in paras)


class RichSender:
    """The only caller of Bot API 10.x message methods.

    Sending a rich message uses rich_message = {"markdown": ...} | {"html": ...}
    (an object, not a block list). The model's markdown is passed through
    unescaped — that is the point of the feature. If Telegram rejects it, we
    retry as escaped HTML, and if that fails too we fall back to the plain
    sendMessage + chunk_text path the bot used before rich messages existed.
    """

    def __init__(self, call, enabled: bool = True):
        self._call = call
        self._enabled = enabled

    def _payload(self, chat_id: int, thread_id: int | None, extra: dict) -> dict:
        payload = {"chat_id": chat_id, **extra}
        if thread_id:                       # 0/None → a chat without topics
            payload["message_thread_id"] = thread_id
        return payload

    async def send(self, chat_id: int, thread_id: int | None, text: str) -> None:
        if self._enabled:
            for body in ({"markdown": text}, {"html": render_html(text)}):
                try:
                    await self._call(
                        "sendRichMessage",
                        self._payload(chat_id, thread_id, {"rich_message": body}),
                    )
                    return
                except Exception:
                    log.warning("sendRichMessage(%s) failed", next(iter(body)), exc_info=True)
        await self._send_plain(chat_id, thread_id, text)

    async def _send_plain(self, chat_id: int, thread_id: int | None, text: str) -> None:
        for chunk in chunk_text(text):
            await self._call(
                "sendMessage", self._payload(chat_id, thread_id, {"text": chunk})
            )

    async def send_draft(self, chat_id: int, thread_id: int | None, status: str,
                         draft_id: int = 1) -> None:
        """Ephemeral ~30s streaming preview. Best-effort: a failed draft must never
        abort the turn — only the final send matters."""
        if not self._enabled:
            return
        try:
            await self._call("sendRichMessageDraft", self._payload(
                chat_id, thread_id,
                {"draft_id": draft_id, "rich_message": {"markdown": status}},
            ))
        except Exception:
            log.debug("sendRichMessageDraft failed", exc_info=True)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_rich.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/gateway/rich.py tests/test_rich.py
git commit -m "feat(gateway): rich message sender with markdown->html->plain degradation and status drafts"
```

---

### Task 7: `gateway/presenter.py` — status mapping, draft dedupe, typing loop

**Files:**
- Create: `src/claude_hermes/gateway/presenter.py`
- Test: `tests/test_presenter.py`

**Interfaces:**
- Consumes: `RichSender` (Task 6)
- Produces: `status_for(tool_name: str) -> str`
- Produces: `Presenter(sender, chat_id, thread_id, chat_action, typing_interval=4.0)` where `chat_action: Callable[[], Awaitable[None]]`
- Produces: `async Presenter.on_event(tool_name: str) -> None` (the `progress` callback handed to `AgentRunner.run_turn`)
- Produces: `async Presenter.finalize(text: str) -> None`
- Produces: `Presenter` is an async context manager — the typing loop runs for the body and is cancelled on exit.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_presenter.py`:

```python
import asyncio

from claude_hermes.gateway.presenter import Presenter, status_for


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
    assert "memory" in status_for("mcp__hermes__memory_save").lower()
    assert "memory" in status_for("mcp__hermes__session_search").lower()
    assert "schedul" in status_for("mcp__hermes__cron_manage").lower()
    assert "writ" in status_for("").lower()          # "" → the model is writing prose
    assert status_for("SomeUnknownTool")             # unknown tools still get a status


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_presenter.py -v`
Expected: FAIL — `ModuleNotFoundError: claude_hermes.gateway.presenter`

- [ ] **Step 3: Implement**

Create `src/claude_hermes/gateway/presenter.py`:

```python
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_STATUS = {
    "WebSearch": "🔎 Searching the web…",
    "WebFetch": "🔎 Searching the web…",
    "Read": "🗂 Reading files…",
    "Glob": "🗂 Reading files…",
    "Grep": "🗂 Reading files…",
    "Write": "✏️ Writing files…",
    "Edit": "✏️ Writing files…",
    "Bash": "⚙️ Running a command…",
    "Agent": "🤖 Delegating to a subagent…",
    "Task": "🤖 Delegating to a subagent…",
    "TodoWrite": "📋 Planning…",
    "mcp__hermes__memory_save": "🧠 Working with memory…",
    "mcp__hermes__session_search": "🧠 Working with memory…",
    "mcp__hermes__cron_manage": "⏰ Scheduling…",
    "mcp__hermes__cost_report": "💰 Checking costs…",
    "mcp__hermes__skill_admin": "🛠 Editing skills…",
}
_WRITING = "✍️ Writing…"
_WORKING = "💭 Working…"


def status_for(tool_name: str) -> str:
    """One status line per tool. An empty name means the model is emitting prose."""
    if not tool_name:
        return _WRITING
    return _STATUS.get(tool_name, _WORKING)


class Presenter:
    """The live progress UI for one turn.

    The draft is a compact status indicator — a single line — pushed only when the
    status changes. The full reply is sent once, as the final message, so the user
    never watches a wall of text stream in. Everything here is best-effort: the
    typing loop and the drafts must never abort the turn.
    """

    def __init__(self, sender, chat_id: int, thread_id: int | None, chat_action,
                 typing_interval: float = 4.0):
        self._sender = sender
        self._chat = chat_id
        self._thread = thread_id
        self._chat_action = chat_action
        self._interval = typing_interval
        self._last: str | None = None
        self._task: asyncio.Task | None = None

    async def on_event(self, tool_name: str) -> None:
        status = status_for(tool_name)
        if status == self._last:
            return
        self._last = status
        await self._sender.send_draft(self._chat, self._thread, status)

    async def finalize(self, text: str) -> None:
        await self._sender.send(self._chat, self._thread, text)

    async def _typing(self) -> None:
        # A Telegram chat action lasts ~5s, so refresh it a little sooner.
        while True:
            try:
                await self._chat_action()
            except Exception:
                log.debug("chat action failed", exc_info=True)
            await asyncio.sleep(self._interval)

    async def __aenter__(self) -> "Presenter":
        self._task = asyncio.create_task(self._typing())
        return self

    async def __aexit__(self, *exc) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_presenter.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/gateway/presenter.py tests/test_presenter.py
git commit -m "feat(gateway): presenter with status-only drafts, dedupe and a typing loop"
```

---

### Task 8: Cron jobs get their own topic

**Files:**
- Modify: `src/claude_hermes/db.py:22-41` (schema + migrations)
- Modify: `src/claude_hermes/cron/store.py:12-24`
- Modify: `src/claude_hermes/cron/ticker.py:47-53`
- Modify: `src/claude_hermes/tools.py:16-19, 45-94`
- Test: `tests/test_cron_store.py`, `tests/test_cron_ticker.py`, `tests/test_cron_tool.py`, `tests/test_db.py`

**Interfaces:**
- Consumes: `TopicManager.create/close` (Task 5)
- Produces: `Database.open` applies `_MIGRATIONS` — `jobs.deliver_thread_id INTEGER`
- Produces: `JobStore.create(..., next_run_at: str, deliver_thread_id: int | None = None) -> int`
- Produces: `TurnContext(session_key: str, chat_id: int, thread_id: int | None = None)`
- Produces: `make_handlers(..., topics=None)` / `build_hermes_server(..., topics=None)`
- Produces: `CronTicker` calls `deliver(chat_id, text, thread_id)` — a 3-arg callback.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_db.py`:

```python
async def test_jobs_migration_adds_deliver_thread_id_and_is_idempotent(tmp_path):
    import aiosqlite

    from claude_hermes.db import Database

    path = tmp_path / "old.db"
    conn = await aiosqlite.connect(path)          # a pre-threads database
    await conn.execute(
        """CREATE TABLE jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            kind TEXT NOT NULL, value TEXT NOT NULL, prompt TEXT NOT NULL,
            deliver_chat_id INTEGER NOT NULL, origin_session_key TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active', next_run_at TEXT,
            last_run_at TEXT, last_status TEXT, created_at TEXT NOT NULL)"""
    )
    await conn.commit()
    await conn.close()

    db = await Database.open(path)                 # migrates
    await db.close()
    db = await Database.open(path)                 # runs again: no-op, no error
    cur = await db.conn.execute("PRAGMA table_info(jobs)")
    cols = {r[1] for r in await cur.fetchall()}
    assert "deliver_thread_id" in cols
    await db.close()
```

Add to `tests/test_cron_store.py`:

```python
async def test_create_persists_deliver_thread_id(tmp_path):
    db = await Database.open(tmp_path / "state.db")
    js = JobStore(db)
    jid = await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                          "2026-07-13T10:00:00+00:00", deliver_thread_id=77)
    assert (await js.get(jid))["deliver_thread_id"] == 77
    jid2 = await js.create("k", "interval", "3600", "p", 42, "tg:private:42",
                           "2026-07-13T10:00:00+00:00")
    assert (await js.get(jid2))["deliver_thread_id"] is None      # flat delivery
    await db.close()
```

In `tests/test_cron_ticker.py`, the fake `deliver` grows a third argument, and a new test asserts it is threaded through:

```python
    async def deliver(chat_id, text, thread_id=None):
        delivered.append((chat_id, text, thread_id))
```

```python
async def test_delivery_targets_the_jobs_topic(tmp_path, monkeypatch):
    db, js, ticker, delivered, ran = await make(tmp_path, monkeypatch)
    await js.create("j", "interval", "3600", "p", 42, "tg:private:42",
                    "2026-07-12T10:00:00+00:00", deliver_thread_id=77)
    await ticker.tick(dt("2026-07-12T10:00:31"))
    assert delivered == [(42, "output", 77)]
    await db.close()
```
(The existing ticker tests' `delivered` assertions become 3-tuples ending in `None`.)

Add to `tests/test_cron_tool.py`:

```python
class FakeTopics:
    def __init__(self, thread_id=77, fail=False):
        self.created = []
        self.closed = []
        self._thread_id = thread_id
        self._fail = fail

    async def create(self, chat_id, name):
        self.created.append((chat_id, name))
        return None if self._fail else self._thread_id

    async def close(self, chat_id, thread_id):
        self.closed.append((chat_id, thread_id))


async def test_create_job_opens_a_topic_and_stores_it(tmp_path):
    db, handlers, js, topics = await make_tool(tmp_path)     # see below
    await handlers["cron_manage"]({
        "action": "create", "name": "digest", "schedule": "1d", "prompt": "summarize",
    })
    assert topics.created == [(42, "digest")]
    assert (await js.get(1))["deliver_thread_id"] == 77


async def test_topic_failure_still_creates_the_job(tmp_path):
    db, handlers, js, topics = await make_tool(tmp_path, fail=True)
    reply = await handlers["cron_manage"]({
        "action": "create", "name": "digest", "schedule": "1d", "prompt": "summarize",
    })
    assert "Created job" in reply["content"][0]["text"]
    assert (await js.get(1))["deliver_thread_id"] is None     # flat delivery
```

```python
async def test_delete_job_closes_its_topic(tmp_path):
    db, handlers, js, topics = await make_tool(tmp_path)
    await handlers["cron_manage"]({
        "action": "create", "name": "digest", "schedule": "1d", "prompt": "summarize",
    })
    await handlers["cron_manage"]({"action": "delete", "job_id": 1})
    assert topics.closed == [(42, 77)]
    assert await js.get(1) is None
```

`make_tool` builds the handler dict the way the existing tests in that file already do — with `TurnContext(session_key="tg:private:42", chat_id=42)`, a real `JobStore`, and now a `FakeTopics` passed as `topics=`.

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_db.py tests/test_cron_store.py tests/test_cron_ticker.py tests/test_cron_tool.py -v`
Expected: FAIL — no `deliver_thread_id` column; `create()` rejects the keyword; `deliver` takes 2 args; `make_handlers` rejects `topics`.

- [ ] **Step 3: Implement the schema and the migration**

In `src/claude_hermes/db.py`, add the column to `_SCHEMA`'s `jobs` table (for fresh databases):

```python
CREATE TABLE IF NOT EXISTS jobs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT NOT NULL,
    kind               TEXT NOT NULL,
    value              TEXT NOT NULL,
    prompt             TEXT NOT NULL,
    deliver_chat_id    INTEGER NOT NULL,
    deliver_thread_id  INTEGER,
    origin_session_key TEXT NOT NULL,
    state              TEXT NOT NULL DEFAULT 'active',
    next_run_at        TEXT,
    last_run_at        TEXT,
    last_status        TEXT,
    created_at         TEXT NOT NULL
);
```

and, below `_SCHEMA`, the migration for databases that already exist:

```python
# Columns added after the initial schema shipped; ALTER existing DBs on open.
_MIGRATIONS = {
    "jobs": {"deliver_thread_id": "ALTER TABLE jobs ADD COLUMN deliver_thread_id INTEGER"},
}


async def _migrate(conn) -> None:
    for table, columns in _MIGRATIONS.items():
        cur = await conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cur.fetchall()}
        for column, ddl in columns.items():
            if column not in existing:
                await conn.execute(ddl)
    await conn.commit()
```

and call it in `Database.open`, right after `executescript`:

```python
    @classmethod
    async def open(cls, path: Path) -> "Database":
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        await conn.executescript(_SCHEMA)
        await conn.commit()
        await _migrate(conn)
        return cls(conn)
```

- [ ] **Step 4: Implement the store, the ticker and the tool**

`src/claude_hermes/cron/store.py` — `create` takes the thread:

```python
    async def create(
        self, name: str, kind: str, value: str, prompt: str,
        deliver_chat_id: int, origin_session_key: str, next_run_at: str,
        deliver_thread_id: int | None = None,
    ) -> int:
        cur = await self._conn.execute(
            """INSERT INTO jobs (name, kind, value, prompt, deliver_chat_id,
                                 deliver_thread_id, origin_session_key, next_run_at,
                                 created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, kind, value, prompt, deliver_chat_id, deliver_thread_id,
             origin_session_key, next_run_at, datetime.now(timezone.utc).isoformat()),
        )
        await self._conn.commit()
        return cur.lastrowid
```

`src/claude_hermes/cron/ticker.py` — deliver into the job's topic:

```python
            try:
                await self._deliver(job["deliver_chat_id"], text, job["deliver_thread_id"])
            except Exception:
                log.exception("cron delivery failed for #%s", job["id"])
```

`src/claude_hermes/tools.py` — `TurnContext` gains the thread, `make_handlers` accepts `topics`, `create` opens a topic and `delete` closes it:

```python
@dataclass(frozen=True)
class TurnContext:
    session_key: str
    chat_id: int
    thread_id: int | None = None


def make_handlers(store: MemoryStore, db: Database, ctx: TurnContext | None = None,
                   job_store=None, cfg=None, sidecar=None, skills_dir=None,
                   topics=None) -> dict:
```

inside `cron_manage`, in the `create` branch, after the chat is resolved and before `job_store.create`:

```python
            name = args.get("name", "job")
            # Each job gets its own topic, so its reports never interrupt a
            # conversation. A failure to open one degrades to flat delivery.
            thread_id = await topics.create(chat, name) if topics is not None else None
            jid = await job_store.create(
                name, sched.kind, sched.value, prompt,
                chat, ctx.session_key if ctx else "", first.isoformat(),
                deliver_thread_id=thread_id,
            )
            return reply(f"Created job #{jid} '{name}', next run {first.isoformat()}.")
```

and the `delete` branch closes the topic before deleting the row:

```python
        if action == "delete":
            job = await job_store.get(jid)
            if topics is not None and job and job["deliver_thread_id"]:
                await topics.close(job["deliver_chat_id"], job["deliver_thread_id"])
            await job_store.delete(jid)
            return reply(f"Deleted #{jid}.")
```

Finally, `build_hermes_server` must accept and forward `topics=` to `make_handlers` (same one-line pass-through as `job_store`).

- [ ] **Step 5: Run the affected tests**

Run: `.venv/Scripts/python -m pytest tests/test_db.py tests/test_cron_store.py tests/test_cron_ticker.py tests/test_cron_tool.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS. (`runner._execute` builds the server without `topics`; the default `None` keeps it green until Task 9 wires it.)

- [ ] **Step 7: Commit**

```bash
git add src/claude_hermes/db.py src/claude_hermes/cron src/claude_hermes/tools.py tests/
git commit -m "feat(cron): one topic per job, closed on delete; deliver_thread_id migration"
```

---

### Task 9: `gateway/titles.py` — auto-name a fresh thread

**Files:**
- Create: `src/claude_hermes/gateway/titles.py`
- Test: `tests/test_titles.py`

**Interfaces:**
- Consumes: `TopicManager.rename` (Task 5), `Database.get_session` (existing)
- Produces: `Titler(cfg, db, topics)`
- Produces: `async Titler.is_new_thread(session_key: str, thread_id: int | None) -> bool`
- Produces: `Titler.spawn(chat_id: int, thread_id: int, text: str) -> None` (fire-and-forget; keeps a strong reference to the task)
- Produces: `TITLE_APPEND: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_titles.py`:

```python
import asyncio

import claude_hermes.gateway.titles as titles_mod
from claude_hermes.config import Config
from claude_hermes.db import Database
from claude_hermes.gateway.titles import Titler


class SpyTopics:
    def __init__(self):
        self.renamed = []

    async def rename(self, chat_id, thread_id, name):
        self.renamed.append((chat_id, thread_id, name))


class FakeResult:
    def __init__(self, result):
        self.result = result


def fake_query(produced="Ansible deploy flow"):
    def _query(prompt, options):
        async def _gen():
            yield FakeResult(produced)
        return _gen()
    return _query


async def test_thread_without_a_session_row_is_new(tmp_path):
    db = await Database.open(tmp_path / "state.db")
    t = Titler(Config(), db, SpyTopics())
    assert await t.is_new_thread("tg:private:1:t9", 9) is True
    await db.upsert_session("tg:private:1:t9", "sdk-1", "APPEND", "2026-07-13T10:00:00+00:00")
    assert await t.is_new_thread("tg:private:1:t9", 9) is False
    assert await t.is_new_thread("tg:private:1", None) is False    # no thread → nothing to name
    await db.close()


async def test_spawn_titles_and_renames_the_topic(tmp_path, monkeypatch):
    monkeypatch.setattr(titles_mod, "query", fake_query("Ansible deploy flow"))
    db = await Database.open(tmp_path / "state.db")
    topics = SpyTopics()
    t = Titler(Config(), db, topics)
    t.spawn(42, 9, "how do I deploy with ansible?")
    await asyncio.sleep(0)                       # let the fire-and-forget task run
    await asyncio.gather(*t._tasks)
    assert topics.renamed == [(42, 9, "Ansible deploy flow")]
    await db.close()


async def test_a_failing_title_call_is_swallowed(tmp_path, monkeypatch):
    def boom(prompt, options):
        raise RuntimeError("model down")

    monkeypatch.setattr(titles_mod, "query", boom)
    db = await Database.open(tmp_path / "state.db")
    topics = SpyTopics()
    t = Titler(Config(), db, topics)
    t.spawn(42, 9, "hi")
    await asyncio.sleep(0)
    await asyncio.gather(*t._tasks)              # must not raise
    assert topics.renamed == []
    await db.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_titles.py -v`
Expected: FAIL — `ModuleNotFoundError: claude_hermes.gateway.titles`

- [ ] **Step 3: Implement**

Create `src/claude_hermes/gateway/titles.py`:

```python
from __future__ import annotations

import asyncio
import logging

from claude_agent_sdk import ClaudeAgentOptions, query

from ..config import NO_ADVISOR_ENV, Config
from ..db import Database
from .topics import TOPIC_NAME_LIMIT

log = logging.getLogger(__name__)

TITLE_APPEND = """\
You name Telegram topics. Given the user's first message in a new topic, reply with
a title of at most 6 words that says what the topic is about. No quotes, no emoji,
no trailing period. Output the title and nothing else."""


class Titler:
    """Names a freshly opened thread from its first message.

    A thread is 'new' exactly when it has no session row yet — so spawn() must be
    called BEFORE the turn runs, or the turn will have created the row and the
    thread will no longer look new.
    """

    def __init__(self, cfg: Config, db: Database, topics):
        self._cfg = cfg
        self._db = db
        self._topics = topics
        self._tasks: set[asyncio.Task] = set()

    async def is_new_thread(self, session_key: str, thread_id: int | None) -> bool:
        if not thread_id:
            return False
        return await self._db.get_session(session_key) is None

    def spawn(self, chat_id: int, thread_id: int, text: str) -> None:
        task = asyncio.create_task(self._run(chat_id, thread_id, text))
        self._tasks.add(task)                     # keep a ref: the loop only weakly
        task.add_done_callback(self._tasks.discard)  # references tasks

    async def _run(self, chat_id: int, thread_id: int, text: str) -> None:
        try:
            title = await self._title(text)
        except Exception:
            log.warning("titling failed for chat=%s thread=%s", chat_id, thread_id,
                        exc_info=True)
            return
        if title:
            await self._topics.rename(chat_id, thread_id, title)

    async def _title(self, text: str) -> str:
        options = ClaudeAgentOptions(
            model=self._cfg.title_model,
            max_turns=1,
            allowed_tools=[],
            # Empty setting_sources on purpose: with "project" the agent_home
            # CLAUDE.md persona loads and the model ANSWERS the message instead
            # of titling it.
            setting_sources=[],
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": TITLE_APPEND},
            env=NO_ADVISOR_ENV,
        )
        out = ""
        async for msg in query(prompt=text, options=options):
            if getattr(msg, "result", None):
                out = msg.result
        return out.strip().strip('"')[:TOPIC_NAME_LIMIT]
```

Add the model setting to `src/claude_hermes/config.py` (next to the other Phase-2 knobs):

```python
    title_model: str = "claude-haiku-4-5-20251001"   # cheap one-shot for thread titles
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_titles.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/gateway/titles.py src/claude_hermes/config.py tests/test_titles.py
git commit -m "feat(gateway): auto-name a fresh thread from its first message"
```

---

### Task 10: Wire it together in the dispatcher and the entrypoint

**Files:**
- Modify: `src/claude_hermes/gateway/telegram.py:53-84` (`create_dispatcher`)
- Modify: `src/claude_hermes/main.py:22-78`
- Modify: `src/claude_hermes/runner.py:108-116` (pass `topics` into the MCP server)
- Modify: `src/claude_hermes/config.py` (`rich_enabled`)
- Modify: `pyproject.toml` (add `httpx`)
- Modify: `config.yaml.example`, `README.md`

**Interfaces:**
- Consumes: `RichSender` (6), `Presenter` (7), `TopicManager` (5), `Titler` (9), `handle_event(..., progress=)` (4)
- Produces: `bot_call(method: str, payload: dict) -> dict` — a raw Bot API call over `httpx`, injected into `RichSender` and `TopicManager`.
- Produces: `Config.rich_enabled: bool = True`

Task 1 decides the transport: raw `httpx` is the default (it works regardless of what aiogram types). If Task 1 found aiogram's typed Rich Messages complete, the swap happens inside `bot_call` only — nothing else changes.

- [ ] **Step 1: Add the dependency and the config flag**

`pyproject.toml`:

```toml
dependencies = [
    "claude-agent-sdk>=0.1.0",
    "aiogram>=3.7",
    "aiosqlite>=0.20",
    "pyyaml>=6.0",
    "croniter>=2",
    "httpx>=0.27",
]
```

`src/claude_hermes/config.py`:

```python
    rich_enabled: bool = True    # kill switch: false → plain sendMessage everywhere
```

Run: `.venv/Scripts/pip install -e ".[dev]"`

- [ ] **Step 2: Add `bot_call` and build the objects in `main.py`**

In `src/claude_hermes/main.py`, inside `run()`, after the `Bot` is created:

```python
    import httpx

    from .gateway.presenter import Presenter
    from .gateway.rich import RichSender
    from .gateway.titles import Titler
    from .gateway.topics import TopicManager

    api = f"https://api.telegram.org/bot{cfg.telegram_token}"
    http = httpx.AsyncClient(timeout=30.0)

    async def bot_call(method: str, payload: dict) -> dict:
        """Raw Bot API 10.x call. aiogram does not type Rich Messages, and its
        make_request expects a TelegramMethod — so call the HTTP API directly."""
        r = await http.post(f"{api}/{method}", json=payload)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"{method} failed: {data}")
        return data.get("result") or {}

    sender = RichSender(bot_call, enabled=cfg.rich_enabled)
    topics = TopicManager(bot_call)
    titler = Titler(cfg, db, topics)
    runner._topics = topics
```

`deliver` grows the thread argument and routes through the sender:

```python
    async def deliver(chat_id: int, text: str, thread_id: int | None = None) -> None:
        await sender.send(chat_id, thread_id, text)
```

Pass `sender`, `titler` and `db` into `create_dispatcher`, and close the client in the `finally` block: `await http.aclose()`.

- [ ] **Step 3: Pass `topics` into the MCP server**

In `src/claude_hermes/runner.py`, add `self._topics = None` next to `self._job_store = None`, and forward it:

```python
        server, mcp_tool_names = build_hermes_server(
            self._store, self._db, ctx=ctx, job_store=self._job_store, cfg=self._cfg,
            sidecar=self._sidecar, skills_dir=self._skills_dir, topics=self._topics,
        )
```

`TurnContext` gains the thread id, parsed from the key it already receives — add a `thread_id_from_key` helper next to `chat_id_from_key` in `sessions.py`:

```python
def thread_id_from_key(session_key: str) -> int | None:
    tail = session_key.rsplit(":", 1)[-1]
    if tail.startswith("t") and tail[1:].isdigit():
        return int(tail[1:])
    return None
```

and use it in `_execute`:

```python
        ctx = TurnContext(session_key=session_key,
                          chat_id=chat_id_from_key(session_key) or 0,
                          thread_id=thread_id_from_key(session_key))
```

- [ ] **Step 4: Rewrite the aiogram handler**

In `src/claude_hermes/gateway/telegram.py`:

```python
def create_dispatcher(runner, cfg: Config, bot_username: str, acl, *,
                      sender, titler, db):
    """aiogram wiring — exercised by the Docker smoke run, not unit tests."""
    from aiogram import Dispatcher, types

    from .presenter import Presenter

    dp = Dispatcher()

    @dp.message()
    async def on_message(message: types.Message):
        if not message.text:
            return
        mention = f"@{bot_username}"
        mentioned = mention in message.text or (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.username == bot_username
        )
        thread_id = getattr(message, "message_thread_id", None)
        event = MessageEvent(
            chat_type=message.chat.type,
            chat_id=message.chat.id,
            user_id=message.from_user.id if message.from_user else 0,
            text=message.text.replace(mention, "").strip(),
            mentioned=mentioned,
            thread_id=thread_id,
        )

        key = build_session_key(event.chat_type, event.chat_id,
                                user_id=event.user_id, thread_id=thread_id)
        # BEFORE the turn: a thread is 'new' only until the turn writes its session row.
        if await titler.is_new_thread(key, thread_id):
            titler.spawn(event.chat_id, thread_id, event.text)

        async def chat_action() -> None:
            await message.bot.send_chat_action(
                message.chat.id, "typing", message_thread_id=thread_id
            )

        presenter = Presenter(sender, event.chat_id, thread_id, chat_action=chat_action)
        try:
            async with presenter:
                reply = await handle_event(runner, cfg, event, acl=acl,
                                           progress=presenter.on_event)
            if reply:
                await presenter.finalize(reply)
        except Exception:
            log.exception("turn failed for chat %s", message.chat.id)
            await presenter.finalize("Something went wrong on my side — try again.")

    return dp
```

(`build_session_key` and `MessageEvent` are already imported at the top of the module; add `build_session_key` to the existing `from ..sessions import ...` line.)

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS — no unit test imports `create_dispatcher` (aiogram wiring is smoke-tested).

- [ ] **Step 6: Update the docs and the example config**

`config.yaml.example` — add, with comments:

```yaml
# Sessions are eternal within a Telegram thread. Set either of these to re-enable
# the old timer-based resets (idle_reset_hours: 24.0, daily_reset_hour: 4).
idle_reset_hours: null
daily_reset_hour: null

rich_enabled: true                              # false → plain messages, no drafts
title_model: claude-haiku-4-5-20251001          # cheap one-shot that names new threads
```

`README.md` — add a "Threads" section under Run:

```markdown
## Threads
Enable **Threaded Mode** for the bot in @BotFather (Bot Settings → Mini App). Each
topic in the private chat is an isolated agent session that lives as long as the
topic does; `/new` clears the current topic's context. New topics are auto-named
from their first message. Each scheduled job opens its own topic and it is closed
when the job is deleted.
```

- [ ] **Step 7: Commit**

```bash
git add src/claude_hermes pyproject.toml config.yaml.example README.md
git commit -m "feat: wire threads, rich streaming and per-job topics into the telegram gateway"
```

---

### Task 11: Docker smoke run

**Files:** none (verification only)

- [ ] **Step 1: Build and start**

```bash
docker compose up -d --build && docker compose logs -f --tail=50
```
Expected: `claude-hermes polling as @<botname>`.

- [ ] **Step 2: Walk the flows and confirm each**

- [ ] Open a **new topic** in the private chat, send "how do I deploy with ansible?" → the topic is renamed to a short title within a few seconds.
- [ ] While the turn runs → a draft ticks through statuses (💭 → 🔎 → ✍️), and "typing…" stays alive for the whole turn.
- [ ] The final reply renders **bold and a table** (ask for one explicitly).
- [ ] Send a message in a **second topic** → the agent has no memory of the first (isolated lanes).
- [ ] `/new` in a topic → ack, and the next message starts from a clean context.
- [ ] Ask the agent to schedule a job ("каждый день в 9 присылай дайджест") → a new topic named after the job appears; wait for a fire (or use `run_now`) → the report lands **in that topic**.
- [ ] Delete the job → the topic is closed.
- [ ] Send a message in a chat **without** threads → everything still works, flat (the legacy path).

- [ ] **Step 3: Record the result**

Append a "Smoke run" section to `docs/superpowers/notes/2026-07-13-bot-api-10-verification.md` with a line per flow (OK / broken + what happened).

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/notes/2026-07-13-bot-api-10-verification.md
git commit -m "docs: smoke-run results for threads, rich streaming and per-job topics"
```

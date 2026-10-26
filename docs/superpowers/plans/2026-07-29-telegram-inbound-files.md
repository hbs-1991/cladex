# Inbound Files (photos & documents) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Photos and documents sent to the Telegram bot reach `run_turn` — images visible to the model, documents reachable by path — instead of being silently dropped.

**Architecture:** A new `gateway/attachments.py` owns filename/path safety and the download. `telegram.py` gains one testable coroutine, `intake_attachments`, that ACL-gates on the caption *before* fetching a byte — the same order voice notes already use before spending a transcription call. `runner.py` gains a prompt builder that returns a bare string when there is nothing attached (so every existing path is byte-identical) and a fresh async iterator carrying image content blocks when there is.

**Tech Stack:** Python 3.12, aiogram 3.x, `claude-agent-sdk==0.2.126`, pytest with `asyncio_mode = auto`, SQLite/FTS5.

**Spec:** `docs/superpowers/specs/2026-07-29-telegram-inbound-files-design.md` (commit `3bacdbc`).

## Global Constraints

- **All persistent state lives under `CH_HOME`.** `tar ./data` must remain a complete backup. Attachments go to `CH_HOME/agent_home/inbox/<lane>/` — inside `CH_HOME`, and inside the agent's `cwd` (`options.py:27`) so `Read`/`Glob` reach them with no new mount.
- **Gate before spend.** `_verdict` runs before any download. An un-addressed attachment in a group must produce no download call *and* no typing indicator.
- **Size ceiling: 20 MB**, checked from the Telegram object's `file_size` before download. That is the Bot API's own `getFile` limit.
- **Retention: none.** Files are never pruned.
- **No new DB schema.** Attachment provenance rides in the text passed to `record_message`.
- **Media groups: one turn per attachment.** No buffering, no `media_group_id` timer.
- **Run tests with** `.venv/Scripts/python.exe -m pytest`.
- **Run the wiki CLI under WSL** — `.llm-wiki/llm-wiki` is a Linux ELF binary and cannot execute on this Windows host:
  `wsl -d Ubuntu -- bash -lc 'cd /mnt/d/projects/claude-hermes && ./.llm-wiki/llm-wiki <cmd>'`
- **Existing text and voice flows must not change shape.** `_build_prompt` returning a bare `str` when `attachments` is empty is the guard; Task 4 Step 1 tests it explicitly.

## File Structure

| File | Responsibility |
|---|---|
| `src/claude_hermes/gateway/attachments.py` *(new)* | `Attachment`/`Spec` dataclasses, filename & lane-path safety, Telegram→Spec extraction, the download. No ACL, no aiogram types. |
| `src/claude_hermes/gateway/telegram.py` *(modify)* | `Intake` + `intake_attachments` (gate→size→download ordering); `on_message` wiring. |
| `src/claude_hermes/gateway/events.py` *(modify)* | `MessageEvent.attachments` |
| `src/claude_hermes/runner.py` *(modify)* | `_compose_text`, `_build_prompt`, `attachments` threaded through `run_turn`→`_execute` |
| `tests/test_attachments.py` *(new)* | Pure helpers + `download_all` |
| `tests/test_attachments_intake.py` *(new)* | Gate/size/error ordering — the five required cases |
| `tests/test_runner_attachments.py` *(new)* | Prompt shape, image blocks, FTS row |
| `docs/llm-wiki/components/gateway.md`, `docs/llm-wiki/flows/message-turn.md` *(modify)* | Remove the text/voice-only claim |

---

### Task 1: Verify the CLI renders image blocks from stream-json

This is a **gate**, not a feature. The SDK's end of the contract is confirmed (`_internal/query.py:839` forwards streamed dicts verbatim; `subprocess_cli.py:469` and `:352` show `--input-format stream-json` and `--resume` are independent flags). The CLI's end is not. If this fails, Task 4 changes: no image blocks, images become path-only and the agent opens them with `Read` (which handles images). Everything else in the plan is unaffected.

**Files:**
- Create: `<scratchpad>/smoke_image_block.py` (scratch — never committed)

**Interfaces:**
- Consumes: nothing.
- Produces: a yes/no answer that decides whether Task 4 Step 5 includes image blocks.

- [ ] **Step 1: Write the smoke script**

Write to the session scratchpad directory (not the repo):

```python
"""One real CLI call: does a stream-json image block reach the model?"""
import base64
import struct
import zlib
from pathlib import Path

import anyio
from claude_agent_sdk import AssistantMessage, TextBlock, query
from claude_hermes.options import base_options


def solid_png(w: int, h: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)   # 8-bit truecolour
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


async def main() -> None:
    home = Path(__file__).resolve().parent / "smoke-home"
    (home / "agent_home").mkdir(parents=True, exist_ok=True)
    png = solid_png(64, 64, (220, 20, 60))          # crimson

    async def stream():
        yield {
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode()}},
                {"type": "text",
                 "text": "Name the single colour that fills this image. One word."},
            ]},
            "parent_tool_use_id": None,
            "session_id": "default",
        }

    opts = base_options(home, allowed_tools=[], max_turns=1)
    async for msg in query(prompt=stream(), options=opts):
        if isinstance(msg, AssistantMessage):
            for b in msg.content:
                if isinstance(b, TextBlock):
                    print("MODEL SAID:", b.text)


anyio.run(main)
```

Set `PYTHONPATH` to the repo `src/` when running so `claude_hermes.options` imports.

- [ ] **Step 2: Run it**

```bash
cd "D:/projects/claude-hermes"
PYTHONPATH=src .venv/Scripts/python.exe "<scratchpad>/smoke_image_block.py"
```

Expected on success: `MODEL SAID:` naming red/crimson.
Expected on failure: an error about the content block, or the model saying it sees no image.

- [ ] **Step 3: Record the verdict**

Append one line to the spec under "Verified constraints", replacing the "Not verified" paragraph's status:

- On success: `**Verified 2026-07-29:** the CLI renders stream-json image blocks; the model named the colour.`
- On failure: `**Verified 2026-07-29: FAILED** — the CLI does not render stream-json image blocks. Images are path-only; Task 4 omits image blocks and Task 4 Steps 5/7 drop the image-block assertions.`

- [ ] **Step 4: Commit the spec update**

```bash
git add docs/superpowers/specs/2026-07-29-telegram-inbound-files-design.md
git commit -m "docs(spec): record whether the CLI renders stream-json image blocks"
```

---

### Task 2: Attachment types and path safety

**Files:**
- Create: `src/claude_hermes/gateway/attachments.py`
- Test: `tests/test_attachments.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `IMAGE_TYPES: frozenset[str]`, `MAX_ATTACHMENT_BYTES: int`
  - `Attachment(path: Path, filename: str, media_type: str, is_image: bool)` — frozen dataclass
  - `Spec(media: object, original: str, stored: str, media_type: str, size: int)` — frozen dataclass
  - `safe_name(message_id: int, raw: str) -> str`
  - `lane_dir(home: Path, session_key: str) -> Path`
  - `specs(message) -> list[Spec]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_attachments.py`:

```python
from types import SimpleNamespace

from claude_hermes.gateway.attachments import lane_dir, safe_name, specs


def test_safe_name_cannot_escape_its_directory(tmp_path):
    name = safe_name(42, "../../etc/passwd")
    assert "/" not in name and "\\" not in name and ".." not in name
    assert (tmp_path / name).parent == tmp_path


def test_safe_name_keeps_a_readable_name_and_prefixes_the_message_id():
    # The prefix does two jobs: two `report.pdf` from different messages cannot
    # collide, and a message id never changes, so the path stays stable.
    assert safe_name(42, "Q3 report.pdf") == "42_Q3_report.pdf"


def test_safe_name_falls_back_when_nothing_survives_sanitizing():
    assert safe_name(7, "...") == "7_file"
    assert safe_name(7, "") == "7_file"


def test_lane_dir_is_under_agent_home_and_flattens_the_session_key(tmp_path):
    assert lane_dir(tmp_path, "tg:private:1:t9") == (
        tmp_path / "agent_home" / "inbox" / "tg-private-1-t9"
    )


def test_specs_picks_the_largest_photo_size():
    # Telegram orders PhotoSize smallest -> largest.
    msg = SimpleNamespace(
        message_id=5,
        photo=[SimpleNamespace(file_size=100), SimpleNamespace(file_size=9000)],
        document=None,
    )
    [s] = specs(msg)
    assert s.size == 9000
    assert s.media_type == "image/jpeg"
    assert s.original == "photo.jpg" and s.stored == "5_photo.jpg"


def test_specs_keeps_the_document_name_and_mime():
    msg = SimpleNamespace(
        message_id=5, photo=None,
        document=SimpleNamespace(file_name="Q3 report.pdf",
                                 mime_type="application/pdf", file_size=10),
    )
    [s] = specs(msg)
    assert s.original == "Q3 report.pdf"
    assert s.stored == "5_Q3_report.pdf"
    assert s.media_type == "application/pdf"


def test_specs_is_empty_for_a_plain_text_message():
    assert specs(SimpleNamespace(message_id=1, photo=None, document=None)) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_attachments.py -q
```

Expected: `ModuleNotFoundError: No module named 'claude_hermes.gateway.attachments'`

- [ ] **Step 3: Write the implementation**

Create `src/claude_hermes/gateway/attachments.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# The four the API accepts. A Telegram `photo` is always JPEG; a `document`
# carrying one of these is treated as an image too, which is what a user who
# drags a PNG into the chat expects.
IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

# The Bot API's own getFile ceiling. Anything larger fails on Telegram's side
# regardless, so refusing here just buys a message that says why.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class Attachment:
    """A file that landed on disk and is about to reach the turn."""

    path: Path          # absolute, under CH_HOME/agent_home/inbox/<lane>/
    filename: str       # the ORIGINAL Telegram name, for the model to refer to
    media_type: str
    is_image: bool


@dataclass(frozen=True)
class Spec:
    """One downloadable thing, resolved from a Telegram message but not yet fetched."""

    media: object       # the aiogram object handed to bot.download
    original: str       # the name the user sees
    stored: str         # sanitized + message-id-prefixed; what lands on disk
    media_type: str
    size: int


def safe_name(message_id: int, raw: str) -> str:
    """A filename that cannot escape its directory.

    Every character outside [A-Za-z0-9._-] becomes '_', which neutralizes both
    separators, so no traversal survives — and the message-id prefix means the
    result is never bare '..' either. Stripping leading/trailing '.' and '_'
    keeps '../../etc/passwd' from turning into a name that merely *looks*
    alarming in a log line.
    """
    cleaned = _UNSAFE.sub("_", (raw or "").strip())[:120].strip("._") or "file"
    return f"{message_id}_{cleaned}"


def lane_dir(home: Path, session_key: str) -> Path:
    """Where one session lane's attachments live.

    Under agent_home because that is the agent's cwd (options.py), so its Read
    and Glob reach the file with no new mount — and under CH_HOME, so `tar` on
    the data dir is still a complete backup (NFR-5).
    """
    lane = _UNSAFE.sub("-", session_key) or "lane"
    return home / "agent_home" / "inbox" / lane


def specs(message) -> list[Spec]:
    """What is downloadable on this message. A list so the caller has one shape
    regardless of whether it was a photo, a document, or neither."""
    out: list[Spec] = []
    photo = getattr(message, "photo", None)
    if photo:
        out.append(_spec(message, photo[-1], "photo.jpg", "image/jpeg"))
    doc = getattr(message, "document", None)
    if doc is not None:
        out.append(_spec(
            message, doc,
            getattr(doc, "file_name", None) or "document",
            getattr(doc, "mime_type", None) or "application/octet-stream",
        ))
    return out


def _spec(message, media, original: str, media_type: str) -> Spec:
    return Spec(
        media=media,
        original=original,
        stored=safe_name(getattr(message, "message_id", 0), original),
        media_type=media_type,
        size=getattr(media, "file_size", None) or 0,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_attachments.py -q
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/gateway/attachments.py tests/test_attachments.py
git commit -m "feat(attachments): sanitized names and per-lane paths under CH_HOME"
```

---

### Task 3: Download attachments to the lane directory

**Files:**
- Modify: `src/claude_hermes/gateway/attachments.py` (append)
- Test: `tests/test_attachments.py` (append)

**Interfaces:**
- Consumes: `Spec`, `Attachment`, `IMAGE_TYPES`, `lane_dir`, `specs` from Task 2.
- Produces: `async download_all(bot, items: list[Spec], dest: Path) -> list[Attachment]`

- [ ] **Step 1: Write the failing tests**

First replace the import block at the top of `tests/test_attachments.py` with:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_hermes.gateway.attachments import (
    download_all,
    lane_dir,
    safe_name,
    specs,
)
```

Then append:

```python
class FakeBot:
    """Records every download and writes a known payload."""

    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    async def download(self, media, destination):
        self.calls.append(destination)
        if self._fail:
            raise RuntimeError("telegram is down")
        Path(destination).write_bytes(b"payload")


async def test_download_all_creates_the_lane_dir_and_flags_an_image(tmp_path):
    msg = SimpleNamespace(
        message_id=5, photo=None,
        document=SimpleNamespace(file_name="a.png", mime_type="image/png",
                                 file_size=7),
    )
    bot = FakeBot()
    dest = lane_dir(tmp_path, "tg:private:1")
    assert not dest.exists()

    [att] = await download_all(bot, specs(msg), dest)

    assert att.path == dest / "5_a.png"
    assert att.path.read_bytes() == b"payload"
    assert att.filename == "a.png"          # the ORIGINAL name, not the stored one
    assert att.is_image is True


async def test_download_all_marks_a_pdf_as_not_an_image(tmp_path):
    msg = SimpleNamespace(
        message_id=5, photo=None,
        document=SimpleNamespace(file_name="r.pdf", mime_type="application/pdf",
                                 file_size=7),
    )
    [att] = await download_all(FakeBot(), specs(msg),
                               lane_dir(tmp_path, "tg:private:1"))
    assert att.is_image is False
    assert att.media_type == "application/pdf"


async def test_download_all_propagates_a_failure(tmp_path):
    msg = SimpleNamespace(message_id=5, photo=[SimpleNamespace(file_size=1)],
                          document=None)
    with pytest.raises(RuntimeError):
        await download_all(FakeBot(fail=True), specs(msg),
                           lane_dir(tmp_path, "tg:private:1"))
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_attachments.py -q
```

Expected: `ImportError: cannot import name 'download_all'`

- [ ] **Step 3: Write the implementation**

Append to `src/claude_hermes/gateway/attachments.py`:

```python
async def download_all(bot, items, dest: Path) -> list[Attachment]:
    """Fetch every Spec into `dest`. Raises on the first failure — the caller
    decides what the user sees, because only it knows the chat."""
    dest.mkdir(parents=True, exist_ok=True)
    saved: list[Attachment] = []
    for s in items:
        path = dest / s.stored
        await bot.download(s.media, destination=path)
        saved.append(Attachment(
            path=path, filename=s.original, media_type=s.media_type,
            is_image=s.media_type in IMAGE_TYPES,
        ))
    return saved
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_attachments.py -q
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/claude_hermes/gateway/attachments.py tests/test_attachments.py
git commit -m "feat(attachments): download Telegram media into the lane directory"
```

---

### Task 4: Carry attachments into the turn

**Files:**
- Modify: `src/claude_hermes/runner.py:87-153` (`run_turn`, `_run_turn_locked`), `:155-228` (`_execute`)
- Test: `tests/test_runner_attachments.py`

**Interfaces:**
- Consumes: `Attachment` from Task 2.
- Produces:
  - `_compose_text(user_text: str, attachments) -> str`
  - `_build_prompt(user_text: str, attachments) -> str | AsyncIterator[dict]`
  - `AgentRunner.run_turn(session_key, user_text, progress=None, user_id=0, attachments=None) -> str`

> **If Task 1 failed:** omit the `blocks` image-block list comprehension in Step 3 (leave only the text block) and delete `test_prompt_with_an_image_carries_a_base64_block` from Step 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner_attachments.py`:

```python
import base64

import claude_hermes.runner as runner_mod
from claude_hermes.config import Config
from claude_hermes.db import Database
from claude_hermes.gateway.attachments import Attachment
from claude_hermes.runner import AgentRunner, _build_prompt, _compose_text
from claude_hermes.sessions import SessionRouter
from claude_hermes.wiki import WikiStore


class FakeResult:
    """Duck-typed ResultMessage stand-in (runner matches on attributes)."""

    def __init__(self, session_id="sdk-1", result="ok"):
        self.session_id = session_id
        self.result = result
        self.is_error = False


def make_fake_query(captured):
    async def fake_query(*, prompt, options):
        captured.append(prompt)
        yield FakeResult()

    return fake_query


async def make_runner(tmp_path):
    db = await Database.open(tmp_path / "state.db")
    wiki = WikiStore(tmp_path)
    wiki.seed()
    cfg = Config()
    return AgentRunner(cfg, tmp_path, db, SessionRouter(db, cfg), wiki), db


def _png(tmp_path) -> Attachment:
    p = tmp_path / "42_shot.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nDATA")
    return Attachment(path=p, filename="shot.png", media_type="image/png",
                      is_image=True)


def _pdf(tmp_path) -> Attachment:
    p = tmp_path / "42_report.pdf"
    p.write_bytes(b"%PDF-1.4")
    return Attachment(path=p, filename="report.pdf",
                      media_type="application/pdf", is_image=False)


def test_prompt_without_attachments_is_still_a_bare_string():
    """The guard on every existing call site — text, voice, /learn, cron."""
    assert _build_prompt("hi", None) == "hi"
    assert _build_prompt("hi", []) == "hi"


async def test_prompt_with_an_image_carries_a_base64_block(tmp_path):
    att = _png(tmp_path)
    stream = _build_prompt("what is this", [att])
    assert not isinstance(stream, str)

    [msg] = [m async for m in stream]
    blocks = msg["message"]["content"]
    assert msg["type"] == "user" and msg["message"]["role"] == "user"
    assert blocks[0]["type"] == "image"
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[0]["source"]["data"] == base64.standard_b64encode(
        att.path.read_bytes()).decode()
    # the path rides along too, so the agent can re-open the file, not only look
    assert blocks[-1]["type"] == "text"
    assert "what is this" in blocks[-1]["text"]
    assert str(att.path) in blocks[-1]["text"]


async def test_a_document_is_passed_by_path_not_inlined(tmp_path):
    att = _pdf(tmp_path)
    [msg] = [m async for m in _build_prompt("summarise", [att])]
    blocks = msg["message"]["content"]
    assert [b["type"] for b in blocks] == ["text"]
    assert "report.pdf" in blocks[0]["text"]
    assert str(att.path) in blocks[0]["text"]


def test_compose_text_names_every_attachment(tmp_path):
    text = _compose_text("look", [_png(tmp_path), _pdf(tmp_path)])
    assert "look" in text
    assert "shot.png" in text and "report.pdf" in text


def test_build_prompt_returns_a_fresh_iterator_each_call(tmp_path):
    """_execute runs twice on the resume-retry path and an async generator is
    single-use — a shared one would be exhausted on the second attempt."""
    att = _png(tmp_path)
    assert _build_prompt("x", [att]) is not _build_prompt("x", [att])


async def test_attachment_provenance_is_searchable(tmp_path, monkeypatch):
    runner, db = await make_runner(tmp_path)
    monkeypatch.setattr(runner_mod, "query", make_fake_query([]))
    await runner.run_turn("tg:private:1", "look", attachments=[_pdf(tmp_path)])
    hits = await db.search_messages("report")
    assert any(h["role"] == "user" for h in hits)
    await db.close()


async def test_a_plain_turn_still_sends_a_string_prompt(tmp_path, monkeypatch):
    runner, db = await make_runner(tmp_path)
    captured = []
    monkeypatch.setattr(runner_mod, "query", make_fake_query(captured))
    await runner.run_turn("tg:private:1", "hi")
    assert captured == ["hi"]
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_runner_attachments.py -q
```

Expected: `ImportError: cannot import name '_build_prompt' from 'claude_hermes.runner'`

- [ ] **Step 3: Add the prompt builders to `runner.py`**

Add `import base64` to the imports at the top, then add these two module-level functions after `_is_cli_death` (around `runner.py:58`):

```python
def _compose_text(user_text: str, attachments) -> str:
    """The one description of the attachments, used for BOTH the model's text
    block and the messages_fts row. One function on purpose: if the searchable
    history and what the model actually saw were built separately they would
    drift, and the drift would be invisible until someone searched for a file
    the agent had already read."""
    if not attachments:
        return user_text
    lines = [f"[attachment: {a.filename} — saved at {a.path}]" for a in attachments]
    return "\n".join([user_text, *lines])


def _build_prompt(user_text: str, attachments):
    """A bare string when nothing is attached — the shape every call site has
    always used, so the cached prefix is untouched and text/voice turns are
    byte-identical to before.

    Otherwise a FRESH async iterator on every call. _execute runs a second time
    on the resume-retry path (see _run_turn_locked); building this once above
    that retry would hand the second attempt an exhausted generator.
    """
    text = _compose_text(user_text, attachments)
    if not attachments:
        return text

    # Images before text: the documented ordering for vision requests. Each is
    # also named by path in the text block, so the agent can re-open or process
    # the file rather than only look at it.
    blocks: list[dict] = [
        {"type": "image", "source": {
            "type": "base64",
            "media_type": a.media_type,
            "data": base64.standard_b64encode(a.path.read_bytes()).decode(),
        }}
        for a in attachments if a.is_image
    ]
    blocks.append({"type": "text", "text": text})

    async def _stream():
        yield {
            "type": "user",
            "message": {"role": "user", "content": blocks},
            "parent_tool_use_id": None,
            "session_id": "default",
        }

    return _stream()
```

- [ ] **Step 4: Thread `attachments` through the three signatures**

In `runner.py`, make exactly these edits:

`run_turn` (`:87`):
```python
    async def run_turn(self, session_key: str, user_text: str, progress=None,
                       user_id: int = 0, attachments=None) -> str:
        async with self._locks[session_key]:
            return await self._run_turn_locked(
                session_key, user_text, progress, user_id, attachments
            )
```

`_run_turn_locked` (`:92`):
```python
    async def _run_turn_locked(
        self, session_key: str, user_text: str, progress=None, user_id: int = 0,
        attachments=None,
    ) -> str:
```

Both `self._execute(...)` calls inside it (`:113` and `:127`) gain a trailing keyword:
```python
                user_text, resume_id, append, session_key, progress, ctx,
                attachments=attachments,
```

The `record_message` call for the user turn (`:139`):
```python
        await self._db.record_message(
            session_key, "user", _compose_text(user_text, attachments), now_iso
        )
```

`_execute` (`:155`):
```python
    async def _execute(
        self, user_text: str, resume_id: str | None, append: str, session_key: str,
        progress=None, ctx: TurnContext | None = None, attachments=None,
    ) -> tuple[str | None, str | None, TurnStats]:
```

And its `query` call (`:202`):
```python
        async for msg in query(prompt=_build_prompt(user_text, attachments),
                               options=options):
```

- [ ] **Step 5: Run the new and the existing runner tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_runner_attachments.py tests/test_runner.py tests/test_runner_extensions.py -q
```

Expected: all pass. `test_runner.py` passing unchanged is the proof that no existing path changed shape.

- [ ] **Step 6: Commit**

```bash
git add src/claude_hermes/runner.py tests/test_runner_attachments.py
git commit -m "feat(runner): carry attachments into the turn as image and path content"
```

---

### Task 5: Gate-then-download intake

**Files:**
- Modify: `src/claude_hermes/gateway/telegram.py` (imports, new constants after `_VOICE_FAIL` at `:17`, new `Intake`/`intake_attachments` after `should_title` at `:59`)
- Test: `tests/test_attachments_intake.py`

**Interfaces:**
- Consumes: `specs`, `download_all`, `lane_dir`, `MAX_ATTACHMENT_BYTES` (Tasks 2–3); `_verdict`, `MessageEvent`, `build_session_key` (existing).
- Produces:
  - `Intake(verdict: str, content: str = "", attachments: list = [], error: str | None = None)`
  - `async intake_attachments(message, *, cfg, acl, gate: MessageEvent, home: Path, chat_action=None) -> Intake`

Extracting this from `on_message` is what makes the ordering testable at all — `create_dispatcher` is documented as "exercised by the Docker smoke run, not unit tests" (`telegram.py:240`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_attachments_intake.py`:

```python
from pathlib import Path
from types import SimpleNamespace

from claude_hermes.config import Config
from claude_hermes.gateway.events import MessageEvent
from claude_hermes.gateway.telegram import intake_attachments


class AllowAcl:
    async def check(self, user_id, chat_type):
        return "allow"


class DenyAcl:
    async def check(self, user_id, chat_type):
        return "deny"


class FakeBot:
    def __init__(self, fail=False):
        self.calls = []
        self._fail = fail

    async def download(self, media, destination):
        self.calls.append(destination)
        if self._fail:
            raise RuntimeError("telegram is down")
        Path(destination).write_bytes(b"payload")


def photo_msg(*, size=1000, fail=False):
    return SimpleNamespace(message_id=42, document=None,
                           photo=[SimpleNamespace(file_size=size)],
                           bot=FakeBot(fail=fail))


def doc_msg(*, name="Q3 report.pdf", mime="application/pdf", size=1000):
    return SimpleNamespace(
        message_id=42, photo=None,
        document=SimpleNamespace(file_name=name, mime_type=mime, file_size=size),
        bot=FakeBot(),
    )


def gate(text="", *, chat_type="private", mentioned=False):
    return MessageEvent(chat_type=chat_type, chat_id=1, user_id=7, text=text,
                        mentioned=mentioned)


async def run(message, gate_ev, home, *, acl=None, chat_action=None):
    return await intake_attachments(
        message, cfg=Config(group_require_mention=True), acl=acl or AllowAcl(),
        gate=gate_ev, home=home, chat_action=chat_action,
    )


async def test_photo_with_a_caption_is_saved_and_keeps_the_caption(tmp_path):
    msg = photo_msg()
    r = await run(msg, gate("what is this"), tmp_path)
    assert r.verdict == "run" and r.error is None
    assert r.content == "what is this"
    [att] = r.attachments
    assert att.is_image and att.media_type == "image/jpeg"
    assert att.path.parent == tmp_path / "agent_home" / "inbox" / "tg-private-1"
    assert att.path.read_bytes() == b"payload"


async def test_document_with_a_caption_keeps_its_original_filename(tmp_path):
    r = await run(doc_msg(), gate("summarise this"), tmp_path)
    assert r.content == "summarise this"
    [att] = r.attachments
    assert att.filename == "Q3 report.pdf"      # what the model is told
    assert att.path.name == "42_Q3_report.pdf"  # what is on disk
    assert att.is_image is False


async def test_an_attachment_with_no_caption_still_runs_with_placeholder_text(tmp_path):
    r = await run(photo_msg(), gate(""), tmp_path)
    assert r.verdict == "run"
    assert len(r.attachments) == 1
    assert r.content.strip()          # a neutral placeholder, never empty


async def test_an_unaddressed_group_attachment_downloads_nothing(tmp_path):
    msg = photo_msg()
    seen = []

    async def chat_action():
        seen.append(1)

    r = await run(msg, gate("nice pic", chat_type="supergroup", mentioned=False),
                  tmp_path, chat_action=chat_action)

    assert r.verdict == "ignore"
    assert r.attachments == []
    assert msg.bot.calls == []        # not one byte fetched
    assert seen == []                 # and no typing indicator leaked presence


async def test_a_denied_user_downloads_nothing(tmp_path):
    msg = photo_msg()
    r = await run(msg, gate("hi"), tmp_path, acl=DenyAcl())
    assert r.verdict == "deny"
    assert msg.bot.calls == []


async def test_a_failed_download_becomes_a_visible_error(tmp_path):
    msg = photo_msg(fail=True)
    r = await run(msg, gate("look"), tmp_path)
    assert r.verdict == "run"
    assert r.attachments == []
    assert r.error and "download" in r.error.lower()


async def test_an_oversize_file_is_refused_before_any_download(tmp_path):
    msg = doc_msg(size=25 * 1024 * 1024)
    r = await run(msg, gate("read this"), tmp_path)
    assert r.error and "20MB" in r.error and "Q3 report.pdf" in r.error
    assert msg.bot.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_attachments_intake.py -q
```

Expected: `ImportError: cannot import name 'intake_attachments'`

- [ ] **Step 3: Add imports and constants to `telegram.py`**

At the top, add to the existing imports:

```python
from dataclasses import dataclass, field
```

and after `from .events import MessageEvent`:

```python
from .attachments import MAX_ATTACHMENT_BYTES, download_all, lane_dir, specs
```

After `_VOICE_FAIL` (`:17`), add:

```python
_ATTACH_TOO_BIG = (
    "'{name}' is {mb:.1f}MB — Telegram only lets me download files up to 20MB. "
    "Send a smaller version, or put it somewhere I can fetch."
)
_ATTACH_FAIL = "I couldn't download that file — try sending it again."
_NO_CAPTION = "(sent with no caption)"
```

- [ ] **Step 4: Write `Intake` and `intake_attachments`**

Insert after `should_title` (`:59`):

```python
@dataclass
class Intake:
    """What the attachment path decided, before any turn runs."""

    verdict: str
    content: str = ""
    attachments: list = field(default_factory=list)
    error: str | None = None


async def intake_attachments(message, *, cfg: Config, acl, gate: MessageEvent,
                             home: Path, chat_action=None) -> Intake:
    """Gate first, download second.

    The order is the whole point. An attachment in a group we are not addressed
    in must cost nothing — no download, and no typing indicator either, since
    that would announce the bot in a chat it is supposed to be silent in. Voice
    notes gate the same way before spending a transcription call. The size check
    reads file_size off the Telegram object, so a refusal is also free.
    """
    verdict = await _verdict(cfg, gate, acl)
    if verdict != "run":
        return Intake(verdict)

    items = specs(message)
    for s in items:
        if s.size > MAX_ATTACHMENT_BYTES:
            return Intake(verdict, error=_ATTACH_TOO_BIG.format(
                name=s.original, mb=s.size / (1024 * 1024)))

    if chat_action is not None:
        await chat_action()

    key = build_session_key(gate.chat_type, gate.chat_id,
                            user_id=gate.user_id, thread_id=gate.thread_id)
    try:
        saved = await download_all(message.bot, items, lane_dir(home, key))
    except Exception:
        log.exception("attachment download failed for chat %s", gate.chat_id)
        return Intake(verdict, error=_ATTACH_FAIL)

    return Intake(verdict, content=gate.text or _NO_CAPTION, attachments=saved)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/Scripts/python.exe -m pytest tests/test_attachments_intake.py -q
```

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add src/claude_hermes/gateway/telegram.py tests/test_attachments_intake.py
git commit -m "feat(gateway): ACL-gate attachments on the caption before downloading"
```

---

### Task 6: Wire the handler

**Files:**
- Modify: `src/claude_hermes/gateway/events.py:1-12`
- Modify: `src/claude_hermes/gateway/telegram.py:110` (`handle_event` tail), `:248-307` (`on_message`)
- Modify: `tests/test_gateway.py:19`, `tests/test_approve_command.py:26` (fake `run_turn` signatures)

**Interfaces:**
- Consumes: `Intake`, `intake_attachments` (Task 5); `specs` (Task 2); `run_turn(..., attachments=...)` (Task 4).
- Produces: `MessageEvent.attachments: list` (defaults to `[]`).

- [ ] **Step 1: Add `attachments` to `MessageEvent`**

Replace the whole of `src/claude_hermes/gateway/events.py`:

```python
from dataclasses import dataclass, field


@dataclass
class MessageEvent:
    chat_type: str
    chat_id: int
    user_id: int
    text: str
    mentioned: bool
    thread_id: int | None = None
    message_id: int = 0
    attachments: list = field(default_factory=list)
```

- [ ] **Step 2: Pass them from `handle_event`**

Replace the last line of `handle_event` (`telegram.py:110`):

```python
    return await runner.run_turn(key, text, progress=progress,
                                 user_id=event.user_id,
                                 attachments=event.attachments)
```

- [ ] **Step 3: Update the two existing `run_turn` fakes**

`tests/test_gateway.py:19`:
```python
    async def run_turn(self, session_key, text, progress=None, user_id=0,
                       attachments=None):
        self.calls.append((session_key, text))
        return self.reply
```

`tests/test_approve_command.py:26`:
```python
    async def run_turn(self, key, text, progress=None, user_id=0, attachments=None):
```
(keep that method's existing body unchanged)

- [ ] **Step 4: Rewrite the intake block of `on_message`**

Replace `telegram.py:249-297` — from `media = message.voice or message.audio` down to and including the `else:` branch that sets `content`/`mentioned` — with:

```python
        media = message.voice or message.audio
        files = specs(message)
        if not message.text and media is None and not files:
            return

        mention = f"@{bot_username}"
        reply_to_bot = (
            message.reply_to_message is not None
            and message.reply_to_message.from_user is not None
            and message.reply_to_message.from_user.username == bot_username
        )
        thread_id = thread_lane(message)
        user_id = message.from_user.id if message.from_user else 0
        is_voice = media is not None
        saved: list = []

        async def send_typing() -> None:
            await message.bot.send_chat_action(
                message.chat.id, "typing", message_thread_id=thread_id
            )

        if is_voice or files:
            # Non-text intake. Gate on the caption/reply BEFORE spending a
            # transcription call or a download — a note or a photo in a group we
            # aren't addressed in must cost nothing. Then fall through to the
            # normal turn machinery.
            caption = message.caption or ""
            gate = MessageEvent(
                chat_type=message.chat.type, chat_id=message.chat.id, user_id=user_id,
                text=caption.replace(mention, "").strip(),
                mentioned=(mention in caption) or reply_to_bot,
                thread_id=thread_id, message_id=message.message_id,
            )
            if is_voice:
                verdict = await _verdict(cfg, gate, acl)
                if verdict == "ignore":
                    return
                if verdict == "deny":
                    if message.chat.type == "private":
                        await sender.send(message.chat.id, thread_id, _REFUSAL)
                    return
                if voice is None:
                    await sender.send(message.chat.id, thread_id, _VOICE_OFF)
                    return
                try:
                    await send_typing()
                    audio = await _download_media(message.bot, media)
                    content = await voice.transcribe(audio, _media_filename(media))
                except Exception:
                    log.exception("voice intake failed for chat %s", message.chat.id)
                    await sender.send(message.chat.id, thread_id, _VOICE_FAIL)
                    return
            else:
                intake = await intake_attachments(
                    message, cfg=cfg, acl=acl, gate=gate, home=home,
                    chat_action=send_typing,
                )
                if intake.verdict == "ignore":
                    return
                if intake.verdict == "deny":
                    if message.chat.type == "private":
                        await sender.send(message.chat.id, thread_id, _REFUSAL)
                    return
                if intake.error:
                    await sender.send(message.chat.id, thread_id, intake.error)
                    return
                content, saved = intake.content, intake.attachments
            mentioned = True  # already authorized by the gate above
        else:
            content = message.text.replace(mention, "").strip()
            mentioned = (mention in message.text) or reply_to_bot
```

Then add `attachments=saved` to the `MessageEvent(...)` constructed just below (`:299-307`):

```python
        event = MessageEvent(
            chat_type=message.chat.type,
            chat_id=message.chat.id,
            user_id=user_id,
            text=content,
            mentioned=mentioned,
            thread_id=thread_id,
            message_id=message.message_id,
            attachments=saved,
        )
```

`send_typing` is defined this early because the voice and attachment paths both need it before the `Presenter` exists.

- [ ] **Step 5: Drop the now-duplicate `chat_action` closure**

The existing `chat_action()` closure (`telegram.py:315-318`) is now byte-for-byte identical to `send_typing`. Delete it and pass the one that already exists:

```python
        presenter = Presenter(sender, event.chat_id, thread_id,
                              chat_action=send_typing)
```

`delete_message()` (`:320-321`) is unchanged.

- [ ] **Step 6: Run the whole suite**

```bash
.venv/Scripts/python.exe -m pytest -q
```

Expected: every test passes, including the pre-existing `test_gateway.py`, `test_voice.py`, `test_approve_command.py`, and `test_send_document.py`.

- [ ] **Step 7: Check the module imports cleanly**

```bash
.venv/Scripts/python.exe -c "import claude_hermes.gateway.telegram as t; print(t.intake_attachments, t.Intake)"
```

Expected: both objects print. (`create_dispatcher` itself is only exercised by the Docker smoke run.)

- [ ] **Step 8: Commit**

```bash
git add src/claude_hermes/gateway/events.py src/claude_hermes/gateway/telegram.py \
        tests/test_gateway.py tests/test_approve_command.py
git commit -m "feat(gateway): accept photos and documents in on_message"
```

---

### Task 7: Update the wiki

**Files:**
- Modify: `docs/llm-wiki/components/gateway.md`
- Modify: `docs/llm-wiki/flows/message-turn.md`
- Possibly modify: `docs/llm-wiki/components/core-runtime.md`, `docs/llm-wiki/decisions/prompt-cache-discipline.md`, `docs/llm-wiki/glossary.md`

**Interfaces:**
- Consumes: everything built in Tasks 2–6.
- Produces: a passing `.llm-wiki/llm-wiki validate`.

> **Pre-existing scope, flagged.** `validate` **already fails** on `main` before any of this work, with 4 `stale-evidence` errors: `components/core-runtime.md`, `decisions/prompt-cache-discipline.md`, `flows/message-turn.md`, `glossary.md`. The acceptance criterion "validate passes" therefore pulls in three pages this feature did not touch. Do **not** blindly re-fingerprint them — a fingerprint asserts the page was checked against its evidence. Read each, fix any claim the drift invalidated, then fingerprint.

- [ ] **Step 1: Confirm the pre-existing failures**

```bash
wsl -d Ubuntu -- bash -lc 'cd /mnt/d/projects/claude-hermes && ./.llm-wiki/llm-wiki validate'
```

Expected: JSON with 4 `stale-evidence` errors on the paths listed above.

- [ ] **Step 2: Rewrite `components/gateway.md`**

Add to the `evidence:` list in the frontmatter, after the `telegram.py` entry:
```yaml
  - path: src/claude_hermes/gateway/attachments.py
```

Add to the `## Structure` list, after the `events.py` bullet:
```markdown
- `attachments.py` — `Attachment` / `Spec` (attachments.py), `safe_name`
  (message-id-prefixed, `[A-Za-z0-9._-]` only — no traversal survives),
  `lane_dir` (`CH_HOME/agent_home/inbox/<lane>/`, inside the agent's cwd so its
  own `Read`/`Glob` reach the file), `specs`, `download_all`. 20MB ceiling
  (`MAX_ATTACHMENT_BYTES`), the Bot API's own `getFile` limit.
```

Replace the first sentence of `## Update flow` (currently
"`on_message` (telegram.py:248) accepts text or voice/audio only.") with:

```markdown
`on_message` (telegram.py:248) accepts text, voice/audio, photos and documents.
Every non-text intake is ACL-gated on the caption **before** any paid work —
transcription for voice, download for attachments (`intake_attachments`) — so an
un-addressed note or photo in a group costs nothing and does not even leak a
typing indicator. Attachments land under `CH_HOME/agent_home/inbox/<lane>/` with
a sanitized, message-id-prefixed filename; images additionally reach the turn as
base64 image content blocks (`_build_prompt`, runner.py) while documents are
passed by path for the agent to open. A caption-less attachment runs with
placeholder text. Files over 20MB, and failed downloads, get a user-visible
error rather than silence. A media group arrives as one update per item, so each
becomes its own turn. With no `OPENAI_API_KEY` a voice note gets a "voice isn't
set up" reply.
```

- [ ] **Step 3: Rewrite step 1 of `flows/message-turn.md`**

Replace the line at `:23` ("1. **Update** — `on_message` (telegram.py:248) accepts text or voice/audio.") with:

```markdown
1. **Update** — `on_message` (telegram.py:248) accepts text, voice/audio, photos
   and documents. Non-text intake gates on the caption before transcribing or
   downloading; attachments ride on `MessageEvent.attachments` into `run_turn`.
```

Read the rest of the page and fix anything else the stale-evidence drift invalidated.

- [ ] **Step 4: Reconcile the three untouched stale pages**

Read each page against the specific evidence files it claims, and correct any claim that no longer holds:

| Page | Evidence to re-read | Why it is stale |
|---|---|---|
| `components/core-runtime.md` | `main.py`, `runner.py`, `options.py`, `prompt.py`, `config.py`, `paths.py`, `db.py`, `sessions.py`, `wiki.py`, `hooks.py`, `tools.py` | Pre-existing drift **plus** this feature: Task 4 changed `run_turn`, `_run_turn_locked` and `_execute` signatures and added `_compose_text`/`_build_prompt`. If the page documents any of those signatures or states that the prompt is always a string, update it. |
| `decisions/prompt-cache-discipline.md` | `docs/PRD.md`, `2026-07-14-extensions-mcp-plugins-design.md`, `wiki.py`, `hooks.py`, `prompt.py`, `runner.py` | Pre-existing drift **plus** `runner.py`. The cache-relevant fact to confirm the page still states correctly: the system prompt and toolset are unchanged by this feature, and `_build_prompt` returns a bare string when nothing is attached, so no existing turn's cached prefix moved. |
| `glossary.md` | `docs/PRD.md`, `sessions.py`, `extensions/catalog.py`, `wiki.py`, `hooks.py`, `prompt.py`, `learning/review.py`, `learning/curator.py` | **Purely pre-existing drift** — this feature touches none of these files. Fix whatever the drift invalidated; do not add attachment vocabulary here unless the page already defines gateway intake terms. |

If a page's prose is still accurate against its evidence, fingerprinting it in Step 5 is the correct and honest outcome — the hash records that it was checked at this commit.

- [ ] **Step 5: Fingerprint every changed page**

```bash
wsl -d Ubuntu -- bash -lc 'cd /mnt/d/projects/claude-hermes && \
  for p in components/gateway.md flows/message-turn.md components/core-runtime.md \
           decisions/prompt-cache-discipline.md glossary.md; do \
    ./.llm-wiki/llm-wiki fingerprint --page "docs/llm-wiki/$p"; done'
```

- [ ] **Step 6: Validate**

```bash
wsl -d Ubuntu -- bash -lc 'cd /mnt/d/projects/claude-hermes && ./.llm-wiki/llm-wiki validate'
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add docs/llm-wiki
git commit -m "docs(llm-wiki): the gateway takes photos and documents, not just text and voice"
```

---

### Task 8: End-to-end check against a real bot

**Files:** none (manual verification against the acceptance criteria).

- [ ] **Step 1: Run the full suite one more time**

```bash
.venv/Scripts/python.exe -m pytest -q
```

- [ ] **Step 2: Send a photo in a private chat**

Expected: a reply that demonstrably describes the image's content.

- [ ] **Step 3: Send a `.pdf` or `.csv` with a caption**

Expected: a reply where the agent has read, or clearly can read, the file from the workspace path it was given.

- [ ] **Step 4: Post an attachment in a group without addressing the bot**

Expected: no reply, and no new file under `CH_HOME/agent_home/inbox/`.

- [ ] **Step 5: Confirm the files survive a restart and stay inside `CH_HOME`**

```bash
ls -R "$CH_HOME/agent_home/inbox"
```
Restart the container, then list again — same files. Confirm nothing was written outside `CH_HOME`.

- [ ] **Step 6: Send a plain text message and a voice note**

Expected: both behave exactly as before.

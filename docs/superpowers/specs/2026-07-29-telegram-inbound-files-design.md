# Inbound files: photos and documents reach the turn

Date: 2026-07-29
Status: draft — not yet run past implementation-advisor

## Problem

`on_message` (`src/claude_hermes/gateway/telegram.py:248`) opens with:

```python
media = message.voice or message.audio
if not message.text and media is None:
    return
```

A photo or a document therefore returns early. Not "unsupported" — *silent*. No reply,
no log line, no error. The user sends a screenshot, watches the bot say nothing, and has
no way to tell a dropped attachment from a crashed process. Voice notes were given a real
intake path (`telegram.py:263-294`) and files were not, so the gateway accepts exactly two
of the message types a person actually sends.

The wiki records the limitation accurately in two places, which is why both must change:
`docs/llm-wiki/components/gateway.md:53` ("accepts text or voice/audio only") and
`docs/llm-wiki/flows/message-turn.md:23`.

## What voice already establishes

The voice branch is the precedent and this design follows it deliberately rather than
inventing a second shape:

1. Build a `MessageEvent` from the **caption**, run `_verdict` on it, and return on
   `ignore`/`deny` — **before** spending a transcription call (`telegram.py:267-280`).
   An un-addressed note in a group costs nothing.
2. Only then do the paid/IO work (`_download_media`, `voice.transcribe`).
3. Fall through to the ordinary turn machinery with the result as `content`, and
   `mentioned = True` because the gate already authorized it (`telegram.py:294`).

Attachments get the same three beats, with download in place of transcription.

## Verified constraints

Checked against the pinned SDK (`claude-agent-sdk==0.2.126`, `pyproject.toml:11`) rather
than assumed:

- `query(prompt=...)` accepts `str | AsyncIterable[dict[str, Any]]`.
- `_internal/query.py:839` writes each streamed dict through `json.dumps` **verbatim**.
  An `{"type": "image", "source": {...}}` content block passes to the CLI untouched — the
  SDK does no schema validation that could reject it.
- The transport **always** passes `--input-format stream-json`
  (`_internal/transport/subprocess_cli.py:469`), independently of whether the caller gave
  a string or an iterable. `--resume` is a separate flag (`:352`). Streaming input and
  session resume therefore compose; the runner's resume-retry machinery
  (`runner.py:116-129`) is unaffected.
- `base_options` sets `cwd = home / "agent_home"` (`options.py:27`). Anything saved under
  `agent_home/` is inside `CH_HOME` (NFR-5: `tar ./data` stays a complete backup) *and*
  natively reachable by the agent's `Read`/`Glob`/`Grep`, with no new mount and no
  path allowlist change.
- `run_turn` has exactly one production caller (`telegram.py:110`). A keyword-only
  parameter with a default is additive.

**Verified 2026-07-29:** the CLI renders stream-json image blocks; the model named the
colour. One real call sent a 64×64 crimson PNG as a `{"type": "image", "source":
{"type": "base64", ...}}` block through `query(prompt=<async iterator>)` and asked for
the single colour filling it; the reply was `Crimson`. This was the one assumption that
could have invalidated a section of the design — it holds, so images reach the turn as
base64 image content blocks rather than degrading to path-only.

## Decisions

Four questions were open at design time. All four took the conservative option.

**Media groups → one turn per attachment.** Telegram delivers each item of an album as a
separate update sharing a `media_group_id`, and only the first carries the caption.
Buffering them into one turn needs an in-memory debounce keyed on that id, a flush path,
and a buffer that is lost on restart — a new stateful failure mode in the handler for an
uncommon case. Each update runs its own turn instead; items 2..N get the placeholder text
because Telegram gave them no caption.

**Retention → keep indefinitely.** Files stay under `CH_HOME`. Age or size pruning bounds
disk at the cost of a path the agent recorded in an earlier session silently ceasing to
resolve, which is a worse failure than disk growth on a personal agent.

**Size ceiling → refuse above 20MB.** That is the Bot API's own `getFile` download limit,
so a larger file fails at Telegram's side regardless. The check reads `file_size` off the
Telegram object, so a refusal costs no download. The reply names the file and its size
rather than surfacing a generic failure.

**DB record → fold into the recorded user text.** `run_turn` already calls
`record_message(session_key, "user", user_text, …)` (`runner.py:139`), and
`messages_fts` is a plain FTS5 table over `(session_key, role, content, ts)`
(`db.py:15-17`). Appending an attachment line to that text makes the filename and path
searchable via `session_search` for free — no schema change, no migration, and no second
source of truth alongside the files on disk.

## Design

### 1. `gateway/attachments.py` (new)

```python
@dataclass(frozen=True)
class Attachment:
    path: Path        # absolute, under CH_HOME/agent_home/inbox/<lane>/
    filename: str     # the original Telegram name, for the model to refer to
    media_type: str   # "image/jpeg", "application/pdf", …
    is_image: bool    # media_type is one of the four the API accepts
```

Pure functions, unit-testable with no bot and no filesystem:

- `specs(message)` — the largest `PhotoSize` from `message.photo`, or `message.document`.
  Returns a list so the caller has one shape regardless of type. A Telegram `photo` has
  no `file_name`, so its name is derived (`photo.jpg`); a `document` keeps its own.
- `safe_name(message_id, raw)` — strips directory components, keeps `[A-Za-z0-9._-]` and
  collapses the rest to `_`, caps length, rejects empty/`.`/`..`, and prefixes
  `message_id`. The prefix does two jobs: two `scan.pdf` from different messages cannot
  collide, and the name is stable (a message id never changes), so a path the agent wrote
  down keeps resolving.
- `lane_dir(home, session_key)` — `home/agent_home/inbox/<sanitized session_key>`;
  `tg:private:1:t9` becomes `tg-private-1-t9`.

One IO function: `download_all(bot, specs, dest)` → `list[Attachment]`, via
`bot.download(media, destination=path)`.

`is_image` covers `image/jpeg`, `image/png`, `image/gif`, `image/webp` — the four the API
accepts. A Telegram `photo` is always JPEG; a `document` carrying `image/png` is treated
as an image too, which is what a user who drags a PNG into the chat expects.

### 2. Intake — `telegram.py`

The early return widens:

```python
if not message.text and media is None and not specs(message):
    return
```

The caption-gate block (currently voice-only, `telegram.py:267-274`) is shared between
voice and attachments. Gate-then-download then lives in one coroutine:

```python
async def intake_attachments(message, *, cfg, acl, gate: MessageEvent, home) -> Intake
```

returning `Intake(verdict, content, attachments, error)`. Order inside it is load-bearing:

1. `_verdict(cfg, gate, acl)` — on `ignore` or `deny`, return immediately with an empty
   attachment list. The downloader is never reached.
2. `file_size` check against the 20MB ceiling — still before any download.
3. Download into `lane_dir`. Any exception is caught, logged, and returned as `error`.

`content` is the caption, or a neutral placeholder when there is none.

`on_message` then handles the result exactly as it does for voice: `ignore` returns
silently; `deny` sends `_REFUSAL` in a private chat and stays silent in a group
(`telegram.py:277-280`); a set `error` is sent to the chat and the turn does not run;
otherwise it falls through with `mentioned = True`, already authorized by the gate.

**Why this is a separate coroutine rather than inline in `on_message`:** `create_dispatcher`
is documented as "exercised by the Docker smoke run, not unit tests"
(`telegram.py:240`). Two of the requirements — an un-addressed group attachment must not
download, and a failed download must produce a visible error — are assertions about
ordering and error handling inside the handler. They can only be tested if that logic
lives outside the aiogram decorator. `on_message` stays thin wiring, exactly as it
already is for voice.

New user-visible strings alongside `_VOICE_OFF`/`_VOICE_FAIL`: `_ATTACH_TOO_BIG` (names
the file and its size), `_ATTACH_FAIL`, and the no-caption placeholder.

### 3. Turn plumbing

`MessageEvent` gains `attachments: list[Attachment] = field(default_factory=list)`.
`run_turn` gains keyword-only `attachments=None`, threaded to `_execute`.

**Prompt construction happens inside `_execute`, not `_run_turn_locked`.** The
resume-retry path calls `_execute` a second time (`runner.py:127`) and an async generator
is single-use — building the prompt once above that call would hand an exhausted iterator
to the retry. The builder is called fresh per `_execute` invocation.

With no attachments it returns the plain `str`. Every existing path — text, voice,
`/learn`, and the background forks that never had attachments — is byte-identical to
today, so the cached prefix is untouched.

With attachments it returns an async iterator yielding one message:

```python
{"type": "user",
 "message": {"role": "user", "content": blocks},
 "parent_tool_use_id": None,
 "session_id": "default"}
```

`blocks` is image blocks first (base64, per-attachment `media_type`), then a single text
block. Images before text is the documented ordering for vision requests.

The text block is the caption (or placeholder) followed by one line per attachment naming
the original filename and the on-disk path. Images get that line **too**: requirement 4
says visible to the model *in addition to* being saved, and a path lets the agent re-open
or process the file rather than only look at it. Documents are path-only, per
requirement 5 — the agent decides whether to open them.

A single `_compose_text(user_text, attachments)` produces that text, and both the prompt
block and the `record_message` call read from it. One function, so the FTS row and what
the model actually saw cannot drift apart.

### 4. Tests — `tests/test_attachments.py`

`SimpleNamespace` fakes with a recording `bot.download`, matching the existing
`test_gateway.py` style (`tests/test_gateway.py:146`).

Required by the task:

1. Photo with a caption → one image `Attachment`, `content` is the caption.
2. Document with a caption → one non-image `Attachment` keeping the original filename.
3. Attachment with no caption → `content` is the placeholder, attachment still present.
4. Un-addressed group attachment → verdict `ignore`, **zero** download calls.
5. Download raises → `Intake.error` is set, no exception escapes.

Added:

6. Oversize file → refused, zero download calls (the check precedes IO).
7. `safe_name` on `../../etc/passwd` → result stays inside the lane directory.
8. The prompt builder returns a bare `str` when there are no attachments — the guard
   that the existing text and voice flows did not change shape.

The two existing `run_turn` test fakes (`tests/test_gateway.py:19`,
`tests/test_approve_command.py:26`) gain the `attachments` parameter.

### 5. Wiki

`docs/llm-wiki/components/gateway.md` — rewrite the "Update flow" paragraph, add
`attachments.py` to Structure and to the `evidence:` list.
`docs/llm-wiki/flows/message-turn.md` — rewrite step 1.
Then `.llm-wiki/llm-wiki fingerprint --page <page>` on both, and
`.llm-wiki/llm-wiki validate`.

## Out of scope

Outbound files (the agent sending documents back already exists as `send_document`,
`tools.py:401`), and video, stickers, location, and contact message types.

## Known wart

A photo captioned `/new` downloads the file and then resets the lane, leaving an orphaned
file. Adding a branch to skip download for gateway-intercepted commands means a second
place that must know the command list, which will go stale. The waste is one file in a
rare case; it is left alone deliberately.

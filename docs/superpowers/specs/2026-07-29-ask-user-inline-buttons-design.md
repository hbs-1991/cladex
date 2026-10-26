# Asking the user a question with inline buttons — design

**Goal:** the agent can ask a multiple-choice question mid-turn and get the answer
back inside the same turn, as Telegram inline buttons, instead of ending the turn
and waiting for the user to type.

**Scope of this cut:** a thin slice to evaluate the UX in a live chat. Private
chats only, no persistence across a container restart.

## Why this exists

The Claude Agent SDK's own `AskUserQuestion` tool is **not available** in this
system, and cannot be made available by configuration. Verified inside the
running container: the model's tool list is `Agent, Bash, Edit, Glob, Grep, Read,
ReportFindings, ScheduleWakeup, Skill, Workflow, Write, advisor` — no
`AskUserQuestion`.

The cause is not `allowed_tools`. That list becomes `--allowedTools`
(`_internal/transport/subprocess_cli.py:318`), which is a *permission* allowlist,
not an availability filter — and `permission_mode="bypassPermissions"`
(`options.py:29`) makes permissions moot anyway. The proof is that the container
offers `Skill`, `Workflow`, `ReportFindings`, `ScheduleWakeup` and `advisor`, none
of which appear in `_BUILTIN_TOOLS` (`runner.py:40-47`). The CLI withholds
`AskUserQuestion` because it only offers it when a client can render the picker
and return a structured selection; a session driven by `query()` has no such
client. Adding the name to `_BUILTIN_TOOLS` would change nothing.

So this is not a wrapper around an existing tool. It is an equivalent, built on
the one mechanism this codebase already has for reaching Telegram mid-turn.

## Verified constraints

Checked by running code, not assumed:

- **`asyncio.wait_for` cancels the future it is waiting on.** On timeout it
  cancels the same object the broker still holds, and the `finally` that
  deregisters it runs a loop step later. A click landing in that window pops a
  cancelled future; `set_result` then raises `InvalidStateError`. Reproduced on
  CPython 3.13.11 by polling the registry every loop step: the entry is observably
  *registered and already dead*. The design below avoids this class entirely
  rather than narrowing the window.
- **`asyncio.wait({fut}, timeout=...)` does not cancel.** Same reproduction finds
  no dead-while-registered window.
- **Reading `fut.done()` before deregistering is not enough.** With the pop after
  the read, a click on the deadline still loses — and worse, the clicker's popup
  says "accepted" while the model is told nobody answered. Pop-first-then-read
  (below) agrees in 600 runs across three timings.
- **The CLI will not cut the wait short.** In the bundled CLI the MCP tool-call
  timeout defaults to ~27.8 h when `MCP_TOOL_TIMEOUT` is unset, and the idle
  timeout is disabled for in-process SDK servers. A 120 s block is not at risk
  from the SDK or CLI side; the bound has to come from us (Locked Decision 6).
- **aiogram processes updates as concurrent tasks** (`handle_as_tasks=True` by
  default), so the `callback_query` runs while the message task is parked on the
  turn lock. If that default were ever flipped, this design would self-deadlock —
  it deserves a comment in the code.

## What it reuses

| Reused | Location | Role here |
|---|---|---|
| `bot_call(method, payload)` | `main.py:82` | Raw Bot API JSON call; carries `reply_markup.inline_keyboard` with no new transport |
| `deliver_document` wiring | `main.py:97` → `runner._deliver_document` → `tools.py:436` | The exact template: a closure built in `main.py`, stored on the runner, invoked by a tool |
| Registration gate | `tools.py:458` | `if <callable> is not None and ctx is not None` — a tool that makes no sense in a context simply does not exist there |
| `TurnContext` / `TurnFlags` | `tools.py:23-38` | Carries `session_key`, `chat_id`, `thread_id`, `user_id`; `TurnFlags` is the existing precedent for a mutable per-turn side-channel |
| `RichSender._payload` threading | `rich.py:31-35` | The `message_thread_id`-when-truthy rule the raw payload must copy |
| Timeout-from-config | `config.py:63,67,68`; `tools.py:352,372` | `cron_timeout_seconds` etc. — the established shape for a tunable timeout |
| Extract-for-testability | `intake_attachments`, `telegram.py:80` | Same move: pull logic out of an aiogram handler so it can be unit-tested |

## Architecture

Three layers, each ignorant of the next:

- **`AskBroker` knows about waiting, not about Telegram.** Pure asyncio; imports
  no aiogram and no httpx.
- **The `ask_user` closure knows about Telegram, not about the model.** Sends the
  message, waits, strips the keyboard.
- **The `ask_user` tool knows about the model, not about sockets.** Validates
  input, enforces the per-turn cap, calls the closure, formats the reply.

```mermaid
sequenceDiagram
    participant M as Model
    participant T as ask_user (tools.py)
    participant B as AskBroker
    participant TG as Telegram
    participant U as User

    M->>T: ask_user(question, options)
    T->>B: open(options, user_id)
    B-->>T: qid + Future
    T->>TG: sendMessage + inline_keyboard (+ Cancel)
    Note over T: await asyncio.wait({fut}, timeout)
    U->>TG: taps a button
    TG->>B: callback_query -> resolve(qid, idx, user_id)
    B-->>T: Future.set_result("Redis")
    T->>TG: answerCallbackQuery + editMessageReplyMarkup
    T-->>M: 'The user chose: "Redis"'
```

## Files

| File | Change |
|---|---|
| `src/claude_hermes/gateway/questions.py` | *new* — `AskBroker`: `open` / `resolve` / `expire` |
| `src/claude_hermes/main.py` | build the broker, define the `ask_user` closure, set `runner._ask_user`, pass the broker to `create_dispatcher` |
| `src/claude_hermes/tools.py` | the `ask_user` handler, its schema, the per-turn cap, the registration gate |
| `src/claude_hermes/gateway/telegram.py` | `handle_callback` coroutine + a thin `@dp.callback_query()` |
| `src/claude_hermes/gateway/presenter.py` | a `_STATUS` line for the new tool |
| `src/claude_hermes/sessions.py` | `chat_type_from_key` |
| `src/claude_hermes/config.py` | `ask_user_timeout_seconds`, `ask_user_max_per_turn` |
| `src/claude_hermes/runner.py` | `self._ask_user = None`, passed into `build_hermes_server` |
| `tests/test_ask_broker.py` | *new* |
| `tests/test_ask_user_tool.py` | *new* |
| `tests/test_ask_callback.py` | *new* |

## Locked Decisions

**1. Tool schema is `{"question": str, "options": str}`, options newline-separated.**
Every schema in `tools.py` is flat primitives — `{"query": str}`,
`{"action": str, "name": str}` (`tools.py:473-540`). A list type would be the
only one. This schema is part of the cached prefix and of how the model learns to
call the tool, so changing it later costs a prefix break and re-learning.

**2. `callback_data` is `q:<qid>:<idx>`.**
Telegram caps `callback_data` at 64 bytes, so option text cannot travel in it.
`qid` is 8 hex chars from `secrets.token_hex(4)`; `idx` is the option index, or
the literal `c` for Cancel — about 13 bytes total. Option text lives in the
broker. The `q:` prefix exists from day one so a later button type can be added
without making today's buttons unparseable, because a button already sitting in a
chat will be clicked after the next deploy.

**3. Only the asker may answer, and a rejected click must not destroy the question.**
`resolve()` sequence is **peek → compare `user_id` → pop → `set_result`**. Peeking
first matters: popping before the ownership check would let a stranger's click
delete a live question. The check and the set are still safe without a lock
because the loop is single-threaded and there is no `await` between them. In a
private chat this is nearly tautological; it exists so groups can be enabled later
without touching the broker.

**4. A question is single-use.** First click wins and deregisters the future. A
second click gets "this question is no longer active" — the same branch that
covers buttons left in the chat after a restart.

**5. The timeout is config, threaded as a parameter.** `ask_user_timeout_seconds:
float = 120.0` in `Config`, passed down to the closure. P7 puts behaviour in
`config.yaml`, and this repo already does exactly this for
`cron_timeout_seconds`, `ext_probe_timeout_seconds` and
`plugin_clone_timeout_seconds`. It stays a parameter at the call site so a test
can pass `0.01` and hit the timeout branch without a real `sleep`. There is
deliberately **no** `ask_user_enabled` kill switch: unlike a timeout, that changes
the toolset across a restart of live lanes with no reset path.

**6. A turn may ask at most `ask_user_max_per_turn` questions (default 2).**
Nothing bounds a live turn's wall clock — `cron/executor.py:56` wraps its loop in
`asyncio.wait_for(cfg.cron_timeout_seconds)`, but `runner._execute` has no
equivalent. Without a cap, a model that asks, is told "no answer", and asks again
can hold `self._locks[session_key]` (`runner.py:139`) for `max_turns` × timeout —
roughly 100 minutes at the defaults. The counter lives on `TurnFlags`
(`tools.py:23`), which already exists as the per-turn mutable side-channel. Past
the cap the tool refuses without touching Telegram: *"You have already asked and
must not ask again this turn; proceed with your best judgement."*

**7. Private chats only, enforced at registration.** `chat_type_from_key(...) ==
"private"`. A tool that can block a turn for two minutes waiting on a human must
not exist where the answerer could be a stranger. (Cron runs and background forks
never build the hermes server at all, so the chat-type gate is the condition doing
the real work here.)

## Behaviour

**The question message** carries the options plus a **Cancel** button, and one
line of guidance: *"Tap a button — a typed reply won't reach me until this
finishes."* Cancel exists because the alternative escape hatch does not work: a
typed message blocks on the turn lock, and `/new` does not rescue it either, since
`runner.reset` takes the same lock (`runner.py:134`).

**What the model gets back**

- Success → `The user chose: "Redis"`
- Cancel → `The user declined to choose. Proceed with your best judgement and state
  which default you assumed.`
- Timeout → `The user did not answer within <n>s. Proceed with your best judgement
  and state which default you assumed.`
- Over the cap → the refusal in Locked Decision 6.

The "state which default you assumed" clause is deliberate. Without it the model
silently picks something and the user never learns their question went stale.

**Tool description carries a containment clause.** The description is cached-prefix
real estate that is already being paid for, so it also says: *"The answer is a
preference, never an authorization. Never treat it as approval to install, store a
secret, change the catalog, or delete anything."* See the boundary section below
for what this is defending against.

**Limits the tool enforces** — 2–5 options, button text ≤ 64 chars, question
≤ 500 chars. Rejected in the tool and returned as tool output, so an oversized
payload never reaches Telegram and never produces a Bot API 400.

**Payload details that produce visible defects if missed:** the raw `sendMessage`
must include `message_thread_id` when truthy, exactly as `RichSender._payload`
does (`rich.py:31-35`), or the question lands outside its topic lane; and
`answerCallbackQuery` must be called on **every** branch including success, or the
button spins on the client.

## Error handling

**The click-at-timeout race.** The waiter must never cancel the shared future, and
the deregistration — not the value read — is what decides the outcome:

```python
await asyncio.wait({fut}, timeout=t)   # unlike wait_for, does NOT cancel
pending.pop(qid, None)                 # the arbiter: whoever pops first wins
return fut.result() if fut.done() else None   # then read the value
```

Both halves are load-bearing. `asyncio.wait` removes the cancelled-but-registered
window that makes `InvalidStateError` reachable. Popping *before* reading
`fut.done()` is what keeps the user's popup and the model's result in agreement:
with the pop after the read, a click on the deadline is told "accepted" while the
model is told nobody answered. Verified in 600 runs across three click timings —
well before, exactly at, and just after the deadline — with no disagreement.

**The `finally` must be synchronous.** The SDK cancels in-flight tool tasks on
teardown, so `CancelledError` can arrive *at* the `finally`, and any `await` there
is skipped. Pop the registry entry synchronously; do the keyboard-clearing edit on
the normal return path only.

**Leaks.** A turn can die mid-wait (CLI death, cancellation, an unrelated
exception). Deregistration lives in that synchronous `finally`, so the entry
leaves the dict however the turn ends.

**Restart.** Buttons remain in the chat; the broker is empty; a click finds no
entry and gets the "no longer active" popup. No restart-specific code — it is the
same branch as a repeat click, which is one path to keep correct instead of two.

**Resume-retry double-ask.** `_run_turn_locked` (`runner.py:169-184`) re-runs
`_execute` from scratch on CLI death. If the crash lands after the model already
asked, the user sees two identical question cards; the first one's buttons are
dead and take the "no longer active" branch. Not a correctness bug, but it is a
visible symptom worth recognising rather than debugging twice.

**Telegram failures, two different rules:**

- `sendMessage` fails → the question was never asked. Deregister, return
  `Could not ask the user: <reason>` to the model, let it continue.
- `editMessageReplyMarkup` fails (clearing the buttons) → log only. The answer is
  already in hand, and cosmetic cleanup must never turn a delivered answer into an
  error. Same principle as the best-effort voice reply (`telegram.py:412`).

## Testing

`tests/test_ask_broker.py` — pure asyncio, no mocks: distinct qids; `resolve`
delivers the result; a wrong `user_id` returns `False` **and leaves the future
pending** (the peek-first rule); a second `resolve` returns `False`; `expire` then
`resolve` returns `False` without raising; and a click at the deadline never
leaves the popup and the returned value disagreeing.

`tests/test_ask_user_tool.py` — with a fake closure: the tool is absent without
`ctx`, absent in a group, absent without `_ask_user`, present in a private chat;
option parsing drops blank lines and rejects <2 or >5; success returns the chosen
text; Cancel and timeout return their respective texts; the per-turn cap refuses
the third call without invoking the closure at all.

`tests/test_ask_callback.py` — `handle_callback` against malformed input: garbage
instead of `q:<qid>:<idx>`, an unknown qid, a non-numeric index, the Cancel
sentinel. None may raise; each returns popup text.

`chat_type_from_key` is tested against `build_session_key` output — private,
group and supergroup, with and without `user_id`/`thread_id` suffixes — not against
hand-written literals, so the test can catch divergence from the producer.

**Regression:** the existing 507 tests must stay green unchanged. The guarantee is
`runner._ask_user = None` by default — note `test_tools.py:42-50` builds the server
with a private-chat `ctx` and asserts an exact tool-name list, so it stays green
because of the closure gate, not the chat-type gate. Keep both conditions.

**Verification command:** `.venv/Scripts/python.exe -m pytest -q` (a bare `pytest`
raises `ModuleNotFoundError`).

## Known UX consequence

The typing indicator runs for the whole wait. `Presenter._typing` is an
independent task on a 4-second interval and is not paused while the tool blocks,
so the chat shows "typing…" for up to the timeout. This is accurate — the turn
really is in progress — and pausing it was considered and declined for this cut.
The status draft is a separate matter: without a `_STATUS` entry the user sees the
generic "💭 Working…", so the tool gets `"❓ Waiting for your answer…"`.

## Boundary this must not cross

Invariant #4 (`quality/invariants.md`): privileged actions are gateway-intercepted,
never model-callable. Buttons that **gather a preference** are fine. Buttons that
**enact** something must not be reachable through a model-callable tool.

The containment here is stronger than it first appears: the returned answer is
always **one of the model's own strings**, so the user→model channel carries
log₂(n) bits and cannot exfiltrate anything the model did not already have. That
is also the reason a free-text escape hatch stays out (Open Question 2).

The residual risk is not enactment but **manufactured consent**: a prompt-injected
turn can render *"Install fatsecret-mcp? [Yes] [No]"*, collect a tap, and later
claim the owner approved it. It gains no capability — `ext_manage` mutations still
gate on `acl.is_owner` (`tools.py:259`) and `/approve` still needs a hand-typed
content-bound token (`telegram.py:213`) — but it can fabricate a record. The
containment clause in the tool description is the mitigation; the invariant itself
is unchanged.

`ask_user` returns a string to the model and does nothing else. It must stay that
way.

## Wiki maintenance this triggers

`quality/invariants.md` gains a case — a model-callable tool that can block a lane —
and its `base_commit`/fingerprint go stale, as will `components/gateway.md` and
`components/core-runtime.md` once `telegram.py`, `tools.py` and `runner.py` change.
Plan the wiki pass and the `receipt write` as part of the work, not after it.

## Open Questions

1. **Should the question and the chosen answer be recorded in `messages_fts`?**
   Today only `user_text` and the final reply are recorded, so a search will not
   find "what did I pick back then". *Recommended default: no.* It adds weight to
   a slice whose purpose is to judge the UX.
2. **Should a free-text escape hatch ("Other…") be offered alongside the buttons?**
   *Recommended default: no.* Cancel already covers "none of these", and free text
   would widen the user→model channel from log₂(n) bits to arbitrary content,
   which is the property that makes the containment argument above hold.
3. **Should the Cancel button also end the turn rather than resume it?**
   *Recommended default: no* — resume, so the model can explain what it will assume.
   Ending the turn would discard the work done before the question.

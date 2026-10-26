---
id: decision.threads-as-session-lanes
kind: decision
status: current
summary: A Telegram topic is a session lane; sessions are eternal within a thread; /new is the reset.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:e7e9a8d89895114752cd6b182c606087eb9528194a3a96c75967ed27161dc245
evidence:
  - path: docs/superpowers/specs/2026-07-13-telegram-threads-sessions-design.md
  - path: README.md
relations:
  - component.gateway
  - flow.message-turn
---
# Decision: Telegram threads are session lanes

## Context

Originally one Telegram chat was one session lane whose boundary was guessed by
timers (`idle_reset_hours`, `daily_reset_hour`). The user could not start a fresh
conversation on demand; every topic shared one transcript. Telegram Bot API 10.0
(2026-05) added topics in private chats ("Threaded Mode") and let bots create
them; 10.1 added rich messages and streaming drafts (threads design spec).

## Decision

- **Session key stays a `TEXT` string, extended with the thread id:**
  `tg:private:<chat_id>:t<thread_id>`; absent thread → the previous format
  byte-for-byte, so no migration of `sessions`, the message corpus or
  `turn_costs`.
- **Sessions are eternal within a thread**; timer resets default off. The only
  automatic reset is the failure path (dead transcript on `resume` → drop row,
  replay once).
- **`/new` is the manual reset** for the current topic's lane. A Customize
  mutation in the browser is the only thing that resets *every* lane at once
  (`runner.reset_all`), because it has no lane of its own and changes the prefix
  all of them cache; a lane loses its resume id, never its thread row.
- **Scheduled jobs get their own topic**, closed when the job is deleted.
- **All Bot API 10.x surface is confined to `modules/telegram/infrastructure/
  rich_sender.py` and `modules/telegram/infrastructure/topics.py`** because the API is young and churn must cost one file.
- **Drafts carry a one-line status, never the reply body**; the final formatted
  message is what commits to the chat, with a degradation chain down to plain
  chunked `sendMessage`.

## Consequences

- The user controls context boundaries by opening/closing topics.
- New topics are auto-named from their first message (titler) — except the
  content plan's «Контент» topic, which carries its approval cards and is
  never renamed ([[component.gateway]]).
- A cron delivery is posted outside any lane, so the lane of the job's own
  topic starts blind to it; the runner quotes the latest report into the first
  turn after each delivery (see [[component.cron]]).
- Threaded Mode carries a Telegram Stars fee (accepted in the spec).
- The lane, not the chat, turned out to be the reusable primitive: the web
  channel added `web:<user_id>:<uuid4 hex>` as a third key shape with no
  mapping table and no change to routing, and `chat_type_from_key` answers
  `"private"` for it so every private-only rule applies unchanged
  ([[decision.web-as-a-channel]]).

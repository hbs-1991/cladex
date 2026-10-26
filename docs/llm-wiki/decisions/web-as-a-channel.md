---
id: decision.web-as-a-channel
kind: decision
status: current
summary: The browser is a second channel over the same lanes, exposed through an outbound Cloudflare Tunnel; PRD P8 reworded, "Telegram is the UI" dropped.
verification:
  base_commit: 01094046215a669ab2ade7be691b49c547418d16
  evidence_fingerprint: sha256:b145b17a8630c7e0e1ebd7af34956e1ad858c3151955f8cb0b4fda3947a3bd55
evidence:
  - path: docs/superpowers/specs/2026-09-08-cladex-web-ui-design.md
  - path: docs/PRD.md
  - path: docker-compose.yml
  - path: src/claude_hermes/modules/turns/application/bus.py
  - path: src/claude_hermes/http/app.py
  - path: src/claude_hermes/core/auth.py
  - path: src/claude_hermes/modules/identity/presentation/router.py
relations:
  - component.web
  - component.core-runtime
  - flow.message-turn
---
# Decision: the web is a channel, not a second application

## Context

Telegram was the only surface, and the PRD said so twice: goal G4 and principle
P8 ("no public inbound surface"), with "Telegram is the UI" listed as a
non-goal. But a chat client cannot show a turn's steps, its tool calls or the
files it produced, and a phone keyboard is a poor place to read a report. The
obvious alternatives were a separate frontend service talking to a new API
process, or a hosted assistant runtime.

## Decision

- **One process, two channels.** One HTTP app (Starlette then, FastAPI since
  the PostgreSQL wave) runs on uvicorn as a task in the same asyncio loop as
  aiogram. There is no second deployable, no second
  database connection, no second copy of the runner.
- **The lane is the unit both channels share.** `session_key` is the thread id
  on the wire; a browser lane is `web:<user_id>:<uuid4 hex>` and
  `chat_type_from_key` answers `"private"` for it, so every private-only rule
  (the `ask_user` gate above all) applies unchanged. There is no mapping table.
- **A typed event stream, not a progress callback.** The runner emits
  `TurnEvent`s into a `TurnBus`; the browser and the Telegram presenter are both
  just listeners ([[component.web]]). The runner therefore knows nothing about
  HTTP, and adding a third channel adds a listener, not a branch.
- **Exposure is an outbound tunnel.** A `cloudflared` sidecar dials Cloudflare
  and the hostname routes to `http://agent:8080` on the compose network. No
  `ports:` anywhere. **PRD P8 is reworded** to: no inbound ports on the host,
  all connectivity outbound; the single public surface is an HTTPS URL behind
  Cloudflare Tunnel, behind Telegram-identity authorization. The non-goal
  "Telegram is the UI" is removed.
- **Identity is Telegram's.** The Login Widget in a browser and `initData` as a
  Mini App are the only ways in — no password store, no second user table, the
  same `AccessControl` and the same first-user-claims-ownership rule.
- **Off by default.** `web_enabled: false` means the web package is never
  imported; the only trace of the feature in a Telegram-only deployment is three
  unused tables.

## Consequences

- Cloudflare cuts a request at 100 seconds, so `POST /messages` answers `202`
  immediately and the turn is read from the SSE stream. Every long-running
  interaction has to be event-shaped; nothing may block on a turn completing.
- `turn_events` is now durable per-turn history alongside the `messages`
  corpus. Text
  deltas are deliberately excluded so the table grows per block, not per token.
- The bot token gains a second consumer shape: `sha256(bot_token)` for the
  widget and `HMAC-SHA256("WebAppData", bot_token)` for the Mini App must be
  derived in `main.py` before the token is popped from the environment. The web
  layer never sees the token itself.
- P8 has one sanctioned exception: `docker-compose.caddy.yml`, for a host with
  no Cloudflare account, puts Caddy on 80/443 with its own Let's Encrypt
  certificate in front of `agent:8080`. The agent still publishes nothing; the
  inbound surface is TLS plus the same Telegram-identity login.
- The image needs Node at build time (the `webbuild` stage) even though the
  runtime image ships only `dist`.
- Ownership is **not** claimable from a browser: `POST /api/auth/telegram`
  requires an `allow` verdict, and the ACL only claims an unowned deploy for a
  `private` chat type. A deploy whose owner is unset rejects every web login,
  so finding the hostname first buys a stranger nothing.

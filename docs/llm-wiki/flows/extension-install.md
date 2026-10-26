---
id: flow.extension-install
kind: flow
status: current
summary: From chat request to installed extension — catalog, secrets, probe; and the draft→approve path for new entries.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:cbe88274848c2d3f8449e26982f375ef20c36fccd1653fbfebc39c8c9e03863e
evidence:
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/modules/extensions/application/services/admin_service.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_drafts.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_secrets.py
  - path: src/claude_hermes/modules/extensions/presentation/cli.py
  - path: src/claude_hermes/modules/extensions/application/services/approval_service.py
  - path: src/claude_hermes/modules/telegram/application/services/commands.py
  - path: scripts/approve_draft.py
relations:
  - component.extensions
  - decision.readonly-catalog-boundary
---
# Flow: installing an extension

## Installing a catalog entry

1. User asks in chat; the model calls `ext_manage(action="add", name=...)`
   (modules/turns/infrastructure/hermes_server.py). Owner-only, fails closed without ACL/ctx.
2. The name must exist in `catalog.load_all()` — a raw command or URL from
   chat is refused.
3. Missing declared secrets → the tool returns the `/secret NAME=` lines and
   installs nothing. The user sends `/secret NAME=value`; the gateway stores
   it Fernet-sealed in the `extension_secrets` table and deletes the message.
4. MCP: `compose.server_spec` + `probe` — registered in the `extensions`
   table only if the probe reports `connected`. Plugin: `clone_plugin` at the
   pinned ref, then registered.
5. The mutation flags `reset_lane`: the current thread's context restarts on
   the next message because tool schemas are part of the cached prefix.

## Growing the catalog (draft → approve)

1. User sends a URL; the agent fetches it, works out a manifest and calls
   `ext_manage(action="draft")` → `drafts.save` runs the same
   `catalog.parse` and queues the YAML **verbatim** in the `catalog_drafts`
   table (`data/catalog-drafts/` until Wave 3a — a directory the agent could
   rewrite behind an approver's back). The draft is
   inert: `load_all` never reads it.
2. **In-chat approval** (owner-only, gateway-intercepted): `/approve <name>`
   → `approvals.begin` snapshots the bytes and shows the ANSI-stripped
   executable surface + an 8-hex token bound to those exact bytes.
   `/approve <name> <token>` → `approvals.confirm` →
   `writer.promote(m, raw)` into `user-catalog/` → re-verified via
   `catalog.get` before claiming success. Repo names cannot be shadowed.
   A verified confirm then resets the session lane the command was typed in
   (`runner.reset`, as `/new`): the model never saw the gateway exchange, and
   the drafting thread would otherwise keep believing the draft is queued and
   not installable. Failed or unverified confirms do not reset. Limit: draft
   and approve are assumed to share a thread, the normal usage.
3. **Host approval** (the original hard-boundary path):
   `python scripts/approve_draft.py <name>` prints the surface, asks y/N,
   copies into `catalog/`, and unlinks the copy if it does not load back.
4. The catalog needs no restart either way — it is re-read every turn from
   the live bind mount. Install then proceeds as above: the approved name now
   exists in the catalog, and the agent runs `ext_manage(action="add")` when
   asked.

## From the browser

1. Owner opens `/customize/connectors` ([[component.web]]). The list is the merged
   catalog with each entry's registry row folded in, so a row says `Not connected`,
   `Connected · N tools`, `Disabled`, `Needs reconnect` or `Error: …`.
2. **Connect** on a `secret` entry opens a dialog with one field per declared
   secret → `POST /api/connectors/{name}/secrets` (only declared names; values are
   stored in `extension_secrets`, never echoed back) → `POST /connect`, which is the same
   `manage.connect` chat runs: spec → probe → register on `connected`.
3. **Connect** on an `oauth` entry goes to `POST /{name}/oauth/start`: discovery,
   dynamic client registration if the provider supports it (otherwise the owner
   pastes a client id and secret under Advanced), PKCE, and the browser leaves for
   the provider's consent page. The provider returns it to
   `GET /api/connectors/oauth/callback` — a top-level navigation carrying the
   SameSite=Lax cookie and the single-use state — which exchanges the code, stores
   the token row, mirrors the access token into `extension_secrets` and probes. Success
   redirects to `/customize/connectors?connected=<name>`; any failure redirects
   with `?error=<code>` and installs nothing. A failed probe keeps the sign-in so
   Connect can retry without a second consent screen; Disconnect forgets it.
4. **Add custom connector** is the browser's answer to the draft→approve path for
   a remote server: the owner's own request (owner cookie + matching `Origin`)
   writes a user-catalog manifest through the same `CatalogWriter.promote` and the
   same `catalog.parse`, with `scopes: [turn, cron]` and a `<NAME>_TOKEN` variable.
   The catalog stays the only source of what runs — a chat message still cannot
   author one, and a repo name cannot be shadowed (409 `name_taken`).
5. **Add plugin** is the browser's way to file a `kind: plugin` draft without a chat:
   `POST /api/drafts` (owner-only) turns the form's fields into a manifest through the
   same `drafts.save`, so the ref must still be a tag or a full sha and the repo must
   still be `https://`. It queues; it does not promote. The dialog fills those fields
   from a pasted `…/tree/<sha>/<dir>` link (`lib/pluginSource.ts`) because a
   40-character sha typed by hand is how a pin ends up wrong — and leaves every field
   visible, because the link is a convenience and the manifest is the claim.
6. **Browser approval** is the third approval channel, sharing the implementation
   with the other two: `POST /api/drafts/{name}/review` → `approvals.begin`
   returns the same surface and the same 8-hex token; `POST /{name}/approve` sends
   it back and `approvals.confirm` promotes the **snapshot**. A draft rewritten in
   between is `409 stale_draft`, `is_repo_entry` is `409 shadowed`, and the
   post-write `catalog.get` read-back answers `500 unverified` with the draft left
   queued so a retry is free. Owner-only routes, never tools, so the agent cannot
   approve its own draft. `DELETE /api/drafts/{name}` — discard — is the one thing
   this channel has that the other two do not.
7. **Every browser mutation ends in `runner.reset_all()`** — connect, enable,
   disable, remove, the OAuth callback, draft approve, and the plugin and skill
   mutations. The browser has no current thread to reset, so it resets them all:
   the new server list lives in each lane's cached prompt prefix, and a lane
   resuming an old SDK session never re-reads it. (This page used to say no reset
   was needed "because every lane is handed the new list on its next turn anyway";
   that was wrong — a resumed session is not a new prefix — and it was the bug the
   Customize cut was chartered to close.) The reset comes after the row write, the
   `ext_manage` ordering rule; a refused mutation resets nothing.

---
id: decision.readonly-catalog-boundary
kind: decision
status: current
summary: The read-only catalog mount is the security boundary; the agent proposes, the human approves.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:408054225c9da48cf300a044d9c7584c10c6f0744a0b3fdd193acc492b5363ca
evidence:
  - path: README.md
  - path: docs/superpowers/specs/2026-07-15-catalog-from-chat-design.md
  - path: docker-compose.yml
relations:
  - component.extensions
  - flow.extension-install
  - decision.multi-user-isolation
---
# Decision: Read-only catalog mount as the security boundary

## Context

An stdio MCP server manifest is an executable command (`npx` + `args`) or code to
clone (`repo`@`ref`). The agent runs as the owner of `/data`, has Bash, and there
is no uid separation inside the container — any manifest directory the agent
could write to is a manifest directory it could forge (README "Adding a catalog
entry"; catalog-from-chat design spec "Background").

## Decision

- Only catalog entries are installable. The registry (the `extensions` table)
  records *that* an extension is installed; *what it runs* comes only from the
  reviewed manifest in `catalog/`, mounted into the container read-only.
- The work is split: the agent may **propose** (`ext_manage(action="draft")` →
  inert row in the `catalog_drafts` table that `catalog.load_all()` never reads);
  only the human may **approve** (`scripts/approve_draft.py` on the host, which
  shows the executable surface and asks y/N).
- The validator narrows what reaches the human: no shell strings, command
  allowlist (`npx`/`uvx`/`python`), https-only URLs, declared `${VAR}`s, pinned
  plugin refs. Judging package trustworthiness stays human.
- The chat-approval extension (catalog-from-chat spec) deliberately accepts a
  *soft* boundary for the user-catalog on `/data`: `/approve` is
  gateway-intercepted so the model can never enact it, and the write goes through
  a `CatalogWriter` port so a hard sidecar boundary (S1) can be restored without
  rewriting call sites. The repo catalog always wins on name collision.
- **The browser is a third approval channel, not a fourth authoring one.**
  `POST /api/drafts/{name}/{review,approve}` ([[component.web]]) is the same
  `approvals.begin`/`confirm` pair behind an owner cookie, so what it adds is a
  better *reading* of the surface — and it stays a route, never a tool, so a
  prompt-injected turn cannot reach it. There is deliberately **no**
  `POST /api/plugins`: no single request may register a plugin, because a plugin is
  code the SDK loads and runs.

  Amended 2026-09-12: a form may **propose** a plugin, on exactly the terms the
  agent may. `POST /api/drafts` (owner-only, `kind: plugin` only) writes a manifest
  into `catalog_drafts` through the same `drafts.save` and the same
  `catalog.parse`, and that directory is one `catalog.load_all` never reads — so
  what the form produces is inert until the owner reviews the surface and approves
  the snapshot, which is a second request with a content-bound token. The original
  wording ("no form anywhere may author its manifest") described the authoring step
  as the boundary; it is not, and never was — the agent has always been able to
  write that file with Bash, and the queue is a queue precisely because writing to
  it grants nothing. The boundary is promotion, and promotion still happens in one
  place. What the form buys is that adding a plugin no longer requires a chat turn
  or a shell on the host; what it must never gain is a path that promotes without
  the reading step.

  The custom-*connector* form is the older exception and a different one: it writes
  the catalog entry directly, and only because it cannot produce a `stdio` entry —
  `catalog.parse` demands a `command` and the form has no field for one.

## Consequences

- No chat message can make the agent execute an arbitrary command.
- **A plugin is the one catalog entry that can bring servers the validator never
  saw.** The CLI loads a plugin's own `.mcp.json` (the SDK's `strict_mcp_config`
  docstring names "plugin-provided servers"; `options.py` leaves the flag at its
  default), so those servers get no manifest, no registry row and no probe. Two
  rules answer it, both in the install path rather than in the validator, because
  the files only exist once the pinned ref is fetched: a bundled server that would
  **run a command** is refused outright (`local_server`), and the remote ones are
  listed on the confirmation screen the owner reads before any row is written
  ([[component.web]], `web/plugins.py`). The catalog still decides *which
  repository at which revision*; what it cannot do is read a file that does not
  exist until the fetch.
- Growing the repo catalog requires a PR or the host-side approve script.
- Env-scrubbing of tokens is defense-in-depth, not a boundary: a same-uid
  process can still read `/proc/1/environ` (catalog-from-chat spec).
- **Wave 4a's per-user workspaces do not add a second boundary.** A `users`
  row, an `owner_id` per row and a `PreToolUse` path guard per lane confine an
  accident and a well-behaved model, but every turn is still the same uid, so
  the guard is TOCTOU-racy and `Bash` is not guarded by it at all. This mount
  stays the only hard boundary; a container per user is the follow-up
  ([[decision.multi-user-isolation]]).

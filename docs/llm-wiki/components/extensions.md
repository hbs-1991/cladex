---
id: component.extensions
kind: component
status: current
summary: Catalog of MCP servers and plugins — manifest validation, a per-owner registry, install lifecycle, per-owner encrypted secrets and OAuth tokens, and the shared draft/approve queue in PostgreSQL.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:bff921bc57a41ee96cfaa9494bcab35bf9f60b156293aafc1ec42ae2bd86f805
evidence:
  - path: src/claude_hermes/modules/extensions/domain/manifest.py
  - path: src/claude_hermes/modules/extensions/domain/paths.py
  - path: src/claude_hermes/core/workspace.py
  - path: src/claude_hermes/modules/extensions/domain/drafts.py
  - path: src/claude_hermes/modules/extensions/domain/approvals.py
  - path: src/claude_hermes/modules/extensions/domain/secrets.py
  - path: src/claude_hermes/modules/extensions/domain/compose.py
  - path: src/claude_hermes/modules/extensions/domain/plugin_surface.py
  - path: src/claude_hermes/modules/extensions/domain/oauth.py
  - path: src/claude_hermes/modules/extensions/domain/models.py
  - path: src/claude_hermes/modules/extensions/application/ports.py
  - path: src/claude_hermes/modules/extensions/application/services/admin_service.py
  - path: src/claude_hermes/modules/extensions/application/services/approval_service.py
  - path: src/claude_hermes/modules/extensions/application/services/compose_service.py
  - path: src/claude_hermes/modules/extensions/application/services/extension_service.py
  - path: src/claude_hermes/modules/extensions/application/services/oauth_service.py
  - path: src/claude_hermes/modules/extensions/application/services/plugin_service.py
  - path: src/claude_hermes/modules/extensions/infrastructure/catalog_repository.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_drafts.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_snapshots.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/pg_secrets.py
  - path: src/claude_hermes/modules/extensions/presentation/routers/secrets.py
  - path: src/claude_hermes/modules/extensions/infrastructure/redis_pending_states.py
  - path: src/claude_hermes/modules/extensions/infrastructure/refresh_locks.py
  - path: src/claude_hermes/modules/extensions/infrastructure/secret_key.py
  - path: src/claude_hermes/modules/extensions/infrastructure/file_catalog_writer.py
  - path: src/claude_hermes/modules/extensions/infrastructure/git_installer.py
  - path: src/claude_hermes/core/crypto.py
  - path: src/claude_hermes/modules/extensions/presentation/cli.py
  - path: scripts/import_data_dir.py
  - path: src/claude_hermes/modules/extensions/infrastructure/mcp_prober.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/extension_repository.py
  - path: src/claude_hermes/modules/extensions/infrastructure/repositories/oauth_token_repository.py
  - path: src/claude_hermes/modules/extensions/presentation/agent_tool.py
  - path: src/claude_hermes/modules/extensions/presentation/routers/connectors.py
  - path: src/claude_hermes/modules/extensions/presentation/routers/plugins.py
  - path: src/claude_hermes/modules/extensions/application/services/queries.py
  - path: src/claude_hermes/core/db/migrations/versions/0006_per_user_extensions.py
  - path: tests/modules/extensions/test_extensions_per_user.py
  - path: tests/modules/turns/test_turns_two_users.py
  - path: src/claude_hermes/core/app_context.py
  - path: src/claude_hermes/modules/turns/infrastructure/hermes_server.py
  - path: src/claude_hermes/modules/extensions/wiring.py
  - path: src/claude_hermes/modules/extensions/README.md
  - path: catalog/plugins/marketing.yaml
  - path: scripts/approve_draft.py
relations:
  - contract.catalog-manifest
  - flow.extension-install
  - contract.state-db
  - decision.readonly-catalog-boundary
  - decision.modules-and-ports
  - decision.redis-taskiq-scaling
  - decision.multi-user-isolation
---
# Extensions

Lets the agent gain MCP servers and Claude Code plugins from a curated catalog,
driven from chat, without a novel executable ever being authorable by a chat
message.

## Modules

Wave 2 moved this component into `src/claude_hermes/modules/extensions/`; paths
below are relative to that module root; the `extensions/*.py` shims Wave 2 left
behind went in Step 5 ([[decision.modules-and-ports]]). `wiring.py` is the module's own
composition helper — presentation and infrastructure are siblings under the layer
contract, so something has to introduce them.

- `domain/manifest.py` + `infrastructure/catalog_repository.py` — the single source of truth for the manifest format and
  validator (`parse`, modules/extensions/domain/manifest.py) and the loader (`load_all`,
  modules/extensions/infrastructure/catalog_repository.py; `_merge`, modules/extensions/infrastructure/catalog_repository.py — **repo catalog wins on name
  collision**). Reviewed catalog dir from `CH_CATALOG` (code); user-catalog
  under `CH_HOME` (state). See [[contract.catalog-manifest]].
- `infrastructure/repositories/extension_repository.py` — the registry over the `extensions` table: records *that*
  something is installed, **for one owner** (`(owner_id, name), kind,
  catalog_version, state, installed_at, installed_by, last_error`), never *what it
  runs*.
- `infrastructure/git_installer.py` — `clone_plugin` (modules/extensions/infrastructure/git_installer.py): `git init` → `remote add` →
  `fetch --depth 1` by pinned ref (a sha verbatim; a tag as
  `refs/tags/<ref>` so a same-named branch cannot answer) → `checkout
  FETCH_HEAD`; verifies `.claude-plugin/plugin.json`; removes the clone on any
  failure; one shared deadline across git calls.
- `infrastructure/repositories/pg_secrets.py` + `domain/secrets.py` — the
  `extension_secrets` table, **Fernet-sealed**. `PgSecrets` takes a
  `core.crypto.SecretBox`, never a key string; `load(owner_id=)` opens every
  row of one owner, `put(name, value, owner_id=)` seals and upserts,
  `forget(name, owner_id=)` deletes — two people's `NOTION_TOKEN` are two rows
  under one box. `names()` is the
  half that lists without decrypting — name -> `updated_at` — so the browser's
  secrets list ([[component.web]]) can be served with no plaintext in scope, and
  still answers under a rotated key, where `load()` would skip the row.
  `interpolate` (modules/extensions/domain/secrets.py) leaves unknown `${VAR}` literal so
  mis-config fails visibly. The composition root builds ONE box while
  `CH_SECRETS_KEY` is still in the environment and hands it to `wiring.build`;
  since Step 5 there is no second way to a box — `secret_key.bind()`, the
  module-level `oauth_client.ensure_fresh` and the `LegacyOAuthRefresher` that
  needed them went with the shims, and `wiring.build` binds the one
  `OAuthService(wire=oauth_client.http_wire, secrets=, locks=)`, so a token
  refreshed after the startup pop is sealed with the same key the box holds.
- `infrastructure/repositories/pg_drafts.py` + `domain/drafts.py` — the
  `catalog_drafts` table, the queue the agent proposes into. `save(raw)` runs the
  **same** `catalog.parse` the reviewed catalog does and stores `raw` **exactly as
  submitted**, because the approval token is `sha256(raw)[:8]`; `safe`
  (modules/extensions/domain/drafts.py) strips control chars so a hostile README
  cannot forge the review screen; `describe` renders the executable surface.
  `rejected()` re-validates on read and writes the verdict back
  (`status='rejected'` with the validator's own words), so a draft the validator
  has since tightened against is visible rather than silently gone. `load_all`
  never reads drafts — a queue, not a boundary.
- `application/services/approval_service.py` + `infrastructure/repositories/pg_snapshots.py` — two-step confirm over the
  `approval_snapshots` table: `begin` snapshots the draft **bytes** and issues a
  token = first 8 hex of sha256 of those exact bytes
  (`ApprovalService.begin`, modules/extensions/application/services/approval_service.py), so a post-review edit cannot slip through; `confirm`
  promotes the snapshot, not the live draft. Storing the parsed manifest instead
  would make the token check compare a manifest to itself.

## Wave 3a: three files became three tables

`secrets.env`, `catalog-drafts/` and `pending-approvals/` lived on `/data`, which
the agent owns and reaches with Bash. `secrets.env` was therefore readable by the
agent's own shell, and `catalog-drafts/` was a directory it could rewrite behind an
approver's back. Migration `0003` replaced all three with tables the agent reaches
only through the application ([[contract.state-db]]):

| Was | Is | Port (all **async**, and none takes `home` any more) |
|---|---|---|
| `data/secrets.env` (0600) | `extension_secrets` (Fernet) | `SecretStore.load / names / put / forget` |
| `data/catalog-drafts/` | `catalog_drafts` | `DraftStore.save / pending / rejected / read / delete` |
| `data/pending-approvals/` | `approval_snapshots` | `SnapshotStore.write / read / clear` |

Two more pieces of process state followed, because a second process now exists
([[decision.redis-taskiq-scaling]]): `PendingStates` (the OAuth flow state) has a
`RedisPendingStates` implementation with a 10-minute TTL, and the per-connector
refresh lock has `RefreshLocks` over `core.redis.Lock` on
`lock:oauth:<owner>:<name>` (`lock:oauth:<name>` until per-user connectors). The
in-memory pair stays for inline mode; the ports are what the services see either
way.

**The operator path moved with them.** `python -m claude_hermes.main drafts
list|show <name>|delete <name>` (over `presentation/cli.py`) is how the queue is
read now, and `scripts/approve_draft.py` — still the only writer of `catalog/`, and
still running on the **host**, outside the container — calls those through
`docker compose exec -T agent` (`CH_DRAFTS_CMD` overrides the wrapper). That the
approver reads through the app and writes from outside it is the security model,
not a deployment detail ([[decision.readonly-catalog-boundary]]).
`scripts/import_data_dir.py` is the one-shot that carries an existing `data/` into
the tables, encrypting the secrets on the way in; it deletes nothing
([[system.operations]]).
- `application/services/compose_service.py` + `domain/compose.py` — manifests → SDK options. `for_scope` (modules/extensions/application/services/compose_service.py) loads
  catalog + secrets, drops (never raises) a broken extension into
  `last_error`, and persists only verdicts the current scope evaluated so a
  cron pass cannot clear a turn-only server's error. `server_spec`
  (modules/extensions/domain/compose.py) refuses to spawn with missing declared secrets.
  `plugin_root` re-checks subdir containment against the resolved clone.
- `infrastructure/plugin_reader.py` + `domain/plugin_surface.py` — `components(root)`: what an installed plugin adds, read from the
  clone on every listing rather than stored — a listing is a page load, the files
  are local, and a value read from disk cannot go stale (the opposite trade-off
  from `tool_count`, which is stored because reading it costs a network probe).
  Returns `{commands, skills, agents, hooks, mcp, author, plugin_version}`, `None`
  when there is no `.claude-plugin/plugin.json`. `_stems` is deliberately
  non-recursive: Claude Code namespaces `commands/nested/deep.md`, and listing it
  as a bare `deep` would name a command the plugin does not have. Nothing here
  raises — a junk `plugin.json` yields a blank author, because a listing that dies
  on one bad clone is worse than one that shows a blank field. `is_present(home, m)`
  is the same check as a boolean, and is what lets a row say the clone vanished
  before the next turn records `last_error`.
  The same module reads the servers a plugin brings. `bundled_servers(root)` parses
  `mcpServers` out of the plugin's `.mcp.json` (and out of its `plugin.json`, which
  costs one look and closes the question of whether the CLI reads it there too);
  `_classify` answers one question — "will installing this start a process here?" —
  and a process needs an argv, so `local` is exactly: a `command` or `args` key
  (with no `type` at all, which is how every stdio server in the wild is written),
  or an entry that is not a mapping, where we cannot read what it says. Everything
  else is remote, **including a server that declares a transport and has nothing to
  dial**: `{"type": "http", "url": ""}` is how the knowledge-work marketplace ships
  `gmail` and `google calendar`, leaving the endpoint to the client, and a `${VAR}`
  placeholder reads the same. The earlier rule was "a remote server is a `url` and
  nothing else, everything else fails closed to local", and it refused all three
  shipped plugins on `gmail would run: (no command)` — a refusal naming a command
  that did not exist, over an entry that spawns nothing. Failing closed is right
  where the answer is unknown and wrong where it is known; `(no url — not
  configured)` is what the surface shows for one of these. `local_servers(root)` is
  what install refuses on. `surface(m, root)` renders the whole lot — repo, ref, subdir,
  commands, skills, agents, a loud line for hooks, and every server with its
  transport and target — through `drafts.safe`, because all of it was written by
  whoever owns the repository and it is about to be printed on the one screen the
  owner is supposed to trust. `surface_token` is the full sha256 of that text,
  unlike `approvals.token_of`'s eight hex: nobody retypes this one, the browser
  carries it between two requests.
- `infrastructure/mcp_prober.py` — `probe` (modules/extensions/infrastructure/mcp_prober.py) connects one MCP server with
  `ClaudeSDKClient.get_mcp_status()` — no model call, no cost; never raises;
  timeout → `failed`.
- `infrastructure/file_catalog_writer.py` — the `CatalogWriter` port (`promote(m, raw)`) — the
  `/approve` handler depends on this port, not a path; `FileCatalogWriter`
  writes approved bytes atomically into `user-catalog/`. Port is the seam for
  a future isolated approver sidecar (hard boundary S1).

## Wave 4a: the clones move

`agent_home` became one tree per user in 4a, so the plugin clones moved out of it
(below). What 4a decided about ownership — that `extensions`, `extension_secrets`
and `oauth_tokens` carry no `owner_id` — **no longer holds**: see "Per-user
connectors", after this section.

`catalog_drafts` is the one that gained a column, and only as an audit field:
`owner_id` records **who proposed** a draft. The queue itself, the approval and
the write into `user-catalog/` stay deploy-wide and owner-only, exactly as
[[decision.readonly-catalog-boundary]] describes.

**Plugin clones moved from `agent_home/plugins/<name>` to `CH_HOME/plugins/<name>`**
(`domain/paths.PLUGINS_SUBDIR` now resolves against `home`, not `home/agent_home`).
`agent_home` became one tree per user in 4a, so a clone left inside the owner's
`agent_home` could only be loaded by a non-owner's turn if that turn's read guard
admitted the owner's whole tree — which is the boundary the wave exists to draw.
The clones are deploy-wide because a plugin is.

`Workspace.ensure()` performs the one-time `os.replace(home/"agent_home"/"plugins",
home/"plugins")` when the old directory exists and the new one does not, logged at
WARNING with both paths and the entry count, because it moves a deployment's
plugin clones out from under any path a human has written down. Every process
(agent, worker, scheduler) runs `ensure()` for the owner at boot, so three of these
can race: `os.replace` is atomic, the loser gets an `OSError`, and the only thing
that matters afterwards is whether the destination is there — which is what is
re-checked. `plugin_root`'s containment check is unchanged.

## Per-user connectors

Migration `0006` re-keys `extensions`, `extension_secrets` and `oauth_tokens` from
`name` to `(owner_id, name)`, `owner_id BIGINT NOT NULL REFERENCES users(id) ON
DELETE CASCADE` ([[contract.state-db]], [[decision.multi-user-isolation]]). What
is shared and what is personal:

- **shared** - the catalog (`catalog/` `:ro` and `user_catalog/`), the
  `catalog_drafts` queue, the `approval_snapshots`, and every plugin **clone**
  under `CH_HOME/plugins/<name>`;
- **personal** - which of them you have enabled, your API keys, your OAuth
  account, and the `state` / `last_error` / `tool_count` / `tool_names` verdict
  your own probe produced.

**Every port takes `*, owner_id: int` - required, keyword-only, no default.**
`owner_id: int = 0` would turn a forgotten call site into a silent read of the
deploy owner's credentials; with no default it is a `TypeError` at the seam:

| port | signature |
|---|---|
| `ExtensionRepository` | `add(name, kind, catalog_version, installed_by, now, …, *, owner_id)`, `get/list/enabled/set_state/set_error/set_tools/remove(…, *, owner_id)`, plus `owners_of(name) -> list[int]` — the one deliberately unscoped read, below |
| `SecretStore` | `load/names(*, owner_id)`, `put(name, value, *, owner_id)`, `forget(name, *, owner_id)` |
| `TokenRepository` | `get/delete(name, *, owner_id)`, `put(name, *, owner_id, now, **fields)` |
| `OAuthRefresher` | `ensure_fresh(home, tokens, m, *, owner_id)` |
| `ExtensionsQuery` | `enabled_plugin_roots(*, owner_id) -> list[tuple[str, Path]]` |
| `PromptCacheInvalidator` | `__call__(owner_id)` — the reset is that owner's lanes, not every lane |

`ComposeService.for_scope(scope, *, owner_id)` is the one call the runner and the
cron pass make, and the owner is fed from the two places that already know it:
`ctx.owner_id` on a turn (the **lane** owner, never the sender) and
`job["owner_id"]` on a routine. `AdminService.handle(..., *, owner_id)` reads the
lane owner's rows; its mutations stay behind `is_owner` and fail closed.
`tests/modules/turns/test_turns_two_users.py` asserts the end of that chain over
the real `ComposeService`: B's turn composes none of A's MCP servers and carries
no `mcp__<name>` for them.

**`adopt` - enabling a plugin you do not have a row for.** A plugin's clone is
shared code and only the owner may fetch or delete it, so "enable it for me"
cannot be a state flip on a row a second user does not have.
`ExtensionService.adopt(m, *, user_id, now, owner_id)` checks the clone is really
on disk (`.claude-plugin/plugin.json`) and writes that owner's row; `False` - the
route's 404 - rather than a row pointing at a directory that is not there.
`remove(name, …, *, owner_id, remove_clone=False)` is the mirror: a second user
dropping their row leaves the tree where it is, and only the owner's delete passes
`remove_clone=True`.

**The cascade, and the one unscoped read.** `owners_of(name)` answers *which
owners hold a row for this name*, and exists for the two clone-level events:

- `DELETE /api/plugins/{name}` (owner-only) removes **every** owner's row, then
  the tree once, then invalidates each of those owners' prefixes - a row left
  pointing at a deleted clone is the half-state the registry may not have.
- A re-fetch for review (`fetch_for_review`) replaces the tree the SDK loads
  while every row still describes the old one, so it returns the affected owners
  and the route resets each of their lanes there and then - confirm may never
  come.

It is a list of ids and never a credential; nothing else in the module reads
across owners.

**The OAuth refresh lock is `lock:oauth:<owner>:<name>`** - the caller builds the
name, the `RefreshLocks` port and both adapters are unchanged. Two owners
refreshing the same connector are two independent sign-ins and must not queue
behind each other; one owner's `turn` and `cron` composing at once still must,
because a provider that rotates refresh tokens refuses the loser. The key change
is why a rolling upgrade has to drain the queue first ([[system.operations]]).

## ext_manage tool (modules/turns/infrastructure/hermes_server.py)

**The tool is declared here, in `presentation/agent_tool.py`, and nowhere else.**
`TOOL_NAME`, `TOOL_DESCRIPTION` and `TOOL_SCHEMA` are the single definition; the
composition root puts them on `core.app_context.ExtensionAdminTool` (the port
`turns` receives) and `hermes_server.declared_spec(port)` reads all three back off
it — all three or nothing, since a half-declared tool would register with a name
from one place and a description from another, and the description is **cached
prompt text** ([[decision.prompt-cache-discipline]]). `build_hermes_server` keeps
no copy to fall back to: an `ext_manage` handler that reaches it without a
declaration raises `ValueError` at boot rather than registering under a guessed
description. `tests/test_ext_manage_guidance.py` and
`tests/modules/turns/test_turns_tool_gates.py` pin the text.

Registered only when `ext_store`, `home` and `acl` are all wired — fails
closed. Read actions (`catalog`, `list`, `drafts`) for any allowed user, and
since per-user connectors they answer about **the lane owner's** rows
(`handle(..., *, owner_id)`); mutations stay behind `acl.is_owner`:

- `draft` — validate + queue; not installed, not installable.
- `add`/`test` — name must exist in the catalog; missing secrets → returns
  the `/secret NAME=` lines and installs nothing; MCP registers only when the
  probe reports `connected`; plugin `add` clones at the pinned ref. An `oauth`
  entry cannot be connected from chat at all: the reply sends the owner to
  Customize → Connectors, because the sign-in happens in a browser.
- `enable`/`disable`/`remove` — registry state changes.

Every successful mutation calls `_flag_reset(owner_id)` (modules/extensions/application/services/admin_service.py) →
`ctx.flags.reset_lane` → the runner resets the session lane after the row
write, because tool schemas live in the cached prompt prefix
([[decision.prompt-cache-discipline]]). The web channel's invalidator
(`main._invalidate`) is the same signal at deployment scale and is scoped the
same way: `reset_owned(owner_id, include_unowned=owner_id == deploy_owner)`, so a
connector one person enables evicts **their** warm lanes and nobody else's — the
NULL-owner belt (group lanes, and lanes bound while the deploy was unclaimed)
being the deploy owner's. A **confirmed `/approve` also resets**
the lane it was typed in (via `runner.reset`, same as `/new`): the exchange is
gateway-intercepted, so the model never sees it, and the drafting thread would
otherwise keep believing the draft is queued and uninstallable — the 16.07
plugin-dev refusal. Failed/stale/unverified approve paths do not reset. The
catalog itself needs no restart — it is re-read every turn from disk; the
post-approval install is the agent's own `add`.

## Approving from the browser

There are now two approval channels over one implementation. Chat's is
`/approve <name> <token>`, gateway-intercepted. The browser's is
[[component.web]]'s `drafts.py`: `POST /api/drafts/{name}/review` →
`approvals.begin`, `POST /api/drafts/{name}/approve` with the token →
`approvals.confirm` → `writer.promote`. Both are owner-only and both promote the
**snapshot**, so the guarantee is identical: the 8-hex token is bound to the exact
bytes the human read, and a draft rewritten in between is refused.

What differs is only what each channel can show and do:

| | chat | browser |
|---|---|---|
| the surface | a Telegram message | a `<pre>` in `DraftDialog`, verbatim |
| local-command banner | prepended text | the same text, plus a `local_command` flag the dialog draws a strip from |
| discard | — | `DELETE /api/drafts/{name}` |
| rejected drafts | invisible | listed with their reason |
| after approve | `runner.reset` on the lane it was typed in | `runner.reset_all()` — the browser has no lane |

The browser route is a route, not a tool: `ext_manage` gained nothing, so a
prompt-injected turn still cannot enact its own draft
([[decision.readonly-catalog-boundary]]). And it stays an *approval* screen — the
browser never authors a plugin manifest, because a plugin is code the SDK loads
and runs, so there is no `POST /api/plugins`. A custom **connector** form is safe
by contrast only because it cannot produce a `stdio` entry: `catalog.parse` would
demand a `command` and the form has no field for one.

## Connectors and OAuth

A *connector* is nothing new: a catalog MCP entry + a registry row + credentials.
What the browser adds is a second way to hand the credentials over, and OAuth —
the one thing chat cannot do, since a sign-in happens in a browser.

- **`auth[].kind`** (`secret` by default, or `oauth`) is checked by the one
  `catalog.parse` every channel shares. An `oauth` entry forbids `prompt`,
  requires `header: Authorization` and `format: "Bearer ${NAME}"`, and is refused
  on a `stdio` transport — a browser sign-in means nothing for a process spawned
  locally. `Manifest.oauth_entries()` / `plain_secret_names()` split the two kinds;
  `secret_names()` still returns both, because an access token is mirrored into
  `extension_secrets` like any other secret.
- **`tokens.py`** — `TokenStore` over `oauth_tokens` (`(owner_id, name)` PK since
  `0006`, issuer, client
  id/secret, access and refresh token, `expires_at`, scope), reached as
  `store.tokens`, so `for_scope`, the tool and the router get at tokens through the
  object they already hold. `put` is a field-wise upsert: `oauth/start` writes the
  issuer and client id, the callback adds the tokens. The **access token is
  mirrored into `extension_secrets`** under the manifest's variable name — which is what
  lets `compose.server_spec` build the Authorization header with no OAuth-shaped
  branch anywhere in composition. The refresh token never leaves the table, and
  `extensions.tool_count` (new, nullable) is what the row's "N tools" reads.
- **`oauth.py`** — discovery (RFC 9728 protected-resource document → RFC 8414
  authorization-server metadata), dynamic client registration (RFC 7591), PKCE,
  the code exchange, refresh and revocation. Two guards: every discovered URL must
  be https (`_require_https`, `localhost` excepted) because the resource server is
  the party naming where the client secret and the owner's browser go; and every
  token request carries the same RFC 8707 `resource` — the manifest URL — on the
  authorize, exchange and refresh legs alike.
- **`OAuthService.ensure_fresh`** runs inside `for_scope` before `server_spec`: it refreshes an
  access token expiring within `REFRESH_MARGIN` (60 s), re-mirrors it, and
  `for_scope` re-reads the secrets table so the spec carries the new one. It holds a
  per-owner-per-connector lock (an `asyncio.Lock` inline,
  `lock:oauth:<owner>:<name>` in Redis when there is one) and reads the row inside it — `turn` and `cron`
  compose concurrently and a provider that rotates refresh tokens (Notion does)
  refuses the second use of one, which used to leave the loser recording an error
  for a connector that had just refreshed. When the owner has to sign in again it
  raises `NeedsReauth` and `for_scope` records the literal string **`needs_reauth`**
  in `last_error`: the sentinel `manage.state_of` turns into the `needs_reauth`
  state and the row's Reconnect button.
- **`PendingStates`** keeps started flows for ten minutes, single use, bound to the
  owner who pressed Connect (another user's callback consumes nothing). In process
  memory inline, and in Redis with a 10-minute TTL when `CH_REDIS_URL` is set — the
  callback can land on a different process, or on this one after a redeploy, so
  process memory would have been a flow that silently could not complete. A restart
  mid-flow still means pressing Connect again in inline mode.
- **`manage.py`** is the one implementation of connect / test / enable / disable /
  remove that both `ext_manage` and [[component.web]]'s connectors router call, so
  the two channels cannot drift on what "connected" means. `connect` is secrets →
  spec → probe → register and registers only on a `connected` probe; its
  `Outcome.code` (`secrets_missing`, `oauth_required`, `compose_failed`,
  `probe_failed`) is what each channel turns into its own words. `to_wire` is the
  shape the SPA reads and never carries a value — only whether a secret is set.

## Shipped catalog

`catalog/mcp/filesystem.yaml` (`npx @modelcontextprotocol/server-filesystem /data`)
and `catalog/mcp/fetch.yaml` (`uvx mcp-server-fetch`), both `scopes: [turn, cron]`,
no auth. `catalog/mcp/fatsecret.yaml` — nutrition tracking, `uvx --from git+…@<sha>
fatsecret-mcp`, four `auth` secrets, `scopes: [turn, cron]`; four action-dispatch
tools rather than the upstream twenty, because every tool schema is cached-prefix
cost on every turn ([[decision.prompt-cache-discipline]]).

Thirteen connector entries ship for the browser. Eleven are hosted http servers
with `kind: oauth` auth — `notion`, `github`, `figma`, and the eight lifted out of
the plugins' bundled `.mcp.json` files where they were names the CLI could never
dial: `slack`, `linear`, `asana`, `atlassian`, `intercom`, `hubspot`, `canva`,
`amplitude` (US; EU projects live on `mcp.eu.amplitude.com` and need a custom
connector). A bundled server gets no credential from this system, so the same
vendor reached through the catalog is the difference between a name in a prompt and
tools the agent can call. `github-pat.yaml` is the same GitHub endpoint behind a
personal access token, for a fine-grained token instead of a sign-in; `hostinger`
is stdio (`npx hostinger-api-mcp`) and is why the runtime image runs Node 24. Figma grants `mcp:connect` only to approved
clients — if its sign-in is refused, the owner pastes a client id and secret from
their own Figma OAuth app under Advanced.

Three plugin entries ship in `catalog/plugins/`: `marketing`, `finance` and
`design`, each a subdirectory of
`https://github.com/anthropics/knowledge-work-plugins` pinned to
`11ac5eeaca27c764913a06f1e61a7cb712790b1a`, `scopes: [turn, cron]`. They were
picked for having **nothing that executes** — no `commands/`, no `agents/`, no
`hooks/` — so installing one adds seven or eight `SKILL.md` files to the prompt
index and nothing that runs on a turn. They do each bundle a `.mcp.json`; every
plugin in that repo does, except the plugin-manager, so "skills only, no
`.mcp.json`" (the original design decision) described a set that does not exist
there. The narrowed rule, verified at that ref and recorded in
`docs/superpowers/specs/2026-09-10-cladex-plugins-skills-design.md` §12: every
bundled server is `"type": "http"` — at a vendor URL, or (for `gmail` and
`google calendar`, in all three) with an **empty** `url`, which is the shape that
broke install until `_classify` learned to read it; no `stdio`, so nothing spawns
locally — and **none of them receives a credential from this system**, because
`compose.server_spec` interpolates secrets only for a catalog manifest and a
plugin reaches the SDK as `{"type": "local", "path": …}` whose `.mcp.json` carries
no header and no env. Each manifest's `description` names the bundled servers and
says so, and the plugin page renders "Brings its own tools — a bundled MCP
server." from `plugins.components`.

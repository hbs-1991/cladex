# Extensions: MCP Servers and Plugins from Chat — Design

**Status:** approved, ready for planning
**Date:** 2026-07-14
**Scope:** a curated catalog of MCP servers and Claude Code plugins, a registry of what is
installed, a chat-driven way to install them, and the unified `ClaudeAgentOptions` builder that
makes all of it land in one place. **Webhooks are explicitly out of scope** — they collide with
PRD principle P8 (no public inbound surface) and are deferred to their own spec.

## Goal

The user, from Telegram alone, can see which extensions exist, install one, supply its secret,
and start using its tools in the next message — without touching the host, editing YAML, or
restarting the container.

Today the agent's capabilities are fixed at build time. `runner.py:135` hardcodes
`mcp_servers={"hermes": server}`; there is no notion of a plugin; and the five call sites that
build `ClaudeAgentOptions` each restate the shared invariants by hand.

## Background: we do not need to build what Hermes built

`NousResearch/hermes-agent` hand-rolls a plugin system (`hermes_cli/plugins.py`, 2466 lines) and
an MCP client (`tools/mcp_tool.py`, 5627 lines) because its agent loop is hand-rolled on the
OpenAI SDK. We are on the Claude Agent SDK, which already provides both:

| Need | Claude Agent SDK gives us |
|---|---|
| External MCP servers | `ClaudeAgentOptions.mcp_servers` accepts `McpStdioServerConfig` / `McpHttpServerConfig` / `McpSSEServerConfig` alongside our in-process `McpSdkServerConfig`. |
| Tool namespacing | The CLI already namespaces MCP tools as `mcp__<server>__<tool>` — the same convention Hermes deliberately copied. |
| Plugins | `ClaudeAgentOptions.plugins: list[SdkPluginConfig]` — `{"type": "local", "path": str}`. A plugin is a directory with `.claude-plugin/plugin.json` carrying skills, subagents, slash commands, hooks and its own MCP servers. |
| Reload without restart | We rebuild `ClaudeAgentOptions` on **every turn** (`runner.py:127`), so a registry change is picked up by the next message. Hermes needs `refresh_agent_mcp_tools()`; we need nothing. |

What we take from Hermes is judgement, not code: a **curated catalog** (its `optional-mcps/`), a
**paranoid validator** on anything that becomes an executable command (its
`hermes_cli/mcp_security.py`, which exists because of a real June-2026 supply-chain attack), and
**pinned refs** — manifests never float HEAD.

What we deliberately do not take: platform adapters (we have Telegram), model-provider plugins
(PRD §2.2 declares provider-agnosticism a non-goal), memory providers (we have our own), tool
override, and middleware.

## Locked Decisions

- **Catalog-only installs.** The agent may install only what a manifest in `catalog/` describes.
  Arbitrary `command` / `url` from chat is refused. An stdio MCP server is a command executed in
  the agent's container; a chat message must never be able to author one.
- **The registry stores the fact of installation, not the specification.** The spec lives entirely
  in the catalog manifest, keyed by name. There is therefore no path from chat to a novel
  executable — the only source of truth for *what runs* is a reviewed file in the repository.
- **The catalog is code, not state.** It lives in `catalog/` in the repository and is read
  directly via `_REPO_ROOT` (like `bundle/`), never seeded into `agent_home`. Seeding uses
  copy-if-absent (`paths._copy_missing`), which would freeze the catalog at first-run and starve
  users of new entries.
- **Secrets live in `CH_HOME/secrets.env`, not `.env`.** `.env` is on the host and is read by
  docker-compose (`env_file`) only at container start; the agent process can neither write it nor
  make it reload. `secrets.env` sits on the `/data` volume, mode `0600`, written atomically by us
  and read at server-spawn time. `.env` remains what it is: the bot's own two tokens (PRD P7 is
  preserved — secrets in env-shaped files, behavior in `config.yaml`).
- **A secret never passes through the model.** `/secret NAME=value` is intercepted in the gateway's
  command chain, before `runner.run_turn` is called. The key therefore never enters the LLM
  context, the SDK transcript, or `messages_fts`. The user's message is deleted via
  `deleteMessage`.
- **Installing resets the current thread.** Tool schemas are part of the cached prefix (PRD P1).
  A successful `add` / `enable` / `remove` calls the same code as `/new` for that lane and says so.
  New tools are visible from the next message.
- **Extensions are visible to the turn and to cron only — MCP servers and plugins alike.**
  `review`, `curator` and `titler` keep their narrow toolsets: they have a fixed job, and every
  extra tool schema is paid for on every API call (PRD P2). Concretely, `base_options` defaults
  `plugins` to empty and only turn/cron pass the composed list.
- **The builder covers four call sites, not five. The titler is a deliberate exception.**
  `base_options` owns exactly the three invariants the *agentic* call sites share — `cwd`,
  `setting_sources=["project"]`, `permission_mode="bypassPermissions"`. The titler shares none of
  them on purpose (`tools=[]`, `setting_sources=[]`, no `cwd`, a bare-string system prompt — every
  one load-bearing, see the comments in `titles.py`). Routing it through the builder would mean
  overriding all three, which is not reuse. It stays hand-built, with a comment saying why.
  `env` is likewise **not** an invariant: the foreground turn keeps the advisor tool, background
  forks pass `NO_ADVISOR_ENV` (`config.py`). Each call site keeps its own `env`.
- **Probing a server costs nothing.** `ClaudeSDKClient.get_mcp_status()` connects the configured
  servers and returns per-server `status`, `tools` and `error` without a model call. This is the
  mechanism behind both `test` and the probe inside `add`.
- **One tool, not two.** A single `ext_manage` covering both kinds — its schema is on every API
  call, so two tools would be paid for twice (PRD P2). The kind of an extension is *derived from
  the catalog*, never passed in: `add`/`remove`/`enable`/`disable` take a `name`, and the manifest
  says whether it is an MCP server or a plugin. `kind` survives only as an optional filter on
  `catalog` and `list`.

## Architecture

```
runner._execute ─┐
                 ├─→ options.base_options(cfg, home, plugins=…)
cron.run_job ────┘         ↑
                           │
              extensions.compose.for_scope("turn" | "cron")
                           │
              ┌────────────┼────────────┐
          catalog       store        secrets
        (repo yaml)   (state.db)  (secrets.env)

review / curator / titler → base_options(), without compose
```

### New modules

| File | Purpose |
|---|---|
| `src/claude_hermes/options.py` | `base_options(home, **overrides)` — owns only what the four agentic call sites share and must not drift: `cwd=agent_home`, `setting_sources=["project"]`, `permission_mode="bypassPermissions"`. Everything legitimately different (`tools`, `allowed_tools`, `hooks`, `resume`, `max_turns`, `env`, `plugins`) stays at the call site, where it is read. |
| `extensions/probe.py` | `probe(home, name)` → `(status, tools, error)` via `ClaudeSDKClient.get_mcp_status()`. No model call. |
| `extensions/catalog.py` | Load and validate `catalog/**/*.yaml`. Knows nothing about SQLite. |
| `extensions/store.py` | CRUD over the `extensions` table. Knows nothing about MCP. |
| `extensions/secrets.py` | `secrets.env`: read, atomic write (`0600`), `${VAR}` interpolation. |
| `extensions/compose.py` | The only place the three sources meet. Produces `(mcp_servers, tool_names, plugins)` for a scope. Pure: testable without git, network or a container. |
| `extensions/install.py` | The only place with filesystem side effects: `git clone` of a plugin. |
| `catalog/mcp/*.yaml`, `catalog/plugins/*.yaml` | The curated manifests. |

### Changed

- `db.py` — the `extensions` table.
- `tools.py` — the `ext_manage` handler + tool, registered conditionally, exactly as `cron_manage` is.
- `runner.py`, `cron/executor.py`, `learning/review.py`, `learning/curator.py` — switch to
  `base_options`. `gateway/titles.py` does not (see Locked Decisions) and gains a comment saying so.
- `acl.py` — `is_owner(user_id)`; `check()` also allows `allowed_users`, and extension installs
  must be owner-only.
- `gateway/telegram.py` — the `/secret` command.
- `gateway/presenter.py` — a status line for `mcp__hermes__ext_manage`.

## Data model

```sql
CREATE TABLE IF NOT EXISTS extensions (
    name TEXT PRIMARY KEY,                  -- catalog entry key: 'linear', 'n8n'
    kind TEXT NOT NULL,                     -- 'mcp' | 'plugin'
    catalog_version TEXT NOT NULL,          -- manifest version at install time
    state TEXT NOT NULL DEFAULT 'enabled',  -- 'enabled' | 'disabled'
    installed_at TEXT NOT NULL,
    installed_by INTEGER,                   -- telegram user_id — provenance
    last_error TEXT                         -- why the server failed to come up last time
);
```

No `spec_json` column. This is not an omission: it is the enforcement mechanism for
catalog-only installs.

## Catalog manifest format

`manifest_version: 1`. Unknown top-level keys are a validation error, not a warning — a typo in a
security-relevant file must fail loudly.

### MCP — the shape (illustrative, not a shipped entry)

```yaml
manifest_version: 1                   # schema version — validated
name: example
label: Example
description: What this server is for.
version: 1.0.0                        # entry version — recorded in the registry
transport: http                       # stdio | http | sse
url: https://mcp.example.com/mcp
auth:
  - name: EXAMPLE_API_KEY
    prompt: "Example API key (Settings → API)"
    url: https://example.com/settings/api
    header: Authorization             # http/sse: which header it fills
    format: "Bearer ${EXAMPLE_API_KEY}"
scopes: [turn, cron]
```

**Which entries actually ship.** Only servers whose transport and auth we have verified. The first
two are no-auth stdio servers from the official MCP servers project — they exercise the stdio path
end to end without us inventing anyone's header names. Entries requiring an API key are added one
at a time, each with its auth shape confirmed against that vendor's docs. **Servers that
authenticate via OAuth (Linear, Notion, GitHub's hosted MCP, …) cannot ship yet** — OAuth is out
of scope for this iteration, and a manifest that claims a bearer key where the vendor wants an
OAuth dance would simply not connect. The auth-bearing code path is covered by a fixture manifest
in the tests, not by a shipped entry.

An stdio entry replaces `url` with `command` / `args` / `env`, and `${VAR}` interpolates into
`env`.

**Validation rules** (`catalog.py`) — these are the most security-relevant lines in the feature:

- `transport` ∈ `{stdio, http, sse}`.
- `stdio`: `command` must be one of an allowlist (`npx`, `uvx`, `python`). No shell interpreters,
  no pipes, no `&&`, no substitution — `args` is a list, never a string.
- `http` / `sse`: `url` must be `https://`.
- Plugin `repo` must be `https://` too — it is handed to git, where `ext::…` is arbitrary
  command execution and a leading `-` is an option, not an address. Held to the same standard
  as `url` for the same reason.
- Plugin `subdir` (optional): a plain relative path, every segment starting alphanumeric — which
  refuses `..`, `-flag` and `.hidden` in one rule. It is joined onto the clone, so `install` and
  `compose` additionally re-check containment after resolution: the parse-time rule runs before
  the repo exists and therefore cannot see a symlink. That re-check is defense in depth, not a
  boundary — a plugin runs its own code the moment the SDK loads it.
- Every `${VAR}` referenced anywhere must be declared in `auth`.
- `scopes` ⊆ `{turn, cron}`.

### Plugin — `catalog/plugins/<name>.yaml`

```yaml
manifest_version: 1
name: example-plugin
label: Example
description: ...
version: 1.0.0
repo: https://github.com/owner/repo   # https only — this string is argv for git
ref: v1.2.0                           # required — manifests never float HEAD
subdir: plugins/example               # optional — the plugin's directory INSIDE the repo
auth: []                              # optional, same shape as above
scopes: [turn, cron]
```

The kind is carried by the directory (`catalog/mcp/` vs `catalog/plugins/`), not by a field —
one fact, one place.

## Flows

### Installing an MCP server ("подключи мне Linear")

1. The model calls `ext_manage(action="catalog")`, sees `linear`, calls
   `ext_manage(action="add", name="linear")`.
2. The handler checks ACL (owner only), loads the manifest, finds `LINEAR_API_KEY` missing from
   `secrets.env`, and returns `needs_secret` with the manifest's prompt and URL. **Nothing is
   installed.**
3. The model relays it: "send `/secret LINEAR_API_KEY=…`, get it here: …".
4. The gateway intercepts `/secret` before the runner, writes the pair, deletes the user's message,
   replies "saved".
5. The model calls `add` again. The secret is present, so `compose` probes the server (the same
   code path as `action=test`). If its tools list, we write the `extensions` row, reset this
   thread's session, and reply: "Linear connected (7 tools), this thread's context starts fresh."
6. The user's next message carries `mcp__linear__*` in the toolset.

Installing a plugin differs only at step 5: fetch the pinned ref into
`agent_home/plugins/<name>` under one shared timeout (`git init` + `remote add` +
`fetch --depth 1 origin <ref>` + `checkout FETCH_HEAD`), verify `.claude-plugin/plugin.json` exists
at the plugin root (the clone, or its `subdir`), and only then write the registry row.

Fetch rather than `clone --branch`: `--branch` takes a branch or a tag and cannot take a commit
sha, while the validator accepts a sha and calls it the strongest pin — so the safest manifest
anyone could write was the one guaranteed to fail at install. A tag is fetched as `refs/tags/<ref>`
so a branch of the same name cannot answer for it. The timeout is one deadline across all four
calls, not one per call: per-call would silently quadruple the configured budget, on the user's turn.

### `ext_manage` actions

| Action | Arguments | Effect |
|---|---|---|
| `catalog` | `kind` (optional filter) | What is available to install. |
| `add` | `name` | Install. May return `needs_secret` instead. Resets the lane on success. |
| `list` | `kind` (optional filter) | What is installed, with `state` and `last_error`. |
| `enable` / `disable` | `name` | Flip `state`. Resets the lane. |
| `remove` | `name` | Drop the row (and the plugin directory). Resets the lane. |
| `test` | `name` | Probe without installing: does the server come up, which tools does it list. |

## Error handling

The invariant: **the registry does not lie.** Every row is either a working extension or one with
an honest `last_error`. There are no half-states.

| Failure | Behavior |
|---|---|
| Server fails to come up during `add` | Nothing is written to the registry. The reason is reported. Nothing to clean up. |
| Server breaks later (key expired, host down) | `compose` catches it while building, records `last_error`, **drops that server from this turn**, and continues. One dead server must not kill the conversation. |
| Manifest gone from the catalog (image rolled back) | Registry row exists, manifest does not → the server is skipped, `last_error` explains. No crash. |
| `git clone` fails | `agent_home/plugins/<name>` is removed; nothing is written to the registry. |
| `/secret` from a non-owner | Refused — but the message is deleted anyway; it may have carried someone's real key. |

## Testing

- **`catalog.py` — the validator.** Malformed YAML, unknown transport, unknown top-level key,
  `command: bash` / `sh -c` / a piped string, an `http` URL that is not `https`, a `${VAR}` not
  declared in `auth`. These are the most important tests in the feature.
- **`store.py`** — CRUD; re-`add` of an existing name; `remove` of a missing one.
- **`secrets.py`** — atomic write, `0600`, interpolation, missing variable.
- **`compose.py`** — registry + catalog + secrets → the expected `mcp_servers` dict; a disabled
  server is absent; a `scopes: [turn]` server is invisible to cron; a dead server is dropped
  rather than raising.
- **`options.py`** — a regression test that the four agentic profiles get identical `cwd` /
  `setting_sources` / `permission_mode`, and that overrides win over the defaults.
- **`telegram.py`** — `/secret` never reaches the runner; a non-owner is refused.
- The five-call-site refactor is covered by the existing suite, which must pass unchanged.

## Out of scope (deliberate)

Webhooks. OAuth for MCP (keys and headers only). Hot reload without a lane reset. Servers outside
the catalog. Plugin updates (`git pull`). Tool override and middleware.

## Residual risk

The secret transits Telegram's servers. This is unavoidable if the user is to install extensions
from a phone, and it is accepted.

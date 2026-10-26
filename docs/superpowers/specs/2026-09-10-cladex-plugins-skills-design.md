# CladeX Customize: Plugins and Skills — design

**Goal:** finish `/customize`. The Plugins tab lets the owner see, install,
enable, disable and remove Claude Code plugins from the reviewed catalog, and —
for the first time — read a drafted catalog entry's executable surface on a real
screen before approving it. The Skills tab makes the agent's procedural memory
visible and editable: what skills exist, where each came from, how often it is
used, what the curator did to it, and the text of `SKILL.md` itself.

**Scope of this cut:** the two placeholder tabs (`ComingSoon`, `Customize.tsx`)
become real, plus the shared draft-review surface, plus the session-reset rule
that the Connectors tab has been missing since it shipped. Out of scope: a
browser form that authors a plugin manifest (see *Locked decisions* §1), any
deletion path for skills (PRD P4), curator controls (run now / pause / rollback
— a later cut), and plugin-provided MCP servers gaining their own credentials UI
(they are connectors and already have one).

Spec follows `2026-09-09-cladex-connectors-design.md`; the interface strings go
through the dictionary from `2026-09-09-cladex-i18n-user-menu-design.md`.

## Why this exists

The extensions machinery has supported `kind: plugin` end to end since Phase 4 —
`catalog.parse` validates `repo`/`ref`/`subdir`, `install.clone_plugin` fetches
at a pinned ref, `compose.for_scope` hands the SDK `{"type": "local", "path": …}`
— and none of it is reachable except by asking the agent to call `ext_manage` in
chat. `catalog/plugins/` is empty, so nothing is installable at all. A plugin is
also the one extension whose value is a *list* — commands, skills, agents, hooks
— which a chat reply is a poor way to show.

The draft → approve path has the same problem from the other end. A drafted
manifest's executable surface (`drafts.describe`) is exactly the text a human
must read carefully, and the two places it can be read today are a Telegram
message and a terminal running `scripts/approve_draft.py` on the host. The
browser is simply the better review screen, and `web/app.py` already
authenticates the owner on every request.

Skills are the artefact this system changes most: the review pass writes them
after most turns, the curator ages and consolidates them, `.usage.json` records
provenance and counts. All of it is invisible. `skill_admin` can list and pin
from chat, but the owner has to ask the agent to run it, and it cannot show the
one thing that matters — the skill's actual text.

## Verified constraints

- **Plugins.** `catalog.parse` requires `repo` to start with `https://` and `ref`
  to be a 40-hex sha or a release tag; `subdir` is a plain relative path
  ([[contract.catalog-manifest]]). `install.clone_plugin` (install.py:58) is
  `git init` → `remote add` → `fetch --depth 1` → `checkout FETCH_HEAD`, verifies
  `.claude-plugin/plugin.json` under `plugin_root`, removes the whole directory on
  any failure, and takes one shared deadline (`cfg.plugin_clone_timeout_seconds`,
  default 120 s). `compose.plugin_root` (compose.py:32) re-checks subdir
  containment against the resolved clone; `for_scope` (compose.py:83) drops a
  plugin whose `.claude-plugin/plugin.json` is missing into `last_error` rather
  than handing the SDK a dead path.
- **Registry.** `ExtensionStore` rows are `name, kind, catalog_version, state,
  installed_at, installed_by, last_error, tool_count, tool_names` — *that*
  something is installed, never *what it runs*. `manage.state_of` derives the
  state; `manage.enable/disable/remove` already work for `kind == "plugin"`
  (`remove` calls `install.remove_plugin`).
- **Drafts.** `drafts.pending(home)` returns every draft that still parses,
  `drafts.rejected(home)` the ones that no longer do, with reasons.
  `approvals.begin(home, name)` snapshots the draft bytes under
  `pending-approvals/` and returns `(manifest, surface, token)` where the token is
  the first 8 hex of `sha256(bytes)`; `approvals.confirm(home, name, token)`
  returns the *snapshot* — not the live draft — iff the token matches;
  `approvals.clear` unlinks both. `drafts.safe()` strips control characters, so
  the surface is already safe to render.
- **Skills.** The library is `<home>/agent_home/.claude/skills/<name>/SKILL.md`;
  archived skills sit under `.archive/<name>`; `.usage.json` is the sidecar
  (`created_by`, `created_at`, `last_used`, `use_count`, `patch_count`, `state`,
  `pinned`, `archived_at`). `archive.archive_skill` / `restore_skill` move
  directories and mark the sidecar; **no deletion path exists** (PRD P4).
  `paths.seed_skills` copies the bundled skills from `CH_ASSETS/skills` only when
  the destination is absent, so "bundled" is a fact about the *source tree*, not
  about anything recorded on disk.
- **Concurrency.** `learning/review.py` forks a background pass with Write/Edit
  confined to `[skills_dir, wiki_root]` after most turns, and `curator.py` runs
  aging and an optional consolidation pass. Both write the same `SKILL.md` files a
  browser editor writes. Nothing today serialises them.
- **Backups.** `backup.snapshot(src, logs_dir, reason=…, kind=…)` writes
  `logs/<kind>_backups/<utc-id>/` and prunes to `cfg.backup_keep` (5). `_KINDS`
  (backup.py:12) maps `"skills"` and `"wiki"` to their roots — the map is the only
  thing that needs extending for a third kind.
- **Cache discipline.** Tool schemas, the plugin list and the skill descriptions
  all sit in the cached prompt prefix ([[decision.prompt-cache-discipline]], PRD
  P1). `ext_manage` handles this with `ctx.flags.reset_lane` → `db.reset_session`
  after the row write (runner.py:345); `/approve` calls `runner.reset(session_key)`
  directly. **`web/connectors.py` resets nothing** — connecting a connector from
  the browser leaves every lane resuming a session whose toolset has changed.
  `db.reset_session` (db.py:191) is a single-lane `DELETE`; there is no bulk form.
- **Web layer.** `ApiMiddleware` requires the exact `Origin` on every mutation,
  the session cookie everywhere outside `PUBLIC`, and stamps
  `request.state.user_id` / `is_owner`. `ApiError` is the one error shape.
  Cloudflare cuts a request at 100 s (`web/turns.py` says so and answers 202 for
  that reason).
- **Frontend.** `react-markdown` ^10.1.0 is already a direct dependency
  (`web/package.json`), so a `SKILL.md` renders without a new package. The
  connectors tab's card grid, `StateControl`, `connectorMenu`,
  `useConnectorActions` and `SecretsDialog` are the patterns to follow.

## Locked decisions

1. **The browser never authors a plugin manifest.** A custom *connector* is a
   remote URL: the form in `AddConnectorDialog` cannot produce a `stdio` entry,
   because `catalog.parse` would demand a `command` and the form has no field for
   one. A plugin is the opposite — it is code fetched and loaded into the SDK,
   running its own hooks the moment it is enabled. The path for a new plugin stays:
   agent drafts (or a human writes the YAML) → human approves → install. The
   browser makes the *approval* good; it does not become a second way to skip it.
2. **Draft approval keeps the two-step content-bound token.** `POST
   /api/drafts/{name}/review` calls `approvals.begin` and returns
   `{kind, name, label, surface, token}`; the dialog renders `surface` verbatim in
   a monospace block; `POST /api/drafts/{name}/approve` sends the token back and
   the server calls `approvals.confirm`. The guarantee is unchanged from chat: the
   token is bound to the exact bytes the human read, so a draft rewritten between
   the two calls invalidates it (`stale_draft`). The request is an
   owner-authenticated browser action, never a model-callable tool — a
   prompt-injected turn still cannot enact its own draft.
3. **A draft appears on the tab of its kind.** `mcp` drafts on Connectors,
   `plugin` drafts on Plugins, as a "Pending review" section above the grid. One
   `drafts` store slice and one `<PendingDrafts kind>` component serve both. The
   browser also gains what chat never had: **discard**
   (`DELETE /api/drafts/{name}`), which unlinks the draft and any snapshot.
4. **What a plugin provides is read live from the clone, never stored.**
   `plugins.components(root)` counts `commands/*.md`, `skills/*/SKILL.md`,
   `agents/*.md`, and the presence of `hooks/hooks.json` and `.mcp.json`, reading
   `.claude-plugin/plugin.json` for the author and version. A listing is a page
   load, the files are local, and a value read from disk can never be stale — the
   opposite trade-off from `tool_count`, which costs a network probe.
   `present: false` in the wire is how a row whose clone vanished is visible
   before the next turn records `last_error`.
5. **Web installs are synchronous and capped.**
   `timeout = min(cfg.plugin_clone_timeout_seconds, 85.0)`: a shallow fetch of a
   plugin repo is seconds, and the 100 s Cloudflare cut-off would turn the
   configured 120 s into a request that dies with the clone still running. A
   background task with an `installing` state buys nothing at this size and adds a
   state machine to the registry.
6. **Skills are editable from the browser.** View, create, edit `SKILL.md`,
   pin/unpin, archive/restore. Not a deletion path (P4) and not a file manager: a
   skill's `references/` and `scripts/` are *listed* with sizes, but only
   `SKILL.md` is writable from here. Anything more is the agent's job in chat,
   where it already has Write and Bash.
7. **One implementation, two channels.** `learning/library.py` holds
   `list_skills`, `read_skill`, `write_skill`, `create_skill` and `to_wire`, and
   `skill_admin`'s `list` action is refactored onto it — the
   `extensions/manage.py` precedent, so the tool and the router cannot drift on
   what "stale" means.
8. **An edit is guarded by an etag.** `GET /api/skills/{name}` returns
   `etag = sha256(SKILL.md bytes)`; `PUT` requires it and answers **409
   `stale_edit`** with the current content when it no longer matches. The
   background review pass writes these same files after most turns; without the
   precondition, the owner's Save would silently discard whatever the agent had
   just learned.
9. **An edit snapshots first, under its own backup kind.** `_KINDS` gains
   `"skill_edits": ("skill_edit_backups", "skills.tar.gz")`. Reusing
   `kind="skills"` would let a few browser saves evict the `pre-curator-run`
   snapshot that the curator's rollback instructions point at.
10. **A mutation that changes the cached prefix resets every lane.**
    `AgentRunner.reset_all()` → `db.reset_all_sessions()` (`DELETE FROM sessions`).
    Called after: plugin install/remove/enable/disable, draft approve, skill
    create/edit/archive/restore, **and the connectors mutations that reset nothing
    today** (connect, enable, disable, remove). Not called for pin/unpin, which
    changes no text the model sees. A turn in flight writes its session row back
    when it ends and so keeps its session — the reset is for idle lanes, which is
    every lane in practice when someone is clicking in Customize.
11. **Owner-only mutations, every allowed user may read.** The connectors rule,
    unchanged.
**Amendment (2026-09-11), after the owner chose to let plugins keep their servers.**
Decisions 4 and 5 are superseded on one point: install is **two calls**, not one.
`POST /install` fetches and answers with `plugins.surface` — everything the plugin
adds, including every server its own `.mcp.json` declares — plus a token that is the
sha256 of that text; `POST /install/confirm` sends the token back and only then is
the registry row written. A fetched-but-unconfirmed clone is inert, because
`for_scope` reads `store.enabled()`. `DELETE` with no row is Cancel.

The reason is a fact the original spec got wrong twice: a plugin brings MCP servers
the catalog never sees. The CLI reads the plugin's `.mcp.json` itself (the SDK's
`strict_mcp_config` docstring names "plugin-provided servers"; `options.py` leaves
the flag at its default), so those servers get no manifest, no registry row, no
probe, and `for_scope` cannot drop a broken one. Two rules answer it, both at install
time because the files do not exist until the pinned ref is fetched:

- a bundled server that would **run a command** is refused (`400 local_server`) and
  the clone is removed — a remote server is a URL the owner reads on the confirmation
  screen, a local one is an arbitrary argv on a route built for URLs, and real
  plugins ship them (`discord` and `fakechat` in the official marketplace run
  `bun run …`);
- the remote ones are listed on that screen, named and unreviewed, before anything
  is registered.

`_classify` fails closed: anything that is not a `url` with an http/sse transport
counts as local.

12. **Three plugin entries ship in the repo catalog**, pinned to a sha:
    `marketing`, `finance` and `design` from
    `https://github.com/anthropics/knowledge-work-plugins` (`subdir: marketing`
    etc.). They give the tab something real to install on day one.

    **Amended 2026-09-10, during implementation.** This decision originally read
    "all skills-only (no hooks, no bundled MCP server)". The first half holds and
    the second does not: at the pinned ref
    `11ac5eeaca27c764913a06f1e61a7cb712790b1a` every plugin in that repo except
    `cowork-plugin-management` (a plugin-manager, not a knowledge-work domain)
    ships a `.mcp.json` beside its `skills/`, so the "skills only" phrasing
    described a set that does not exist there. Rather than drop the entries or
    pretend, the decision is narrowed to what was actually verified at that ref:

    - **No hooks, no commands, no agents** in any of the three — nothing that
      executes on a turn. `marketing` and `finance` are eight skills each,
      `design` seven; only `.claude-plugin/`, `skills/`, `.mcp.json`,
      `CONNECTORS.md`, `README.md` and `LICENSE` are present.
    - **Every server in every bundled `.mcp.json` is `"type": "http"`** at a
      vendor URL (Slack, Canva, Figma, HubSpot, Notion, BigQuery, Linear, …).
      There is no `stdio` entry, so installing one spawns no local process.
    - **None of them receives a credential from this system.** `compose.py`
      interpolates secrets only into a *catalog manifest's* `server_spec`; a
      plugin is handed to the SDK as `{"type": "local", "path": …}` and its
      `.mcp.json` carries no header and no env, so those servers reach the model
      unauthenticated and answer nothing an owner has not signed into elsewhere.

    What ships instead of the deleted guarantee is disclosure. Each manifest's
    `description` names the bundled servers and says this system hands them no
    credentials; `plugins.components` already reports `mcp: true`, and the plugin
    page renders "Brings its own tools — a bundled MCP server." above the trust
    note. The honest summary is: installing one adds markdown to the prompt plus
    a list of remote servers nobody is logged into — weaker than "and nothing
    else", and stronger than anything the repo could support at this ref.

## Data model

Nothing new in `state.db`. Plugins reuse the `extensions` table
(`kind = "plugin"`), skills stay files plus `.usage.json`, drafts stay files.

### `PluginWire` — `GET /api/plugins` items

```ts
{
  name: string; label: string; description: string;
  source: "catalog" | "custom";          // custom = a user-catalog entry (approved draft)
  repo: string; ref: string; subdir: string; version: string;
  scopes: string[];                      // ["turn"] | ["turn","cron"]
  state: "available" | "installed" | "disabled" | "error";
  present: boolean;                      // the clone is on disk and has plugin.json
  last_error: string | null;
  installed_at: string | null;
  components: {                          // null until installed
    commands: string[]; skills: string[]; agents: string[];
    hooks: boolean; mcp: boolean; author: string; plugin_version: string;
  } | null;
}
```

`state` derives from the registry row exactly as connectors do
(`manage.state_of`), with `connected` spelled `installed`.

### `DraftWire` — `GET /api/drafts` items

```ts
{ name: string; kind: "mcp" | "plugin"; label: string; description: string;
  transport: string | null; repo: string | null; ref: string | null }
```

plus `rejected: [{ file: string; reason: string }]` — the drafts that no longer
parse, which `pending()` drops silently and an operator otherwise waits forever
for.

### `SkillWire` — `GET /api/skills` items

```ts
{
  name: string; title: string; description: string; broken: boolean;
  source: "bundled" | "agent" | "user";
  state: "active" | "stale" | "archived";
  pinned: boolean; use_count: number; patch_count: number;
  created_at: string | null; last_used: string | null; last_patched: string | null;
  files: number; bytes: number;
}
```

- `title` / `description` come from the `SKILL.md` YAML frontmatter; a skill whose
  frontmatter will not parse still lists, with `description: ""` and
  `broken: true`, so the page can say so instead of hiding it.
- `source`: `"bundled"` when the name exists in `paths.bundled_skill_names()`,
  otherwise the sidecar's `created_by` (`"agent"` | `"user"`); a directory with no
  sidecar entry at all reads as `"user"`, matching `UsageSidecar`'s own default.
- The listing is the union of live directories, `.archive/` directories and
  sidecar keys, so a skill can never be invisible because one of the three
  disagrees.

### `SkillDetail` — `GET /api/skills/{name}`

`SkillWire` plus `{ content: string; etag: string; tree: [{path, bytes}] }`.
`content` is `SKILL.md`; `tree` lists every other file under the skill directory
with its size, capped at 200 entries.

## API

All routes sit under the existing `ApiMiddleware`: JSON content type, exact
`Origin` on mutations, cookie everywhere. Reads need any allowed user; every
mutation needs `request.state.is_owner` (403 `forbidden`).

### `web/plugins.py`

| Route | Does |
|---|---|
| `GET /api/plugins` | catalog `kind == "plugin"` entries merged with rows, components read from disk; catalog first, then custom, both by label |
| `POST /api/plugins/{name}/install` | `clone_plugin` at the capped timeout → `store.add` → `reset_all` → the wire row. Idempotent: `clone_plugin` removes and re-fetches, so this is also "update to the manifest's ref" |
| `POST /api/plugins/{name}/enable` | `manage.enable` → `reset_all` |
| `POST /api/plugins/{name}/disable` | `manage.disable` → `reset_all` |
| `DELETE /api/plugins/{name}` | `manage.remove` (drops the row and the clone) → `reset_all` → 204 |

Errors: 404 `not_found` (no such plugin in the catalog), 502 `install_failed`
(carrying `InstallError`'s message — git's stderr, already trimmed), 403
`forbidden`.

### `web/drafts.py`

| Route | Does |
|---|---|
| `GET /api/drafts` | `drafts.pending` + `drafts.rejected` |
| `POST /api/drafts/{name}/review` | `approvals.begin` → `{surface, token}`; 404 `no_draft` |
| `POST /api/drafts/{name}/approve` | `approvals.confirm(token)` → `writer.promote` → re-read through `catalog.get` (P5: do not claim it landed until the reader sees it) → `approvals.clear` → `reset_all`. 409 `stale_draft` on a token mismatch, 409 `shadowed` when `catalog.is_repo_entry(name)`, 500 `unverified` when the promoted entry is not visible |
| `DELETE /api/drafts/{name}` | unlink the draft and any snapshot → 204 |

### `web/skills.py`

| Route | Does |
|---|---|
| `GET /api/skills` | `library.list_skills` |
| `GET /api/skills/{name}` | detail + `content` + `etag` + `tree`; 404 `not_found` |
| `POST /api/skills` | `{name, content}` → create the directory and `SKILL.md`, `sidecar.mark_created(name, "user", now)` → `reset_all`. 409 `name_taken`, 400 `bad_name`, 400 `bad_frontmatter`, 413 `too_large` |
| `PUT /api/skills/{name}` | `{content, etag}` → snapshot (`kind="skill_edits"`) → write → `sidecar.bump_patch` → `reset_all`. 409 `stale_edit` carrying the current `{content, etag}` |
| `POST /api/skills/{name}/pin` \| `/unpin` | `sidecar.set_pinned`; **no reset** |
| `POST /api/skills/{name}/archive` | refuses a pinned skill (409 `pinned`), else `archive.archive_skill` → `reset_all` |
| `POST /api/skills/{name}/restore` | `archive.restore_skill` → `reset_all`; 409 `restore_failed` |

Validation on write: `name` matches `^[a-z0-9][a-z0-9_-]*$` (the catalog's own
rule — it is a directory name), `content` ≤ 64 KiB, frontmatter parses and carries
a non-empty `name` and `description`. The resolved path must stay under
`skills_dir` — checked with `is_relative_to` after `resolve()`, the
`FileStore.resolve` rule.

## Interface

Routes: `/customize/plugins`, `/customize/plugins/:name`, `/customize/skills`,
`/customize/skills/:name`. `ComingSoon` is deleted from `Customize.tsx` and the
route table.

**Plugins tab** — the connectors grid, one card per plugin: a puzzle tile, label,
first sentence, a `From the catalog` check or a `Custom` badge, and the same
square `StateControl` (a plus to install, a check when installed, a power icon
when disabled, a warning when the clone is gone). A "Pending review" section sits
above the grid when a `plugin` draft is queued.

**Plugin page** — the description in full, then *What it adds*: the command,
skill and agent names as chips, and a plain-language line for hooks ("runs its own
hooks on every turn") and a bundled MCP server ("brings its own tools"), because
those two are what a reader should slow down on. Then `repo@ref` as a link to the
exact tree, `subdir`, scopes, installed-at, and the same trust note the connector
page carries, worded for code rather than for a remote server.

**Draft review dialog** — the manifest's `surface` verbatim in a monospace block
(pre-wrapped, never rendered as markdown), the `⚠️ LOCAL COMMAND` banner rendered
as a warning strip when `approvals.describe_with_banner` produced one, and two
buttons: Approve and Discard. Approve posts the token from the `review` call.

**Skills tab** — a list, not a grid, in the routines-row idiom: name and
description on the left, badges (`Bundled` / `From the agent` / `Yours`, `Pinned`,
`Stale`), and `used {n} times · last {date}` in muted text. Above it: a filter
segmented control (`Active` / `Archived` / `All`), a search box that filters on
name and description client-side, and a `New skill` button for the owner.

**Skill page** — the rendered `SKILL.md` (`react-markdown` + `remark-gfm`), the
stats as a definition list, the file tree, and the actions: Edit, Pin/Unpin,
Archive/Restore. Edit swaps the rendered body for a monospace `<textarea>` with
Save / Cancel; Save sends the etag and, on 409, shows "The agent changed this
skill while you were editing" with a Reload button that replaces the buffer.

**i18n**: new `plugins.*`, `skills.*` and `drafts.*` sections in `en.ts` and
`ru.ts`; the `customize.comingSoon*` keys are deleted. Not translated: plugin and
skill names, descriptions from `plugin.json` / frontmatter, the draft surface, git
error text.

## Errors and edge cases

- **A catalog entry disappears under an installed plugin.** `for_scope` already
  records `no longer in the catalog`; the listing shows only catalog entries, so
  such a row would be invisible there. The listing therefore appends rows with
  `kind == "plugin"` that have no manifest as `state: "error"`, `present: false`
  and the recorded `last_error` — the one case where a row without a manifest must
  still be shown, because Remove is the only way out of it.
- **A clone that vanished** (a failed re-install, a wiped volume): `present:
  false` and an inline "Reinstall" action, which is `install` again.
- **A draft that stops parsing** between the listing and the review call: 404
  `no_draft`, and the `rejected` list explains why on the next load.
- **Two browsers approving the same draft:** the second `confirm` finds no
  snapshot and answers 409 `stale_draft`.
- **A skill archived while its page is open:** the detail route reads
  `.archive/<name>` too, so the page keeps working and offers Restore.
- **A curator pass mid-edit:** the etag catches it; `PUT` never merges.
- **`reset_all` during a live turn:** the turn's session row is written back at
  the end and survives. Documented on the method, not worked around.

## Testing

- `tests/test_skill_library.py` — listing merges the three sources; bundled vs
  agent vs user; frontmatter parsing including a broken one; write/create
  validation and the containment check.
- `tests/test_web_plugins.py` — list shape with and without a row; install with a
  fake `clone_plugin`; enable/disable/remove; non-owner 403; a row whose manifest
  is gone still lists; `reset_all` called on exactly the mutations that should
  call it.
- `tests/test_web_drafts.py` — review returns a token bound to the bytes; approve
  promotes the snapshot, not a draft rewritten in between; a repo-catalog name is
  refused; discard unlinks both files.
- `tests/test_web_skills.py` — detail etag; `PUT` with a stale etag is 409 and
  carries the current content; a pinned skill refuses archive; restore; the
  snapshot lands under `logs/skill_edit_backups/`; no route deletes anything.
- `tests/test_reset_all.py` — `db.reset_all_sessions` empties the table; the
  connectors mutations now call it.
- Frontend: store slices (`plugins`, `skills`, `drafts`) as `connectors.test.ts`
  does; `PluginCard`, `SkillRow`, `DraftDialog` component tests; the i18n
  no-stray-Cyrillic test stays green.
- E2E: `web/e2e/customize.spec.ts` gains a plugins walk (install → installed), a
  skills walk (open → edit → save) and a draft walk (review → approve), all
  against `fake-server.ts`.

## Risks and open questions

- **The skills editor and the review pass want the same files.** The etag makes a
  lost update visible rather than silent, but it does not serialise them: a `PUT`
  that lands *while* the pass is mid-edit still wins the race for the last write.
  A file lock shared with `review.py` is the fix if this ever bites; it is not
  worth the coupling before it does.
- **`reset_all` is a blunt instrument.** Every lane loses its cached prefix,
  including other users' private lanes, for a change that may affect one scope. It
  is correct and it is rare (someone is clicking in Customize); a per-scope reset
  would need the registry to know which lanes composed which extensions, which is a
  bigger idea than this cut deserves.
- **Shipping plugin entries pins a sha that ages.** A pinned ref is the whole
  point, but it means the catalog needs a refresh PR when upstream moves. The
  install route being idempotent ("install again = re-fetch at the manifest's
  ref") is what makes that a one-click follow-up rather than a remove-and-add.

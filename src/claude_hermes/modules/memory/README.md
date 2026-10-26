# `modules/memory` — the agent wiki

## Purpose

`agent_home/wiki` is the agent's declarative memory: the one tree where the model
owns every byte. This module seeds it, reads its index into the system prompt,
enforces its rules on every fork that can write, serves it to the owner as a graph,
and runs the periodic lint pass that keeps it honest.

It owns **one** database table, `wiki_pages`, and that table is a **cache**: a
derived full-text index over the markdown, rebuilt from disk. The state is still
files — dropping the table and rebooting costs nothing but a reindex, and no code
path here treats a row as a fact the markdown does not also carry.

## Layout

```
domain/
  constants.py   index.md/log.md names, seed namespaces, headers, legacy migrations,
                 TRUNCATION_NOTE, WIKI_LINT_TOOLS
  page.py        frontmatter + wikilink parsing, read_page() -> one graph node
  prompts.py     build_wiki_lint_prompt(dry_run, *, shared_readonly=False)
  tables.py      WikiPageRow — the ORM row of the INDEX. `tables.py`, not
                 `models.py`: the application imports `domain.models`, and only
                 infrastructure and `core.db.registry` may see sqlalchemy
application/
  ports.py       WikiIndexPort, WikiLintPort, WikiIndexerPort (provided) ·
                 WikiPageStore, ForkRunner, LintOptionsFactory (consumed)
  services/graph.py  WikiGraphService(root): index_text, resolve, graph, page
  services/index.py  WikiIndexService(store, *, clock=None) -> WikiIndexerPort:
                     touch(owner_id, workspace, rel_paths), reindex(owner_id, workspace)
  services/lint.py   WikiLintService(wiki_root, runner, options, *, workspace=None,
                     after_lint=None) -> WikiLintPort  (one per workspace since 4b)
infrastructure/
  wiki_store.py  WikiStore(root, index_max_chars=8000, *, shared_root=None) ->
                 WikiIndexPort; WikiStore.for_workspace(ws) is the per-turn
                 constructor (Wave 4a) and picks the shared root for a non-owner
  page_index.py  PgWikiIndex(engine) -> WikiPageStore + search(query, *, owner_id,
                 limit=8, depth=1), the port turns' `memory_search` consumes
  turn_hooks.py  WikiTurnState + the six hook factories (incl. index_flush_factory)
                 + wiki_hooks() provider
  lint_fork.py   SdkForkRunner, WikiLintOptions(cfg, home, wiki_root, workspace=None),
                 build_wiki_lint_service(cfg, home, wiki_root, workspace=None,
                 *, after_lint=None)
presentation/
  router.py        router: APIRouter(tags=["memory"]) - the two GETs, both owner-only
  dependencies.py  get_graph -> WikiGraphService, the module's one provider
  schemas.py       graph_wire, page_wire (no request model: neither route has a body)
```

## Public surface

**Ports provided** (other modules declare their own structural copies):

- `WikiIndexPort` — `root: Path`, `index() -> str`, `index_mtime() -> float`.
  Satisfied by `WikiStore`. Consumed by turns (system prompt + index-refresh hook).
  **One store per user since Wave 4a**: the constructor argument is a WORKSPACE
  ROOT, not `CH_HOME`, and `WikiStore.for_workspace(ws)` is how a turn builds the
  lane owner's. For the deploy owner `root is CH_HOME` and the paths are unchanged.
- `WikiLintPort` — `async lint(dry_run: bool = False) -> str | None`. Satisfied by
  `WikiLintService`. Consumed by learning's curator pass. Returns `None` when there
  is no wiki on disk or the fork failed; it never raises. **One per workspace
  since Wave 4b** (D4): learning's wiring asks `wiki_lint_for(workspace)` — a
  `main.build_app()` closure over `build_wiki_lint_service(cfg, home,
  ws.wiki_root, workspace=ws)` — for each user's scope, so a non-owner's curator
  pass lints THEIR wiki in THEIR tree. `workspace=None` is the deploy owner's,
  the pre-4b meaning. There is no deploy-wide lint object any more:
  `MemoryBundle` carries `wiki`, `wiki_root` and `hooks` (Step 5 dropped its
  `lint` field, which nothing had read since 4b). The two routes stay owner-only.
- `wiki_hooks(wiki_root, state, *, index=None, seen=None, session_key="",
  webfetch_hint=False, indexer=None, owner_id=0, workspace=None)
  -> dict[str, list[HookMatcher]]` — memory's contribution to a fork's hook table,
  grouped by SDK event name. **Additive**: the caller merges these lists with its own
  (the runner adds `Bash`; the review fork adds the skills write guard). With an
  `indexer` the `Stop` list gains the index flush AFTER the gate; without one the
  table is byte-for-byte what it was.
- `WikiIndexerPort` — `async touch(owner_id, workspace, rel_paths)`,
  `async reindex(owner_id, workspace)`. Satisfied by `WikiIndexService`. Consumed
  by the Stop hook here, by the review fork through `core.app_context.WikiForkGuards`,
  and by `main.build_app()`'s boot sweep. `workspace` is duck-typed on `.root`,
  `.wiki_root`, `.is_owner`; `owner_id <= 0` means "do not index".
- `PgWikiIndex.search(query, *, owner_id, limit=8, depth=1) -> list[dict]` — the
  shape turns declares as its own `WikiSearch` Protocol for `memory_search`. Each
  hit carries `owner_id, path, ns, title, summary, tags, links, shared, snippet,
  via, hop`; `via` is `search` (hop 0), `link` or `backlink`.

**Ports consumed by this module for the index**: `WikiPageStore` —
`upsert(owner_id, page, *, content, shared, now)`, `delete(owner_id, path)`,
`paths(owner_id)`. Satisfied by `PgWikiIndex`, so the application layer never
sees sqlalchemy.

**Ports consumed**: `ForkRunner` (`async run(prompt, options) -> str | None`),
`LintOptionsFactory` (`build(*, dry_run: bool) -> object`). Both are satisfied inside
this module by `infrastructure/lint_fork.py`; they exist so the application layer
never imports `claude_agent_sdk` (import-linter contract).

**Dependencies** (`presentation/dependencies.py`, built on `core.di.provide`):

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_graph` | `wiki_root` | `WikiGraphService` (built per request over the `Path`) |

That is the whole list — `wiki_root` is the one `provide("<name>")` in this module,
and it was the whole list when a `routes(deps)` closure read the bundle (Wave 2b). **Whether this router is mounted at all is the shell's decision**: a deployment
without a wiki (`deps.wiki_root is None`) gets no wiki routes and the app's `/api/*`
catch-all answers the 404 — the 503 guard inside `provide` is unreachable in a
correctly assembled app. Both routes are owner-only through `core.auth.require_owner`.

**Routes** — `router: APIRouter(tags=["memory"])`:

| Method | Path | `operation_id` | Body |
|---|---|---|---|
| GET | `/api/wiki/graph` | `memory_get_graph` | `{pages: [{path, ns, title, summary, tags, updated, links, in_index}], namespaces, missing}` |
| GET | `/api/wiki/pages/{path:path}` | `memory_get_page` | the same node plus `content` and `etag` (sha256 of the bytes) |

**Tools / events**: none. The module publishes no MCP tool and no event — the
`memory_search` tool lives in `turns` and reaches this module only through the
`WikiSearch` port `PgWikiIndex` satisfies.

## The index (`wiki_pages`)

One row per `(owner_id, path)`, where `path` is the wiki-root-relative posix path
**without** `.md` — the same string `read_page()` and `WikiGraphService.graph()`
use, so the graph route and the index name a page identically. Columns: `ns`,
`title`, `summary`, `tags[]`, `links[]`, `content` (body, frontmatter stripped,
capped at 200k chars), `shared`, `indexed_at`, and a generated `content_tsv`
(`'simple'`, GIN-indexed — the configuration `messages.content_tsv` already uses,
because Russian stems badly under any other). `owner_id` references `users(id)`
`ON DELETE CASCADE`: a wiki belongs to exactly one person, and an orphan row is a
page searchable by nobody.

**Three reindex triggers, no periodic job:**

| trigger | what it writes |
|---|---|
| the Stop hook of a turn **and of a review fork** | `touch()` over `WikiTurnState.pages` |
| a non-dry-run lint | `reindex()` of that tree, through `after_lint` |
| boot, main process only | `reindex()` of every user whose wiki exists on disk |

The review fork is on that list because it is the wiki's main rewriter (curation
runs out of band): without its flush `memory_search` quotes a page the fork has
already replaced. A page edited by hand over SSH is picked up at the next boot.

**The `shared/` namespace** is `Workspace.shared_wiki_root` — `shared/` inside the
OWNER's wiki. The owner writes it with the same `Write`/`Edit` and the same
`index.md` discipline as any other page; everybody else gets it as a read root,
as a `## Shared wiki (read-only…)` section in their prompt index (capped at a
quarter of `index_max_chars`), and as a denied write root. Rows whose `path`
starts with `shared/` are written `shared = true` **only when the tree being
indexed is the owner's** — a non-owner's own `shared/` folder is private to them.
A non-owner's search predicate is `owner_id = :me OR shared`, and identity is
`(owner_id, path)` everywhere, because a user may invent their own `shared/home`
beside the owner's.

## Invariants

- **`wiki/raw/` is immutable.** `raw_guard_factory` is the only thing enforcing it;
  every fork that can Write/Edit installs it (turn, review, lint). A source that can
  be amended is not a source.
- **A page changed without `index.md` and `log.md` is drift.** `stop_gate_factory`
  blocks once per turn; `stop_hook_active` is the loop breaker, and the second pass
  logs instead of trapping the turn.
- **The graph is built by scanning the directory, never from `_NAMESPACES`.** That
  tuple is seed-only; the agent invents folders (`content/` exists in production).
- **Both wiki routes are owner-only.** An allowlisted user may chat; only the owner
  reads the memory. The gate is `Depends(require_owner)` on each route, and it refuses
  with `403 forbidden` / "owner only" — the same answer the hand-written
  `owner_only(request)` gave, which `tests/test_web_wiki.py` and
  `tests/modules/memory/test_memory_router.py` both pin.
- **`resolve()` proves containment twice** — segment check (`..`, empty, drive-qualified)
  before touching disk, `is_relative_to` after resolving (a planted symlink). Moved
  verbatim from `web/wiki.py`; `tests/test_web_wiki.py::test_nothing_outside_the_wiki_can_be_read`
  and `tests/modules/memory/test_memory_graph.py` both walk the same list.
- **`seed()` is idempotent via the `index.md` sentinel** and never rewrites content.
  That is what lets the composition root call it per turn (`wiki_for`) for a
  brand-new user without ever touching an existing wiki.
- **A turn reads its own lane owner's wiki, in both places.** `TurnService` builds
  the store from `TurnContext.workspace` (the system append and the `index_mtime`
  watermark) and `WikiHooksProvider.build(..., workspace)` builds the hooks over
  the same root. Miss either and user B's byte-stable cached prefix carries user
  A's index while B's Stop gate nags about — and B's `Write` reaches — A's wiki,
  through a path the read guard never sees (advisor blocker B1).
- **The lint fork merges its env**: `{**NO_ADVISOR_ENV, **workspace_env(...)}`. The
  SDK lays `env` over `os.environ` but not over itself, so a replace would drop
  `CLAUDE_CODE_DISABLE_ADVISOR_TOOL` and re-enable the advisor on a background pass.
- **The lint fork runs the DEPLOY's fork model, never the lane's**:
  `cli_id(cfg.curator_model or cfg.review_model or cfg.model)`, with no effort —
  nobody waits on a lint pass, and a user whose lane is on an expensive model must
  not make the deployment's housekeeping expensive. `cli_id` is the one spelling a
  model reaches the CLI in; `workspace_env` also carries
  `CLAUDE_CODE_NO_MODEL_FALLBACK=1`, so the CLI's own availability chain cannot
  answer as something else. `SdkForkRunner.run` logs `wiki_lint_fork_ran
  {model, cache_read_share}` afterwards: which model actually answered, and
  `cache_read / (input + cache_read + cache_creation)` over the MAIN-LOOP calls
  only — a `ResultMessage`'s usage is the sum over every call of the fork, and the
  CLI's own Haiku side call is not the fork. `learning` has the same rule and its
  own copy of the loop (`fork_usage.drain_fork`): a module never imports another.
- **The lint fork is confined like a turn** (Wave 4b, D4). `WikiLintOptions.build`
  runs it in `workspace`: `base_options(ws.root)` for `cwd`, `workspace_env(ws)`
  for that tree's `CLAUDE_CONFIG_DIR` and `TMPDIR`, a `PreToolUse` `path_guard_factory`
  over `[ws.agent_home, ws.tmp_dir, ws.logs_dir, ws.plugins_dir]` with
  `cwd=ws.agent_home` (the same roots a foreground turn gets; no catalog root —
  no fork reads a manifest), `write_guard_factory(roots, cwd=ws.agent_home)` so a
  relative `file_path` resolves where the path guard resolves it (advisor B-8:
  stacked on one matcher, two hooks with different cwds would disagree), and
  `disallowed_tools=["Bash", "Agent", "Task"]` — under `bypassPermissions` the
  `tools=` list only auto-approves, and this is the control that takes a tool
  away. A non-owner's lint cannot read the owner's wiki while fixing its own.
- **A dry-run lint is read-only by construction**: the options factory hands the write
  guard an empty root list, so every write is denied — not a promise asked of the model.
- **The index truncates loudly** (`TRUNCATION_NOTE`), and the router reads the *raw*
  index, never `WikiStore.index()`, so a truncated index cannot mark real pages unindexed.
- **A failed index write never reaches a turn.** `WikiIndexService.touch`/`reindex`
  catch `Exception` and log; the Stop-hook flush additionally bounds the call with
  `asyncio.wait_for(..., FLUSH_TIMEOUT_SECONDS)`. The failure to fear is not a
  swallowed exception but a hung connection holding the SDK's reply, and the
  `after_lint` callback is swallowed separately so a pass that fixed the wiki
  still reports what it did.
- **The index is rebuilt from disk; disk wins.** Nothing reads a row to decide what
  a page says: `touch()` re-reads the file, a path whose file is gone becomes a
  `delete`, and `reindex()` converges the table to `page_paths(wiki_root)`.
- **The owner's `WikiStore.index()` output is byte-identical to the pre-shared
  one** (`shared_root=None`), and `tests/modules/memory/test_memory_store_shared.py`
  pins it. It is a cached prompt prefix (P1); a feature for other users may not
  move one byte of it.
- **`owner_id` and the workspace must agree.** For a non-owner the service requires
  `workspace.root.name == str(owner_id)` — the `users/<id>` convention identity
  writes — and logs an error and writes nothing otherwise. Today both come from
  `lane_owner`, resolved once per turn, and cannot diverge; the tripwire is against
  a future caller pairing user A's tree with owner B's id, which would make A's
  private pages searchable by B, silently.

## Known debts

- ~~The wiki is per user; everything that CURATES it is not.~~ — **paid in Wave
  4b**: the lint pass and the curator run per workspace, and a non-owner's run
  follows their learning grant — their own choice in Settings → Users since 0009,
  else the `users_learning_enabled` default (a non-owner's wiki is tidied by their
  own weekly pass, in their own tree, reported under `users/<id>/logs/curator/`).
  What stays the owner's is `GET /api/wiki/*` — the Memory page reads one wiki, and it is the
  owner's, by decision rather than by debt.

- ~~`wiki.py`, `hooks.py`, `web/wiki.py` re-export shims; `router.routes(deps)`;
  the duplicate `build_wiki_lint_prompt` in learning; `MemoryBundle.lint`.~~
  **Deleted in Step 5.** `main.py` and `http/app.py` import the module directly;
  the lint pass exists only per scope — `main.build_app()` hands learning a
  `wiki_lint_for(ws)` closure over `build_wiki_lint_service(cfg, home,
  ws.wiki_root, workspace=ws)`, and each scope's `CuratorService` gets its own
  `WikiLintService` as its `WikiLintPort`. The turn-hook table is
  `core.app_context.WikiHooksProvider(wiki, wiki_hooks=wiki_hooks,
  new_state=WikiTurnState)`, built by the composition root alone.
- Wave 2b converted this module first, as the reference the other eight follow:
  `router` is an `APIRouter`, the service arrives through `Depends(get_graph)`, and
  `tests/modules/memory/test_memory_router.py` swaps it with
  `app.dependency_overrides`.
- `deps.wiki_root` stays duck-typed: `provide("wiki_root")` reads it off
  `app.state.deps` by name, so the bundle is still a structural contract rather than a
  typed one. It becomes typed when `WebDeps` is replaced in Step 4.

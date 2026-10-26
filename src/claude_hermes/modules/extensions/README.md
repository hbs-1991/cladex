# `extensions`

## Purpose

Everything about MCP servers and Claude Code plugins: what the catalog offers, what is
installed, the secrets and OAuth tokens they need, and the two approval paths (chat and
browser) that stand between a drafted manifest and something that runs in the agent's
container.

The load-bearing asymmetry of this module: **the agent proposes, a human promotes.**
`catalog/` is mounted read-only into the container; the draft queue is a table on the
state database and nothing installs from it. Every rule below exists to keep those two
apart.

Wave 3a moved three things off the `/data` volume and into PostgreSQL — extension
secrets (Fernet-sealed), the draft queue and the approval snapshots. `secrets.env`
was readable by anything running as the agent, including the agent's own Bash;
`catalog-drafts/` was a directory the agent could rewrite behind an approver's back.
The host approver (`scripts/approve_draft.py`) now reads the queue *through* the app
and still writes `catalog/` itself, which is the half that was always the point.

## Layout

```
domain/          manifest.py  exceptions.py  drafts.py  approvals.py  secrets.py
                 compose.py   paths.py       registry.py  plugin_surface.py
                 oauth.py     probe.py       models.py (ORM: Extension, OauthToken,
                              ExtensionSecret, CatalogDraft, ApprovalSnapshot)
                 Wave 4a adds `catalog_drafts.owner_id` (FK -> `users(id)`,
                 ON DELETE SET NULL) — who PROPOSED a draft, audit only.
                 Migration `0006` re-keys `Extension`, `ExtensionSecret` and
                 `OauthToken` to `(owner_id, name)`, `owner_id` NOT NULL with
                 ON DELETE CASCADE: enablement and credentials are per user.
                 The catalog, the draft queue, the approval snapshots and the
                 plugin clones stay deploy-wide.
application/     ports.py  dto.py
                 services/  approval_service  compose_service  extension_service
                            oauth_service     plugin_service   admin_service  queries
infrastructure/  catalog_repository  file_catalog_writer  git_installer  mcp_prober
                 oauth_client        plugin_reader        pending_states
                 redis_pending_states  refresh_locks      secret_key
                 repositories/  extension_repository  oauth_token_repository
                                pg_secrets  pg_drafts  pg_snapshots
presentation/    dependencies.py (get_extensions + the three guards)  schemas.py
                 agent_tool.py  cli.py (the `drafts` subcommands)
                 routers/  connectors.py  plugins.py  drafts.py — a `router: APIRouter`
                           each, all three tagged `extensions`
wiring.py        the composition: infrastructure + application -> the three routers
```

`wiring.py` sits outside the four layers on purpose: presentation and infrastructure are
siblings under the `extensions layers` contract and may not import each other, so
something has to introduce them.

## Public surface

### Ports consumed (`application/ports.py`)

| Port | Methods |
|---|---|
| `CatalogReader` | `load_all(root=None)`, `get(name, root=None)`, `is_repo_entry(name)` |
| `CatalogWriter` | `promote(m: Manifest, raw: bytes) -> None` |
| `DraftStore` | `async save(raw, *, owner_id=None)`, `async pending()`, `async rejected() -> [(kind, name, reason)]`, `async read(kind, name) -> str \| None`, `async delete(kind, name)` |
| `SnapshotStore` | `async write(kind, name, raw)`, `async read(kind, name)`, `async clear(kind, name)` |
| `SecretStore` | `async load(*, owner_id) -> dict[str, str]`, `async names(*, owner_id)`, `async put(name, value, *, owner_id)`, `async forget(name, *, owner_id)` |
| `PluginInstaller` | `async clone(m, timeout=120.0) -> Path`, `remove(name)` |
| `PluginReader` | `components(root)`, `bundled_servers(root)`, `local_servers(root)`, `surface(m, root)`, `is_present(home, m)` |
| `ServerProber` | `async __call__(home, name, spec, timeout=60.0) -> ProbeResult` |
| `PendingStateStore` | `async issue(name, owner_id, verifier, now) -> str`, `async take(state, owner_id, now) -> Pending \| None` |
| `RefreshLocks` | `__call__(name) -> async context manager` — one lock per OWNER per connector, around an OAuth refresh; the caller builds the name `f"{owner_id}:{name}"` |
| `ExtensionRepository` | `add`, `get`, `list`, `enabled`, `set_state`, `set_error`, `set_tools`, `remove` — every one `*, owner_id` — plus `async owners_of(name) -> list[int]`, the one unscoped read, for the two clone-level events |
| `TokenRepository` | `async get(name, *, owner_id)`, `async put(name, *, owner_id, now, **fields)`, `async delete(name, *, owner_id)` |
| `OAuthWire` | `async discover_issuer(issuer, resource="", scopes=())`, `async refresh(disc, *, refresh_token, client_id, client_secret, now)` |
| `OAuthRefresher` | `async ensure_fresh(home, tokens, m, now=None, *, owner_id)` |
| `PromptCacheInvalidator` | `async __call__(owner_id) -> None` — that owner's lanes, not every lane |
| `AccessCheck` | `async is_owner(user_id) -> bool` |

### Ports provided (what other modules consume)

| Capability | Object | Signature |
|---|---|---|
| `ScopeComposer` | `ComposeService` | `async for_scope(scope: str) -> Composed` — `Composed(mcp_servers, tool_names, plugins, errors)`. Consumed by turns (`TurnService`'s `ExtensionComposePort`, via `core.app_context.ExtensionCompose`) and scheduler (`SdkJobExecutor`). |
| `ExtensionsQuery` | `ExtensionsQueryService` | `async enabled_plugin_roots() -> list[tuple[str, Path]]` — consumed by turns' `SlashSourcePort`, via `http.deps.PluginSlashSources`. |
| `ExtensionAdminPort` | `ExtensionAdminService` | `async handle(action, *, name, kind, manifest, user_id, is_owner) -> str` — the `ext_manage` body, consumed by turns. |
| chat commands | `PgSecrets` + `ApprovalService` + `CatalogRepository` | telegram's `CommandService` declares `SecretStorePort`, `ApprovalPort`, `CatalogPort` and `CatalogWriterPort`, all **async** since Wave 3a. `ApprovalService` satisfies `ApprovalPort` directly now that it takes no `home`, and `core.app_context.BoundApprovals` is gone — the composition root passes the service straight through. `TranslatingSecrets` stays, because it translates this module's `SecretNameError` into telegram's, and its `put` is `async` and awaits the store. |
| operator CLI | `presentation/cli.py` | `await cli.run(argv, drafts)` — `main.py`'s `drafts list\|show\|delete` subcommands. |

### Routes

Four `router: APIRouter(tags=["extensions"])`, one per file in
`presentation/routers/{connectors,plugins,drafts,secrets}.py`. Paths, `operation_id`s and error
`code` strings are frozen (spec req. 36) — a generated client names its methods after
the `operation_id`.

| Method | Path | `operation_id` | Gate |
|---|---|---|---|
| GET | `/api/connectors` | `extensions_list_connectors` | any user |
| POST | `/api/connectors` | `extensions_create_connector` | owner (201) |
| GET | `/api/connectors/oauth/callback` | `extensions_oauth_callback` | owner, refused as a **redirect** |
| POST | `/api/connectors/{name}/secrets` | `extensions_put_connector_secrets` | owner (204) |
| POST | `/api/connectors/{name}/connect` | `extensions_connect_connector` | owner |
| POST | `/api/connectors/{name}/oauth/start` | `extensions_start_connector_oauth` | owner |
| POST | `/api/connectors/{name}/test` | `extensions_test_connector` | owner |
| POST | `/api/connectors/{name}/enable` | `extensions_enable_connector` | owner |
| POST | `/api/connectors/{name}/disable` | `extensions_disable_connector` | owner |
| DELETE | `/api/connectors/{name}` | `extensions_delete_connector` | owner (204) |
| GET | `/api/plugins` | `extensions_list_plugins` | any user |
| POST | `/api/plugins/{name}/install` | `extensions_install_plugin` | owner |
| POST | `/api/plugins/{name}/install/confirm` | `extensions_confirm_plugin_install` | owner |
| POST | `/api/plugins/{name}/enable` | `extensions_enable_plugin` | owner |
| POST | `/api/plugins/{name}/disable` | `extensions_disable_plugin` | owner |
| DELETE | `/api/plugins/{name}` | `extensions_delete_plugin` | owner (204) |
| GET | `/api/drafts` | `extensions_list_drafts` | any user |
| POST | `/api/drafts` | `extensions_create_draft` | owner (201), `kind: plugin` only |
| POST | `/api/drafts/{name}/review` | `extensions_review_draft` | owner |
| POST | `/api/drafts/{name}/approve` | `extensions_approve_draft` | owner |
| DELETE | `/api/drafts/{name}` | `extensions_discard_draft` | owner (204) |
| GET | `/api/secrets` | `extensions_list_secrets` | owner |
| PUT | `/api/secrets/{name}` | `extensions_put_secret` | owner (204) |
| DELETE | `/api/secrets/{name}` | `extensions_delete_secret` | owner (204) |

Registration order is load-bearing: the OAuth callback is declared *before* the
`/api/connectors/{name}/…` routes so `oauth` is never taken for a connector name, and
the plugins router declares its fixed segments before the bare `/{name}`, whose only
method is DELETE. There is deliberately **no** `POST /api/plugins`.

The owner gate is `core.auth.require_owner`: a route dependency where the handler does
not need the object, a parameter (`user: CurrentUser = Depends(require_owner)`) where
it does. Route dependencies are solved first, so a non-owner is refused **before** the
catalog is asked for a manifest — the order `owner_only(request)` had as every
handler's first line. The OAuth callback is the exception: a top-level navigation must
come back as `?error=forbidden`, so it takes `current_user` and decides itself. The
secrets router puts that gate on the `APIRouter` itself rather than per route: every
path on it, the read included, is owner-only, and a listing of what credentials a
deployment holds is not something a plain user may read.

`GET /api/secrets` is the one place the stored names are listed, and it lists what the
catalog declares beside them (`set: false`, `updated_at: null`) so the reply is what
the deployment *needs*, not only what it already has. It reads `SecretStore.names()` —
the ciphertext is never opened, so no response here can carry a value, and a rotated
`CH_SECRETS_KEY` still lists correctly. Declared-but-unset **oauth** names are left out:
those arrive by signing in at the provider, never by typing.

Error codes the SPA depends on: `secrets_missing` (carries a `missing` array),
`oauth_required`, `probe_failed`, `install_failed`, `local_server`, `not_fetched`,
`stale_install`, `stale_draft`, `shadowed`, `name_taken`, `unverified`, `write_failed`,
`no_draft`, `bad_manifest`, `bad_kind`, `bad_request`, `forbidden`, `not_found`.

### Dependencies

`presentation/dependencies.py`, built on `core.di.provide`:

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_extensions` | `extensions` | `ExtensionsDeps` |

One provider, not eighteen: the *bundle* attribute these routers read is `extensions`
— the only `provide("<name>")` in this module — and what it holds is the
`ExtensionsDeps` view of the whole module. At composition
`http.deps.extensions_view(ctx.extensions, cfg=, catalog_root=, invalidate=)` builds
it out of the `ExtensionsBundle` `main.build_app()` already made (`wiring.build`),
so an install and the turn that has to forget its prompt prefix talk about the same
services. The view carries:

```
cfg  home  catalog_root  catalog  secrets  store  tokens  writer  pending
drafts  approvals  extensions  plugins  reader  probe_fn  installer  oauth  invalidate
```

`cfg` is read for `web_public_origin`, `ext_probe_timeout_seconds` and
`plugin_clone_timeout_seconds` only. `invalidate` is `ctx.runner.reset_all` — the
extensions invalidator, every lane's prefix, because an install changes every
lane's toolset. The three routers are mounted when `deps.extensions is not None`.
`tests/modules/extensions/test_extensions_router.py` swaps the whole view with
`app.dependency_overrides[get_extensions]`.

Three more dependencies sit on top of that one, each the per-request helper every
handler used to open with: `get_connector` and `get_plugin` (`manifest_or_404`, `404
not_found`) and `draft_name` (drafts' `named` — `valid_name` before a route param
reaches an unlink, `404 no_draft`).

**Request bodies** (`presentation/schemas.py`, the only place Pydantic may appear):
`ConnectorCreate` for `POST /api/connectors`; the other three bodies stay `RawJsonBody`
(`Annotated[Any, Body()]`) because their handlers open with their own
`isinstance(body, dict)` branch answering a frozen code — a model would answer
`400 bad_request` from the shell's validation handler instead.

### Tables (migration `0003`)

Three tables this module owns outright, next to `extensions` and `oauth_tokens`:

| Table | Key | Columns | Why it is a table |
|---|---|---|---|
| `extension_secrets` | `(owner_id, name)` | `ciphertext BYTEA`, `updated_at` | The Fernet token is bytes; round-tripping it through TEXT is one encoding assumption away from an undecryptable row. The key is `CH_SECRETS_KEY`, held in the environment and popped before anything can spawn the CLI, so the ciphertext and the key are never in the same place. |
| `catalog_drafts` | `(kind, name)` | `raw TEXT`, `status` (`pending`/`rejected`), `reason`, `owner_id` (Wave 4a), `created_at` | `raw` is the YAML exactly as submitted — never a re-serialisation — because the approval token is `sha256(raw)[:8]`. A draft the validator has since tightened against becomes `status='rejected'` with the validator's own words, so an unapprovable request is visible rather than silently gone. |
| `approval_snapshots` | `(kind, name)` | `raw BYTEA`, `created_at` | The bytes a human was shown, held between the two halves of `/approve`. Storing the parsed manifest would make the token check compare a manifest to itself. |

`PgSecrets` takes a `core.crypto.SecretBox`, never a key string. `main.build_app`
builds ONE box while `CH_SECRETS_KEY` is still in the environment — `run()` and the
worker startup pop it moments later — and passes it as `wiring.build(..., box=box)`;
every store in the bundle, including the one `OAuthService` refreshes tokens
through, holds that object. `infrastructure/secret_key.secret_box()` is the
fallback `wiring.build(box=None)` reaches (one generated key, cached for the
process, loud): a test path, since `run()` refuses to boot without the variable.
The `bind()` that used to hand the box to call paths building their own stores went
in Step 5 with those paths.

### Tools

`mcp__hermes__ext_manage` — description, schema and handler in
`presentation/agent_tool.py`; registration and the gating rule stay in turns. The
description text is a prompt-cache invariant.

### Events

None emitted yet. The prompt-cache reset is still an awaited call through the
`PromptCacheInvalidator` port; the event (`core.events.ExtensionSetChanged`, with
turns subscribing) does not exist — Wave 2b was the `APIRouter` wave and did not
add it, so it is still ahead of us (Wave 3, with the rest of `core.events`).

## Invariants

1. **A plugin clone lives at `CH_HOME/plugins/<name>`, deploy-wide** (Wave 4a,
   D8) — `domain/paths.plugin_dir` resolves `PLUGINS_SUBDIR` against `home`, not
   against `home/agent_home`. `agent_home` became one tree per user in this wave;
   a plugin did not, because an install changes the cached prompt prefix and the
   MCP servers of EVERY turn, so it is the owner's decision and the owner's
   credentials. Left under the owner's `agent_home`, a non-owner's turn could only
   load a plugin if its read guard admitted the owner's whole tree — exactly the
   boundary this wave draws. `Workspace.ensure()` performs the one-time
   `os.replace(home/"agent_home"/"plugins", home/"plugins")` for a live volume,
   logged at WARNING with both paths and the entry count; the losing concurrent
   `os.replace` catches its `OSError` and re-checks the destination. Nothing in
   the shipped `bundle/`, `skills/` or `agents/` names the old clone path
   (checked). The clone stays shared and only the owner may fetch or delete it;
   a second user *adopts* it (`ExtensionService.adopt`), which writes their own
   enablement row and no new executable.
2. **Agent proposes, human promotes.** `CatalogReader` has no way to see the draft
   queue; `DraftStore` has no `promote`. Since Wave 3a the queue is a table the
   reader has no connection to at all, and `scripts/approve_draft.py` — the only
   writer of `catalog/` — runs on the host and reads the queue through
   `docker compose exec -T agent python -m claude_hermes.main drafts show`.
3. `/approve` and `/secret` are intercepted by the gateway **before** `run_turn` and are
   never MCP tools. `/secret` deletes the carrying message even when the request is
   denied.
4. Promotion goes through `CatalogWriter.promote(m, raw)` on all three channels
   (chat, browser, host script).
5. **An approval is bound to bytes.** `begin` snapshots the draft and returns
   `token_of(raw)`; `confirm` promotes the *snapshot*. Plugins do the same with
   `surface_token` recomputed from the tree on disk at confirm time.
6. **Anti-downgrade:** `is_repo_entry(name)` refuses a user-catalog write that would
   shadow a reviewed entry — checked in `POST /api/drafts`, `POST /api/drafts/{name}/approve`,
   `POST /api/connectors`, and both steps of the chat `/approve`.
7. **Post-promotion verification:** the catalog is re-read after a write; a manifest that
   cannot be read back answers `unverified` / `write_failed` and the draft stays queued.
8. Routes are not tools. Every mutation is owner-only; there is no `POST /api/plugins`;
   route ordering comments are load-bearing.
9. `valid_name()` guards every route param that reaches a path (`rmtree`, `unlink`) —
   at the edge and again in the service. Kept after the queue moved into the
   database: `DELETE /api/plugins/{name}` still reaches an `rmtree`, and a name is
   still the wrong thing to trust.
10. A plugin whose bundled `mcpServers` would start a process in our container is refused
   at install (`400 local_server`) and its clone removed.
11. **Secrets never surface.** `connector_to_wire` emits `{"name", "prompt", "set": bool}`.
12. `last_error == "needs_reauth"` is the exact string the connectors tab parses.
13. The prompt-cache reset fires **after** the registry row or catalog entry is written,
    never before.
14. An stdio server's `command` must be in `{npx, uvx, python}`; a plugin's `ref` must be
    a release tag or a full sha; `repo` and `url` must be `https://`.
15. **Wave 4a: the draft queue is deploy-wide and `catalog_drafts.owner_id` is
    audit only.** Both proposers pass it — `ext_manage` from `user_id` (the tool is
    owner-gated and fails closed, so the sender IS the owner) and `POST /api/drafts`
    from `require_owner`'s user — and nothing branches on it. It is deliberately not
    a scope on the reads: an install changes the cached prompt prefix and the MCP
    servers every turn composes, so approval is the owner's decision (D8), and a
    queue partitioned by proposer would let one user hide a draft from the person
    who has to approve it. `put_raw` COALESCEs the column so `_reject` — which
    re-puts a row it read back without one — cannot erase who proposed it.
16. **Extensions, their secrets and their OAuth tokens are per user** —
    `PRIMARY KEY (owner_id, name)` on all three tables since migration `0006`,
    `owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE`. This
    supersedes D8's "they stay deploy-wide": what is shared is what says something
    *exists* — the catalog, the draft queue, the approval snapshots, the plugin
    clones — while enablement and credentials belong to a person. **Every port
    takes `*, owner_id: int`, required, keyword-only, with NO default**, so a call
    site that forgets one is a `TypeError` rather than a silent read of the deploy
    owner's credentials; `owner_id: int = 0` anywhere in this module is a bug.
    `ComposeService.for_scope(scope, *, owner_id)` is fed `ctx.owner_id` on a turn
    (the LANE owner, never the sender) and `job["owner_id"]` on a routine.
    `ext_manage` mutations stay owner-gated and fail closed; only its reads became
    per-lane-owner. `installed_by` stays audit, now within one owner's row.

## Known debts

| Debt | Paid by |
|---|---|
| ~~The `extensions/` legacy package: alias shims over the infrastructure modules, the `compose` / `manage` / `store` / `tokens` / `usercatalog` adapters (`ExtensionStore(db)`, `TokenStore(db)`, the positional `for_scope` / `connect` heads), `web/{connectors,plugins,drafts}.py` with their `routes(deps)`, `wiring.{connectors,plugins,drafts}_routes`, `wiring.web_deps`, `wiring.LegacyOAuthRefresher`, the module-level `oauth_client.ensure_fresh`, `secret_key.bind`, the `.engine` properties on the two SQL repositories.~~ **Deleted in Step 5.** `main.build_app()` builds `SqlExtensionRepository(engine)` and `SqlOauthTokenRepository(engine)` and hands them to `wiring.build(store=, tokens=)`, which binds `OAuthService(wire=oauth_client.http_wire, secrets=, locks=)` directly; the routers get `OAuthOps()`; `http.deps.extensions_view` is the one builder of `ExtensionsDeps`. | done |
| ~~`presentation/agent_tool.py` is written but nothing imports it.~~ Closed in Wave 2b: `TOOL_NAME`/`TOOL_DESCRIPTION`/`TOOL_SCHEMA` here are the only definition. `main.py` puts them on `core.app_context.ExtensionAdminTool`, `hermes_server.declared_spec` reads them off the port, and the server keeps no copy — an undeclared port raises. | done |
| ~~`PendingStates` is process memory and `oauth_service._refresh_locks` a module global.~~ Closed in Wave 3a: `infrastructure/redis_pending_states.py` (10-minute TTL) and `infrastructure/refresh_locks.py` (`lock:oauth:<owner>:<name>`) are the redis-mode implementations, behind the `PendingStateStore` and `RefreshLocks` ports; the in-memory pair stays for inline mode. | done |
| ~~`SecretStore` is a 0600 env file on the volume; `scripts/approve_draft.py` reads the drafts queue straight off the host filesystem.~~ Closed in Wave 3a: `PgSecrets`/`PgDrafts`/`PgSnapshots` over the `0003` tables, and the host script reads the queue through the app. | done |
| ~~`infrastructure/secret_key.py` falls back to a **process-ephemeral** Fernet key when `CH_SECRETS_KEY` is unset.~~ Paid in Phase C: `main.run` and the worker startup call `require_secrets_key()` and refuse to boot without the key, and `build_app` builds ONE box from the pre-pop environment and passes it as `wiring.build(box=)`, so every store shares it instead of re-reading an environment the pop has emptied. The generated fallback (`secret_box()`) is now reachable only by a test or a direct `build_app()` call. | Wave 3a |
| `core.workspace` still creates `catalog-drafts/` and `pending-approvals/` and still exposes `secrets_path`; nothing reads them any more. `scripts/import_data_dir.py` is the one-shot that carries an existing `data/` into the tables, and it deletes nothing. | open — a workspace cleanup, not a shim |
| `PgDrafts.rejected()` re-validates on read and *writes* the verdict back (`status='rejected'`), so a read path performs a write. It is idempotent and bounded by the queue size, but a listing route doing that is worth revisiting once the validator stops moving. | Wave 4 |
| `ComposeService` takes `home` and `root` as data rather than a `Workspace`; `ExtensionService.connect`/`remove` still need `home` for the prober and the clone. | Wave 3 |

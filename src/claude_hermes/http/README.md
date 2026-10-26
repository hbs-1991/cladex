# `http/` — the HTTP composition root

The FastAPI app is assembled here and nowhere else. It is a composition root
beside `main.py` and `workers/` rather than a module — the fourth root, added
deliberately in Step 5 (the spec said "in `core/` (or `main.py`)", but `core/`
may not import a module and `build_web_app` imports seventeen routers, while
`main.py` is the process root and already long). So the HTTP assembly gets the
same standing the queue assembly has (`workers/taskiq_app.py`): a package that
sits *above* the modules and imports downwards only. The import-linter layers
contract spells the three roots as one layer, `main : http : workers` (`:`, not
`|` — they may import each other: `main` imports `http.app` and `workers.tasks`,
`workers.taskiq_app` imports `main` lazily), over `modules`, `integrations`,
`core`. The package name shadows nothing: `claude_hermes.http` is the only way
to reach it, and no `sys.path` entry in the repo puts `src/claude_hermes` itself
on the path.

Before Step 5 the same code lived at `web/{app,deps,errors}.py`; `web/errors.py`
folded into `app.py` (`error_response` is the one survivor — `ApiError` went,
every module router aliases `ApplicationException` itself).

## Files

- `app.py` — `build_web_app(ctx_or_deps, extra_routes=None, **kwargs)`. Takes an
  `AppContext` (through `deps.web_deps`) or a ready `WebDeps` (the tests) and
  mounts every module's `presentation.router`, in the order the paths need
  (`turns` before `threads`), each behind the one mounting condition the shell
  decides — `mount(module, enabled=...)`, the whole of the mounting policy:
  `identity`, `turns`, `voice` and `commands` always; `threads` when
  `deps.threads is not None`; `files` when `deps.file_store is not None`;
  `routines` when `deps.scheduler is not None`; `content` when
  `deps.content is not None` (`content_enabled` — off, `/api/content*` is the
  catch-all's 404, which the SPA reads as "content disabled"); `connectors`, `plugins` and
  `drafts` when `deps.extensions is not None`; `skills` when
  `deps.learning is not None` (the `LearningScopes` registry — every skills
  provider resolves the requesting user through it); `wiki` when
  `deps.wiki_root is not None`; `telegram` when `telegram.webhook_enabled(cfg)`.
  The routers are imported inside the function, not at module scope, so `app`
  stays importable while a router is a stub.
  Around them: `TransportPolicyMiddleware` (an exact `Origin` and the route's
  content type on every `/api/*` mutation), `core.auth.CurrentUserMiddleware`
  (the session cookie, re-checked against `acl.admitted` per request), the
  `/api/{rest}` 404 catch-all, `/healthz`, the SPA mount, and the two exception
  handlers that render `ApplicationException` and a validation failure as the one
  error shape `error_response` produces: `{"error": {"code", "message"}}`.
  Validation failures answer `400 bad_request`, never FastAPI's 422.
- `deps.py` — `WebDeps`, the bundle stored on `app.state.deps`; `web_deps(ctx)`
  builds it from the context; `thread_guard`, `PluginSlashSources` and
  `extensions_view` are the three adapters between a module's port and the object
  the context holds.

## The bundle contract

A module's providers read the bundle **by attribute name** through
`core.di.provide("<name>")`, and `WebDeps` carries the names some provider reads
plus the three the shell itself uses — nothing else (Step 5 dropped `db`,
`job_store`, `ext_store`, `library`, `skills_dir`, `sidecar`, `usage` and
`logs_dir`: owner-scope objects no router had read since 4b). The name is the
contract; each module's README lists the ones it reads, and this table is the
union of the `provide("<name>")` calls in the ten `presentation/dependencies.py`
files:

| Attribute | Read by (`provide` calls) |
|---|---|
| `cfg` | identity (`web_session_days`, `web_secret`, `web_public_origin`), learning (`backup_keep`), telegram (`telegram_mode`, `telegram_webhook_secret`) |
| `runner` | learning (`reset_owned`, bound per request to the caller's scope); turns publishes `get_runner` but no turns route reads it |
| `acl` | identity, and the shell's `CurrentUserMiddleware` (`acl.admitted`) |
| `bus`, `turn_events`, `turns`, `auto_title`, `broker`, `voice`, `home`, `slash_sources`, `sdk_commands` | turns |
| `owned`, `file_store` | turns and files |
| `threads` | sessions |
| `visibility` (a property over `threads`) | files' lane guard |
| `scheduler` | scheduler |
| `content` | content — the plan's `ContentService`; `None` unless `content_enabled`, and then the router is not mounted |
| `extensions` | the three extensions routers — one provider over the `ExtensionsDeps` view `extensions_view` builds at composition |
| `learning` | learning — the `LearningScopes` registry every skills provider resolves the requesting user through |
| `wiki_root` | memory |
| `telegram`, `update_dedup` | telegram's webhook route |
| `login_key`, `webapp_key`, `bot_username`, `now` | identity |
| `dist_dir` | the shell's SPA mount (no provider) |
| `invalidate`, `catalog_root` | no provider; the shell's own inputs to the extensions view, mirrored on the bundle so `tests/web_helpers.py` can build the view from them. `invalidate` is `runner.reset_all` — the EXTENSIONS invalidator only, because an install changes every lane's prefix; a skill edit uses learning's own `reset_owned` |

A test swaps a collaborator by overriding the module's provider
(`app.dependency_overrides[get_files] = lambda: fake`), never by rebuilding the
bundle; `tests/web_helpers.py::make_client` builds a `WebDeps` over the test engine
with real module objects and one fake runner.

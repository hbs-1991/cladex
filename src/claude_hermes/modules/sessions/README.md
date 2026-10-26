# `modules/sessions`

## Purpose

A **session lane** is one conversation: a `web:` browser thread, a private
Telegram chat, a forum topic, or a `cron:<id>` job lane. This module owns three
things about a lane and nothing else:

- **the SDK session** — whether the lane's Claude session is still the same
  conversation, or has idled/crossed the daily boundary and must start over
  (`SessionRouter`);
- **which model the lane runs** — the one gatherer behind
  `core.model_catalog.resolve()`, and the one writer of a lane's explicit
  (model, effort) (`SessionService`);
- **the thread** — the metadata that must outlive a session reset (title,
  channel, owner, archived, `has_turns`), plus the rule that decides who may see
  which lane (`ThreadService`, `domain/policies.visible`);
- **the recall corpus** — the `messages` table behind `mcp__hermes__session_search`
  (`MessageRepository`).

Naming a thread from its first message lives here too (`ThreadTitleService` +
`SdkTitleGenerator`), because the title is a property of the thread.

The six session-key functions are **not** here: they are an application-wide
grammar (`core/session_keys.py`), re-exported by `domain/keys.py`.

## Layout

```
domain/
  models.py      Session, Message, Thread (SQLAlchemy ORM; the tables). Wave 4a
                 adds `sessions.owner_id` and `messages.owner_id`, and gives the
                 existing `threads.owner_user_id` its FK to `users(id)`
                 (ON DELETE SET NULL, nullable). The owner of a lane is
                 `core.session_keys.lane_owner` (D3) — the same rule the workspace
                 resolver and migration 0005 apply, so they cannot disagree.
                 `messages.owner_id` is denormalised on purpose: recall must stay
                 ONE GIN query (`owner_id = :uid AND content_tsv @@ …`).
  keys.py        re-export of core.session_keys — the key grammar
  policies.py    visible(thread, user_id, is_owner)
  constants.py   TITLE_PROMPT, TITLE_MAX_WORDS, TITLE_MAX_CHARS, MANUAL_TITLE_MAX_CHARS
  dto.py         MessageHit / SEARCH_FIELDS — the frozen recall wire shape
application/
  ports.py       SessionStore, ThreadStore, MessageStore, TitleGenerator,
                 EventPublisher, LaneActivity, OwnerDefaults
  services.py    SessionRouter, SessionService, ThreadService, ThreadTitleService
infrastructure/
  repositories.py     SessionRepository, MessageRepository, ThreadRepository (AsyncEngine)
  title_generator.py  SdkTitleGenerator, _clean (the one-shot model call)
presentation/
  router.py       router: APIRouter(tags=["sessions"]) - the eight routes, PATH_PREFIX
  schemas.py      to_wire(thread, running), model_wire, detail_wire, ThreadPatch
                  (the rename body), ThreadModelPut (the model body)
  dependencies.py get_threads -> ThreadService, get_lane_models -> SessionService,
                  owned_thread -> the thread or 404
```

`domain/models.py` carries 0008's four columns: `threads.model`/`threads.effort`
(what a person CHOSE for the lane — on the thread so it outlives `/new` and every
reset) and `sessions.model`/`sessions.effort` (what actually RAN the lane's
current session, written from `AssistantMessage.model`). 0009 adds
`sessions.shell_enabled` (nullable BOOLEAN): whether the lane's CURRENT session
was started with a shell — the turn's `TurnContext.has_shell`, stamped here and
read back by the next turn (invariant 25).

## Public surface

### Ports consumed (declared in `application/ports.py`)

| Port | Methods | Satisfied by |
|---|---|---|
| `SessionStore` | `get`, `upsert(..., *, index_mtime=None, owner_id=None, model=None, effort=None, shell_enabled=None)`, `touch(..., *, index_mtime=None, owner_id=None, model=None, effort=None, shell_enabled=None)`, `set_session_pair`, `owner_of`, `reset`, `reset_all`, `reset_owned(owner_id, *, include_unowned=False)` | `SessionRepository(engine)` |
| `ThreadStore` | `upsert(..., *, talked=False)`, `get`, `list`, `set_title`, `set_archived`, `model_of`, `set_model` | `ThreadRepository(engine)` |
| `MessageStore` | `record(..., *, owner_id=None)`, `last`, `search(query, limit, *, owner_id=None)`, `last_ts(*, owner_id=None)` | `MessageRepository(engine)` |
| `TitleGenerator` | `async generate(text) -> str` | `SdkTitleGenerator(model)` |
| `EventPublisher` | `async emit(session_key, turn_id, type, payload)` | turns' `TurnBus` |
| `LaneActivity` | `is_running(session_key) -> bool` | turns' `TurnBus` |
| `OwnerDefaults` | `async default_of(user_id) -> (model, effort)` | identity's `AccessControl` |

### Ports provided (what other modules receive at composition)

- `SessionRouter` satisfies turns' **`SessionLane`**:
  `route(session_key, now)`,
  `bind(session_key, sdk_session_id, system_append, now_iso, *, index_mtime=None, owner_id=None, model=None, effort=None, shell_enabled=None)`,
  `touch(session_key, now_iso, *, index_mtime=None, owner_id=None, model=None, effort=None, shell_enabled=None)`,
  `owner_of(session_key)`, `get(session_key)`, `alive(row, now)`,
  `set_session_pair(session_key, model, effort)`, `reset(session_key)`,
  `reset_all()`, `reset_owned(owner_id, *, include_unowned=False)`,
  `is_unbound(session_key)`.
- `SessionService` satisfies turns' and telegram's **`LaneModels`**:
  `effective(session_key, owner_id=None, *, now=None) -> core.model_catalog.Choice`
  and `set_model(session_key, model, effort) -> (model, effort)` (raises
  `core.exceptions.NotFound` for a lane with no thread row and
  `ValueError("bad_model" | "bad_effort")` for a write the resolver would ignore).
  Also `offered_models() -> list[ModelInfo]` and `offered_ids() -> list[str]`.
- `ThreadService` satisfies turns'/scheduler's **`ThreadRegistry`**:
  `touch(session_key, channel, owner_user_id, now, *, talked=False)`; and files'
  **`ThreadVisibility`**: `get_visible(session_key, user_id, is_owner) -> dict`
  (raises `core.exceptions.NotFound`). Also `get`, `list_visible`, `create_web`,
  `set_title`, `set_archived`, `is_running`.
- `ThreadTitleService` satisfies **`ThreadTitles`**: `autotitle(session_key, text)
  -> asyncio.Task | None` and `apply(session_key, title)`.
- `MessageRepository` satisfies turns' **`MessageLog`** (`record`, `last`,
  `last_ts`) and **`MessageSearch`** (`search`), and learning's `last_ts`.

### Routes — `router: APIRouter(tags=["sessions"])`, in registration order

| Method | Path | `operation_id` | |
|---|---|---|---|
| GET | `/api/threads` | `sessions_list_threads` | paged list, `?archived=1&before=<ts>&limit=<n≤200>` |
| POST | `/api/threads` | `sessions_create_thread` | create a web lane → 201 |
| POST | `/api/threads/{id:path}/archive` | `sessions_archive_thread` | 204 |
| POST | `/api/threads/{id:path}/unarchive` | `sessions_unarchive_thread` | 204 |
| PUT | `/api/threads/{id:path}/model` | `sessions_set_thread_model` | `ThreadModelPut`, 400 `bad_model` / `bad_effort` |
| GET | `/api/threads/{id:path}` | `sessions_get_thread` | |
| PATCH | `/api/threads/{id:path}` | `sessions_rename_thread` | `ThreadPatch`, 400 `bad_request` when empty |
| DELETE | `/api/threads/{id:path}` | `sessions_delete_thread` | the `archive` handler again, 204 |

Wire shape (frozen): `{id, title, channel, archived, running, last_event_at,
created_at}`. **`GET …/{id}` additionally** carries `{model, effort,
effective_model, effective_effort}` — the lane's explicit pair (both `null` when
it has none) and what its next turn will actually run. The LIST route does not:
a per-row model would be a session read and an owner-default read for every line
of the sidebar. `PUT …/{id}/model` takes `{"model": str|null, "effort": str|null}`
and answers with those four keys alone; `model: null` clears the lane's choice
(the effort goes with it) and redirects its live session to the lane's default.

### Dependencies (`presentation/dependencies.py`, built on `core.di.provide`)

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_threads` | `threads` | `ThreadService` |
| `get_lane_models` | `lane_models` | `SessionService` |

That is the whole list. `threads` was the whole list when a `routes(deps)` closure
read `deps.threads` (Wave 2b); `lane_models` is the model switcher's addition and
the composition root wires it beside `threads`. `owned_thread(id, threads=Depends(get_threads),
user=Depends(current_user)) -> dict` is the module's second dependency and the line
every per-thread route used to start with; `id` binds to the route's own path
parameter, so one dependency serves `/api/threads/{id:path}` and
`…/{id:path}/archive`. **Whether this router is mounted at all is the shell's
decision** (`deps.threads is not None`), so `provide`'s 503 guard is unreachable in a
correctly assembled app.

### Tools / events

No agent tools. Emits two events through `EventPublisher`, both with an empty
`turn_id` because neither came from a turn: `thread.title` with `{"title": …}`
and `thread.model` with `{"model", "effort"}` — the lane's EXPLICIT pair only.
What a lane resolves to depends on the reader's own owner default, so a tab that
sees `thread.model` re-reads `GET /api/threads/{id}` for the `effective_*`.

## Invariants

1. **The app shell must register the turns router BEFORE this one.**
   `/api/threads/{id:path}` swallows `/api/threads/{id}/messages`.
2. **A foreign thread is 404, never 403.** A 403 would confirm that a lane with
   that key exists (`ThreadService.get_visible`).
3. **Paging filters in Python AFTER the SQL LIMIT, and keeps pulling batches.**
   A user whose only lane sits behind 50 group lanes must not get an empty page
   and a null cursor. Do not push `visible` into SQL naively.
4. **`has_turns` only ever goes up.** `ThreadStore.upsert` ORs the flag; a plain
   touch (a title fetch, a topic being provisioned) cannot un-say a turn.
   Creating a lane is not taking a turn in it: `create_web` leaves it false.
5. **An automatic title never overwrites a typed one** (`set_title(..., manual=False)`
   carries `WHERE title_manual = 0`).
6. **The titler must run BEFORE the turn.** `SessionRouter.is_unbound` is true
   only while the lane has no session row; once the turn creates it the thread no
   longer looks new.
7. **The title call is hand-built, not `base_options()`** — no cwd, no project
   setting sources, `tools=[]`, a bare-string system prompt. Every one of those
   is load-bearing; the comments in `title_generator.py` say why.
8. **`TITLE_MAX_CHARS = 128` is Telegram's forum-topic name limit.** One title is
   both a DB value and a topic name; changing it here silently changes Telegram.
9. **Task sets are held on the service instance**, never on a module global.
10. **Repositories take an `AsyncEngine`**, never a facade over one.
11. **The listing query is tolerant, and the conversion to typed parameters did not
    change that.** `archived` is a `str | None` compared to `"1"` (a `bool` parameter
    would answer 400 for `?archived=maybe`) and `limit` is `min(limit, 200)` in the
    handler (`Query(le=200)` would answer 400 for `?limit=500`). Neither refusal ever
    existed. `ThreadPatch.title` is `Any` with a `""` default for the same reason: the
    empty/whitespace case is the router's own `400 bad_request` / "title is required",
    not a schema's wording. `tests/modules/sessions/test_sessions_router.py` walks all
    three.
12. **Wave 4a — `owner_id` is decided by the caller, never here.** The rule is
    `core.session_keys.lane_owner(session_key, channel, user_id, deploy_owner)` and
    it needs a deploy owner, which this module may not know (it would mean
    importing identity). So the turn runner resolves it once and hands it to
    `bind`/`touch` and to `threads.touch`; the same value is what the turn's
    workspace is resolved from, which is why the row and the running turn cannot
    disagree about a lane.
13. **`owner_id` follows the `index_mtime` rule: `None` means "leave it alone",
    never "clear it".** A caller with no owner to offer (an import script) must
    not null a column the turn wrote. `ThreadStore.upsert`
    spells the same rule as `COALESCE(threads.owner_user_id, excluded.owner_user_id)`:
    an existing owner is never overwritten (a lane does not change hands) and a row
    the `0005` backfill could not derive heals on the next turn.
14. **`visible` reads the column; it does not re-derive the owner from the key.**
    Two implementations of one rule is how the sidebar and `sessions.owner_id` drift
    apart. The `is_owner` belt covers only a non-`web:`, non-`cron:` lane whose
    `owner_user_id` is somebody else's or NULL — deliberately not a `web:` lane, so
    the owner never reads another person's browser thread.
15. **Recall is scoped inside the one GIN query.** `search(query, limit, owner_id=)`
    adds `WHERE owner_id = :uid` to the same statement as the text match. Filtering
    after the `LIMIT 8` would turn "eight of A's matches" into "no matches" for B —
    an isolation hole that looks like an empty index.
16. **Wave 4b — `reset_owned` is `reset_all` narrowed to one owner's lanes**
    (`DELETE FROM sessions WHERE owner_id = :o`, plus `OR owner_id IS NULL` when
    `include_unowned`). A skill one user edits changes the index in THEIR cached
    prefix and nobody else's, so a skill mutation evicting every lane would throw
    away every other user's warm cache for nothing (advisor B-4); `reset_all`
    stays for the extensions mutations, which change everybody's toolset.
    `include_unowned` is the deploy owner's belt: a lane bound while
    `deploy_owner()` was still `0` wrote NULL (the runner passes `None` for owner
    `0`), those lanes are the owner's, and only the caller that IS the owner may
    say so — learning's `get_invalidate` passes `include_unowned=scope.is_owner`.
17. **There is one resolution order and it lives in `core.model_catalog.resolve`.**
    `SessionService.effective` only GATHERS: the lane's explicit pair
    (`threads.model/effort`) → the pair its live session runs
    (`sessions.model/effort`) → the lane owner's default through `OwnerDefaults`,
    never on a `lane_is_shared` lane → the deploy default with no effort. A
    channel that re-implemented any part of that order would answer a different
    question from the turn that actually calls the CLI.
18. **"Live" is `SessionRouter`'s own expiry rule, applied WITHOUT deleting.**
    `route` resets an expired lane because the turn that asked is about to start a
    fresh session; `effective` asks the same question as a reader through
    `alive(row, now)`, and a read that dropped the row would turn opening a model
    menu into a lost conversation.
19. **A live session with a NULL pair counts as `(normalize(cfg.model), None)`,
    not as nothing.** Such a session began before `0008` and therefore ran the
    deploy default; reading it as nothing would let a personal default set today
    move a conversation whose cached prefix is already scoped to another model
    (P1).
20. **The pair on `sessions` is what ANSWERED, never what was asked for.**
    `bind`/`touch` are handed `AssistantMessage.model`, so a fallback or a
    substitution leaves the row saying what the session really runs.
    `model=None` on either writer means "nothing to say" and leaves both columns
    alone — the `index_mtime` rule again; `set_session_pair` is the only writer
    that changes them without a turn.
21. **Clearing a lane's pair redirects its live session to the lane's default.**
    `set_model(key, None, …)` writes what `resolve` gives with no explicit pair
    and no session — the owner's default, or the deploy default on a shared lane
    — as the session's pair. Left alone, the session would keep running the model
    just cleared until it expired; nulled, it would read as a pre-0008 session
    (invariant 19) and hide the owner's own default until `/new`. The write does
    not touch `updated_at` (a click is not activity), and the SDK session
    survives: the switch is a priced, shown cold write on the next turn.
22. **Every model write is validated server-side, in the catalog's words.**
    `set_model` calls `core.model_catalog.validate` and raises
    `ValueError("bad_model" | "bad_effort")`, which each channel spells in its
    own: `400 bad_model` / `400 bad_effort` on the route. A lane with no thread
    row raises `NotFound` — there is nothing to hang a choice on, and Telegram
    turns that into "send a message here first".
23. **`PUT …/{id}/model` is registered BEFORE the generic `{id:path}` routes.**
    `{id:path}` matches `web:1:a/model` too, and Starlette serves the first full
    match — the order is what keeps this route reachable the day one of the
    generic routes grows a PUT.
24. **The titler carries `CLAUDE_CODE_NO_MODEL_FALLBACK=1` in its own env.**
    Every other CLI process gets it from `workspace_env()`, but the title call is
    hand-built and never goes near that builder, so without its own copy it would
    be the one place the CLI could still answer on a model nobody chose — on
    `title_model`'s budget.
25. **`sessions.shell_enabled` is carried, never interpreted (0009).** Whether a
    lane has a shell is the turn's decision (`TurnContext.has_shell`, from the
    lane owner's grant); this module only stores it on `bind`/`touch`, returns it
    on `get`/`route` (the whole row), and drops it with the row on every reset.
    It follows the `owner_id` rule — `None` leaves the column alone, a bool
    writes it — because a NULL there is not "unknown", it reads back as "started
    before 0009, under the old deploy-wide rule", which the turn compares
    differently. Nothing here resets a lane when a grant changes: the turn does
    it at its own start (`modules/turns/README.md`), the only place it cannot
    race a running turn's write-back.

## Known debts

Step 5 deleted the legacy surface this module used to serve: `sessions.py`
(`SessionRouter(db, cfg)`, `_DbSessionStore`, `_DbThreadStore`), `web/threads.py`
and its `routes(deps)`, `db.py` (the `Database` facade, every method of which had
been a one-line delegation to `SessionRepository` / `MessageRepository` /
`ThreadRepository` since Phase C), and `presentation/router.routes(deps)`.
`main.build_app()` builds `SessionRepository(engine)` / `ThreadRepository(engine)`
and hands the module objects to the runner; `http.deps.web_deps(ctx)` passes
`owned` (`thread_guard`) and `auto_title` (`titles.autotitle`). `gateway/titles.py`'s
`Titler` moved to telegram (`modules/telegram/infrastructure/titler.py`) with
its collaborators declared as telegram ports — `TitleGeneratorPort`
(`SdkTitleGenerator`), `LaneBindingPort` (`is_unbound`) and
`ThreadTitleWriterPort` (`ThreadTitleService.apply`); `title_for` (no reader)
went. `core/session_keys.py::thread_owner` and its `domain/keys.py` re-export
went too (no call site). Every `modules.sessions` import outside the module is a
composition root's.

| Debt | What | Paid by |
|---|---|---|
| `MessageStore` has no service over it | see below | when a second reader appears |

Not a debt but worth knowing: `MessageStore` has no service over it yet —
`recall` lives in the turns module's tool handler, which receives the repository
through its `MessageSearch` port.

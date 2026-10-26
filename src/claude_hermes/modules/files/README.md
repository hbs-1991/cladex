# `files`

## Purpose

Files that cross the turn boundary, addressed by opaque ids. A browser upload
lands in the lane's inbox under a name it did not choose; a file the agent
produced is copied into the lane's outbox; both become one row and one
`/api/files/{id}` URL that is served only after the lane's visibility check
passes. Nothing outside this module names a path on disk.

## Layout

```
domain/
  naming.py        safe_name(message_id, raw), lane_token(session_key) — re-exported
                   from core/naming.py since Step 5 (telegram applies safe_name to a
                   download before this module sees it, and modules may not import
                   each other; the two share one regex, so they moved together)
  addressing.py    normalize/split/split_s3/s3_address — what a stored value means
  exceptions.py    FileTooLarge (413/too_large), UnknownAttachment (400/bad_request)
  models.py        FileRecord (the `files` table). Wave 4a adds `owner_id`
                   (FK -> `users(id)`, ON DELETE SET NULL, nullable, index
                   `ix_files_owner`): the mirror root is resolved from the ROW's
                   owner, never the requester's.
application/
  ports.py         BlobStore (async), FileRepository, ThreadVisibility
  dto.py           to_wire(record)  — the `/api/files/{id}` literal
  service.py       FileService(repo, blobs, max_upload_bytes=None)
infrastructure/
  layout.py        blob_root/inbox_dir/outbox_dir (lane_dir = inbox_dir),
                   resolve_within(root, rest) — the containment check, one body
  local_blob_store.py  LocalBlobStore(home) — the BlobStore over the filesystem
  s3_blob_store.py     S3BlobStore(client, cache_root) — the bucket + local mirror
  repository.py    FileRepository(engine) — the only SQL on `files`
                   (insert carries owner_id: the lane's owner, Wave 4a)
presentation/
  router.py        router: APIRouter — POST /api/uploads, GET /api/files/{id}
  dependencies.py  get_files, get_lane (LaneGuard),
                   lane_owner_of(thread) — the owner an upload is stamped with
```

## Stored addresses (Wave 3a)

`files.path` holds an address, not a host path:

| Backend | Address | Key |
|---|---|---|
| `blob_backend: local` | `local:<key>` | `inbox/<lane>/<name>`, `outbox/<lane>/<name>` |
| `blob_backend: s3` | `s3://<bucket>/<key>` | the same key, unchanged |

The key is `lane_token(session_key)` plus the sanitized name, so the two backends
read each other's layout and a migration to MinIO is an upload, not a rewrite of
the table. A row with **no scheme** is a row written before Wave 3a, when the
store wrote absolute paths; `domain.addressing.normalize` reads it as `local:`
and `FileService.resolve` applies that rule for every backend — the compat is one
function, not a branch in each store.

## Public surface

### Ports consumed (declared in `application/ports.py`)

| Port | Methods | Satisfied today by |
|---|---|---|
| `BlobStore` | `async put(key, data, owner_id=0) -> str`, `async copy_in(key, src, owner_id=0) -> str`, `async local_path(stored, owner_id=0) -> Path` (raises `PermissionError` outside that owner's root), `async open(stored, owner_id=0) -> bytes` | `infrastructure.local_blob_store.LocalBlobStore(roots)` or `infrastructure.s3_blob_store.S3BlobStore(client, roots)` |
| `FileRepository` | `async insert(file_id, session_key, path, name, media_type, size, created_at) -> None`, `async get(file_id) -> dict \| None` | `infrastructure.repository.FileRepository(engine)` |
| `ThreadVisibility` | `async get_visible(session_key, user_id, is_owner) -> dict` (raises `NotFound`) | sessions' `ThreadService`, through `WebDeps.visibility` (a property over `threads`); a bundle that carries no `visibility` may carry the bare `owned` callable instead, and `get_lane` reads whichever is there |

`local_path` raises instead of returning `None`, because "outside the root" is a
security refusal and not a miss. `open` is declared as of Wave 3a: with an object
store behind the port, a consumer that only wants the bytes (a worker on a volume
whose mirror is cold) should not pay for a download it will never read again. The
whole port is async for the same reason — a sync port would have meant either a
blocking socket call on the event loop or two ports for two backends.

### One root per owner (Wave 4a)

A store no longer freezes its root in `__init__`. It takes a **`RootLookup`** —
`async (owner_id) -> Path`, `infrastructure/roots.py` — and asks it on every call
with **the row's** `owner_id`. The composition root binds it to
`WorkspaceResolver.mirror_root`, so the root is the `agent_home` of whoever owns
the row; `0` (a row written before `0005`) answers the deploy
owner's tree, which is exactly where those files are.

Why it could not stay frozen: with a tree per user, a write into user B's `inbox/`
and a read under the deploy owner's `agent_home` are different directories, so
`resolve_within` either raises or hands the model a path its own `Read` is denied
(advisor blocker B4). A `Path` is still accepted at either constructor and keeps
its pre-4a meaning — `LocalBlobStore(home)` writes under `home/agent_home`,
`S3BlobStore(client, cache_root)` mirrors at `cache_root` — which is what the
tests pass.

**The S3 key grammar does not move.** Only the mirror does, so a bucket written
before this wave still reads and the migration is nothing at all.

### `S3BlobStore` and the mirror

`S3BlobStore(client, roots)` writes every blob to the bucket **and** to
`<root>/<key>`, where the root is `blob_root` of the ROW OWNER's workspace — the
very directory `LocalBlobStore` uses. Two consumers cannot be handed a key: aiogram's
`FSInputFile`, and the agent's own `Read`/`Glob` (its cwd is `agent_home`, and
the prompts name `inbox/…` paths). `local_path` downloads a key it does not find
in the mirror, so a redeploy onto a fresh volume loses a cache, never a file: the
bucket is the source of truth. The containment check runs **before** the
download, so a forged row is refused rather than materialized.

### Ports provided (what other modules receive)

`FileService` satisfies `turns`' `FilePublisher` (`publish`, `to_wire`, `get`,
`resolve`), its attachment lookup (`attachments_for(session_key, ids) ->
list[Attachment]`) and — new in Wave 3a — `telegram`'s `LaneFilesPort`
(`put_upload` + `resolve`), so a photo sent in a chat becomes the same kind of
object as a browser upload: a row, an `/api/files/{id}` URL and an entry in the
bucket. Full surface, every method awaited except `to_wire`:
`publish(session_key, path, *, owner_id=None)`, `put_upload(session_key, filename,
data, media_type=None, *, max_bytes=None, owner_id=None)`, `get(file_id)`,
`resolve(record)`,
`open(record)`, `to_wire(record)` (static), `attachments_for(session_key, ids)`.

### Dependencies

`presentation/dependencies.py`, built on `core.di.provide`. A handler writes
`files: FileService = Depends(get_files)` and never sees the bundle; a test swaps one
collaborator with `app.dependency_overrides[get_files]`
(`tests/modules/files/test_files_router.py`).

| Provider | Bundle attribute | Type |
|---|---|---|
| `get_files` | `file_store` | `FileService` |
| `get_lane` | `visibility`, else `owned` (both `required=False`) | `LaneGuard` — `await lane(session_key) -> dict`, `NotFound` if the lane is invisible |

That is the whole list: `file_store`, `visibility`, `owned`. The upload ceiling is
not a provider — the `FileService` the composition root builds carries it
(`cfg.web_upload_max_mb * 1024**2` as `max_upload_bytes`), so the router passes no
`max_bytes` and one number applies in one place.

`get_lane` also takes `core.auth.current_user`, which is where `request.state.user_id`
/ `.is_owner` are read now. **Whether this router is mounted at all is the shell's
decision** (`deps.file_store is not None`); the 503 inside `provide` is a
programming-error guard, not a feature switch.

### Routes

`router: APIRouter(tags=["files"])`.

| Method | Path | `operation_id` | Notes |
|---|---|---|---|
| POST | `/api/uploads` | `files_create_upload` | form fields `thread_id` (`Form`) and `file` (`UploadFile`); 201 with the `to_wire` shape |
| GET | `/api/files/{id}` | `files_get_file` | inline for `image/`, `text/`, `application/pdf`, `application/json`; attachment otherwise |

`file` is declared `File(None)`, not `File(...)`: a missing part used to reach the
handler and be answered `400 bad_request` / "file is required", and a Pydantic 422 in
its place would be a wire break. The lane check runs before it, as it always did.

### Tools / events

None of its own. The SSE `file` event is emitted by `turns`, carrying this
module's `to_wire` payload.

## Invariants

- **No SQL on `files` outside `infrastructure/repository.py`.** Wave 4a gave the
  row an `owner_id` (the LANE's owner, `lane_owner(...)`, read off
  `threads.owner_user_id` by the upload route); the lane visibility check is still
  the first gate, and the column is defence in depth behind it. A second query
  path would be a second place to forget both.
- **`owner_id` is the row's, never the requester's.** `FileService.resolve` /
  `open` re-resolve the blob's mirror root against whatever the ROW says, so a
  guessed or forged file id cannot resolve into the asker's own tree — and a file
  that legitimately belongs to another user is found where it actually is.
- **`attachments_for` checks the owner as an AND, never an OR.** Same lane AND
  same owner; a row with a NULL `owner_id` is refused whenever an owner is offered,
  because "belongs to nobody" must not read as "belongs to you". `owner_id=None`
  skips the check.
- **`local_path` containment, against the ROW OWNER's root.** The resolved path
  must have that owner's `agent_home` (or the S3 mirror root, which is the same
  directory) among its `parents`, or `PermissionError`. Moved verbatim from
  `filestore.resolve` into `infrastructure/layout.resolve_within`, the single
  body both stores call — a second copy is a second place to weaken it. On the
  S3 backend it runs before anything is fetched or written.
- **`safe_name`** neutralizes every character outside `[A-Za-z0-9._-]` and
  prefixes an id, so no traversal and no bare `..` survives. Moved verbatim.
- **`lane_token(session_key)`** is the single derivation of a lane's path
  segment and becomes the object-key prefix in Wave 3 — changing it orphans
  every `files.path` already written.
- **`/api/files/{id}` is frozen** (spec req. 36) and stays ownership-checked: no
  presigned object URLs in Wave 3, they would hand out the bytes without the
  check.
- **One size ceiling for both channels**: the rule is `FileService.put_upload`,
  not a router check a tool call could walk around.

## Known debts

`main.build_app()` holds
`FileService(FileRepository(engine), LocalBlobStore(home), cfg.web_upload_max_mb * 1024**2)`
as `AppContext.files`; `http.deps.web_deps(ctx)` passes it as `deps.file_store` and
`deps.visibility` is sessions' `ThreadService`. Step 5 deleted the legacy path
around it: `filestore.py` (`LocalFileStore(db, home)`, `_DatabaseFileRepository`),
`web/files.py` and its `routes(deps)`, `get_upload_limit` and the `.service` reach
in `get_files`, and `gateway/attachments.py` — its `specs` / `download_all` are
telegram's `infrastructure/attachments.py::AiogramDownloader` now, and the
`safe_name` it needed lives in `core/naming.py` with `lane_token`. Telegram intake
reaches this module through the `LaneFilesPort` Wave 3a gave it over
`FileService` (`put_upload`, `resolve`) — no new API was invented. What remains:

- `files.path` carries a scheme as of Wave 3a, but rows written earlier still
  hold absolute paths and are read through `normalize`. A one-shot upload of the
  old outbox into the bucket would let the compat branch go; not scheduled.
- `LocalBlobStore`'s bodies are blocking disk calls inside `async def`. Local
  disk, so a hop into a thread would cost more than it saves — but it is a
  promise the signature makes and the body does not keep.
- The `<bucket>` half of an `s3://` address is informational: `S3BlobStore`
  reads the key and uses **its own** client's bucket, so a restore into a
  differently named bucket still resolves. A deployment that must read two
  buckets at once needs a real registry.

Not a debt but worth knowing: `turns.presentation.dependencies.attachments_for`
resolves upload ids for a turn instead of calling `FileService.attachments_for`.
The two agree; the turns copy raises `ApplicationException(400, "bad_request")`
where this one raises `UnknownAttachment`, which renders identically.

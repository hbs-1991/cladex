---
id: decision.minio-blobs
kind: decision
status: current
summary: Uploads and agent-produced files move into a MinIO bucket behind an async BlobStore port, keyed exactly as the on-disk tree was, with that tree kept as a local mirror — and /api/files/{id} stays the only way to read one.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:9edfc6845f4502d3b854a60d175bc985e19e000568fe1ec6ab53c85ad5cfd22d
evidence:
  - path: src/claude_hermes/integrations/s3/client.py
  - path: src/claude_hermes/modules/files/application/ports.py
  - path: src/claude_hermes/modules/files/application/service.py
  - path: src/claude_hermes/modules/files/domain/addressing.py
  - path: src/claude_hermes/modules/files/domain/naming.py
  - path: src/claude_hermes/modules/files/infrastructure/layout.py
  - path: src/claude_hermes/modules/files/infrastructure/local_blob_store.py
  - path: src/claude_hermes/modules/files/infrastructure/s3_blob_store.py
  - path: src/claude_hermes/modules/files/presentation/router.py
  - path: src/claude_hermes/modules/files/README.md
  - path: src/claude_hermes/modules/telegram/application/services/intake.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/main.py
  - path: docker-compose.yml
relations:
  - component.web
  - component.core-runtime
  - contract.state-db
  - decision.redis-taskiq-scaling
  - decision.web-as-a-channel
---
# Decision: MinIO for blobs, with the on-disk tree as a mirror

## Context

Files that cross the turn boundary — a browser upload, a Telegram photo, a file
the agent produced and sent — lived as real files under
`<home>/agent_home/{inbox,outbox}/<lane>/<name>`, with `files.path` holding an
absolute host path. That worked while one container owned the volume. Wave 3a
adds a second process that runs jobs and can produce files
([[decision.redis-taskiq-scaling]]), and req. 23/26 want the state a deployment
cannot lose to be somewhere a `docker compose down -v` does not reach.

Two constraints made "just put it in S3" not a straight swap:

- **The agent reads its own files.** Its cwd is `agent_home`, its tools are
  `Read`/`Glob`, and the prompts hand it `inbox/<lane>/<name>` paths. It cannot be
  given an object key.
- **aiogram wants a file.** `FSInputFile` takes a path, not bytes.

## Decision

### The port goes async, and stored values grow a scheme

`files`' `BlobStore` port is `async put(key, data) -> str`,
`async copy_in(key, src) -> str`, `async local_path(stored) -> Path`,
`async open(stored) -> bytes`. Async for the whole port, not only the S3 half: a
sync port would have meant either a blocking socket call on the event loop or two
ports for two backends. `FileService.publish` / `put_upload` / `resolve` /
`attachments_for` are awaited accordingly, and so are their consumers (files
router, turns' `send_document`, telegram's documents and intake).

`files.path` holds an **address**, read by `domain/addressing.py`:

| Backend | Address | Key |
|---|---|---|
| `blob_backend: local` | `local:<key>` | `inbox/<lane>/<name>`, `outbox/<lane>/<name>` |
| `blob_backend: s3` | `s3://<bucket>/<key>` | the same key, unchanged |
| pre-Wave-3a row | a bare absolute path | read as `local:` by `normalize` |

**The key is deliberately the old path layout.** `lane_token(session_key)` plus
the sanitized name, exactly as the filesystem store wrote it. The two backends
therefore read each other's layout, and migrating a deployment to MinIO is an
upload, never a rewrite of the `files` table. Changing `lane_token` orphans every
row already written, which is why it is an invariant rather than a helper.

The compat rule for schemeless rows lives in `normalize` and nowhere else —
one function, not a branch in each store.

### `S3BlobStore` writes the bucket *and* a local mirror

`S3BlobStore(client, cache_root)` uploads and then writes `<mirror root>/<key>`.
Since Wave 4a the mirror root is not frozen: the store takes a `RootLookup`
(`async (owner_id) -> Path`, `infrastructure/roots.py`) that the composition root
binds to `WorkspaceResolver.mirror_root`, so it is the `agent_home` of **the row's
owner** — the very directory `LocalBlobStore` uses for that owner, and the only
tree that owner's read guard admits. `0` answers the deploy owner's tree, which is
where every pre-`0005` file is. The S3 key grammar did not move, only the mirror.
That is what keeps the agent's `Read`/`Glob` and aiogram's `FSInputFile` working
unchanged.

`local_path` downloads a key it does not find in the mirror, so a redeploy onto a
fresh volume loses a cache, never a file: **the bucket is the source of truth.**
`open` skips the mirror entirely, for a consumer that only wants the bytes — a
worker on a cold volume, most of all.

The containment check (`layout.resolve_within`: the resolved path must have the
store root among its `parents`, or `PermissionError`) is one body shared by both
stores, moved verbatim from `filestore.resolve`, and on the S3 backend it runs
**before** anything is fetched or written. A forged row is refused, not
materialized. A second copy of that check would be a second place to weaken it
([[quality.invariants]]).

The `<bucket>` half of an `s3://` address is informational: the store reads the
key and uses its own client's bucket, so a restore into a differently named bucket
still resolves.

### boto3 in a thread, not aiobotocore

`integrations/s3/client.py` wraps `boto3.client("s3", endpoint_url=…)` and runs
every call through `asyncio.to_thread`: `put_object`, `get_object`, `upload_file`,
`download_file`, `ensure_bucket`, `delete_object`, `list_prefix`, `aclose`. The
plan defaulted to aiobotocore (Open Question 2); the offline build could not
install it, and the interface is identical either way, so the decision is settled
the other way and the swap stays a one-file change.

### `/api/files/{id}`, never a presigned URL

The route is frozen (spec req. 36) and stays ownership-checked: the lane's
visibility check runs before the bytes are served, and download is still a
`FileResponse` off `local_path`.

A presigned object URL would be faster and is the obvious S3 idiom. It is refused
on purpose: a presigned URL **is** the authorization — anyone holding the string
gets the bytes, for as long as it lives, with no reference to who is asking or to
whether the lane is still theirs. The `files` row has no `owner_id`; visibility is
borrowed from the thread, and the borrowing has to happen per request. Handing out
a URL that outlives the check turns "a file in a lane you can see" into "a file
anyone you forwarded a link to can see".

### Telegram intake goes through the service

`LaneWorkspace` used to write straight into `inbox/`. It now fetches into a scratch
directory and hands the bytes to `FileService.put_upload` through a `LaneFilesPort`,
so a chat attachment becomes the same kind of object as a browser upload: a row, an
`/api/files/{id}` URL and — in `s3` mode — an object in the bucket. The bare-`home`
fallback that served the old `gateway.telegram` entry point went in Step 5:
`LaneWorkspace(lane_dir, resolver)` requires the resolver.

## Consequences

- `blob_backend: local` is the default and is unchanged behaviour. A laptop needs
  no MinIO; the bucket simply stays empty.
- `main.build_app()` calls `ensure_bucket()` at boot in `s3` mode — idempotent, and
  a fresh volume needs it.
- **Backups are three things now**, not one: `pg_dump`, `mc mirror` of the bucket,
  and the `data/` archive ([[system.operations]]). A `tar ./data` on an `s3`
  deployment carries a cache, not the files.
- Rows written before Wave 3a still hold absolute paths and are read through
  `normalize`. A one-shot upload of the old outbox into the bucket would let that
  compat branch go; the modular migration's Step 5 touched no data, so it is
  still open in the code. **On the production deployment it has been done** (2026-09-24): the
  tree was mirrored into the bucket and every row — `local:` and bare alike —
  rewritten to `s3://hermes/<key>` ([[system.operations]]). Note that in `s3`
  mode a row that stays `local:` is served from the mirror directory and never
  from the bucket, so an upload *without* the rewrite leaves those rows exactly
  as unprotected as before.
- **Only what passes through `FileService` reaches the bucket**: `put_upload`
  (browser upload, Telegram intake) and `publish` (`send_document`). A file the
  agent merely writes into `agent_home`, and anything a routine produces, stays on
  the volume.
- `LocalBlobStore`'s bodies are blocking disk calls inside `async def` — local
  disk, so a hop into a thread would cost more than it saves, but it is a promise
  the signature makes and the body does not keep.
- `tests/modules/files/test_files_s3.py` runs against the real MinIO container and
  skips with a `make test-stack` reason when it is not up ([[quality.testing]]).

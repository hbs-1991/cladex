---
id: system.operations
kind: system
status: current
summary: The production deployment — host, checkout, the seven-service compose stack (the worker runs the turns since Wave 3b), the test stack, and the upgrade sequence that takes a usable backup of database, bucket and volume.
verification:
  base_commit: 68c332df85c483d0573ee1cbf0ac29a2ad695626
  evidence_fingerprint: sha256:d72ada8476dc5ad056195d14b1a3c733dc70cf9e563fefc0456778c0d0df6dfa
evidence:
  - path: scripts/deploy.sh
  - path: scripts/import_sqlite.py
  - path: scripts/import_data_dir.py
  - path: Makefile
  - path: .env.example
  - path: src/claude_hermes/core/logging.py
  - path: .github/workflows/deploy.yml
  - path: src/claude_hermes/core/logsafe.py
  - path: docs/deploy.md
  - path: docker-compose.yml
  - path: docker-compose.prod.yml
  - path: docker-compose.caddy.yml
  - path: Dockerfile
  - path: config.yaml.example
  - path: src/claude_hermes/workers/tasks.py
  - path: src/claude_hermes/workers/curator.py
  - path: src/claude_hermes/modules/learning/infrastructure/review_slot.py
  - path: src/claude_hermes/core/db/migrations/versions/0006_per_user_extensions.py
  - path: src/claude_hermes/modules/extensions/infrastructure/refresh_locks.py
  - path: src/claude_hermes/modules/extensions/application/services/oauth_service.py
  - path: scripts/model_matrix.py
  - path: src/claude_hermes/core/config.py
  - path: src/claude_hermes/core/model_catalog.py
relations:
  - decision.model-choice-is-lane-state
  - component.core-runtime
  - component.web
  - contract.state-db
  - decision.postgres-and-modular-core
  - decision.redis-taskiq-scaling
  - decision.minio-blobs
  - quality.testing
  - decision.multi-user-isolation
  - decision.prompt-cache-discipline
  - component.gateway
---
# Operations

`docs/deploy.md` is the manual — how to stand a host up from nothing, and why
each step is what it is. This page is the one thing that manual cannot carry,
because it is not a property of the code: **where this project actually runs.**

## The deployment

| | |
|---|---|
| Host | `ssh deploy@<server-ip>` (your server) |
| Checkout | `/home/deploy/claude-hermes`, tracking `main` |
| Stack | `docker-compose.yml` + `docker-compose.prod.yml` + `docker-compose.caddy.yml` |
| Containers | `claude-hermes-agent-1`, `claude-hermes-db-1`, `claude-hermes-caddy-1` — plus `redis`, `minio`, `worker` and `scheduler` from the next deploy of Wave 3a |
| Public origin | `https://agent.example.com` — Caddy terminates TLS on the host |
| Bot | `@your_bot`; the Mini App menu button is registered on every boot |
| Secrets | `.env` beside the compose files: `POSTGRES_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `MINIO_ROOT_PASSWORD`, `CH_SECRETS_KEY`, `OPENAI_API_KEY`, `MINIO_ROOT_USER`, `CH_WEB_SECRET`, `CLADEX_HOST`, `TUNNEL_TOKEN` |
| Database | `postgres:16-alpine` as the `db` service, data in the `pgdata` volume; the agent's `CH_DATABASE_URL` is composed from `POSTGRES_PASSWORD` |
| Behaviour | `data/config.yaml` exists on the host (read 2026-09-23: `model: claude-opus-5`); every knob it does not set sits at its `config.py` default. **`blob_backend: s3` since 2026-09-24** — see *Blobs are in MinIO* below |

The image is built **on the host** (`build: .`), so the server needs the source,
not a registry. It reaches GitHub over a read-only deploy key; it never pushes.

## The seven services

Wave 3a turned `db + agent + cloudflared` into `db + redis + minio + agent +
worker + scheduler + cloudflared` ([[decision.redis-taskiq-scaling]]).

| Service | What it is | What it needs |
|---|---|---|
| `db` | `postgres:16-alpine`, volume `pgdata` | `POSTGRES_PASSWORD` |
| `redis` | `redis:7-alpine --appendonly yes`, volume `redisdata` — the queue and the locks (`lock:oauth:<name>`; the curator's `lock:curator`, `lock:curator:<id>`, `lock:curator:sweep`; and `lock:review`, the one-review-at-a-time slot — see *The curator per user* below) | — |
| `minio` | `quay.io/minio/minio server /data --console-address :9001`, volume `miniodata` — uploads and agent-produced files ([[decision.minio-blobs]]). **quay.io, not Docker Hub**, which stopped serving `minio/minio` (commit `2ba221a`); `mc` in the deploy script's mirror moved with it | `MINIO_ROOT_USER` (defaults to `hermes`), `MINIO_ROOT_PASSWORD` |
| `agent` | HTTP, Telegram polling, **the migrations** — and since Wave 3b it *starts* turns rather than running them: it writes the `turns` row, enqueues the task and watches the lane | everything |
| `worker` | scheduled jobs, learning reviews (in the tree of whoever's turn nudged them, since 4b), the curator sweep (one pass per user) **and the turns** (`--max-async-tasks 4`). N of them are safe; it never polls Telegram | the same image, mounts and environment as `agent` — one YAML anchor, so they cannot drift. **Including `TELEGRAM_BOT_TOKEN`**: whichever process runs a turn sends its `ask_user` keyboard and its `send_document` |
| `scheduler` | a stateless enqueuer of two periodic tasks; a duplicate is harmless. `scheduler_tick` also reaps turns whose worker died, and with `content_enabled` runs the content plan's tick (queue due items, wake the publisher through `wake_if_idle`) — idempotent across workers like the claim | Redis only |

`agent` and `worker` share `./data:/data` and `./catalog:/app/catalog:ro` through
the `x-app` anchor: a mount that differs between them is a scheduled job that
behaves differently depending on which process ran it. `worker` also
`depends_on: agent` — the agent owns `alembic upgrade head`, and a worker that
started first would query a schema that is not there yet.

`redisdata` **is** a volume (`--appendonly yes`) on purpose: the scheduler's claim
has already moved `next_run_at` forward by the time a task is on the list, so a
dropped task is a run that silently never happens. Since Wave 3b a dropped
`run_turn` message costs a user rather than a digest — the lane stays `queued`
until `turn_queue_timeout_seconds` (300 s) expires and the reaper frees it.

Redis also holds three namespaces that are **wire values**, agreed between an API
instance and a worker that may be from two different deploys, and therefore not
renamed casually: `hermes:turn:<lane>` and `hermes:ctl` (the event and control
channels), `hermes:snap:<lane>…` (the in-flight snapshot, every key `EXPIRE`d to
`turn_snapshot_ttl_seconds`) and `hermes:ask:<qid>` / `…:answer` (an open
`ask_user` question and its answer). None of them is durable state: losing the
whole Redis costs the turns in flight, not history.

**Env the app pops.** `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `CH_SECRETS_KEY`,
`CH_S3_ACCESS_KEY`, `CH_S3_SECRET_KEY`, `CH_REDIS_URL`, `CH_DATABASE_URL`,
`POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD` and `CH_WEB_SECRET` are removed from
`os.environ` at startup — in the agent **and** in the worker, both of which spawn
the CLI ([[quality.invariants]]). `CLAUDE_CODE_OAUTH_TOKEN` is the one credential
deliberately left: the CLI subprocess is what it is for.

**`CH_SECRETS_KEY` is the one secret with no second copy.** It is the Fernet key
for `extension_secrets`; the ciphertext is in PostgreSQL and the key is not, which
is the point. Lose it and every connector credential is gone — a database restore
will not bring them back. Back it up where the `.env` is backed up, and on a
rotation keep the old key until every secret has been re-entered. The agent and
the worker refuse to boot without it rather than sealing rows with a key that dies
with the process.

**Running single-process instead.** Drop `worker` and `scheduler` and unset
`CH_REDIS_URL`: everything runs inside the agent exactly as it did before Wave 3a
(`run_now` back to `cron_tick_seconds`). That is also what the whole test suite
runs as.

## The test stack

`make test-stack` starts all three backing services as throwaway containers, each
bound to `127.0.0.1` and to a port nothing else uses, so it can sit beside a real
deployment on the same machine:

| | container | address |
|---|---|---|
| PostgreSQL | `hermes-test-pg` | `127.0.0.1:55432` (`hermes`/`hermes`) |
| Redis | `hermes-test-redis` | `127.0.0.1:56379` — tests use **db 15** |
| MinIO | `hermes-test-minio` | `127.0.0.1:59000`, console `:59001` (`hermes`/`hermes-test`) |

`make test-db` stays as the PostgreSQL-only alias, and it is all the suite
strictly needs: the Redis and MinIO fixtures *skip* with a "run `make
test-stack`" reason rather than failing ([[quality.testing]]).

## Upgrading

`scripts/deploy.sh` is the procedure, not this page — run it on the host and it
does the whole thing:

```bash
ssh deploy@<server-ip> 'cd ~/claude-hermes && ./scripts/deploy.sh main'
```

Or press the button: **Actions → deploy → Run workflow**
(`.github/workflows/deploy.yml`). It runs the full gate against the commit first,
then `ssh`es in and runs the same script. It is `workflow_dispatch` only — every
merge would mean an outage on a bot somebody is talking to, and a stopped stack
kills whatever turn is in flight (see *What a deploy costs*, below). It needs
repository **variables** `DEPLOY_HOST`, `DEPLOY_USER`, `PUBLIC_ORIGIN` and
**secrets** `DEPLOY_SSH_KEY` (a key the host's `authorized_keys` accepts) and
`DEPLOY_KNOWN_HOSTS` (`ssh-keyscan -H <server-ip>`). There is deliberately no
keyscan-at-connect-time fallback: a host key learned on the spot is a key nobody
checked, and the one time it mattered here, the refusal to connect was the whole
value of the check.

**As of 2026-09-11 none of those variables or secrets exist in the repository**
(`gh secret list` and `gh variable list` are both empty), so the button runs the
gate and then stops at `DEPLOY_SSH_KEY is not set`; every deploy so far went the
`ssh` way above. Two things the gate needs that a laptop hides: Python 3.12 (no
`Path.read_text(newline=…)`, that is 3.13) and **Node 22** — jsdom 30 declares
`engines ^22.22.2` and on Node 20 every vitest worker dies before a test runs.

The script's order is **build → stop → back up → start**, and it is not the order
`docs/deploy.md` reads as. Each departure was paid for once:

- **Build before stopping.** `docker compose build` does not touch running
  containers and it is the slow step. Building after the stop turns a one-minute
  outage into five.
- **Back up with the agent stopped.** `tar` over a live `data/` exits non-zero
  with `file changed as we read it` — which silently breaks an `&&` chain and
  leaves a torn archive nobody verified — and a file copied mid-write is not
  a backup you want to discover is bad on the day you need it.
- **Back up through a container, never a bare `tar`.** The container writes as
  uid 1000 and some of what it creates is mode 0700, while the login user is a
  different uid: `du -sh data` on the host prints `Permission denied` and `tar`
  skips those trees with a warning and still exits 0.

The script refuses a dirty checkout, refuses an archive that does not contain
`data/config.yaml` (the one file every deployment has — the check used to name
`data/state.db`, which a host built after the PostgreSQL move does not have), refuses
a dump with no `CREATE TABLE` in it, resolves a bare branch name to the remote's
(a plain `main` would otherwise redeploy whatever the checkout already had), keeps
the last five archives, and prints the rollback line when it is done. It stops early
when the checkout is already at the requested commit; `FORCE=1` rebuilds anyway.

**The script fast-forwards the checkout and then `exec`s the copy it just
wrote.** It updates itself, and bash reads a script lazily from its file
descriptor — it parses as far as it needs and comes back for more — so a `git
merge` that rewrites the file underneath a running copy leaves bash reading the
NEW bytes from the OLD offset. On 2026-09-21 that silently ate a fix to this very
script: the run meant to deploy the fix executed the previous version from start
to finish, aborted in the backup step exactly as before, and did it without the
EXIT trap the new version adds — so production went down a second time, for the
same reason, an hour after the first. The fast-forward is now a stage of its own
and `exec` hands over, so everything from the build onwards is the deployed
commit's own procedure. **A change to `scripts/deploy.sh` therefore only takes
effect on the deploy AFTER the one that ships it** unless the operator
fast-forwards the checkout by hand first and then runs it with `FORCE=1`.

**A refusal after the stop puts the old stack back.** Everything between the stop
and `up -d` is a backup step, and a refusal there is about the backup, never about
whether the old containers could keep serving. Until 2026-09-21 such a refusal
simply ended the script with the stack down and a line asking the operator to
start it by hand — and on 2026-09-21 it did: `mc mirror` answered "Bucket `hermes`
does not exist" on a deployment whose `blob_backend` is `local`, `set -e` ended
the run, and production stayed down until somebody read the output. An `EXIT` trap
now `start`s whatever the script stopped. `start`, not `up -d`: the containers
come back on the image they were created with, so the deploy the script refused
really has not happened. The refusal is still a refusal — non-zero exit, new image
not started.

**`blob_backend` decides whether a missing bucket is fatal, not "is MinIO
running".** Compose starts MinIO on every deployment, so "the service is up" says
nothing about whether anything of this deployment's lives in the bucket. The
mirror is attempted whenever MinIO is there, because a deployment that switched to
`local` yesterday still has yesterday's files in it; a failure only refuses when
`blob_backend: s3`, where a missed blob backup must not be discovered at restore
time.

**Drain the queue before a multi-container upgrade across Wave 4b.** A 4b agent
enqueues `learning_review` with an `owner_id` in the payload; a 4a worker's task
body has no such parameter, so draining that message is a `TypeError` in the
worker and a lost review (the counters fire again next interval, so nothing is
permanently stuck — but the pass is gone). The other direction is safe: a
payload with no `owner_id` is the deploy owner's. `scripts/deploy.sh` stops the
whole stack at once, which is what makes a single-host deploy immune; an operator
rolling `agent` and `worker` separately lets the queue empty (no `queued` turns,
`LLEN` of the taskiq list at zero) before starting the new agent beside an old
worker.

**Drain the queue before the per-user-connectors upgrade too**, for a different
reason: the OAuth refresh lock name changes from `lock:oauth:<name>` to
`lock:oauth:<owner>:<name>`. An old agent and a new worker alive at once take
*different* keys for one connector, so two refreshes can run concurrently — and a
provider that rotates refresh tokens (Notion) refuses the loser, recording
`needs_reauth` on a connector that had just refreshed successfully. `docker
compose stop agent worker` before `./scripts/deploy.sh`, and note that the agent
owns `alembic upgrade head` while `worker` waits on it through `depends_on`.

**Drain the queue before the per-user-grants upgrade as well** (migration `0009`,
[[contract.state-db]]), for a third reason: no task payload changed, but an old
worker still consuming the queue beside the new agent runs every turn, routine,
review and curator sweep it picks up under the old deploy-wide rule — it never
reads the `users` grant columns, so a shell the owner withdrew in Settings →
Users is still handed out while `users_shell_enabled` is on, and it stamps no
`sessions.shell_enabled`. The new code tolerates the unstamped rows (NULL is
compared with the old rule), so the only cost is the old worker ignoring grants
until it is gone: let it finish what is queued and stop it before the new agent
starts. No `.env` or config change; the upgrade itself resets no session.

**Two things that migration needs before it runs.** `0006` re-keys `extensions`,
`extension_secrets` and `oauth_tokens` to `(owner_id, name)`
([[contract.state-db]]):

- **`CH_SECRETS_KEY` must be backed up wherever `.env` is.** It is the Fernet key
  for `extension_secrets`; the ciphertext is in PostgreSQL and the key is not, so
  a database restore does not bring the secrets back without it. This is the one
  credential with no second copy, and it is worth confirming before touching this
  schema rather than after.
- **Take the extra dump the downgrade would need**, because the downgrade keeps
  the deploy owner's rows and deletes everybody else's:

  ```bash
  docker compose exec -T db pg_dump -U hermes -t extensions -t extension_secrets \
    -t oauth_tokens hermes > ~/ext-backup-$(date +%F).sql
  ```

**A second user is admitted from Settings → Users, or through `allowed_users`.** A
fresh host has no `data/config.yaml` at all, and the knob defaults to empty —
nobody but the owner can talk to the agent, and per-user connectors change
nothing for a deploy with one user. Admitting somebody is **Add user** in
Settings → Users (it writes their `users` row and needs no restart), or `allowed_users:
[<their Telegram id>]` in `data/config.yaml` plus `docker compose restart agent
worker`. What they may do beyond talking is per person, on the same page: a
**Bash** switch and a **Learning** switch per row, applied on their next turn with
no restart. Anyone not switched there inherits `users_shell_enabled` and
`users_learning_enabled` (both false) — the first because one container is one
uid and a shell there reaches every other user's tree and credentials whatever
the read guard says; editing either default means restarting `agent` and
`worker` together, since each process reads its own copy. A switch needs a row,
so an `allowed_users` id nobody has met yet shows its switches disabled.
`/data/users/<id>` is created lazily on their first message, and the row then too
if they came in through `allowed_users`; they then sign in to the web UI and connect their **own** accounts under
Connectors. Verify afterwards: the owner's connectors read as *available, not
connected* to them, and the owner's own turns still compose everything they had.

**What moves in the cached prompt prefix at a 4b deploy**, so a first-turn cache
miss on every lane is expected and not a regression: the `send_document` tool
description changed (a relative path now resolves against the agent's home, and
the text says so — the one deliberate exception to "descriptions are
byte-identical" since Wave 2), group lanes lost three tools
(`skill_admin`, `session_search`, `cost_report` — for everybody, the owner
included, so the schema list never flips between senders again), and a
non-owner's private and web lanes GAINED `skill_admin` (their own library).
Every lane re-warms on its next turn; nothing needs a `/new`.

**Offering a model is a proof, then a config edit**
([[decision.model-choice-is-lane-state]]). `models:` in `data/config.yaml` is what
every allowlisted user may switch a chat to, so it is also a spending decision. A
model joins it only after `scripts/model_matrix.py` has run in a throwaway image
built from the branch, under the production `CLAUDE_CODE_OAUTH_TOKEN` passed through
a 0600 env file, never on a command line — `docs/deploy.md`, "Offering a model", has
the exact lines — and answered as itself, with no consent or usage-credit wording,
at every effort. The first run (SDK 0.2.158 / CLI 2.1.280, 2026-09-23) proved
Sonnet 5, Opus 5, Opus 5.5 and Fable 5.1, each with a 1M window unsuffixed. After
the edit restart `agent`, `worker` and `scheduler`: each reads `config.yaml` once at
boot, and `up -d` does not recreate a container whose image did not change.
Removing a model moves every chat on it to its default at the next turn, one cold
cache write each.

## State lives in two places now

`tar ./data` used to be the whole backup. It is not any more: sessions, threads,
messages, jobs, run history, turn events, costs, the extension registry and its
OAuth tokens are in **PostgreSQL**, in the `pgdata` volume, which no `data/`
archive contains ([[contract.state-db]]). Since Wave 3a the database also holds the **extension secrets** (Fernet-sealed),
the **catalog drafts** and the **approval snapshots**, which used to be
`data/secrets.env`, `data/catalog-drafts/` and `data/pending-approvals/`. `./data`
now holds what the agent works *with* — `config.yaml`, `agent_home/` (persona,
skills, plugins and the wiki the model owns), `user-catalog/`, `logs/` — and, when
`blob_backend: s3`, a **cache** of the bucket rather than the files themselves.

A usable backup is therefore three things: the dump, the bucket and the volume.

```bash
D=$(date +%F)
docker compose exec -T db pg_dump -U hermes hermes | gzip > hermes-db-$D.sql.gz
# the bucket, through minio/mc inside minio's own network namespace, so no port
# has to be published and the endpoint is the one minio itself listens on. The two
# MinIO values are picked out of .env with sed rather than sourced: `. ./.env` would
# put the bot token and the Fernet key into the environment of everything that
# follows. scripts/deploy.sh does exactly this.
U=$(sed -n 's/^MINIO_ROOT_USER=//p' .env | head -1)
P=$(sed -n 's/^MINIO_ROOT_PASSWORD=//p' .env | head -1)
docker run --rm --network "container:$(docker compose ps -q minio)" \
  -v "$HOME/hermes-$D-blobs:/backup" --entrypoint sh minio/mc -c \
  "mc alias set src http://127.0.0.1:9000 '${U:-hermes}' '$P' >/dev/null \
   && mc mirror --overwrite src/hermes /backup"
# plus the existing data/ archive that scripts/deploy.sh takes
```

`scripts/deploy.sh` does all three, with `agent`, `worker` **and** `scheduler`
stopped — an agent stopped alone would be backed up while a worker was still
rewriting the skill tree behind it.

And keep `CH_SECRETS_KEY` somewhere else again: a dump restored without it is a
database full of ciphertext nothing can open.

Restore is `gunzip -c hermes-db-$D.sql.gz | docker compose exec -T db psql -U
hermes hermes` into an empty database, with the agent stopped. Take the dump with
the agent stopped too, for the same reason the `data/` archive is taken then:
a turn writing mid-dump is a restore nobody has verified.

A deployment that predates the move migrates once with
`scripts/import_sqlite.py` — `docs/deploy.md` §4a has the exact sequence. The
script is idempotent and leaves `data/state.db` untouched, so the old file is
simply another backup until the new one has been seen to work.

**On this host it has been seen to work, and the file is gone (2026-09-21).**
Every row of the old `state.db` was checked to have a counterpart in PostgreSQL —
`turn_events` by `(turn_id, seq)`, `threads`/`sessions` by `session_key`, `jobs`
and `extensions` by name, `files` by id, nothing missing — and the oldest
`turn_costs` and `job_runs` timestamps are identical on both sides, so the
beginning of history came across too. `data/state.db` was deleted after its copy
inside `hermes-2026-09-18-1245-pre-postgres.tar.gz` was extracted and found
byte-identical (sha256 `ec1f6d84…`), which is where it still is if anyone needs it.
`data/` is tarred on every deploy, so a dead 9.4 MB file was paid for on every one.
Deleting it needs a root container (`docker run --user 0:0 -v "$PWD/data:/data"`):
`data/` belongs to uid 1000 and the login user is 1001.

**"Every row has a counterpart" was not the whole check: none of them had an
owner.** The import ran after the agent had already taken the empty database to
`0005`, and the importer did not attribute what it copied
([[contract.state-db]]), so every imported row sat with `owner_id` NULL. Nothing
failed — the ticker reads NULL as the deploy owner and kept firing every routine —
but everything scoped to the asking user left those rows out: the Routines page
showed none of the 17 routines, `session_search` missed 1144 of 1240 messages, the
cost report 304 of 352 rows. Repaired on 2026-09-24 with 0005's own statements,
`WHERE owner_id IS NULL` only, after a dump
(`~/hermes-2026-09-24-0830-pre-owner-backfill.sql.gz`); the importer now does it
itself. A cheap check on any deploy:

```sql
SELECT count(*) FROM jobs WHERE owner_id IS NULL;   -- 0 on a claimed deploy
```

## Blobs are in MinIO (since 2026-09-24)

Until then the `minio` container ran with no bucket in it at all: `blob_backend`
was never set, so it sat at `local`, and every upload and every file the agent
sent lived only under `data/agent_home/{inbox,outbox}`. The switch was, in order:
`blob_backend: s3` appended to `data/config.yaml` (through the agent container —
the file is uid 1000's; the previous copy is `~/config.yaml.pre-s3-2026-09-24`); a
restart of `agent`, `worker` and `scheduler`, whose boot logs
`s3_bucket_ready bucket=hermes`; `mc mirror` of `inbox/` and `outbox/` into
`hermes/inbox` and `hermes/outbox` (52 objects, 21 MiB — the key IS the path under
`agent_home`, [[decision.minio-blobs]]); and, once every one of the 36 `files` keys
was seen in the bucket, `files.path` rewritten from `local:<key>` and bare
`/data/agent_home/<key>` to `s3://hermes/<key>`. Each row was then read back
through `S3BlobStore.open` in the worker and compared byte-for-byte with the
mirror.

What goes to the bucket is what passes through `FileService`: browser uploads and
Telegram attachments (`inbox/`) and whatever the agent hands over with
`send_document` (`outbox/`). A file the agent only writes into its tree, and
anything a routine produces (a routine has no `send_document`), stays on the
volume and in the `data/` archive. From now on `scripts/deploy.sh` mirrors the
bucket on every deploy and **refuses** when that mirror fails, which it did not
while the backend was `local`.

Three Wave 3a leftovers are still there on purpose: `data/secrets.env`,
`data/catalog-drafts/` and `data/pending-approvals/`. Their contents are in
PostgreSQL, but `secrets.env` holds the connector credentials in plaintext and
`CH_SECRETS_KEY` is the one secret with no second copy — while that file exists it
is the only way back from a lost key. Deleting it is a security-versus-recovery
call for the operator, not housekeeping.

A deployment that predates **Wave 3a** has a second one-shot, `docs/deploy.md`
§4b: `scripts/import_data_dir.py --home /data` carries `secrets.env`,
`catalog-drafts/` and `pending-approvals/` into the three tables, encrypting the
secrets with `CH_SECRETS_KEY` on the way in. Without it an upgraded deployment
silently loses every stored extension credential and every queued draft. It is
idempotent (every write is an upsert keyed the way the table is) and it deletes
nothing — check the counts it prints, confirm `/secret list` and the drafts queue,
then remove the three paths by hand. Drafts that no longer validate come across as
`status='rejected'` with the validator's own words: a request filed under a laxer
validator is still a request a human owes an answer to.

The operator reads the queue **through the app** from then on, because it is a
table:

```bash
docker compose exec -T agent python -m claude_hermes.main drafts list
docker compose exec -T agent python -m claude_hermes.main drafts show <name>
docker compose exec -T agent python -m claude_hermes.main drafts delete <name>
```

`scripts/approve_draft.py` on the host wraps exactly those (`CH_DRAFTS_CMD`
overrides the wrapper for a non-compose host) and still writes `catalog/` itself,
from outside the container. That split is the security model
([[decision.readonly-catalog-boundary]]), not a convenience.

The production healthcheck changed with it: it no longer checks that
`/data/state.db` exists, it opens a connection to `CH_DATABASE_URL`. A container
that cannot reach its database is not healthy, however intact its files are. It
opens and closes its **own** connection inside one `asyncio.run` rather than
borrowing the app's engine (commit `ab28496`): the probe used to leave a
connection behind after the loop it was created on was gone, and every agent and
worker reported unhealthy forever.

## What a deploy costs

**Since Wave 3b this is a smaller bill in redis mode, and it is worth knowing
which container you are stopping.** The turn runs in `worker`, so restarting
`agent` alone no longer kills it: the worker finishes, the events land in
`turn_events` and the reply is written to the `turns` row, visible in the web
thread on the next load. The one casualty is the Telegram message — the agent is
what sends it, from `TurnLauncher.wait`, and nobody is left waiting on that lane
after the restart, so it is not re-sent. Stopping `worker` (or the whole stack,
which `scripts/deploy.sh` does) still costs the turn, and the reaper then frees
the lane within `turn_lease_seconds` and marks the row `lost`. Everything below
describes that case, and it is also what inline mode always does:

- the turn's events up to the stop stay in `turn_events` with **no `turn.end`** —
  the thread shows work that never concluded;
- the lane's `sessions` row is **never rebound**, because the runner writes
  `sdk_session_id` when the turn ends. The next message there resumes the session
  from *before* the killed turn, so the model does not remember what it just did;
- nothing reaches `messages`, so the exchange is missing from search;
- **files the turn wrote survive** — `data/` is untouched by a rebuild — which is
  the dangerous half: the work is on disk and the model has no memory of doing it.

```sql
SELECT turn_id FROM turn_events GROUP BY turn_id
HAVING count(*) FILTER (WHERE type = 'turn.end') = 0;
```

finds them afterwards. A `thread.title` row matches that query harmlessly; a row
whose types include `turn.start` is a real casualty.

**A deploy that moves the hermes toolset costs every lane one fresh session.**
`main.run()` compares the toolset's fingerprint (every hermes tool's name,
description and schema) with the `meta` key `hermes_tools` and,
when it differs, calls `reset_all()` and logs *"hermes toolset changed: every
lane starts a fresh session"* at INFO. That is deliberate — tool schemas are part
of the cached prefix and a resumed session never re-reads it, so the alternative
is a toolset swapped under a live conversation
([[decision.prompt-cache-discipline]]). Nobody loses a thread; everybody loses
the model's memory of the current one and the deploy pays one cold prefix per
lane. Flipping `memory_search_enabled` or `content_enabled` in `config.yaml` is the
same event, and so is a deploy that only rewords a tool's description, so budget
the restart after it the same way.

## Verifying a deploy

```bash
docker compose $COMPOSE ps                    # all Up; db/redis/minio healthy, agent (healthy)
docker logs claude-hermes-agent-1 --since 2m  # "polling as @your_bot", "web channel listening on :8080",
                                              # and "queue mode: redis - the scheduler process drives the clock"
docker logs claude-hermes-worker-1 --since 2m # a startup line, and no Telegram API URL in it
curl -s -o /dev/null -w '%{http_code}\n' https://agent.example.com/api/config      # 200
```

In queue mode the cheapest end-to-end check is a routine: `run_now` on the
Routines page should produce a `job_runs` row within 60 s — the cron granularity,
not `cron_tick_seconds`, which is the inline period. If it does not, the
`scheduler` container is enqueueing into a Redis the `worker` is not reading.

Since Wave 3b the second check is a **turn**: send one message and watch it cross
the split.

```sql
-- right after sending: 'queued' for a moment, then 'running' with a worker_id
SELECT id, status, worker_id, lease_until FROM turns
 ORDER BY created_at DESC LIMIT 5;
-- a lane stuck 'queued' means run_turn is on a list nobody reads;
-- rows piling up 'lost' mean the worker is dying mid-turn
SELECT status, count(*) FROM turns
 WHERE created_at > now()::date::text GROUP BY status;
```

`worker_id` is `<hostname>:<pid>`, so it says which container took it. A `lost`
row carries `error = 'worker lost: lease expired'`; a row that says
`dispatch failed: …` is the opposite problem — the queue refused the enqueue and
the turn never left the agent.

**Redis mode has run against a live worker, not yet on this host.** The
verification checklist in `src/claude_hermes/workers/README.md` was completed on a
dev machine (tests green, a worker executing hand-enqueued tasks, `compose config`
and `compose build`); the one remaining item is watching the first job a
production worker delivers to Telegram ([[decision.redis-taskiq-scaling]]).

**In webhook mode, check what Telegram itself believes.**

```bash
curl -s "https://api.telegram.org/bot<token>/getWebhookInfo"
# expect: your URL, "pending_update_count": 0, no last_error_message
curl -s -o /dev/null -w '%{http_code}\n' https://agent.example.com/healthz      # 200
```

`/healthz` is public, needs no cookie and asks no database — it says "this process
is serving HTTP" and deliberately nothing more. A `last_error_message` of "wrong
response from the webhook" usually means the origin does not resolve to this host,
or TLS is not up yet. `CH_TELEGRAM_WEBHOOK_SECRET` is the credential in two
places: the URL path *and* the `X-Telegram-Bot-Api-Secret-Token` header the agent
requires on every delivery, so a request with the right path and no header is a
401 and a proxy access log holding the path is not enough to forge an update.
Rotating it is one `.env` edit plus a restart; the new URL is registered at boot.
**Going back to polling** is `telegram_mode: polling` and a restart — the process
calls `deleteWebhook` before it starts polling, because `getUpdates` behind a
registered webhook answers `409` forever (`docs/deploy.md` §5b,
[[component.gateway]]).

**First deploy after Wave 4a**: check that the `users` table came out of migration
`0005` with the owner in it and nobody's tree moved.

```sql
SELECT id, role, workspace_dir FROM users ORDER BY role, id;
-- the owner's workspace_dir MUST be '' (their root stays /data itself);
-- everybody else is users/<id>, and that directory is created on first use
```

A row with `role='owner'` and a non-empty `workspace_dir` means the owner's tree
would move and every SDK session would stop resuming. There is exactly one owner
by construction (`users_one_owner`, a unique partial index)
([[decision.multi-user-isolation]], [[contract.state-db]]).

## The curator per user (Wave 4b)

The curator is a **sweep** now: the deploy owner's pass first, then one per
non-disabled user whose learning grant is on, sequentially, every 15 minutes in redis mode (`curator_tick`) or every
`curator_tick_seconds` inline. What the operator needs to know
([[component.learning]]):

- **The grant.** The sweep runs the owner's pass and, after it, a pass for each
  user whose learning grant is on — their **Learning** switch in Settings →
  Users, else `users_learning_enabled` (default `false`); a user without it is
  unobserved, exactly as 4a. A switch applies on the next turn and the next
  sweep with no restart; changing the default is a `config.yaml` edit and a
  restart of `agent` and `worker` together. Granting it is a **cost** decision:
  that user's turns are then observed — one review fork per
  `wiki_nudge_interval` (4) turns without a wiki write and per
  `skill_nudge_interval` (15) tool uses — and they get a weekly curator pass
  whose wiki lint is an LLM fork of up to `curator_max_turns`. All of it is billed to the owner's account
  and nothing meters it per user. The one bound that holds regardless is one
  review fork at a time deploy-wide (`lock:review` in redis mode, the inline
  queue's `learning_review: 1` limit otherwise); heavy traffic degrades into
  dropped nudges, never into N concurrent CLI processes. Each user's library,
  `skill_admin` and Skills page are theirs whatever the grant says.
- **Pausing.** The unsuffixed `meta` row `curator_paused = '1'` is the operator's
  switch and stops the **whole** sweep — every user's pass, reported as one
  `{"ran": false, "reason": "paused"}`. `curator_paused:<user_id>` pauses that one
  user's pass and nobody else's. `skill_admin status` on a private lane reads the
  lane owner's own keys.
- **The locks**, all Redis, all TTL'd so a killed worker frees them by itself:
  `lock:curator:sweep` around the whole sweep (TTL `curator_max_turns` minutes ×
  the number of users — a sweep of N users outlives the 15-minute tick, and
  without it the next tick would start a second sweep behind the first);
  `lock:curator` around the owner's pass (the unsuffixed key) and
  `lock:curator:<user_id>` around each other user's, which is what keeps an
  inline `curator_loop` and a worker off the same tree and what still holds when
  a sweep outlives its outer lock; and `lock:review`, held by whichever review
  fork is running anywhere in the deploy. `redis-cli KEYS 'lock:*'` shows what is
  in flight.
- **Where the output goes.** The owner's reports stay in `logs/curator/<ts>/`;
  every other user's are under `users/<id>/logs/curator/<ts>/`, next to their
  `skill_backups/` and `wiki_backups/` — inside their tree, where their own read
  guard can show them. `meta` carries `curator_last_run_at:<id>`,
  `curator_last_report_path:<id>` and `learning_review_until:<id>` per user, the
  owner's unsuffixed ([[contract.state-db]]).

```sql
-- who has been swept, and who is paused
SELECT key, value FROM meta
 WHERE key LIKE 'curator_last_run_at%' OR key LIKE 'curator_paused%'
 ORDER BY key;
```

**First deploy after Wave 4b**: the seed manifest `agent_home/.claude/.seeded.json`
appears in every tree on the first boot, recording the bundled files that are
still pristine; a bundled skill or `settings.json` that has moved in the bundle
refreshes on the *second* boot. A file the user deleted stays deleted and a
bundled skill sitting in `skills/.archive/` is no longer copied back in on every
restart ([[quality.failure-modes]]).

A route that exists but needs a session answers `401 unauthorized`; a route that
was never mounted answers `404` from the catch-all in `http/app.py`. That
difference is the cheapest check that a new router actually shipped:

```bash
docker exec claude-hermes-agent-1 python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/api/plugins')"
```

## Logs carry no token

They used to. `httpx` logs every Telegram API call at INFO and the bot token is a
path segment of those URLs, so `docker logs` printed a credential that drives the
bot. `core.logging.configure` (called at the top of `run()`, instead of
`logging.basicConfig`) puts one handler on the root logger whose formatter is
`core/logsafe.py::RedactingFormatter` wrapped around structlog's `ProcessorFormatter`:
every line — ours, `httpx`'s, anything's — goes through the rewrite of
`/bot<id>:<secret>` to `/bot<id>:<redacted>`, in the message, in lazy `%s`
arguments and in tracebacks alike. The id half stays: it is public and it says
which bot the line is about. Log lines now carry structlog key-values
(`timestamp level logger event key=value`), which is what to grep for in
`docker logs`.

Logs written **before** 2026-09-11 still contain the token. If any were shipped
anywhere, rotate the token with BotFather.

## Rolling back

`docs/deploy.md` § *Rolling back*: check out the previous commit, rebuild, and —
if state has to go back too — restore the archive **and** the dump taken above. A
rebuild never touches `data/` or the `pgdata` volume, so a code-only rollback
needs no restore. A rollback across a migration is the case to think about
before pressing anything: Alembic ran `upgrade head` at the newer boot, and the
older image will not downgrade it — restore the dump. **Never downgrade a claimed
deploy past `0005`** (Step 5): the `users` row is the only owner record now and
nothing would re-materialise it; recovery is an `INSERT INTO users (id, role,
workspace_dir, …) VALUES (<id>, 'owner', '', …)` ([[contract.state-db]]).

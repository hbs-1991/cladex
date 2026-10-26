#!/usr/bin/env bash
# Upgrade the running deployment on this host, in the order that costs the least
# downtime and still leaves a backup you could actually restore from.
#
# Run it ON the server, from the checkout:  ~/claude-hermes/scripts/deploy.sh
# Or from anywhere:  ssh deploy@<host> 'cd ~/claude-hermes && ./scripts/deploy.sh'
#
# Why this order, rather than the one a first reading of docs/deploy.md suggests:
#
#   build → stop → back up → start
#
#   * Build first. `docker compose build` does not touch running containers and it
#     is the slow step. Building after the stop turns a one-minute outage into five.
#   * Back up with the agent stopped. `tar` over a live `data/` exits non-zero with
#     "file changed as we read it" — which silently breaks an && chain and leaves a
#     torn archive nobody verified. The database is dumped with pg_dump, which is
#     consistent on its own, so PostgreSQL stays up while the agent is stopped.
#   * Stop the worker and the scheduler too. Since Wave 3a they are the processes
#     that run scheduled jobs and curator passes: an agent stopped alone would be
#     backed up while a worker was still rewriting the skill tree behind it.
#   * Back up through a container. The container writes as uid 1000 and some of what
#     it creates is mode 0700; the login user is a different uid, so a bare `tar`
#     skips those trees with a warning and still exits 0.
#   * Back up three things, not one. The state is split: PostgreSQL (sessions,
#     jobs, the registry, the encrypted extension secrets and the draft queue),
#     the MinIO bucket (uploads and agent-produced files, when blob_backend is s3)
#     and ./data (the workspace: wiki, skills, config.yaml). Redis is deliberately
#     not backed up — it holds the queue, and an in-flight job is re-claimed from
#     `jobs` on the next tick.
#
# Anything that is already running keeps running if a step fails: the build and the
# pull happen before the stop, so a broken build never costs an outage.
set -euo pipefail

# Absolute, and taken before the `cd` below, because the second stage re-runs
# this file by path.
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
cd "$(dirname "$0")/.."
COMPOSE=(-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.caddy.yml)
KEEP=${KEEP_BACKUPS:-5}
REF=${1:-origin/main}

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# What we have taken down, and the promise that we put it back.
#
# Everything between the stop and `up -d` is a backup step, and every one of them
# can fail for a reason that has nothing to do with whether the old stack could
# keep serving. Until 2026-09-21 a failure there simply ended the script with the
# containers stopped and a line telling the operator to start them by hand — so a
# missing MinIO bucket, on a deployment that does not use one, turned a
# two-minute deploy into an outage that lasted until somebody read the output.
#
# The refusals are still refusals: the new image is NOT started, the deploy does
# not happen, and the exit code is still non-zero. What changes is that the
# service the refusal was protecting goes back to serving the OLD image while the
# operator reads why. `start` and not `up -d` for exactly that reason — `up -d`
# would recreate the containers against the image the build just replaced, which
# is the deploy this script decided not to do.
STOPPED=()
on_exit() {
  local rc=$?
  if [[ $rc -ne 0 && ${#STOPPED[@]} -gt 0 ]]; then
    printf '\n\033[1m== putting %s back on the previous image — this deploy did not happen\033[0m\n' "${STOPPED[*]}" >&2
    docker compose "${COMPOSE[@]}" start "${STOPPED[@]}" >&2 || \
      echo "could not restart ${STOPPED[*]} — do it by hand: docker compose ${COMPOSE[*]} start ${STOPPED[*]}" >&2
  fi
  exit $rc
}
trap on_exit EXIT

# --- stage one: move the checkout, then hand over -----------------------------
#
# **This file updates itself, so it must not still be running when it does.**
# Bash reads a script lazily from its file descriptor — it parses as far as it
# needs and comes back for more — so a `git merge` that rewrites this file
# underneath a running copy leaves bash reading the NEW bytes from the OLD
# offset. On 2026-09-21 that silently defeated a fix to this very script: the run
# that was supposed to deploy the fix executed the previous version throughout,
# aborted in the backup step exactly as before, and did it without the EXIT trap
# that the new version adds — so production stayed down a second time.
#
# So the fast-forward is a stage of its own and `exec` hands over to the file it
# just wrote. Everything below the handover is guaranteed to be the deployed
# commit's own procedure, which is also the only honest thing for a deploy script
# to run.
if [[ "${DEPLOY_STAGE:-}" != "run" ]]; then
  say "where we are"
  git rev-parse --abbrev-ref HEAD
  BEFORE=$(git rev-parse HEAD)
  echo "current: $(git log --oneline -1)"

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "refusing: the checkout has local changes, and a deploy must be exactly a commit" >&2
    git status --short >&2
    exit 1
  fi

  say "fetch and fast-forward to $REF"
  git fetch --quiet origin
  # A bare branch name means the *remote's* branch. `git fetch` does not move the
  # local one, so deploying "main" would quietly redeploy whatever this checkout
  # already had — the exact surprise a deploy script exists to prevent.
  if [[ "$REF" != */* ]] && git rev-parse --verify --quiet "origin/$REF" >/dev/null; then
    REF="origin/$REF"
    echo "(resolved to $REF)"
  fi
  git merge --ff-only "$REF"
  AFTER=$(git rev-parse HEAD)
  echo "now: $(git log --oneline -1)"

  if [[ "$BEFORE" == "$AFTER" && "${FORCE:-}" != "1" ]]; then
    echo "already at $REF — nothing to deploy (rebuilding anyway would be a free outage)"
    echo "pass FORCE=1 to rebuild and restart at the same commit"
    exit 0
  fi

  say "handing over to this commit's own scripts/deploy.sh"
  # `exec`, so there is one process and no parent waiting on a stale copy. The
  # pre-merge commit travels in the environment because the rollback line at the
  # end names it and stage two can no longer look it up.
  DEPLOY_STAGE=run DEPLOY_BEFORE="$BEFORE" exec "$SELF" "$@"
fi

# --- stage two: the deployed commit's procedure -------------------------------
BEFORE=${DEPLOY_BEFORE:-$(git rev-parse HEAD)}
echo "deploying $(git log --oneline -1)"

say "build the image (the old stack keeps serving)"
docker compose "${COMPOSE[@]}" build

say "stop agent, worker and scheduler — downtime starts here (PostgreSQL and MinIO keep running for the backups)"
# `stop` on a service this compose file does not define is an error, not a no-op,
# so a host still running the single-process topology names only what it has.
RUNNING=(agent)
for svc in worker scheduler; do
  if docker compose "${COMPOSE[@]}" ps --services 2>/dev/null | grep -qx "$svc"; then
    RUNNING+=("$svc")
  fi
done
docker compose "${COMPOSE[@]}" stop "${RUNNING[@]}"
STOPPED=("${RUNNING[@]}")            # from here on, a failure puts them back

say "back up: pg_dump of the database, the bucket, then data/ through a container"
STAMP=$(date +%F-%H%M)
ARCHIVE="$HOME/hermes-$STAMP.tar.gz"
DUMP="$HOME/hermes-$STAMP.sql.gz"
BLOBS="$HOME/hermes-$STAMP-blobs"
# The agent's history (sessions, threads, jobs, registry) is in PostgreSQL now, so
# the tar alone is not a backup. -T: no tty, or the dump gets a carriage return.
docker compose "${COMPOSE[@]}" exec -T db pg_dump -U hermes hermes | gzip > "$DUMP"
if ! gzip -t "$DUMP" || [[ "$(gzip -dc "$DUMP" | grep -c '^CREATE TABLE')" == "0" ]]; then
  echo "refusing: $DUMP is empty or unreadable — the backup is not usable" >&2
  exit 1
fi
# The bucket. With `blob_backend: s3` every upload and every file the agent
# produced is here and NOT on the data volume, so the tar below is not a backup on
# its own. `mc` runs inside minio's own network namespace (`--network
# container:…`), which is why the endpoint is the port minio itself listens on and
# why nothing has to be published on the host.
#
# **`blob_backend`, not "is minio running", decides whether a failure here is
# fatal.** The two are not the same thing and on 2026-09-21 the difference cost an
# outage: the compose file starts MinIO on every deployment, this one has
# `blob_backend: local`, so the bucket had never been created — and `mc mirror`
# answering "Bucket `hermes` does not exist" ended the whole script under `set -e`
# with the agent stopped. A bucket that is not in use has nothing to back up, and
# saying so is the right answer, not an abort.
#
# So the mirror is attempted whenever MinIO is there (a deployment that switched
# to `local` yesterday still has yesterday's files in the bucket, and they are
# worth an archive), and only a deployment that is ACTUALLY using the bucket
# refuses when it cannot be read — a missed blob backup must not be discovered at
# restore time.
BLOB_BACKEND=$(sed -n 's/^blob_backend: *//p' data/config.yaml 2>/dev/null | tr -d '"' | tr -d "'" | head -1)
BLOB_BACKEND=${BLOB_BACKEND:-local}
MINIO_CID=$(docker compose "${COMPOSE[@]}" ps -q minio 2>/dev/null || true)
if [[ -n "$MINIO_CID" ]]; then
  # The credentials live in .env, which this shell has not read. Only the two
  # MinIO names are taken: sourcing the whole file would put the bot token and the
  # Fernet key into the environment of everything this script runs.
  MINIO_USER=$(sed -n 's/^MINIO_ROOT_USER=//p' .env 2>/dev/null | head -1)
  MINIO_PASS=$(sed -n 's/^MINIO_ROOT_PASSWORD=//p' .env 2>/dev/null | head -1)
  BUCKET=$(sed -n 's/^s3_bucket: *//p' data/config.yaml 2>/dev/null | tr -d '"' | tr -d "'" | head -1)
  BUCKET=${BUCKET:-hermes}
  mkdir -p "$BLOBS"
  # `if !`, so a failure is a value this script decides about rather than the end
  # of it. `mc mirror` is one command away from the operator's data either way.
  if docker run --rm --network "container:$MINIO_CID" -v "$BLOBS:/backup" \
      --entrypoint sh quay.io/minio/mc -c \
      "mc alias set src http://127.0.0.1:9000 '${MINIO_USER:-hermes}' '$MINIO_PASS' >/dev/null \
       && mc mirror --overwrite src/$BUCKET /backup"; then
    echo "  bucket '$BUCKET' mirrored to $BLOBS"
  elif [[ "$BLOB_BACKEND" == "s3" ]]; then
    echo "refusing: blob_backend is s3 and the bucket '$BUCKET' could not be read —" >&2
    echo "every upload and agent-produced file would be missing from this backup" >&2
    exit 1
  else
    rmdir "$BLOBS" 2>/dev/null || true      # an empty dir the pruning would keep
    echo "  bucket '$BUCKET' could not be read, and blob_backend is '$BLOB_BACKEND'" \
         "— nothing of this deployment's lives there, so nothing is missing"
  fi
elif [[ "$BLOB_BACKEND" == "s3" ]]; then
  echo "refusing: blob_backend is s3 but the minio service is not running — every" >&2
  echo "upload and agent-produced file would be missing from this backup" >&2
  exit 1
else
  echo "  no minio service — blob_backend is '$BLOB_BACKEND', the files are in data/"
fi

# No && between tar and chown: tar warns (and exits 1) on anything that changed, and
# a stopped stack should give us none — but a warning must not skip the chown and
# leave a root-owned archive the operator cannot read.
docker run --rm --user 0:0 \
  -v "$PWD/data:/data:ro" -v "$HOME:/backup" \
  claude-hermes-agent \
  sh -c "tar -czf /backup/hermes-$STAMP.tar.gz -C / data; chown 1000:1000 /backup/hermes-$STAMP.tar.gz"

# config.yaml is the one file every deployment has; a tar that skipped the tree
# (the uid-1000 / mode-0700 trap above) has no entry for it.
if [[ "$(tar -tzf "$ARCHIVE" | grep -c '^data/config.yaml$')" != "1" ]]; then
  echo "refusing: $ARCHIVE does not contain data/config.yaml — the backup is not usable" >&2
  exit 1
fi
ls -lh "$ARCHIVE" "$DUMP"
if [[ -d "$BLOBS" ]]; then du -sh "$BLOBS"; fi

say "start — downtime ends here"
docker compose "${COMPOSE[@]}" up -d
STOPPED=()   # the new stack is up; a later failure is not an outage to undo

say "verify"
sleep 15
docker compose "${COMPOSE[@]}" ps --format '{{.Name}}\t{{.Status}}'
docker logs claude-hermes-agent-1 --since 2m 2>&1 | grep -E 'polling as|web channel listening|Traceback|ERROR' || true
# The worker is where a queued job actually runs, so its startup line ("worker
# ready") is the proof that redis mode came up, not just that a container is up.
for svc in worker scheduler; do
  cid=$(docker compose "${COMPOSE[@]}" ps -q "$svc" 2>/dev/null || true)
  if [[ -n "$cid" ]]; then
    printf '  -- %s\n' "$svc"
    docker logs "$cid" --since 2m 2>&1 | grep -E 'worker ready|Traceback|ERROR' || true
  fi
done

# A route that exists but needs a session answers 401; one that was never mounted
# answers 404 from the catch-all in web/app.py. That difference is the cheapest
# proof that a new router actually shipped.
for path in /api/config /api/plugins /api/skills /api/drafts /api/connectors; do
  code=$(docker exec -i claude-hermes-agent-1 python - "$path" <<'PY'
import sys, urllib.request, urllib.error
try:
    print(urllib.request.urlopen("http://127.0.0.1:8080" + sys.argv[1], timeout=5).status)
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("ERR")
PY
)
  printf '  %-18s %s\n' "$path" "$code"
done

say "prune backups, keeping $KEEP"
ls -1t "$HOME"/hermes-*.tar.gz "$HOME"/hermes-*.sql.gz 2>/dev/null | tail -n "+$((2 * KEEP + 1))" | while read -r old; do
  echo "  rm $old"
  rm -f "$old"
done
# The bucket mirrors are directories, so they need their own pass — and their own
# count, since there is at most one per deploy.
ls -1dt "$HOME"/hermes-*-blobs 2>/dev/null | tail -n "+$((KEEP + 1))" | while read -r old; do
  echo "  rm -r $old"
  rm -rf "$old"
done

say "deployed $(git log --oneline -1)"
echo "rollback: git checkout $BEFORE && FORCE=1 ./scripts/deploy.sh HEAD"
echo "backup:   $ARCHIVE"

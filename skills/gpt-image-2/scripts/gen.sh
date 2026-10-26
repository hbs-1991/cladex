#!/usr/bin/env bash
# Generate an image via Codex CLI's imagegen tool, reusing the container's
# logged-in ChatGPT session. Supports text-to-image and image-to-image.
#
# The imagegen tool does NOT reliably write a file: on codex-cli 0.130+ it
# writes to $CODEX_HOME/generated_images/<session>/, on older builds it embeds
# the image as base64 inside the session rollout jsonl. This script handles
# both: it snapshots both locations before the run, diffs after, and decodes
# the payload out of the new session file if no image file appeared.
#
# `--enable image_generation` turns the still-under-development tool on;
# `--ephemeral` is intentionally NOT passed so the session is persisted.
#
# `--dangerously-bypass-approvals-and-sandbox` (NOT `--sandbox read-only`):
# codex would otherwise wrap itself in a bubblewrap sandbox, which needs to
# create a user namespace. Inside this container the agent's own Bash already
# runs under a sandbox, so codex's NESTED namespace is denied ("bwrap: No
# permissions to create a new namespace") and the run hangs to timeout. The
# container is itself the sandbox (see options.py: "the container is the
# sandbox"), so codex does not need its own — bypassing it is correct here,
# not a shortcut. imagegen is a network tool call, not shell, so nothing runs
# unsandboxed that wasn't already going to.
#
# Usage:
#   gen.sh --prompt "<text>" --out <path.png> [--ref <image>]... [--timeout-sec N]
#
# Exit codes:
#   0 success (path printed on stdout)
#   2 bad args
#   3 required CLI missing (codex / python3)
#   4 reference image not found
#   5 codex exec failed
#   6 no new session file detected
#   7 image payload not found (imagegen likely did not run: auth / quota / off)
#   8 timed out waiting for another gen.sh run (runs are serialized — see below)

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

PROMPT=""
OUT=""
REF_IMAGES=()
TIMEOUT_SEC=300

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prompt)      PROMPT="$2"; shift 2 ;;
    --out)         OUT="$2"; shift 2 ;;
    --ref)         REF_IMAGES+=("$2"); shift 2 ;;
    --timeout-sec) TIMEOUT_SEC="$2"; shift 2 ;;
    -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -z "$PROMPT" ]] && { echo "Missing --prompt" >&2; exit 2; }
[[ -z "$OUT" ]]    && { echo "Missing --out" >&2; exit 2; }

command -v codex >/dev/null 2>&1 || {
  echo "codex CLI not found. Install Codex CLI and run 'codex login' first." >&2
  exit 3
}
if command -v python3 >/dev/null 2>&1; then PY=python3
elif command -v python >/dev/null 2>&1; then PY=python
else echo "python3 not found" >&2; exit 3
fi

# Respect CODEX_HOME (the container persists it on the /data volume); fall back
# to the default location otherwise.
CODEX_DIR="${CODEX_HOME:-$HOME/.codex}"
SESSIONS_ROOT="$CODEX_DIR/sessions"
IMAGES_ROOT="$CODEX_DIR/generated_images"
mkdir -p "$SESSIONS_ROOT" "$IMAGES_ROOT"

before="$(mktemp)"; after="$(mktemp)"
images_before="$(mktemp)"; images_after="$(mktemp)"
stdout_log="$(mktemp)"; stderr_log="$(mktemp)"; new_sessions_file="$(mktemp)"
trap 'rm -f "$before" "$after" "$images_before" "$images_after" "$stdout_log" "$stderr_log" "$new_sessions_file"' EXIT

# One run at a time, process-wide.
#
# Attribution here is by DIFF of a directory codex shares across every session:
# snapshot generated_images/ before, snapshot after, treat what appeared as ours.
# Two concurrent runs each see BOTH images as new and both pick "the largest" —
# so they hand back the same file under two different --out names, and the caller
# gets a picture that answers someone else's prompt. That is not theoretical:
# 15.08.2026 a ten-image presentation batch came back with five identical files,
# and a slide titled "Istanbul" was a photograph of Italian food. The failure is
# silent — exit 0, a valid PNG, the wrong subject.
#
# flock serializes instead of forbidding: callers may still fire several runs at
# once and each one queues, so the fix needs no cooperation from the caller. The
# lock is released by the kernel when this process exits, so a crashed run cannot
# wedge the next one. The wait has to clear a full generation (plus slack), or a
# queued caller would fail while the lock holder is still legitimately working.
LOCK_FILE="$CODEX_DIR/.gen.lock"
LOCK_WAIT=$(( TIMEOUT_SEC * 4 + 120 ))
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE" || true
  if ! flock -w "$LOCK_WAIT" 9; then
    echo "Timed out after ${LOCK_WAIT}s waiting for another gen.sh run to finish." >&2
    echo "(generation is serialized on $LOCK_FILE — see the comment in this script)" >&2
    exit 8
  fi
fi

find "$SESSIONS_ROOT" -type f -name 'rollout-*.jsonl' -print 2>/dev/null | sort > "$before" || true
find "$IMAGES_ROOT" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' \) -print 2>/dev/null | sort > "$images_before" || true

# `-c model_reasoning_effort="low"`: image generation needs ONE imagegen tool
# call, not deep reasoning. The configured review model can be a heavy reasoner,
# which makes the session think for tens of seconds before it ever calls the
# tool. Forcing low effort for this session cuts that latency without affecting
# the global config used elsewhere.
args=(exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox
      --color never --enable image_generation -c model_reasoning_effort="low")
if [[ ${#REF_IMAGES[@]} -gt 0 ]]; then
  for img in "${REF_IMAGES[@]}"; do
    [[ -f "$img" ]] || { echo "Reference image not found: $img" >&2; exit 4; }
    args+=(-i "$img")
  done
fi

instruction="Use the imagegen tool to generate the image for the following request."
if [[ ${#REF_IMAGES[@]} -gt 0 ]]; then
  instruction+=" Use the attached image(s) as visual reference / input for image-to-image."
fi
instruction+=$'\nRequirements: generate the image directly, return only the image, no explanation.\n\nRequest:\n'"$PROMPT"

# `-i` is variadic (<FILE>...), so a trailing positional prompt would be eaten
# as another image path. Feed the prompt via stdin (codex exec reads stdin when
# no positional prompt is given).
TO=""
command -v timeout >/dev/null 2>&1 && TO="timeout"

set +e
if [[ -n "$TO" ]]; then
  printf '%s' "$instruction" | "$TO" "$TIMEOUT_SEC" codex "${args[@]}" >"$stdout_log" 2>"$stderr_log"
else
  printf '%s' "$instruction" | codex "${args[@]}" >"$stdout_log" 2>"$stderr_log"
fi
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  echo "codex exec failed (exit=$rc). stderr tail:" >&2
  tail -n 40 "$stderr_log" >&2 || true
  exit 5
fi

find "$SESSIONS_ROOT" -type f -name 'rollout-*.jsonl' -print 2>/dev/null | sort > "$after" || true
find "$IMAGES_ROOT" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.webp' \) -print 2>/dev/null | sort > "$images_after" || true

# Prefer a real image file if codex wrote one (0.130+); pick the largest.
new_image="$(comm -13 "$images_before" "$images_after" | while read -r f; do
  [[ -f "$f" ]] && printf '%s\t%s\n' "$(wc -c < "$f")" "$f"
done | sort -rn | head -1 | cut -f2-)"

if [[ -n "$new_image" ]]; then
  mkdir -p "$(dirname "$OUT")"
  cp "$new_image" "$OUT"
  echo "$OUT"
  exit 0
fi

# Fallback: scan new session rollout JSONLs for an embedded base64 payload.
comm -13 "$before" "$after" > "$new_sessions_file" || true
if [[ ! -s "$new_sessions_file" ]]; then
  echo "No new session rollout under $SESSIONS_ROOT and no new image under $IMAGES_ROOT" >&2
  tail -n 40 "$stderr_log" >&2 || true
  exit 6
fi

set +e
$PY "$SCRIPT_DIR/extract_image.py" "$OUT" "$new_sessions_file"
py_rc=$?
set -e

if [[ $py_rc -ne 0 ]]; then
  echo "Image payload not found in any new session file" >&2
  echo "(imagegen likely did not run: auth, quota, or feature off; stderr tail:)" >&2
  tail -n 30 "$stderr_log" >&2 || true
  exit 7
fi

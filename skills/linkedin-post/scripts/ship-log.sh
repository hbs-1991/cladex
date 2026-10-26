#!/usr/bin/env bash
# What the user actually shipped on GitHub in a window, small enough to read.
#
# The naive version of this is `gh api` over every repo, which returns tens of
# thousands of tokens of JSON the model then pays to skim. This asks GitHub's
# search index three narrow questions instead and prints ~40 lines: merged pull
# requests (the best single signal for "a feature landed"), feat/fix commit
# subjects grouped by repo, and which repos moved at all.
#
# Usage: ship-log.sh [--days N] [--author LOGIN]
#   --days    window size, default 7
#   --author  GitHub login, default @me (the authenticated account)
#
# Exit codes: 0 ok · 2 bad args · 3 gh missing · 4 gh not authenticated
set -euo pipefail

DAYS=7
AUTHOR="@me"

while [ $# -gt 0 ]; do
  case "$1" in
    --days)   DAYS="${2:?--days needs a value}"; shift 2 ;;
    --author) AUTHOR="${2:?--author needs a value}"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$DAYS" in ''|*[!0-9]*) echo "--days must be a whole number" >&2; exit 2 ;; esac

command -v gh >/dev/null 2>&1 || { echo "gh CLI not installed" >&2; exit 3; }
gh auth status >/dev/null 2>&1 || {
  echo "gh is not authenticated. On the HOST, not in chat:" >&2
  echo "  docker compose exec -T agent gh auth login --with-token < token.txt" >&2
  exit 4
}

SINCE=$(date -u -d "${DAYS} days ago" +%F)
echo "# Shipped by ${AUTHOR} since ${SINCE} (${DAYS}d window)"

echo
echo "## Merged pull requests"
gh search prs --author="$AUTHOR" --merged-at=">=${SINCE}" --limit 30 \
  --json repository,title,url,closedAt \
  --jq '.[] | "- \(.closedAt[0:10]) [\(.repository.nameWithOwner)] \(.title)\n  \(.url)"' \
  || echo "(pr search failed)"

echo
echo "## feat/fix commits"
# --author-date is the commit's own date, so a batch of old commits pushed today
# does not show up as this week's work. Merge commits are dropped: they carry a
# branch name, not a description of what shipped.
gh search commits --author="$AUTHOR" --author-date=">=${SINCE}" --limit 60 \
  --json repository,commit \
  --jq '[.[] | {repo: .repository.fullName, msg: (.commit.message | split("\n")[0])}]
        | map(select(.msg | test("^(feat|fix|perf)") ))
        | group_by(.repo)[]
        | "### \(.[0].repo)\n" + (map("- " + .msg) | join("\n"))' \
  || echo "(commit search failed)"

echo
echo "## Repos that moved"
# Every owner the token can see, not just the personal account: `gh repo list` with
# no argument lists only your own repos, so a section titled "repos that moved"
# silently dropped every organization repo — which for this user is where half the
# week's work lives. Enumerate the orgs and ask each one.
#
# gh's --jq takes the filter and nothing else — it has no --arg, so SINCE is
# interpolated by the shell into the filter string.
owners=""
if orgs=$(gh api user/orgs --jq '.[].login' 2>/dev/null); then
  owners="$orgs"
fi
{
  gh repo list --limit 40 --json nameWithOwner,pushedAt,description,primaryLanguage \
    --jq ".[] | select(.pushedAt[0:10] >= \"${SINCE}\")
          | \"- \(.nameWithOwner) (\(.primaryLanguage.name // \"?\")) — \(.description // \"no description\")\"" \
    || echo "(personal repo list failed)"
  for org in $owners; do
    gh repo list "$org" --limit 40 --json nameWithOwner,pushedAt,description,primaryLanguage \
      --jq ".[] | select(.pushedAt[0:10] >= \"${SINCE}\")
            | \"- \(.nameWithOwner) (\(.primaryLanguage.name // \"?\")) — \(.description // \"no description\")\"" \
      || echo "(repo list failed for ${org})"
  done
} | sort -u

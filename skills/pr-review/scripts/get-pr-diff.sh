#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO PR_NUMBER" >&2
  echo "Set REPO_MANAGER_REPLAY_SHA to diff the PR base against that commit instead of" >&2
  echo "the current head, so a merged PR can be reviewed as it stood before its fixes." >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

repo="$1"
pr_number="$2"
max_bytes="${REPO_MANAGER_MAX_DIFF_BYTES:-400000}"
replay_sha="${REPO_MANAGER_REPLAY_SHA:-}"

diff_file="$(mktemp)"
trap 'rm -f "$diff_file"' EXIT

echo "## Changed files (additions / deletions / path)"
if [[ -n "$replay_sha" ]]; then
  base="$(gh pr view "$pr_number" --repo "$repo" --json baseRefName --jq '.baseRefName')"
  gh api "repos/${repo}/compare/${base}...${replay_sha}" \
    --jq '.files[] | "+\(.additions)\t-\(.deletions)\t\(.filename)"'
  gh api \
    -H "Accept: application/vnd.github.diff" \
    "repos/${repo}/compare/${base}...${replay_sha}" > "$diff_file"
else
  gh pr view "$pr_number" --repo "$repo" --json files \
    --jq '.files[] | "+\(.additions)\t-\(.deletions)\t\(.path)"'
  gh pr diff "$pr_number" --repo "$repo" > "$diff_file"
fi

echo
echo "## Diff"
size="$(wc -c < "$diff_file")"
if (( size > max_bytes )); then
  head -c "$max_bytes" "$diff_file"
  echo
  echo "[Diff truncated: showing ${max_bytes} of ${size} bytes. Fetch individual files if more detail is needed.]"
else
  cat "$diff_file"
fi

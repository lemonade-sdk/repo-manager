#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO PR_NUMBER" >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

repo="$1"
pr_number="$2"
max_bytes="${REPO_MANAGER_MAX_DIFF_BYTES:-400000}"

echo "## Changed files (additions / deletions / path)"
gh pr view "$pr_number" --repo "$repo" --json files \
  --jq '.files[] | "+\(.additions)\t-\(.deletions)\t\(.path)"'

echo
echo "## Diff"
diff_file="$(mktemp)"
trap 'rm -f "$diff_file"' EXIT
gh pr diff "$pr_number" --repo "$repo" > "$diff_file"
size="$(wc -c < "$diff_file")"
if (( size > max_bytes )); then
  head -c "$max_bytes" "$diff_file"
  echo
  echo "[Diff truncated: showing ${max_bytes} of ${size} bytes. Fetch individual files if more detail is needed.]"
else
  cat "$diff_file"
fi

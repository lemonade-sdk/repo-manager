#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO ISSUE_OR_PR_NUMBER_OR_URL" >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

default_repo="$1"
target="$2"
repo="$default_repo"
number="$target"

if [[ "$target" =~ ^https://github\.com/([^/]+/[^/]+)/(issues|pull)/([0-9]+) ]]; then
  repo="${BASH_REMATCH[1]}"
  number="${BASH_REMATCH[3]}"
fi

if gh pr view "$number" \
  --repo "$repo" \
  --json number,title,url,state,author,body,comments,reviews,reviewRequests,statusCheckRollup 2>/dev/null; then
  exit 0
fi

gh issue view "$number" \
  --repo "$repo" \
  --json number,title,url,state,author,body,comments

#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO COMMIT_SHA" >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

repo="$1"
commit="$2"

gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "repos/${repo}/commits/${commit}/pulls"

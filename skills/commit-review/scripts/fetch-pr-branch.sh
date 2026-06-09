#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 PR_NUMBER [local-branch]" >&2
  echo "Run from inside a cloned repository." >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 2
fi

pr_number="$1"
local_branch="${2:-pr-${pr_number}}"

git fetch origin "pull/${pr_number}/head:${local_branch}"
echo "$local_branch"

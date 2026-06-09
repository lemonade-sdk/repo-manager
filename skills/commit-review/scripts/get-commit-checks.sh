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

echo "## Combined status"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "repos/${repo}/commits/${commit}/status"

echo
echo "## Check runs"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "repos/${repo}/commits/${commit}/check-runs"

echo
echo "## Check suites"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "repos/${repo}/commits/${commit}/check-suites"

echo
echo "## Workflow runs for head SHA"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "repos/${repo}/actions/runs?head_sha=${commit}"

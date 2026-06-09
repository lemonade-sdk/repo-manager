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

pr_number="$(
  gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "repos/${repo}/commits/${commit}/pulls" \
    --jq '.[0].number // empty'
)"

if [[ -z "$pr_number" ]]; then
  echo "No pull request found for commit ${commit} in ${repo}." >&2
  exit 1
fi

echo "## Pull request"
gh pr view "$pr_number" \
  --repo "$repo" \
  --json number,title,url,state,author,body,baseRefName,headRefName,reviewDecision,reviews,comments,commits,files,reviewRequests,statusCheckRollup

echo
echo "## Inline review comments"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "repos/${repo}/pulls/${pr_number}/comments"

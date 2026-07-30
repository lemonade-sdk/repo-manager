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

echo "## Pull request"
gh pr view "$pr_number" \
  --repo "$repo" \
  --json number,title,url,state,isDraft,mergedAt,author,body,baseRefName,headRefName,headRefOid,reviewDecision,reviews,comments,commits,files,reviewRequests,statusCheckRollup

echo
echo "## Inline review comments"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "repos/${repo}/pulls/${pr_number}/comments"

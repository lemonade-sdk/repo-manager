#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO PR_NUMBER" >&2
  echo "Set REPO_MANAGER_REPLAY_SHA to review a PR as it stood at that commit," >&2
  echo "before any human review: reviews, comments, and check results are withheld." >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

repo="$1"
pr_number="$2"
replay_sha="${REPO_MANAGER_REPLAY_SHA:-}"

echo "## Pull request"
if [[ -n "$replay_sha" ]]; then
  echo "[Replay mode: this PR is presented as it stood at ${replay_sha}. Human reviews,"
  echo "comments, and CI results are withheld, so judge the diff on its own.]"
  gh pr view "$pr_number" \
    --repo "$repo" \
    --json number,title,url,isDraft,author,body,baseRefName,headRefName,labels
else
  gh pr view "$pr_number" \
    --repo "$repo" \
    --json number,title,url,state,isDraft,mergedAt,author,body,baseRefName,headRefName,headRefOid,labels,reviewDecision,reviews,comments,commits,files,reviewRequests,statusCheckRollup

  echo
  echo "## Inline review comments"
  gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "repos/${repo}/pulls/${pr_number}/comments"
fi

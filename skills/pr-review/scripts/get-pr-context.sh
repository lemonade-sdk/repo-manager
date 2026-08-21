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
elif [[ "${REPO_MANAGER_WITHHOLD_REVIEWS:-}" == "1" ]]; then
  # Human reviews are withheld so this judgment is derived rather than copied. A tier that
  # can read an existing review will restate it, which is worthless on the unreviewed PRs a
  # pre-review exists for, and unfalsifiable on the rest.
  echo "[Human reviews, review requests, and inline comments are withheld for this run."
  echo "Judge from the diff, the description, and the project guides alone. Do not fetch"
  echo "them by another route, and do not refer to who has or has not reviewed.]"
  gh pr view "$pr_number" \
    --repo "$repo" \
    --json number,title,url,state,isDraft,mergedAt,author,body,baseRefName,headRefName,headRefOid,labels,commits,files,statusCheckRollup
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

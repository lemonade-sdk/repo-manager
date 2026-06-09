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

reviews_endpoint="repos/${repo}/pulls/${pr_number}/reviews?per_page=100"
commits_endpoint="repos/${repo}/pulls/${pr_number}/commits?per_page=100"

latest_approval="$(
  gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$reviews_endpoint" \
    --jq '[.[] | select(.state == "APPROVED") | .submitted_at] | max // ""'
)"

first_approval="$(
  gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "$reviews_endpoint" \
    --jq '[.[] | select(.state == "APPROVED") | .submitted_at] | min // ""'
)"

echo "## Approvals"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$reviews_endpoint" \
  --jq '[.[] | select(.state == "APPROVED") | {user: .user.login, submitted_at, body, commit_id, html_url}]'

echo
echo "## All PR commits"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$commits_endpoint" \
  --jq '[.[] | {sha, date: .commit.author.date, author: .commit.author.name, message: .commit.message}]'

echo
echo "## First approval"
if [[ -z "$first_approval" ]]; then
  echo "No approval reviews found."
  exit 0
fi
echo "$first_approval"

echo
echo "## Latest approval"
echo "$latest_approval"

echo
echo "## Commits after first approval"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$commits_endpoint" \
  --jq "[.[] | select(.commit.author.date > \"${first_approval}\") | {sha, date: .commit.author.date, author: .commit.author.name, message: .commit.message}]"

echo
echo "## Commits after latest approval"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$commits_endpoint" \
  --jq "[.[] | select(.commit.author.date > \"${latest_approval}\") | {sha, date: .commit.author.date, author: .commit.author.name, message: .commit.message}]"

echo
echo "## Patches for commits after first approval"
gh api \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  "$commits_endpoint" \
  --jq ".[] | select(.commit.author.date > \"${first_approval}\") | .sha" |
while IFS= read -r sha; do
  [[ -z "$sha" ]] && continue
  echo "### ${sha}"
  gh api \
    -H "Accept: application/vnd.github.patch" \
    "repos/${repo}/commits/${sha}"
  echo
done

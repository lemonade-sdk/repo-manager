#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO [target-dir]" >&2
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 2
fi

repo="$1"
target="${2:-}"

if [[ -z "$target" ]]; then
  target="$(basename "$repo")"
fi

if [[ -d "$target/.git" ]]; then
  origin_url="$(git -C "$target" remote get-url origin)"
  if [[ "$origin_url" != *"${repo}" && "$origin_url" != *"${repo}.git" ]]; then
    echo "Existing clone origin does not match requested repo." >&2
    echo "Target: ${target}" >&2
    echo "Requested: ${repo}" >&2
    echo "Origin: ${origin_url}" >&2
    exit 1
  fi

  git -C "$target" fetch --all --prune --tags
  echo "$target"
  exit 0
fi

if [[ -e "$target" ]]; then
  echo "Target exists but is not a git repository: ${target}" >&2
  exit 1
fi

gh repo clone "$repo" "$target"
echo "$target"

#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO REF [cache-dir]" >&2
  echo "Set REPO_MANAGER_FULL_DOCS=1 to print complete documents." >&2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
  exit 2
fi

repo="$1"
ref="$2"
cache_root="${3:-${REPO_MANAGER_CACHE_DIR:-.repo-manager/cache/project-docs}}"
safe_repo="${repo//\//__}"
cache_dir="${cache_root}/${safe_repo}/${ref}"

mkdir -p "$cache_dir"

fetch_doc() {
  local path="$1"
  local file="${cache_dir}/${path//\//__}"

  if [[ ! -s "$file" ]]; then
    if ! gh api \
      -H "Accept: application/vnd.github.raw" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "repos/${repo}/contents/${path}?ref=${ref}" > "$file"; then
      rm -f "$file"
      echo "Missing or unreadable: ${path}" >&2
      return 1
    fi
  fi

  echo "$file"
}

print_matching_sections() {
  local file="$1"
  shift
  local patterns=("$@")

  awk -v patterns="$(IFS='|'; echo "${patterns[*]}")" '
    BEGIN {
      n = split(patterns, pats, "|")
      printing = 0
    }
    /^#{1,6}[[:space:]]+/ {
      printing = 0
      lower = tolower($0)
      for (i = 1; i <= n; i++) {
        if (lower ~ pats[i]) {
          printing = 1
          break
        }
      }
    }
    printing { print }
  ' "$file"
}

print_doc() {
  local path="$1"
  local file="$2"
  shift 2

  echo "## ${path}"
  echo "Cached at: ${file}"

  if [[ "${REPO_MANAGER_FULL_DOCS:-}" == "1" ]]; then
    cat "$file"
  else
    print_matching_sections "$file" "$@"
  fi
  echo
}

contribute_file="$(fetch_doc "docs/dev/contribute.md" || true)"
philosophy_file="$(fetch_doc "docs/dev/philosophy.md" || true)"

if [[ -n "${contribute_file:-}" ]]; then
  print_doc \
    "docs/dev/contribute.md" \
    "$contribute_file" \
    "review" "reviewer" "area" "owner" "maintainer" "contribut" "expert"
fi

if [[ -n "${philosophy_file:-}" ]]; then
  print_doc \
    "docs/dev/philosophy.md" \
    "$philosophy_file" \
    "philosophy" "principle" "tenet" "design" "api" "release" "test" "security" "user"
fi

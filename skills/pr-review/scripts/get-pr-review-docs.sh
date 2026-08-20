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

  if [[ "${REPO_MANAGER_FULL_DOCS:-}" == "1" || $# -eq 0 ]]; then
    cat "$file"
  else
    print_matching_sections "$file" "$@"
  fi
  echo
}

# contribute.md is printed in full: the maintainer tables sit under headings
# like "### Features" that no section filter would match.
contribute_file="$(fetch_doc "docs/dev/contribute.md" || true)"
philosophy_file="$(fetch_doc "docs/dev/philosophy.md" || true)"
documentation_file="$(fetch_doc "docs/dev/documentation.md" || true)"
# testing.md is printed in full: its routing table ("Where Tests Go"), its CI
# expectations, and its anti-pattern table are all tables no section filter would keep.
testing_file="$(fetch_doc "docs/dev/testing.md" || true)"

if [[ -n "${contribute_file:-}" ]]; then
  print_doc \
    "docs/dev/contribute.md" \
    "$contribute_file"
fi

if [[ -n "${philosophy_file:-}" ]]; then
  print_doc \
    "docs/dev/philosophy.md" \
    "$philosophy_file" \
    "philosophy" "principle" "tenet" "design" "api" "release" "test" "security" "user"
fi

if [[ -n "${documentation_file:-}" ]]; then
  print_doc \
    "docs/dev/documentation.md" \
    "$documentation_file" \
    "documentation" "checklist" "ai" "contribut" "belongs" "process" "style" "voice" "structure"
fi

if [[ -n "${testing_file:-}" ]]; then
  print_doc \
    "docs/dev/testing.md" \
    "$testing_file"
fi

# The repository tree, filtered twice: docs so peer-doc locations are a lookup rather
# than a guess when judging documentation gaps by precedent, and test/CI paths so the
# suite that owns a change — and whether a workflow would run it — is a lookup too.
tree_file="${cache_dir}/repo-tree.txt"
if [[ ! -s "$tree_file" ]]; then
  if ! gh api \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "repos/${repo}/git/trees/${ref}?recursive=1" \
    --jq '.tree[] | select(.type == "blob") | .path' \
    > "$tree_file"; then
    rm -f "$tree_file"
    echo "Missing or unreadable: repository tree for ${ref}" >&2
  fi
fi

if [[ -s "$tree_file" ]]; then
  echo "## Documentation tree (${ref})"
  echo "Cached at: ${tree_file}"
  grep -E '^(docs/|README\.md$)' "$tree_file" || true
  echo

  echo "## Test and CI tree (${ref})"
  grep -E '^(test/|tests/|\.github/workflows/)' "$tree_file" || true
  echo
fi

#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO [file ...]" >&2
  echo "Reads stdin when no files are provided." >&2
}

if [[ $# -lt 1 ]]; then
  usage
  exit 2
fi

repo="$1"
shift

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

input_file="${tmp_dir}/input.txt"
links_file="${tmp_dir}/links.txt"
numbers_file="${tmp_dir}/numbers.txt"
targets_file="${tmp_dir}/targets.txt"

if [[ $# -eq 0 ]]; then
  cat > "$input_file"
else
  cat "$@" > "$input_file"
fi

: > "$links_file"
: > "$numbers_file"
: > "$targets_file"

grep -Eoh 'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(issues|pull)/[0-9]+' "$input_file" \
  | sort -u > "$links_file" || true

grep -Eoh '([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#[0-9]+' "$input_file" \
  | sort -u > "$numbers_file" || true

cat "$links_file" >> "$targets_file"

while IFS= read -r ref; do
  [[ -z "$ref" ]] && continue
  if [[ "$ref" == \#* ]]; then
    echo "${ref#\#}" >> "$targets_file"
  elif [[ "$ref" =~ ^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([0-9]+)$ ]]; then
    echo "https://github.com/${BASH_REMATCH[1]}/issues/${BASH_REMATCH[2]}" >> "$targets_file"
  fi
done < "$numbers_file"

sort -u "$targets_file" | while IFS= read -r target; do
  [[ -z "$target" ]] && continue
  echo "## ${target}"
  if ! "${script_dir}/get-linked-discussion.sh" "$repo" "$target"; then
    echo "Unable to fetch linked discussion: ${target}" >&2
  fi
  echo
done

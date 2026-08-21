#!/usr/bin/env bash
# Who wrote the code this PR is acting on?
#
# Two modes, because they answer different questions:
#
#   Subject mode (preferred) — pass the identifiers the PR is *about* (a config key, a
#   function, a flag) and this greps the source tree for them and blames the hits. This is
#   what finds the person who built the feature.
#
#   File mode (fallback, no terms given) — blames the lines each hunk modifies. This finds
#   whoever last edited the same lines, which for a docs-only PR is the doc's author rather
#   than the feature's author. Use it when the PR edits code directly.
set -euo pipefail

usage() {
  echo "Usage: $0 OWNER/REPO PR_NUMBER [term ...]" >&2
  echo "  With terms: greps the source tree for each term and blames the hits (subject mode)." >&2
  echo "  Without:    blames the lines the PR's own hunks modify (file mode)." >&2
  echo "Set REPO_MANAGER_CHECKOUT to override the clone location." >&2
}

if [[ $# -lt 2 ]]; then usage; exit 2; fi

repo="$1"; pr="$2"; shift 2
terms=("$@")

checkout="${REPO_MANAGER_CHECKOUT:-.repo-manager/checkout}"
if [[ ! -d "$checkout/.git" ]]; then
  echo "No git checkout at ${checkout}; set REPO_MANAGER_CHECKOUT." >&2
  exit 1
fi

cache_dir="${REPO_MANAGER_CACHE_DIR:-.repo-manager/cache/project-docs}/${repo//\//__}/authors"
mkdir -p "$cache_dir"

base="$(gh pr view "$pr" --repo "$repo" --json baseRefName --jq .baseRefName)"
git -C "$checkout" fetch --quiet origin "$base"
ref="origin/${base}"

# git blame reports commit author names; the maintainer table is keyed by GitHub handle, so
# every distinct commit needs one lookup. Cached because a hot file blames to few commits.
handle_for() {
  local sha="$1" file="${cache_dir}/${1}"
  if [[ ! -s "$file" ]]; then
    gh api "repos/${repo}/commits/${sha}" \
      --jq '"\(.author.login // "unknown")\t\(.commit.author.date[0:10])\t\(.commit.message | split("\n")[0])"' \
      > "$file" 2>/dev/null || printf 'unknown\t\t\n' > "$file"
  fi
  cat "$file"
}

hits_file="$(mktemp)"
trap 'rm -f "$hits_file"' EXIT

blame_range() {  # file, start, end, provenance label
  local file="$1" start="$2" end="$3" label="$4"
  [[ -n "$start" && "$start" -gt 0 ]] || return 0
  git -C "$checkout" blame "$ref" -L "${start},${end}" --porcelain -- "$file" 2>/dev/null \
    | awk -v f="$file" -v lbl="$label" '
        /^[0-9a-f]{40} /{ sha = substr($0, 1, 40) }
        /^author-time /  { t = substr($0, 13) }
        /^\t/ && sha    { print sha "\t" f "\t" lbl; sha = "" }
      ' >> "$hits_file"
}

if [[ ${#terms[@]} -gt 0 ]]; then
  # Subject mode. Restrict to source trees: blaming docs would just find the doc's author.
  for term in "${terms[@]}"; do
    while IFS=: read -r file line _; do
      [[ -n "${file:-}" && -n "${line:-}" ]] || continue
      blame_range "$file" "$line" "$line" "$term"
    done < <(
      # Registry and resource files list the term on every row that carries it, so blaming
      # them finds whoever added models rather than whoever built the feature. At most a few
      # hits per file, so one large file cannot crowd out the rest.
      git -C "$checkout" grep -n -F -e "$term" "$ref" -- \
        'src/*' 'tools/*' 'contrib/*' 'test/*' \
        ':(exclude)src/cpp/resources/*' ':(exclude)*.json' 2>/dev/null \
        | sed "s|^${ref}:||" \
        | awk -F: '{ if (++seen[$1] <= 3) print }' \
        | head -25
    )
  done
else
  # File mode.
  while IFS= read -r line; do
    case "$line" in
      "+++ "*) cur="${line#+++ b/}" ;;
      "@@"*)
        old_start="$(sed -E 's/^@@ -([0-9]+)(,([0-9]+))? .*/\1/' <<<"$line")"
        old_len="$(sed -E 's/^@@ -[0-9]+(,([0-9]+))? .*/\2/;s/^,//' <<<"$line")"
        old_len="${old_len:-1}"
        [[ "$old_len" -gt 0 ]] && blame_range "${cur:-}" "$old_start" "$((old_start + old_len - 1))" "changed hunk"
        ;;
    esac
  done < <(gh api "repos/${repo}/pulls/${pr}/files" --jq '.[] | "+++ b/\(.filename)\n\(.patch // "")"')
fi

if [[ ! -s "$hits_file" ]]; then
  echo "## Code authors for ${repo}#${pr}"
  echo "No blame hits — the PR's subject matched nothing in the source tree."
  exit 0
fi

echo "## Code authors for ${repo}#${pr} (base ${base})"
echo "Blamed via ${terms[*]:-changed hunks}. A handle here is whoever wrote the code, which is"
echo "a separate question from whether they appear in the contribute.md maintainer table."
echo

# Ranking. Raw blamed-line count rewards a term that appears incidentally in many unrelated
# places over a term that appears in the file actually implementing the feature. A file that
# matches several of the search terms is far more likely to be that implementing file, so each
# hit is weighted by how many distinct terms its file matched.
while IFS=$'\t' read -r sha file term; do
  printf '%s\t%s\t%s\n' "$(handle_for "$sha")" "$file" "$term"
done < "$hits_file" \
  | awk -F'\t' '
      { rows[NR] = $0
        handle[NR] = $1; date[NR] = $2; subj[NR] = $3; file[NR] = $4; term[NR] = $5
        if (!((file[NR] SUBSEP term[NR]) in seenft)) { seenft[file[NR] SUBSEP term[NR]] = 1; terms_in[file[NR]]++ }
      }
      END {
        for (i = 1; i <= NR; i++) {
          h = handle[i]; w = terms_in[file[i]]
          score[h] += w; lines[h]++
          if (date[i] > last[h]) { last[h] = date[i]; latest[h] = subj[i] }
          if (!((h SUBSEP file[i]) in seenhf)) { seenhf[h SUBSEP file[i]] = 1
            files[h] = (files[h] ? files[h] ", " file[i] : file[i]) }
        }
        for (h in score) printf "%d\t%s\t%d\t%s\t%s\t%s\n", score[h], h, lines[h], last[h], latest[h], files[h]
      }
    ' \
  | sort -rn \
  | awk -F'\t' '{ printf "- @%s — relevance %s (%s blamed line(s)), last %s\n    latest: %s\n    files: %s\n", $2, $1, $3, $4, $5, $6 }'

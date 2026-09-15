# Commit Reviews

One merged commit, one file. `commits/<sha>.json` is the unit everything else is built from:
the release review synthesizes them, the announcement draws its facts from them, and the
dashboard renders them. It is written once and never rewritten, so a rerun is free and a
sweep is just "review what has no file yet".

## Reviewing

```bash
repo-manager commit review 450bf6c --repo lemonade-sdk/lemonade --state ../lemonade-testing
```

This is job 1: a push to `main` runs it for the pushed SHA. The file is written unless one
already exists; `--force` reviews again.

```bash
repo-manager commit sweep --branch release-v2026.38
```

Reviews every commit in the bucket's range that has no file yet. The range runs from the
newest `v*` tag belonging to an earlier bucket to the branch tip; `--since TAG` and
`--head SHA` override either end. `release build` runs this itself before it generates
anything, so a review missed on `main` is simply done then.

## What the file contains

The `commit-review` skill produces the judgment; repo-manager fills in the facts it should
not have to guess and validates the rest:

| Field | Comes from |
|---|---|
| `sha`, `committed_at`, `subject` | the git checkout |
| `pr_number`, `author` | GitHub, via the commit's associated PR |
| `bucket`, `branch`, `range_start` | the branch being reviewed |
| `summary`, `verdict`, `verdict_reason` | the skill |
| `maintainer_todos`, `shout_outs`, `reviewers` | the skill |
| `evidence` | the skill: review, post-approval commits, tests, manual release testing, API compatibility, security, documentation |

`verdict` is `Clean`, `Needs Attention`, or `Blocker`.

GitHub is the authority on which PR a commit came from and who wrote it. The model is asked
so it can reason about the PR discussion, but its answer is not what gets stored.

## Cherry-picks

A hotfix on a release branch is a cherry-pick of a commit that merged on `main`, and the pick
itself belongs to no PR at all. When a commit message carries a
`(cherry picked from commit <sha>)` trailer, repo-manager follows it and asks GitHub about
the *original* commit, so the review is attributed to whoever wrote the change and links the
PR it was discussed in. The file records the pick in `cherry_picked_from`.

## Validation

A review is not stored until it validates. The skill is asked again, up to three times, with
the list of problems and its own previous attempt:

- `summary`, `verdict`, and `verdict_reason` are present, and the verdict is one of the three.
- Every `evidence` key is filled — "none observed" is a real answer, silence is not.
- **No false green.** A `Clean` verdict with an empty to-do list whose reason still says a
  maintainer has to verify something is rejected. That is the worst output the skill can
  produce: the reader sees the grade and ships. Negated claims are read as what they are, so
  "no maintainer action needed" passes.

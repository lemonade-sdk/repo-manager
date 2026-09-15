# Commit Reviews

One merged commit, one file. `commits/<sha>.json` is the unit everything else is built from:
the release review synthesizes them, the announcement draws its facts from them, and the
dashboard renders them. It is written once and never rewritten, so a rerun is free and a
sweep is just "review what has no file yet".

## Reviewing

```bash
repo-manager commit review 450bf6c --state ../lemonade-testing
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

## Who the to-dos are for

`maintainer_todos` is the one field in this file that leaves the repository. The release
review copies each item onto the checklist a tester works through on a release candidate,
word for word — so the reader is somebody who has the candidate installed, has never seen
this code, and is not going to read the diff.

That reader is the whole rule. An item names what they can touch (the command, the flag, the
endpoint, the setting, the page), says what to do and what they should see, and stands on its
own without the PR beside it. Naming a function, a header, a module or a SHA tells them
nothing they can act on; opening with "consider" or "verify whether" gives them nothing to
report back.

Plenty of what a review turns up is real and is not that: a possible hot path, a refactor
worth revisiting, a reviewer who may not have seen a late commit, test debt. Those go in
`evidence`, where the maintainer reads them. Nothing is discarded — but a checklist that mixes
the two costs the tester the ability to trust any of it, and an empty `maintainer_todos` over
a full `evidence` is a perfectly good review.

The same altitude applies to `evidence.api_compatibility`, because the release's breaking
changes — and from there the release page and the Discord post — are built out of it. Lead
with what the person upgrading notices, not with which module moved.

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
- **Every to-do is one a tester could carry out.** Two failures are readable off the sentence
  itself and are rejected: naming something only the source explains (a function call, a
  source file, a build file, a commit SHA), and opening with a verb that has no pass and no
  fail ("consider", "investigate", "decide whether"). Both are shape, not taste — neither asks
  whether the item is a good idea, which is the skill's judgement to make. A product that
  happens to look like a filename is not one (`llama.cpp` is a backend), and an English word
  spelled in hex is not a SHA (`defaced`), so neither is flagged.

A commit whose review cannot be made to validate is recorded as failed and the sweep carries
on; it shows up as an unreviewed commit in the release review's coverage evidence. A single
bad review never blocks a release.

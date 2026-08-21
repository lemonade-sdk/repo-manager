# PR Reviews

repo-manager can pre-review open pull requests to flag what a human reviewer should look out for. This is advisory — it never replaces the human review.

## The three tiers

The review runs as three gated stages, mirroring `contribute.md`'s own control flow: its Reviewer Expectation lists what an author owes before a human reads the code, and its Review Process only reaches "evaluate scope, request a subject-area expert" after a PR is ready for review. Each tier is a separate skill and a separate `pi` run, so a tier that fails validation re-runs its own judgment instead of the whole review.

**Tier 1 — `pr-triage`** checks the two obligations that decide whether the PR has a reviewable *shape*: does the description honestly map the diff (Reviewer Expectation 1), and does the PR solve one clearly defined problem (Reviewer Expectation 2). Part of the first check is verifying every `Fixes #N` / `Closes #N` reference against the issue it names — those close the issue on merge, so a wrong one silently closes a live bug. Tier 1 also puts the PR on a **review rung** (below). If the description is inaccurate or missing, or the PR bundles unrelated work, the pipeline **stops here** — the remaining tiers never run, and the posted comment asks the author for exactly one fix.

**Tier 2 — `pr-quality`** checks the rest of the author's obligations: alignment with `philosophy.md` and `contribute.md`, documentation per `documentation.md`, testing per `testing.md`, and any breaking API or UX change. These are batched rather than individually gated, so the author gets one complete list instead of a series of round trips. Gaps here stop the pipeline before a reviewer is assigned.

**Tier 3 — `pr-reviewers`** suggests two to three human reviewers. It runs only once tiers 1 and 2 are clean and the PR is not a draft.

Two deliberate exceptions. A **draft PR** runs tiers 1 and 2 but never tier 3 — Review Process 1 says drafts are for CI and AI review before human review, so a draft gets exactly the author-facing feedback it is for. And an **uncleared breaking change** forces tier 3 to run even when other gaps remain, because only a listed maintainer can sign a break off; withholding the slate there would deadlock the PR.

Sections a tier never reached are recorded as `not-evaluated` rather than left absent, so a skipped check never reads as a clean bill, and the posted comment says plainly what has not been checked yet.

## The review rung

There is no `major`/`minor` verdict. `contribute.md`'s Review Process specifies how much review a PR needs, and that is what the artifact records, as `review_requirement`:

| Rung | The guide's words | Reviewers |
| --- | --- | --- |
| `one-reviewer` | minor features and fixes | any 1 |
| `two-with-expert` | major features, refactors, new backends, security-related issues | 2, including 1 subject-area expert |
| `named-approver` | project scope expansion, re-architecture, design language changes | the maintainer the guide names |

Tier 1 reaches the rung by a lookup rather than an impression: it names the surface the diff changes and reads the rung off a table — a flag on an existing command is `one-reviewer`, a new subcommand is `two-with-expert`, a new command that expands what the project does is `named-approver`, and the same three-step ladder applies to endpoints, backends, GUI, code structure, and security. Size is never a rung: a 1,000-line diff adding one flag is `one-reviewer`, while 40 lines registering a new route are not. A low-rung change can be promoted only by naming the capability the project gains, never by calling it significant.

`expert_areas` names the subject areas a reviewer would need in order to count as this PR's expert, in the maintainer table's own vocabulary. `named_approver` is copied out of the guide's Review Process text rather than hardcoded, so it follows the guide if the guide changes.

## Reviewer suggestion

Tier 3 answers who *should* review, which is a different question from who already is. It is not given the PR's reviews, review requests, or review decision — it never calls `get-pr-context.sh` — so a suggestion cannot be a copy of the current assignment. The CLI filters out anyone already requested or reviewing when it acts on the list.

Every PR gets a slate of two or three candidates rather than a single name, so the choice and the fallback stay with the human. Candidates arrive by two routes, recorded in each entry's `basis`:

- `maintainer-table` — their **Subject Areas** cell in `contribute.md` names a surface this PR touches. The `subject_area` is that term copied verbatim, so the match is auditable.
- `code-author` — `git blame` shows they wrote the code this PR acts on, found by `get-pr-code-authors.sh`, which greps the source tree for the identifiers the PR is about and resolves each blamed commit to a GitHub handle through the API. Because every handle comes from a real commit, this route cannot invent a person.

A separate `in_maintainer_table` boolean records table membership independently of why the person was chosen, so a maintainer picked on blame evidence never has to justify themselves with a poorly-fitting table term. A `code-author` who is not in the table is a contributor who knows the code, never a maintainer, and never satisfies a requirement that names one.

## Generate

The flow is generate → read → act. Generate and store a review for one PR:

```bash
repo-manager review-pr 1234
```

Or sweep every open PR that lacks a current review. A PR whose head SHA has moved since its stored review is re-reviewed automatically; drafts are skipped unless `--include-drafts` is passed:

```bash
repo-manager sweep-prs
repo-manager sweep-prs --limit 20 --force
```

## Read

Read stored PR reviews in the terminal (or in the [web UI](#pr-reviews-in-the-web-ui)):

```bash
repo-manager pr-table
repo-manager pr-row 1
```

A stored review has one section per question — description accuracy, alignment, documentation, testing, scope, breaking changes, reviewers. Sections with findings render them as a to-do checklist with the justification and references as sub-bullets; clean sections instead show a one-line "Checked:" note recording what was inspected to earn the clean bill. The review never re-describes the PR beyond a one-sentence summary: the description-accuracy section judges the author's own description against the diff and only spends words on discrepancies.

## Act

Post the review as a PR comment — the comment is prefixed `[AI-assisted review]` per lemonade's AI policy, and re-posting updates the existing comment in place instead of duplicating it. The comment is deliberately a lean subset of the stored review: the attention line, the to-dos, scope, and suggested reviewers (rendered without `@` so nobody is pinged speculatively), with the description-accuracy section included only when it found a problem. The full maintainer context stays on the dashboard.

```bash
repo-manager post-pr-review 1234 --dry-run   # print the comment first
repo-manager post-pr-review 1234
```

Request the suggested reviewers on GitHub. The PR author and anyone who already reviewed or was already requested are skipped, and each reviewer is requested individually so one non-collaborator does not abort the rest:

```bash
repo-manager request-pr-reviewers 1234 --dry-run
repo-manager request-pr-reviewers 1234
repo-manager request-pr-reviewers 1234 --reviewers bitgamma,jeremyfowers
```

PR reviews are stored locally (SQLite + `.repo-manager/reviews/prs/`) and are not part of the published dashboard or `pull`.

## Replaying a merged PR

Tuning the skill means asking whether it would have caught what human reviewers caught — and a merged PR's diff already contains the fixes those reviewers asked for, with their comments sitting in the context. `REPO_MANAGER_REPLAY_SHA` removes both: the diff is taken from the PR base to that commit, and the PR's reviews, comments, and check results are withheld, so the review judges the code as it stood before anyone looked at it.

```bash
REPO_MANAGER_REPLAY_SHA=abc1234 repo-manager review-pr 2603
```

The stored artifact still records the PR's live head SHA, so replay reviews are for evaluation rather than posting. One limit to read replays with: only the diff is rewound. Files the PR does not touch — workflows, `CMakeLists.txt`, the docs tree — are still read at the base branch's current state, so a review of a long-merged PR may cite a line that landed after it.

## PR Reviews in the web UI

The **PR Reviews** tab of the [web UI](web-ui.md) lists stored open-PR reviews with their status, attention level, and scope, and its detail pane includes the act half of the PR flow: **Post review comment** and **Request reviewers** buttons that run the same logic as `post-pr-review` and `request-pr-reviewers`. These buttons only work in the local UI (they use your `gh` credentials); the published static dashboard excludes PR reviews entirely.

The **Status** column tracks where each PR stands in the human review process, derived from live GitHub review activity (one batched GraphQL call per refresh, cached for 60 seconds). The vocabulary, with the full explanation in each badge's tooltip: `Needs triage` (no reviewer assigned, no reviews), `Waiting` (an assigned reviewer hasn't started, or another reviewer's change request is waiting on the author), `Waiting for me` (you are the assigned reviewer, or the author replied to your change request), `Requests` (your change requests are out, waiting on the author), `Handled` (another reviewer has it), and `Approved` (approved but not merged).

When a PR has reviewers but its rung is not yet satisfied, the status says which part is missing rather than asserting that a "core maintainer" is needed: `Needs N more` when the reviewer count is short — naming who already satisfies the expert slot and which area they cover — `Needs subject expert` when nobody reviewing lists any of the PR's `expert_areas` in the maintainer table, and `Needs @handle` when the PR is on the top rung and the maintainer the guide names has not weighed in. These come from parsing the maintainer table in `contribute.md`, so they follow the guide rather than a hardcoded idea of who is senior. The table's **Admin** column is deliberately not consulted: being a repository admin is a permission, not evidence of subject-area expertise. Colors answer "do I need to act?": green means no, gray means someone else is handling it, yellow means yes, red means urgent.

Status is computed from a perspective — by default the `gh`-authenticated login. The **Status as** input in the panel header switches the whole column to any other GitHub ID's perspective (their "Requests" are your "Waiting"), reusing the same cached data.

Two header toggles, both on by default, keep the list focused: **Hide closed PRs** drops merged and closed PRs, and **Hide PRs not into main** drops PRs targeting other branches. Both use the same live GitHub state; when that state is unavailable, rows are shown rather than silently hidden. The detail pane also shows each PR's live `State` (e.g. `OPEN → main`) and how long the review took to generate ("Generated in: NN seconds", also stored as `generation_seconds` in the artifact JSON and database).

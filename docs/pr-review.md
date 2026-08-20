# PR Reviews

repo-manager can pre-review open pull requests to flag what a human reviewer should look out for. This is advisory — it never replaces the human review. Each review checks whether the author's description accurately matches the diff, checks the PR against the target repo's `docs/dev/contribute.md` and `docs/dev/philosophy.md`, verifies documentation shipped with the change per `docs/dev/documentation.md`, verifies the tests shipped with it per `docs/dev/testing.md`, judges whether the scope is major enough to need a second review from a core maintainer, flags breaking API/UX changes (and whether each is documented and maintainer-approved), and suggests reviewers from the maintainer table in `contribute.md`.

The testing check asks the three questions `testing.md` asks: did a test ship with the change (routed through the guide's "Where Tests Go" table, so every gap names the suite that owns the surface), would CI actually run it (a new suite wired into a workflow, a C++ test registered with `register_cpp_ci_test()`, a merge-queue-gated surface carrying its `ci:` label), and could the test fail (the guide's anti-pattern table — structural-only assertions on numeric output, sleeps as success signals, logic reimplemented in the test). It is equally strict about over-testing: a data-table row covered by an existing mechanism, or a new test file for a device variant that belongs behind a flag on the existing suite, is a finding in the other direction. Every flagged issue comes with an imperative to-do naming the step that resolves it, and the attention level (`Routine`/`Elevated`/`High`) is spelled out with what drove it.

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

The **Status** column tracks where each PR stands in the human review process, derived from live GitHub review activity (one batched GraphQL call per refresh, cached for 60 seconds). The vocabulary, with the full explanation in each badge's tooltip: `Needs triage` (no reviewer assigned, no reviews), `Waiting` (an assigned reviewer hasn't started, or another reviewer's change request is waiting on the author), `Waiting for me` (you are the assigned reviewer, or the author replied to your change request), `Requests` (your change requests are out, waiting on the author), `Handled` (another reviewer has it), `Needs core` (in review, but the stored pre-review rates attention High — a core maintainer is still needed), and `Approved` (approved but not merged). Colors answer "do I need to act?": green means no, gray means someone else is handling it, yellow means yes, red means urgent.

Status is computed from a perspective — by default the `gh`-authenticated login. The **Status as** input in the panel header switches the whole column to any other GitHub ID's perspective (their "Requests" are your "Waiting"), reusing the same cached data.

Two header toggles, both on by default, keep the list focused: **Hide closed PRs** drops merged and closed PRs, and **Hide PRs not into main** drops PRs targeting other branches. Both use the same live GitHub state; when that state is unavailable, rows are shown rather than silently hidden. The detail pane also shows each PR's live `State` (e.g. `OPEN → main`) and how long the review took to generate ("Generated in: NN seconds", also stored as `generation_seconds` in the artifact JSON and database).

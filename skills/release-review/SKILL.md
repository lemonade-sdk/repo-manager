---
name: release-review
description: Analyze stored commit-review results for a release range and produce a release-readiness verdict with a prioritized maintainer to-do list. Use when asked to synthesize many commit reviews into release readiness.
---

# Release Review

Analyze a set of commit-review records for one repository release. The input should include the repo, branch, release tag, optional range start tag, head SHA, and stored commit-review outputs or structured summaries. The release tag is the release being prepared, such as `v10.7.0` or `vNext`; the range start tag is the previous release tag used to select commits.

The stored commit reviews may include `maintainer_todos`, `open_maintainer_todos`, and `completed_maintainer_todos`. Treat completed commit to-dos as resolved. Do not re-add a completed commit to-do to the release to-do list unless the completed item reveals a separate unresolved release-level risk that still needs action.

If the caller provides an output file path, writing the artifact is mandatory. Write the artifact before finishing:

- Machine-readable JSON to the requested `.json` path.

Do not rely on printing JSON to the terminal as a substitute for writing the file. The CLI reads the requested artifact path after the skill exits.

The JSON must use this shape:

```json
{
  "repo": "OWNER/REPO",
  "branch": "main",
  "tag_start": "v1.2.3",
  "range_start": "v1.2.2",
  "head_sha": "HEAD_SHA",
  "verdict": "Ready",
  "verdict_reason": "One sentence explaining the release verdict.",
  "prioritized_todos": [
    {
      "priority": "P1",
      "text": "Concise release action."
    }
  ],
  "evidence": {
    "commit_coverage": "...",
    "blockers": "...",
    "manual_release_testing": "...",
    "api_compatibility": "...",
    "security": "...",
    "documentation": "..."
  }
}
```

Do not add alternate top-level action fields such as `recommendations`, `open_release_risks`, or `maintainer_todos_summary` as substitutes for `prioritized_todos`. You may include extra evidence fields, but every maintainer action that affects the verdict must appear in `prioritized_todos`.

Keep the JSON compact and UI-oriented. Do not include exhaustive inventories such as `summary_statistics`, `key_changes`, PR-by-PR lists, shout-out summaries, or long open-risk objects. Put short synthesized prose in `evidence` instead.

## Evaluation

Build a release-level judgment from the commit reviews. Do not re-review every diff unless the supplied data is clearly insufficient.

Synthesize release risk. The release review is not a concatenation of commit-review to-dos. Group related risks into a small number of release-level actions, and drop nitpicks, already-resolved items, and one-off commit concerns that do not affect release quality.

Use the commit-review to-do status:

- Completed commit to-dos count as resolved evidence.
- Open commit to-dos are candidates for release actions, but must be grouped by release risk.
- If many open commit to-dos point to the same theme, create one to-do for that theme.
- If a commit to-do is low-risk or purely local cleanup, keep it out of the release review unless it changes release readiness.

Keep the release to-do list short:

- Aim for 3-7 total items.
- Do not exceed 10 total items unless there are multiple independent P0 blockers.
- Prefer at most 5 P1 items. Coalesce platform, backend, GUI, CLI, API, documentation, and security checks by theme.
- P2 items should be true follow-up items, not every residual concern.

Prioritize maintainer action:

- `P0`: Release-blocking issue. Use for likely security issues, undocumented breaking API changes, major untested shipped behavior, or unresolved `Blocker` commit reviews.
- `P1`: Should be checked or fixed before release, but not clearly blocking. Use for unresolved `Needs Attention` findings on release-surface behavior, important manual test gaps, documentation gaps, or unclear post-approval risk.
- `P2`: Lower-priority follow-up. Use for quality improvements, residual risks not in the release surface, or useful post-release cleanup.

Manual testing should be recommended at the level of release validation themes, not as one item per feature. For example, group backend/platform smoke tests together when they can be executed as one release test matrix.

Use counts carefully. Distinguish commits from PRs. Do not say "all PRs passed CI" or give precise coverage counts unless the supplied data directly supports that statement.

## Verdict

Return exactly one release verdict:

- `Ready`: no maintainer attention required before release.
- `Needs Attention`: at least one P1/P2 item exists, but no P0 item.
- `Blocked`: at least one P0 item exists.

If any unresolved commit review says a maintainer should check something before release, the release verdict cannot be `Ready`. Completed commit to-dos do not prevent `Ready` unless there is still a separate release-level risk.

Do not output verdicts such as `Conditional Pass`, `Pass`, `Fail`, `Clean`, `Minor`, or `Blocker`. If the release can ship only after listed checks are completed, the verdict is `Needs Attention` or `Blocked`, and those checks must be in `prioritized_todos`.

The verdict and to-do list must agree:

- `Ready` requires `prioritized_todos: []`.
- `Needs Attention` requires at least one P1 or P2 to-do and no P0 to-do.
- `Blocked` requires at least one P0 to-do.

## Output Format

```markdown
## Release Verdict: Ready|Needs Attention|Blocked

One sentence explaining the verdict.

## Prioritized To-Do

- P0: ...
- P1: ...
- P2: ...

## Evidence

- Commit coverage:
- Blockers:
- Manual release testing:
- API compatibility:
- Security:
- Documentation:
```

Omit empty priority groups. Keep the to-do list focused on release quality, not code-review nitpicks.

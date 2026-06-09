---
name: release-review
description: Analyze stored commit-review results for a release range and produce a release-readiness verdict with a prioritized maintainer to-do list. Use when asked to synthesize many commit reviews into release readiness.
---

# Release Review

Analyze a set of commit-review records for one repository release range. The input should include the repo, branch, starting tag, head SHA, and stored commit-review outputs or structured summaries.

If the caller provides an output file path, write the artifact before finishing:

- Machine-readable JSON to the requested `.json` path.

The JSON must use this shape:

```json
{
  "repo": "OWNER/REPO",
  "branch": "main",
  "tag_start": "v1.2.3",
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

## Evaluation

Build a release-level judgment from the commit reviews. Do not re-review every diff unless the supplied data is clearly insufficient.

Prioritize maintainer action:

- `P0`: Release-blocking issue. Use for likely security issues, undocumented breaking API changes, major untested shipped behavior, or unresolved `Blocker` commit reviews.
- `P1`: Should be checked or fixed before release, but not clearly blocking. Use for unresolved `Needs Attention` findings on release-surface behavior, important manual test gaps, documentation gaps, or unclear post-approval risk.
- `P2`: Lower-priority follow-up. Use for quality improvements, residual risks not in the release surface, or useful post-release cleanup.

## Verdict

Return exactly one release verdict:

- `Ready`: no maintainer attention required before release.
- `Needs Attention`: at least one P1/P2 item exists, but no P0 item.
- `Blocked`: at least one P0 item exists.

If any commit review says a maintainer should check something before release, the release verdict cannot be `Ready`.

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

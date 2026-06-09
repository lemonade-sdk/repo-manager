---
name: commit-review
description: Analyze a GitHub commit and judge whether it was good for the project. Use when asked to review a merged commit, release candidate commit, branch commit, or PR-associated commit for review quality, test adequacy, breaking changes, security risk, release-hand-test needs, and documentation completeness.
---

# Commit Review

Review one commit in the context of its GitHub repository and associated PR. Produce a maintainer-focused verdict, not a line-by-line code review.

If the caller provides an output file path, write the artifact before finishing:

- Machine-readable JSON to the requested `.json` path.

The JSON must use this shape:

```json
{
  "repo": "OWNER/REPO",
  "commit_sha": "COMMIT_SHA",
  "pr_number": 123,
  "summary": "One sentence summarizing what the PR does.",
  "author": "@github-handle",
  "shout_outs": [
    {
      "handle": "@github-handle",
      "reason": "Exceptional contribution beyond routine review."
    }
  ],
  "verdict": "Clean",
  "verdict_reason": "One sentence explaining the core reason for the grade.",
  "maintainer_todos": [
    {
      "text": "Concise maintainer action."
    }
  ],
  "evidence": {
    "review": "...",
    "post_approval_commits": "...",
    "tests": "...",
    "manual_release_testing": "...",
    "api_compatibility": "...",
    "security": "...",
    "documentation": "..."
  }
}
```

Use an empty array for `shout_outs` or `maintainer_todos` when none apply.

## Required Inputs

Ask for any missing input needed to identify the commit:

- Repository, preferably `OWNER/REPO`
- Commit SHA or ref
- Branch or release context, if relevant

## Context Gathering

Use the bundled scripts when they fit the task. They assume `gh` is installed and authenticated. The scripts are available as `scripts/*.sh` from this package's repository root and as `skills/commit-review/scripts/*.sh` from the skill directory; use whichever path exists in the current environment.

```bash
scripts/clone-repo.sh OWNER/REPO [target-dir]
scripts/fetch-pr-branch.sh PR_NUMBER [local-branch]
scripts/get-associated-prs.sh OWNER/REPO COMMIT_SHA
scripts/get-commit-diff.sh OWNER/REPO COMMIT_SHA
scripts/get-commit-checks.sh OWNER/REPO COMMIT_SHA
scripts/get-commit-pr-context.sh OWNER/REPO COMMIT_SHA
scripts/get-post-approval-commits.sh OWNER/REPO PR_NUMBER
scripts/get-project-review-docs.sh OWNER/REPO REF
scripts/get-linked-discussion.sh OWNER/REPO ISSUE_OR_PR_OR_URL
scripts/extract-and-fetch-linked-discussions.sh OWNER/REPO [file ...]
```

If a script is insufficient, use `gh`, `git`, or repository reads directly.

Prefer the scripts and raw `gh api` endpoints over guessed CLI subcommands. In particular, `gh pr reviews` is not a valid GitHub CLI command; use `scripts/get-commit-pr-context.sh`, `scripts/get-post-approval-commits.sh`, or `gh api repos/OWNER/REPO/pulls/PR_NUMBER/reviews`. If local branch history is needed, fetch the PR ref with `scripts/fetch-pr-branch.sh` instead of assuming the contributor branch name exists in the clone.

Use `scripts/get-project-review-docs.sh` once per repo/ref. It caches `docs/dev/contribute.md` and `docs/dev/philosophy.md` and prints concise relevant sections. Treat that output as the project-doc context for the run. Do not reread those same docs from the clone or GitHub unless a specific required section is missing; if full docs are truly needed, rerun the script with `REPO_MANAGER_FULL_DOCS=1` and explain why.

Gather:

1. The commit diff.
2. Every associated PR for the commit, then the PR author, description, review history, comments, inline comments, linked issues, and linked PRs.
3. For each associated PR, approval reviews and any commits added after the latest approval.
4. Commit status, check runs, check suites, and workflow runs for the commit.
5. The project review ownership mapping and reviewer guidelines from `docs/dev/contribute.md`.
6. The project philosophy from `docs/dev/philosophy.md`.
7. Relevant docs, tests, API specs, CLI code, GUI code, and release-sensitive areas touched by the commit.

If the project docs above are missing, report that as evidence. Do not invent policy.

## Evaluation

Judge the commit against these criteria.

### PR Context

- Summarize what the PR does in one sentence.
- Identify the author's GitHub handle.
- Identify collaborators or reviewers who were substantially involved enough to deserve a shout out. Use a very high bar: include people only when they made an exceptional contribution beyond review, such as substantial hands-on testing, direct contribution to code or architecture, radically changing the solution direction, or catching a major release/security/API risk and materially driving the fix. Do not include routine approvers, drive-by commenters, normal review comments, small suggestions, CI-review comments, filing follow-up issues by itself, or people merely performing expected reviewer responsibilities. Catching issues during review is not enough by itself unless the person also substantially drove the resolution beyond ordinary review. Filing follow-up issues only supports a shout out when it is evidence from substantial hands-on validation or another exceptional contribution.

### Review Quality

- Was the commit adequately reviewed?
- Did at least one reviewer have subject matter expertise according to `docs/dev/contribute.md`?
- Did reviewers apply the project philosophy from `docs/dev/philosophy.md` and reviewer guidelines from `docs/dev/contribute.md`?
- Did reviewers miss release-blocking corner cases that should be revisited?
- Were any commits added after approval? If so, did they follow reviewer guidance and remain within the spirit, scope, and risk profile of the approved PR?
- Flag problematic post-approval commits, especially if they add new behavior, weaken tests, change API/GUI/CLI behavior, alter security-sensitive code, or bypass the substance of the review.
- Be precise about approval timing. Specify whether the analysis uses the first approval, latest approval, or a specific final/relevant approval, and do not summarize post-approval churn in a way that hides commits added after an earlier approval. If the branch changed after an approval and was later re-approved, say that clearly.

### Test Adequacy

- New features should add tests that prove the feature will keep working.
- Fixes should demonstrate the fix in the PR description, comments, or tests.
- The commit should not weaken, delete, skip, or relax existing tests merely to ease merging.
- Be careful with test-skip language. If tests are skipped, relaxed, or deleted, do not say "no tests weakened or deleted" unless that is literally true. Instead judge whether the skip or relaxation is justified and scoped, for example: "No evidence of inappropriate test weakening; the ARM64 skip appears justified because that backend has no ARM64 binary."

### Manual Release Testing

Flag significant shipped or documented behavior that should be tested by hand before the next release, especially:

- New CLI commands or user-visible CLI features.
- Any GUI app change whatsoever.

Do not treat every new platform/build-system capability as release-blocking manual-test work. First decide whether the behavior is part of the upcoming release surface: shipped artifacts, documented supported platforms, user-visible behavior, install paths users can exercise in the release, or release promises in docs. If the change only prepares build-system support for artifacts or platforms that are not actually shipped or advertised in the release, note the residual risk in evidence but do not require maintainer action solely for manual testing.

### API Compatibility

Flag any API breaking change whatsoever, including schema, protocol, config, CLI contract, exported API, documented behavior, persistence format, or integration behavior.

### Security And Malice

Look for any concern whatsoever that the commit is malicious or introduces a security vulnerability. Treat supply-chain changes, credential handling, network calls, code execution paths, auth changes, permission broadening, telemetry, obfuscation, and suspicious generated/minified blobs as high signal.

### Documentation

- API changes must be reflected comprehensively in the spec.
- Major new features should include an example or guide.
- Behavior changes must update existing affected documentation.

## Verdict

Return exactly one grade:

- `Clean`: no maintainer attention required.
- `Needs Attention`: maintainer attention suggested, but not a release blocker.
- `Blocker`: maintainer attention urgently needed, for example an undocumented breaking change, likely security vulnerability, inadequate release-critical testing, or missed release-blocking corner case.

Use `Blocker` for any plausible security issue, undocumented breaking API change, or serious release risk. Use `Needs Attention` for quality gaps that should be checked but are unlikely to block release.

Treat problematic post-approval commits as review-quality failures. Use `Blocker` if a post-approval commit materially changes behavior, risk, API compatibility, security posture, or release testing needs without evidence of renewed review. Use `Needs Attention` if the post-approval commit is probably harmless but should be checked by a maintainer.

If the commit introduces significant shipped or documented behavior that should be manually tested before release, the verdict cannot be `Clean`; use `Needs Attention` with a maintainer to-do unless the missing manual testing creates serious release risk, in which case use `Blocker`. If the behavior is not in the release surface, manual testing may be mentioned as residual risk without changing a `Clean` verdict.

Keep the verdict, one-sentence explanation, maintainer to-do list, and evidence internally consistent:

- If the evidence says a maintainer should check something before release, the verdict cannot be `Clean`; include a `Maintainer To-Do` section.
- If the verdict is `Clean`, do not include language like "should verify before release", "maintainer should check", or "warrants manual verification" unless you explicitly conclude it is not part of the release surface and does not require maintainer action.
- If manual release testing is uncertain, make the to-do "confirm whether this is in the release surface; if so, test it" rather than asserting a blocker.
- Attribute findings carefully. Do not credit a shout out or major catch to a reviewer unless the evidence clearly supports that attribution.

## Output Format

Use this structure:

```markdown
## Summary

One sentence summarizing what the PR does.

## Author

@github-handle

## Shout Outs

- Only include this section if one or more collaborators or reviewers meet the high bar for exceptional involvement.
- Keep each item concise and name the contribution.
- Omit routine reviewers, approvers, and commenters even when their feedback was useful or technically correct.
- If nobody meets the high bar, omit this section entirely.

## Verdict: Clean|Needs Attention|Blocker

One sentence explaining the core reason for the grade.

## Maintainer To-Do

- Only include this section for Needs Attention or Blocker.
- Keep items concise and focused on project quality.
- Do not nitpick style or small local code issues unless they affect release quality, API compatibility, security, testing, review integrity, or documentation.

## Evidence

- Review:
- Post-approval commits:
- Tests:
- Manual release testing:
- API compatibility:
- Security:
- Documentation:
```

For `Clean`, omit `Maintainer To-Do` unless there is a concrete non-blocking action. In `Evidence`, cite the commit, PR, docs, tests, or files inspected.

---
name: commit-review
description: Analyze a GitHub commit and judge whether it was good for the project. Use when asked to review a merged commit, release candidate commit, branch commit, or PR-associated commit for review quality, test adequacy, breaking changes, security risk, release-hand-test needs, and documentation completeness.
---

# Commit Review

Review one commit in the context of its GitHub repository and associated PR. Produce a verdict
and evidence for a maintainer, and a to-do list for a tester. Not a line-by-line code review.
Those are two readers; see "To-dos" for the second, whose list is the part that leaves here.

If the caller provides an output file path, write the artifact before finishing:

- Machine-readable JSON to the requested `.json` path.

The JSON must use this shape:

```json
{
  "repo": "OWNER/REPO",
  "commit_sha": "COMMIT_SHA",
  "pr_number": 123,
  "merge_date": "2026-06-09T12:34:56Z",
  "summary": "One sentence summarizing what the PR does.",
  "author": "@github-handle",
  "reviewers": [
    "@github-handle"
  ],
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
      "text": "One thing a tester can do to the release candidate, and what they should see."
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
- Identify the PR merge date as an ISO 8601 timestamp and put it in `merge_date`. If the associated PR is not merged or no merge date is available, use an empty string.
- Identify the author's GitHub handle.
- Identify the GitHub handles of people who substantively reviewed the PR. Put them in the `reviewers` JSON array even if they do not meet the high bar for a shout out. Do not include the PR author as a reviewer unless they also reviewed someone else's substantial changes on the PR.
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

If this commit adds or changes something a user can reach, and nothing shows a human exercised
it, write a to-do. User-reachable covers all of: an API endpoint, a CLI command or flag, any GUI
change at all, a config option or default, a newly supported model or backend, an installed
artifact or package, and documented behavior.

**Passing CI is not a human exercising it** unless the test drives the same path a user would.
A feature with thorough unit tests and no end-to-end run still needs one.

The exception is work that ships nothing this release — build-system support for an artifact or
platform not yet published or advertised. Note the residual risk in evidence; write no to-do.

### API Compatibility

Flag any API breaking change whatsoever, including schema, protocol, config, CLI contract, exported API, documented behavior, persistence format, or integration behavior.

Lead with what the person upgrading meets: what used to work, what happens now, what they must
change. The release notes and the Discord post are built from this, so an entry about which
module moved or which build variable was renamed reaches users as a sentence they cannot act
on. Internals come after, if they matter.

### Security And Malice

Look for any concern whatsoever that the commit is malicious or introduces a security vulnerability. Treat supply-chain changes, credential handling, network calls, code execution paths, auth changes, permission broadening, telemetry, obfuscation, and suspicious generated/minified blobs as high signal.

### Documentation

- API changes must be reflected comprehensively in the spec.
- Major new features should include an example or guide.
- Behavior changes must update existing affected documentation.

## To-dos

Your to-dos are copied verbatim onto a tester's release checklist. That reader has the
candidate installed, has never seen this code, and will not read the diff.

**Name what they can touch** — the command, flag, endpoint, setting, page. Never a function,
file, module or SHA: they cannot find those and cannot type them.

**Say what to do and what should happen.** "Do this, expect that." An item opening with
Consider, Investigate, Evaluate, Assess, or Verify/Decide whether has no pass and no fail, so
the tester has nothing to report back.

**Assume they have only the release.** No "the change", no "this PR", no reviewer's name, no SHA.

| Instead of | Write |
|---|---|
| Verify whether `parse_config()` is called per request and consider caching. | Start the server with a large config and confirm the first request is as fast as later ones. |
| Confirm the new `--flag` behaves as the reviewer expected. | Run `tool build --flag value` and confirm the output names the value you passed. |
| Check the post-approval commit did not change behavior. | Run the documented quickstart and confirm each step gives the output the guide shows. |

Findings that fail this test are still real — a possible hot path, an unreviewed late commit,
test debt. Put them in `evidence`, where the maintainer reads them.

That escape is for concerns with **no user-visible surface**, not for work that is awkward to
phrase. If this commit changes something a user can see and nothing shows anyone exercised it,
that is a to-do, and finding the words is the job.

## Verdict

Return exactly one grade:

- `Clean`: no maintainer attention required.
- `Needs Attention`: maintainer attention suggested, but not a release blocker.
- `Blocker`: maintainer attention urgently needed, for example an undocumented breaking change, likely security vulnerability, inadequate release-critical testing, or missed release-blocking corner case.

Use `Blocker` for any plausible security issue, undocumented breaking API change, or serious release risk. Use `Needs Attention` for quality gaps that should be checked but are unlikely to block release.

Treat problematic post-approval commits as review-quality failures. Use `Blocker` if a post-approval commit materially changes behavior, risk, API compatibility, security posture, or release testing needs without evidence of renewed review. Use `Needs Attention` if the post-approval commit is probably harmless but should be checked by a maintainer.

If the commit introduces significant shipped or documented behavior that should be manually tested before release, the verdict cannot be `Clean`; use `Needs Attention` with a maintainer to-do unless the missing manual testing creates serious release risk, in which case use `Blocker`. If the behavior is not in the release surface, manual testing may be mentioned as residual risk without changing a `Clean` verdict.

The verdict is a maintainer's attention; the to-do list is a tester's work. They move
independently: a test-only commit with an unreviewed late change is `Needs Attention` with an
empty list, which is not a contradiction and not a reason to invent an errand. One direction is
fixed — a to-do means the verdict is not `Clean`, because work before shipping is attention.

Keep the verdict, one-sentence explanation, maintainer to-do list, and evidence internally consistent:

- If somebody has to exercise this release by hand before it ships, the verdict cannot be `Clean`, and that exercise is a to-do rather than a sentence in evidence.
- If the verdict is `Clean`, do not include language like "should verify before release", "maintainer should check", or "warrants manual verification" unless you explicitly conclude it is not part of the release surface and does not require maintainer action.
- If you are unsure whether the behavior is in the release surface, decide here, on the evidence you have. In: write the test as a to-do. Out: say so in evidence. Never hand the uncertainty over as an item — "check whether this matters" is not something anybody can carry out.
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
- One instruction per item, written to the tester described in "To-dos": the surface they can
  touch, what to do to it, and what they should see.
- Anything that fails that test goes in Evidence instead. Do not reword an internal concern
  into tester-shaped language to keep it on the list.

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

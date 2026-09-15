---
name: commit-review
description: Analyze a GitHub commit and judge whether it was good for the project. Use when asked to review a merged commit, release candidate commit, branch commit, or PR-associated commit for review quality, test adequacy, breaking changes, security risk, release-hand-test needs, and documentation completeness.
---

# Commit Review

Review one commit in the context of its GitHub repository and associated PR. Produce a verdict
and its evidence for a maintainer, and a to-do list for a tester. Not a line-by-line code review.

Those two readers are different people and want different things, and the to-do list is the one
that leaves this repository: every item you write there is copied, word for word, onto the
checklist a tester works through on a release candidate. Write it for them. The section "To-dos"
below is not a style note; it is who the list is for.

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

Flag significant shipped or documented behavior that should be tested by hand before the next release, especially:

- New CLI commands or user-visible CLI features.
- Any GUI app change whatsoever.

Do not treat every new platform/build-system capability as release-blocking manual-test work. First decide whether the behavior is part of the upcoming release surface: shipped artifacts, documented supported platforms, user-visible behavior, install paths users can exercise in the release, or release promises in docs. If the change only prepares build-system support for artifacts or platforms that are not actually shipped or advertised in the release, note the residual risk in evidence but do not require maintainer action solely for manual testing.

### API Compatibility

Flag any API breaking change whatsoever, including schema, protocol, config, CLI contract, exported API, documented behavior, persistence format, or integration behavior.

Describe it the way the person who upgrades meets it: what used to work, what happens now, and
what they have to change. The release notes and the Discord announcement are both built from
this, so an entry written in terms of internal machinery — which module moved, which build
variable was renamed, which function now returns something else — reaches users as a sentence
they cannot act on. Record the internals in the same field afterwards if they matter, but lead
with the upgrade.

### Security And Malice

Look for any concern whatsoever that the commit is malicious or introduces a security vulnerability. Treat supply-chain changes, credential handling, network calls, code execution paths, auth changes, permission broadening, telemetry, obfuscation, and suspicious generated/minified blobs as high signal.

### Documentation

- API changes must be reflected comprehensively in the spec.
- Major new features should include an example or guide.
- Behavior changes must update existing affected documentation.

## To-dos

A to-do is read by a tester, not by you and not by the maintainer who merged this. Picture them:
the release candidate is installed on their machine, they have never seen this repository, they
do not know what this PR changed, and they are not going to read the diff. Every item you write
has to survive that reader, and that gives you three rules.

**Name what they can touch.** The command, the flag, the endpoint, the setting, the model, the
installer, the page, the button — the thing as a user meets it. Never a function, method, class,
file, header, module, variable, or commit SHA. Those can only be found by opening the source,
which this reader will not do, and naming one tells them nothing about what to type. If the
change has no user-visible surface you can name, that is a strong sign you are not looking at a
to-do; see below.

**Say what to do and what should happen.** Every to-do is an instruction with an observable
outcome: do this, and you should see that. An item that opens with "Consider", "Investigate",
"Look into", "Evaluate", "Assess", or "Verify/Decide/Determine whether" is a thought you had,
not a task anyone can finish — it has no pass and no fail, so the tester has nothing to report
back. If you cannot say what the right answer looks like, there is no to-do here.

**Assume they have the release and nothing else.** No "the change", no "this PR", no "as
@someone noted in review", no SHA, no pointing at the diff or the discussion. An item that only
makes sense while looking at the PR is an item the tester cannot begin.

The test, applied to every item before you keep it: *could somebody who has never seen this
repository carry this out with only the release candidate installed, and know whether it
passed?* Three ways the same item usually goes wrong, and what each looks like fixed:

| Instead of | Write |
|---|---|
| Verify whether `parse_config()` is called per request and consider caching the compiled pattern. | Start the server with a large config file and confirm the first request answers in about the same time as later ones. |
| Confirm the new `--flag` handling in the CLI entry point behaves as the reviewer expected. | Run `tool build --flag value` and confirm the output names the value you passed. |
| Check that the post-approval commit did not change behavior. | Run the documented quickstart end to end and confirm each step produces the output the guide shows. |

### What to do with everything else

Plenty of what a review turns up is real and is not a to-do: a function that might be a hot
path, a refactor worth revisiting, a reviewer who may not have seen a late commit, test debt, a
question about internal structure, a decision somebody should make. That is not a reason to
discard it, and it is not a reason to dress it up as a tester instruction. **Put it in
`evidence`**, under whichever key it belongs to, where the maintainer reads it. Leave the to-do
list to work a tester can actually do.

This matters more than it sounds. A list that mixes the two costs the tester the ability to
trust any of it: once two items in a row turn out to be unperformable, the rest stops being read.

**The escape is for concerns with no user-visible surface, not for work that is awkward to
phrase.** If this commit changes something a user can see — a command, a flag, an endpoint, a
setting, an install path, a page — and nothing in the evidence shows somebody exercised it,
that is a to-do, and your job is to find the words for it. Naming the surface is the work.
Moving it to evidence because the first sentence you tried came out in terms of the code is
the failure this section exists to prevent, and it is worse than the shape it replaced: an
unperformable item at least tells the tester something exists. Silence tells them nothing.

Write the to-do when either is true: the release ships behavior nobody has exercised by hand,
or somebody upgrading could be surprised. Write nothing only when the commit genuinely leaves
no one anything to do, which is a real and common answer for an internal refactor with tests.

## Verdict

Return exactly one grade:

- `Clean`: no maintainer attention required.
- `Needs Attention`: maintainer attention suggested, but not a release blocker.
- `Blocker`: maintainer attention urgently needed, for example an undocumented breaking change, likely security vulnerability, inadequate release-critical testing, or missed release-blocking corner case.

Use `Blocker` for any plausible security issue, undocumented breaking API change, or serious release risk. Use `Needs Attention` for quality gaps that should be checked but are unlikely to block release.

Treat problematic post-approval commits as review-quality failures. Use `Blocker` if a post-approval commit materially changes behavior, risk, API compatibility, security posture, or release testing needs without evidence of renewed review. Use `Needs Attention` if the post-approval commit is probably harmless but should be checked by a maintainer.

If the commit introduces significant shipped or documented behavior that should be manually tested before release, the verdict cannot be `Clean`; use `Needs Attention` with a maintainer to-do unless the missing manual testing creates serious release risk, in which case use `Blocker`. If the behavior is not in the release surface, manual testing may be mentioned as residual risk without changing a `Clean` verdict.

The verdict and the to-do list answer two different questions, and they move independently.
The verdict is about **a maintainer's attention**: is there something here somebody who owns
this project should know about? The to-do list is about **a tester's work**: is there something
somebody has to exercise on the release candidate? A test-only commit with a late unreviewed
change needs the first and produces none of the second, and that is a `Needs Attention` with an
empty list — not a contradiction, and not a reason to invent an errand to justify the grade.

The one direction that is fixed: a to-do means the verdict is not `Clean`. Work somebody has to
do before shipping is, by definition, attention.

Keep the verdict, one-sentence explanation, maintainer to-do list, and evidence internally consistent:

- If somebody has to exercise this release by hand before it ships, the verdict cannot be `Clean`, and that exercise is a to-do rather than a sentence in evidence.
- If the verdict is `Clean`, do not include language like "should verify before release", "maintainer should check", or "warrants manual verification" unless you explicitly conclude it is not part of the release surface and does not require maintainer action.
- If you are unsure whether the behavior is in the release surface, decide that here, on the evidence you have — it is your judgement, not the tester's errand. When it is in, write the test as a to-do. When it is out, say so in evidence and leave the list alone. What you must not do is hand the uncertainty over as an item, because "check whether this matters" is not something anybody can carry out.
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

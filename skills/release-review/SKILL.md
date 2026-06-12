---
name: release-review
description: Synthesize stored commit-review results into a release-readiness verdict and a tight, prioritized maintainer to-do list. Use when asked to judge whether a branch is ready to release.
---

# Release Review

The reader is the maintainer about to press the release button. Your job is to hand them exactly what they need to make that call: one verdict, and the shortest possible list of things they would regret shipping without doing. Everything else is noise that costs them time they are spending on a release.

The input includes the repo, branch, release tag, range start tag, head SHA, and a per-commit digest of stored commit reviews (summary, verdict, open to-dos, and test/compatibility/security evidence). Synthesize from this digest; do not re-review diffs unless the digest is clearly insufficient.

## The inclusion test

A to-do earns its place only if both are true:

1. **The maintainer would regret shipping without acting on it.**
2. **Users would notice the consequence in this release** — broken behavior, a missing or untested headline feature, a surprise breaking change, a security exposure.

Everything that fails the test does not get a lower priority — it gets omitted. Specifically excluded, always: code-quality follow-ups, "add tests later" debt, refactoring suggestions, review-process observations ("review was light", "approval came after the bot"), CI flakiness that does not affect shipped artifacts, documentation polish unrelated to behavior changes, and anything whose natural deadline is after the release.

The test cuts both ways: a short list is a constraint, not the goal, and a missing P0/P1 is a worse failure than an extra one. Some things always pass the test when present in the range — a user-facing breaking change whose migration is not yet in the release notes, and a new headline feature with no test evidence on its advertised platforms. "Not a showstopper" does not mean "omit": anything a maintainer should do before shipping is a P1 by definition.

## Priorities

Exactly two priorities exist:

- **P0 — do not ship until resolved.** Evidence of user-visible breakage in shipped artifacts, a likely security issue, a breaking change with no migration path, or a headline feature whose release packaging/tests are failing with the cause not yet understood. Uncertainty about whether release artifacts are broken is itself P0: "we don't know if the package works" blocks a release the same way "the package is broken" does.
- **P1 — verify before shipping.** New user-facing behavior in this release that lacks test evidence and needs a human to confirm it works: a new backend on its advertised platforms, a new command end-to-end, a breaking change's migration story actually written down where users will see it.

There is no P2. If something matters for this release it is P0 or P1; if it does not, it is not in the list.

## Triage first

Every digest entry that has `open_todos` gets an explicit decision before anything else is written. There are exactly three decisions:

- `P0` — this entry contains a do-not-ship-until-resolved issue.
- `P1` — this entry contains something a human must verify before shipping.
- `omit` — users would not notice; say why in one clause ("test debt", "process note", "post-release cleanup").

The decisions go in the artifact's `triage` array, one entry per digest id, and they bind you: the verdict is computed from them (any P0 → `Blocked`; any P1 and no P0 → `Needs Attention`; all omitted → `Ready`), and every kept decision must be represented in `prioritized_todos`. Several kept entries usually merge into one themed to-do — merged entries keep their P0/P1 decision; `omit` only ever means "users would not notice", never "covered elsewhere". The CLI rejects artifacts whose triage is incomplete or whose verdict disagrees with it.

The triage is your worksheet, not the maintainer's reading material. Digest ids like `c5` exist only inside `triage`. Everything else in the artifact — `verdict_reason`, `prioritized_todos`, `evidence` — is written for a human who has never seen the digest: name the feature or behavior ("the Moonshine backend", "the pi agent integration", "the env var removal"), never an id. The CLI rejects digest ids in human-facing fields.

## Writing the to-dos

- At most 6 items. A release with more than 6 genuine ship-blockers and verifications usually means themes were not merged.
- Group manual verification by release-test theme, not by commit: one to-do covering the new-feature smoke matrix (naming each surface to touch) beats five one-feature to-dos.
- Each to-do is one sentence that starts with the action, names the user-visible thing at stake, and says how to check it: "Run X on Y and confirm Z." Never "Consider...", "Note that...", or "Investigate whether..." without saying what decision the answer feeds.
- When several commit reviews repeat the same concern (for example, the same CI test failing across multiple merges), that repetition is signal — merge it into one to-do and say it recurred.

## Verdict

Exactly one of:

- `Ready` — the to-do list is empty.
- `Needs Attention` — at least one P1 and no P0.
- `Blocked` — at least one P0.

The verdict and the list must agree; if the release can only ship after a check happens, that check is in the list and the verdict is not `Ready`. Never output other verdict words (`Pass`, `Conditional`, `Clean`, ...).

Apply this reflexively to your own prose: if your `verdict_reason` or evidence mentions anything that should happen before shipping, the verdict is not `Ready` and each such thing is a to-do. Burying pre-release work in the reason text while reporting `Ready` is the worst possible output — the maintainer reads the verdict and ships.

## JSON artifact

Writing the artifact to the caller-provided `.json` path is mandatory before finishing; the CLI reads that file after the skill exits. Use exactly this shape:

`verdict_reason` is your answer if the maintainer asked you "can we ship?" in person: one or two sentences that name what matters in this release and exactly what stands between it and shipping. It is not a summary of the artifact — no commit statistics, no triage accounting, no restating the to-do list, no digest ids. "Two breaking changes still need release-note coverage and the new Moonshine backend hasn't been verified on its advertised platforms; nothing else blocks the release." is the register to hit.

```json
{
  "verdict": "Blocked",
  "verdict_reason": "Nothing ships until X is fixed: users hit Y on Z. Everything else is release-note coverage.",
  "triage": [
    {"id": "c3", "decision": "P1", "why": "new backend needs platform verification"},
    {"id": "c7", "decision": "omit", "why": "test debt, post-release"}
  ],
  "prioritized_todos": [
    {"priority": "P0", "text": "Resolve X so that users get Y; check by Z."},
    {"priority": "P1", "text": "Run A on B and confirm C."}
  ],
  "evidence": {
    "coverage": "What range was reviewed and anything not covered.",
    "blockers": "Short synthesis of what drove the verdict.",
    "manual_testing": "What human verification this release needs and why.",
    "breaking_changes": "User-facing breaking changes and their migration story.",
    "security": "Security-relevant observations, or 'none observed'."
  }
}
```

- `prioritized_todos` is the only action field. Do not emit `recommendations`, `open_release_risks`, or other alternates.
- `evidence` values are one or two sentences of synthesis each — no PR-by-PR lists, no statistics, no commit inventories, no shout-outs.
- Counts only when the digest directly supports them; never claim "all CI passed" from absence of evidence.

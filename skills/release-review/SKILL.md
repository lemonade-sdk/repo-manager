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

**Documenting breaking changes is never a to-do.** Capture every user-facing breaking change in the `breaking_changes` field instead; the release-announcement step reads that list and documents each one with migration guidance under an enforced coverage check, so "write the migration note for X" is already done by the pipeline and the maintainer has nothing to act on. The only time a breaking change becomes a to-do is when the break itself is *unintended* — a regression that should be fixed or reverted before shipping, not a deliberate change that merely needs writing up. A deliberate breaking change, however large, goes in `breaking_changes` and nowhere in the to-do list.

The test cuts both ways: a short list is a constraint, not the goal, and a missing P0/P1 is a worse failure than an extra one. Some things always pass the test when present in the range — a new headline feature with no test evidence on its advertised platforms. "Not a showstopper" does not mean "omit": anything a maintainer should do before shipping is a P1 by definition.

## Priorities

Exactly two priorities exist:

- **P0 — do not ship until resolved.** Evidence of user-visible breakage in shipped artifacts, a likely security issue, an *unintended* breaking change (a regression that slipped in, as opposed to a deliberate one — deliberate breaks belong in `breaking_changes`, never here), or a headline feature whose release packaging/tests are failing with the cause not yet understood. Uncertainty about whether release artifacts are broken is itself P0: "we don't know if the package works" blocks a release the same way "the package is broken" does. A new *shipping surface* this release is exactly that kind of unknown: when this release adds something users install or download — a new OS or distro target (a new Debian/Ubuntu/Fedora package), an installer, a container image, a wheel for a new platform — and nothing shows the built artifact actually installs and runs there, it is P0 until verified. An untested package is indistinguishable from a broken one and gates every user on that platform at the door; the larger the new surface, the less a passing CI build alone settles it.
- **P1 — verify before shipping.** New user-facing behavior, on a surface that already ships, that lacks test evidence and needs a human to confirm it works: a new backend on platforms Lemonade already supports, a new command end-to-end. The dividing line from P0 is blast radius: if what is unverified is one feature on familiar ground, P1; if it is the shipped artifact's basic integrity on new ground, escalate to P0.

There is no P2. If something matters for this release it is P0 or P1; if it does not, it is not in the list.

Work directly from the digest: for each entry that has `open_todos`, decide whether it is a P0, a P1, or omitted, then write only the kept ones into `prioritized_todos`. Several entries usually merge into one themed to-do. The to-do list is the whole worksheet — it is also written for a human who has never seen the digest, so name the feature or behavior ("the Moonshine backend", "the pi agent integration", "the backend watchdog"), never a digest id.

## Writing the to-dos

- At most 6 items. A release with more than 6 genuine ship-blockers and verifications usually means themes were not merged.
- Group manual verification by release-test theme, not by commit: one to-do covering the new-feature smoke matrix (naming each surface to touch) beats five one-feature to-dos.
- Each to-do is one sentence that starts with the action, names the user-visible thing at stake, and says how to check it: "Run X on Y and confirm Z." Never "Consider...", "Note that...", or "Investigate whether..." without saying what decision the answer feeds.
- When several commit reviews repeat the same concern (for example, the same CI test failing across multiple merges), that repetition is signal — merge it into one to-do and say it recurred.

## Verdict

The verdict is computed from your to-do list, so you cannot contradict it:

- `Ready` — the list is empty.
- `Needs Attention` — at least one P1 and no P0.
- `Blocked` — at least one P0.

The list *is* the verdict: if the release can only ship after a check happens, that check is a to-do. Apply this to your prose too — if `verdict_reason` or evidence mentions anything that should happen before shipping, it is a to-do, not a sentence. Burying pre-release work in prose while the list is empty is the worst possible output: the maintainer reads `Ready` and ships.

## JSON artifact

Writing the artifact to the caller-provided `.json` path is mandatory before finishing; the CLI reads that file after the skill exits. Use exactly this shape:

`verdict_reason` is your answer if the maintainer asked you "can we ship?" in person: one or two sentences that name what matters in this release and exactly what stands between it and shipping. It is not a summary of the artifact — no commit statistics, no restating the to-do list, no digest ids. "Two breaking changes still need release-note coverage and the new Moonshine backend hasn't been verified on its advertised platforms; nothing else blocks the release." is the register to hit.

```json
{
  "verdict": "Blocked",
  "verdict_reason": "Nothing ships until X is fixed: users hit Y on Z. Everything else is release-note coverage.",
  "prioritized_todos": [
    {"priority": "P0", "text": "Resolve X so that users get Y; check by Z."},
    {"priority": "P1", "text": "Run A on B and confirm C."}
  ],
  "breaking_changes": [
    "Removed the --foo flag; pass --bar instead.",
    "Renamed the baz model id to baz-v2; old id no longer resolves."
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
- `breaking_changes` is the canonical, deduplicated list of every user-facing breaking change shipping in this release — one entry per distinct change, each a single sentence naming the change and its migration ("Removed X; use Y instead."). This list is the source of truth: the release-announcement step reads it and must surface every entry, so it must be complete and must not merge two real breaking changes into one entry or list a non-breaking change. Use `[]` when there are none. It must agree with `evidence.breaking_changes`: the prose summarizes the same set this list enumerates. A change that appears here must **not** also appear as a to-do: documenting it is the announcement step's job, enforced automatically, so a deliberate breaking change is captured here and adds nothing to `prioritized_todos`.
- `evidence` values are one or two sentences of synthesis each — no PR-by-PR lists, no statistics, no commit inventories, no shout-outs.
- Counts only when the digest directly supports them; never claim "all CI passed" from absence of evidence.

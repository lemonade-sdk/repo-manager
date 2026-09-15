---
name: release-review
description: Synthesize stored commit-review results into a release-readiness verdict, a tight prioritized maintainer to-do list, and a per-platform tester plan. Use when asked to judge whether a release bucket is ready to ship.
---

# Release Review

The reader is the maintainer about to press the release button. Your job is to hand them
exactly what they need to make that call: one verdict, the shortest possible list of things
they would regret shipping without doing, and a plan the testers can pick up. Everything else
is noise that costs them time they are spending on a release.

The input includes the repo, branch, release bucket, range start tag, head SHA, any open
`candidate` issues testers filed against this bucket, and a per-commit digest of stored
commit reviews. Each digest entry carries `pr_number` and `author` alongside the summary,
verdict, to-dos, and test/compatibility/security evidence. Synthesize from this digest; do
not re-review diffs unless the digest is clearly insufficient. The `pr_number` and `author`
are what let you tell the maintainer who to ask about each to-do — carry them through (see
"Writing the to-dos").

## The inclusion test

A to-do earns its place only if both are true:

1. **The maintainer would regret shipping without acting on it.**
2. **Users would notice the consequence in this release** — broken behavior, a missing or
   untested headline feature, a surprise breaking change, a security exposure.

Everything that fails the test does not get a lower priority — it gets omitted. Specifically
excluded, always: code-quality follow-ups, "add tests later" debt, refactoring suggestions,
review-process observations ("review was light", "approval came after the bot"), CI flakiness
that does not affect shipped artifacts, documentation polish unrelated to behavior changes,
and anything whose natural deadline is after the release.

**Documenting breaking changes is never a to-do.** Capture every user-facing breaking change
in the `breaking_changes` field instead; the release-notes and release-announcement steps read
that list and document each one under an enforced coverage check, so "write the migration note
for X" is already done by the pipeline and the maintainer has nothing to act on. The only time
a breaking change becomes a to-do is when the break itself is *unintended* — a regression that
should be fixed or reverted before shipping, not a deliberate change that merely needs writing
up. A deliberate breaking change, however large, goes in `breaking_changes` and nowhere in the
to-do list.

The test cuts both ways: a short list is a constraint, not the goal, and a missing P0/P1 is a
worse failure than an extra one. Some things always pass the test when present in the range —
a new headline feature with no test evidence on its advertised platforms. "Not a showstopper"
does not mean "omit": anything a maintainer should do before shipping is a P1 by definition.

## Tester reports

Open `candidate` issues are tester feedback on a build of this bucket: a human installed a
candidate and something went wrong. Every one of them becomes a to-do, no exceptions — the
inclusion test does not apply, because a human already applied it by filing the issue. Each
to-do says what the report is and which outcome the maintainer should pick: **fix later**
(ship as is, the issue stays open), **hotfix** (fix on the release branch before tagging), or
**revert** (take the offending change back out). Name the issue number in the to-do text so
the maintainer can open it, and pick the priority from what the report describes.

## Priorities

Exactly two priorities exist:

- **P0 — do not ship until resolved.** Evidence of user-visible breakage in shipped
  artifacts, a likely security issue, an *unintended* breaking change (a regression that
  slipped in, as opposed to a deliberate one — deliberate breaks belong in
  `breaking_changes`, never here), or a headline feature whose release packaging/tests are
  failing with the cause not yet understood. Uncertainty about whether release artifacts are
  broken is itself P0: "we don't know if the package works" blocks a release the same way
  "the package is broken" does. A new *shipping surface* this release is exactly that kind of
  unknown: when this release adds something users install or download — a new OS or distro
  target, an installer, a container image, a wheel for a new platform — and nothing shows the
  built artifact actually installs and runs there, it is P0 until verified. An untested
  package is indistinguishable from a broken one and gates every user on that platform at the
  door; the larger the new surface, the less a passing CI build alone settles it.
- **P1 — verify before shipping.** New user-facing behavior, on a surface that already ships,
  that lacks test evidence and needs a human to confirm it works: a new backend on platforms
  Lemonade already supports, a new command end-to-end. The dividing line from P0 is blast
  radius: if what is unverified is one feature on familiar ground, P1; if it is the shipped
  artifact's basic integrity on new ground, escalate to P0.

There is no P2. If something matters for this release it is P0 or P1; if it does not, it is
not in the list.

Work directly from the digest: for each entry that has to-dos, decide whether it is a P0, a
P1, or omitted, then write only the kept ones into `prioritized_todos`. Several entries
usually merge into one themed to-do. The to-do list is the whole worksheet — it is also
written for a human who has never seen the digest, so name the feature or behavior ("the
Moonshine backend", "the pi agent integration", "the backend watchdog"), never a digest id.

## Writing the to-dos

- At most 6 items. A release with more than 6 genuine ship-blockers and verifications usually
  means themes were not merged.
- Group manual verification by release-test theme, not by commit: one to-do covering the
  new-feature smoke matrix (naming each surface to touch) beats five one-feature to-dos.
- Each to-do is one sentence that starts with the action, names the user-visible thing at
  stake, and says how to check it: "Run X on Y and confirm Z." Never "Consider...", "Note
  that...", or "Investigate whether..." without saying what decision the answer feeds.
- **End every to-do with an attribution tag naming who to ask and the PR(s) it came from**,
  so the maintainer knows who to chase: `(#1234, @author)`. Pull `pr_number` and `author`
  straight from the digest entries the to-do is built from — the PR number renders as a
  clickable link, and the handle is the person closest to the change. When a themed to-do
  merges several commits, list each contributing PR and its author: `(#1234 @alice, #1240
  @bob)`. Drop only the missing half of a tag if the digest lacks it; never invent either,
  and never omit the tag entirely when the digest has the data.
- When several commit reviews repeat the same concern (for example, the same CI test failing
  across multiple merges), that repetition is signal — merge it into one to-do, say it
  recurred, and tag every PR involved.

## The tester plan

The caller lists the platforms this project is hand-tested on. `tester_plan` carries one
entry per platform, in that order, and it is what a tester reads before picking up a machine.
For each platform:

- `changed`: what in this bucket touches that platform, in a sentence. When nothing does, say
  "nothing in this bucket" — that is a real answer, not a gap.
- `exercise`: what a human should actually do on it. Draw this from the commit reviews'
  `manual_release_testing` and `documentation` evidence: the new command to run, the app
  screen to open, the install path to walk. A platform nothing touched still gets the smoke
  check that proves the build works there — install it, start the server, run one model.

Name commands and features, not PRs. The tester has the machine in front of them and needs to
know what to type.

## Verdict

The verdict is computed from your to-do list, so you cannot contradict it:

- `Ready` — the list is empty.
- `Needs Attention` — at least one P1 and no P0.
- `Blocked` — at least one P0.

The list *is* the verdict: if the release can only ship after a check happens, that check is a
to-do. Apply this to your prose too — if `verdict_reason` or evidence mentions anything that
should happen before shipping, it is a to-do, not a sentence. Burying pre-release work in
prose while the list is empty is the worst possible output: the maintainer reads `Ready` and
ships.

## JSON artifact

Writing the artifact to the caller-provided `.json` path is mandatory before finishing; the
CLI reads that file after the skill exits. Use exactly this shape:

`verdict_reason` is your answer if the maintainer asked you "can we ship?" in person: one or
two sentences that name what matters in this release and exactly what stands between it and
shipping. It is not a summary of the artifact — no commit statistics, no restating the to-do
list, no digest ids. "Two breaking changes still need release-note coverage and the new
Moonshine backend hasn't been verified on its advertised platforms; nothing else blocks the
release." is the register to hit.

```json
{
  "verdict": "Blocked",
  "verdict_reason": "Nothing ships until X is fixed: users hit Y on Z. Everything else is release-note coverage.",
  "prioritized_todos": [
    {"priority": "P0", "text": "Resolve X so that users get Y; check by Z (#1234, @author)."},
    {"priority": "P1", "text": "Run A on B and confirm C (#1240, @author)."}
  ],
  "breaking_changes": [
    "Removed the --foo flag; pass --bar instead.",
    "Renamed the baz model id to baz-v2; old id no longer resolves."
  ],
  "tester_plan": [
    {"platform": "Windows", "changed": "The installer now bundles the new backend.", "exercise": "Install from the .exe, start the server, and pull one model."},
    {"platform": "Docker", "changed": "nothing in this bucket", "exercise": "Run the image and hit /api/v1/health."}
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

- `prioritized_todos` is the only action field. Do not emit `recommendations`,
  `open_release_risks`, or other alternates.
- `breaking_changes` is the canonical, deduplicated list of every user-facing breaking change
  shipping in this release — one entry per distinct change, each a single sentence naming the
  change and its migration ("Removed X; use Y instead."). **Write each entry as the user
  experiences it**, because the release page and the Discord post are both built from this
  list and inherit whatever is in it. Name what changed for someone running Lemonade and what
  they must do about it; leave out how it was implemented — build-system internals, action
  names, file paths, and refactors belong in `evidence.breaking_changes`, not here. "Versions
  are now dated, like 2026.39.1 instead of 11.9.0; pin the new format if you pin versions." is
  the register, not a summary of which scripts changed. This list is the source of truth:
  the release-notes and release-announcement steps read it and must surface every entry, so it
  must be complete and must not merge two real breaking changes into one entry or list a
  non-breaking change. Use `[]` when there are none. It must agree with
  `evidence.breaking_changes`: the prose summarizes the same set this list enumerates. A change
  that appears here must **not** also appear as a to-do.
- `tester_plan` covers every platform the caller listed, in that order, with both fields filled.
- `evidence` values are one or two sentences of synthesis each — no PR-by-PR lists, no
  statistics, no commit inventories, no shout-outs.
- Counts only when the digest directly supports them; never claim "all CI passed" from absence
  of evidence.

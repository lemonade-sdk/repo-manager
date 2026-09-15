---
name: release-review
description: Synthesize stored commit-review results into a release-readiness verdict and one prioritized checklist a tester works through on a release candidate. Use when asked to judge whether a release bucket is ready to ship.
---

# Release Review

The reader is a tester who has just installed a release candidate and wants to know what to
try. That is the only reader. Your job is to hand them one prioritized checklist they can work
through, in order, and nothing else.

The input includes the repo, branch, release bucket, range start tag, head SHA, the platforms
this project is tested on, any open `candidate` issues other testers have filed, and a
per-commit digest of stored commit reviews. Each digest entry carries `pr_number` and `author`
alongside the summary, verdict, to-dos, and test/compatibility/security evidence. Synthesize
from this digest; do not re-review diffs unless the digest is clearly insufficient. The
`pr_number` and `author` are what let a tester find the person to ask when something looks
wrong — carry them through (see "Writing the checklist").

## The inclusion test

An item earns its place on the checklist only if both are true:

1. **Somebody would regret shipping without checking it.**
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
for X" is already done by the pipeline and nobody has to act on it. The only time a breaking
change becomes a checklist item is when the break itself is *unintended* — a regression that
should be fixed or reverted before shipping, not a deliberate change that merely needs writing
up. A deliberate breaking change, however large, goes in `breaking_changes` and nowhere in the
checklist.

The test cuts both ways: a short list is a constraint, not the goal, and a missing P0/P1 is a
worse failure than an extra one. Some things always pass the test when present in the range —
a new headline feature with no test evidence on its advertised platforms. "Not a showstopper"
does not mean "omit": anything worth a tester's time before shipping is a P1 by definition.

## Tester reports

Open `candidate` issues are tester feedback on a build of this bucket: a human installed a
candidate and something went wrong. Every one of them becomes a checklist item, no exceptions —
the inclusion test does not apply, because a human already applied it by filing the issue. Each
item says what the report is and which outcome to pick: **fix later**
(ship as is, the issue stays open), **hotfix** (fix on the release branch before tagging), or
**revert** (take the offending change back out). Name the issue number in the item so a reader
can open it, and pick the priority from what the report describes.

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
P1, or omitted, then write only the kept ones into `checklist`. Several entries usually merge
into one item. The checklist is the whole artifact, and it is read by somebody who has never
seen the digest, so name the feature or behavior ("the Moonshine backend", "the `/v1/docs`
endpoint", "the backend watchdog"), never a digest id.

## Writing the checklist

- At most 6 items. A candidate with more than 6 things worth a tester's time usually means
  themes were not merged.
- Each item is one sentence that starts with the action, names the user-visible thing at
  stake, and says how to tell whether it worked: "Run X on Y and confirm Z." Never
  "Consider...", "Note that...", or "Investigate whether..." without saying what the answer
  decides.
- Group by what a tester does in one sitting, not by commit: one item covering the new-feature
  smoke matrix beats five one-feature items. Two commits that a tester would check in the same
  breath are one item.
- **End every item with the PR(s) and handle(s) it came from**: `(#1234, @author)`. Pull them
  straight from the digest. A tester who finds something broken needs to know who to tell, and
  the PR number is a link. When a merged item covers several commits, list each:
  `(#1234 @alice, #1240 @bob)`. Never invent either half, and never omit the tag when the
  digest has the data.
- When several commit reviews repeat the same concern, that repetition is signal: merge it
  into one item and tag every PR involved.

## Platforms

The caller lists the platforms this project is tested on. Every checklist item names the
platforms it applies to, in `platforms`, so a tester picking up a Fedora box can read only the
items that concern them. Use `["all"]` when an item applies everywhere.

Do not write an item for a platform this release did not touch. "Install it and check the
version" on a platform nothing changed is the filler that makes a checklist unreadable, and a
tester already knows to smoke-test the build they installed.

## Verdict

The verdict is computed from your checklist, so you cannot contradict it:

- `Ready` — the list is empty.
- `Needs Attention` — at least one P1 and no P0.
- `Blocked` — at least one P0.

The list *is* the verdict: if the release can only ship after a check happens, that check is a
checklist item. Apply this to your prose too — if `verdict_reason` or evidence mentions anything that
should happen before shipping, it is a to-do, not a sentence. Burying pre-release work in
prose while the list is empty is the worst possible output: a reader sees `Ready` and ships.

## JSON artifact

Writing the artifact to the caller-provided `.json` path is mandatory before finishing; the
CLI reads that file after the skill exits. Use exactly this shape:

`verdict_reason` is your answer if somebody asked you "can we ship?" in person: one or two
sentences that name what matters in this release and exactly what stands between it and
shipping. It is not a summary of the artifact — no commit statistics, no restating the
checklist, no digest ids. "Two breaking changes still need release-note coverage and the new
Moonshine backend hasn't been verified on its advertised platforms; nothing else blocks the
release." is the register to hit.

```json
{
  "verdict": "Blocked",
  "verdict_reason": "Nothing ships until X is fixed: users hit Y on Z. Everything else is release-note coverage.",
  "checklist": [
    {"priority": "P0", "platforms": ["all"], "text": "Resolve X so that users get Y; check by Z (#1234, @author)."},
    {"priority": "P1", "platforms": ["Windows", "Fedora"], "text": "Install the package and confirm C (#1240, @author)."}
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

- `checklist` is the only action field. Do not emit `prioritized_todos`, `tester_plan`,
  `recommendations`, or other alternates.
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
  that appears here must **not** also appear on the checklist.
- Every `platforms` entry is one of the platforms the caller listed, or `"all"`.
- `evidence` values are one or two sentences of synthesis each — no PR-by-PR lists, no
  statistics, no commit inventories, no shout-outs.
- Counts only when the digest directly supports them; never claim "all CI passed" from absence
  of evidence.

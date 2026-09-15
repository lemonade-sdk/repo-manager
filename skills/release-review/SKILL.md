---
name: release-review
description: Rate every maintainer to-do in a release bucket by priority and write the release-readiness verdict a tester works from. Use when asked to judge whether a release bucket is ready to ship.
---

# Release Review

The reader is a tester who has just installed a release candidate and wants to know what to
try, in what order. Everything the commit reviews asked for is already on their checklist. Your
job is to say how much each item matters to *this release*, and to answer "can we ship?".

You are rating, not curating. The caller carries every to-do through verbatim and assembles the
checklist itself, so an item you leave out of `ratings` is not an item you removed — it is a
judgement you failed to make, and it lands in P1 by default, in front of a tester who now has
to make it for you. Do not retype a to-do's text either. The maintainer reads the words the
commit review already wrote; that is what makes the same item tickable in both places.

The input includes the repo, branch, release bucket, range start tag, head SHA, the platforms
this project is tested on, any open `candidate` issues other testers have filed, and a
per-commit digest of stored commit reviews. Each digest entry carries `pr_number` and `author`
alongside the summary, verdict, to-dos, and test/compatibility/security evidence. Each to-do
carries an `id`, and that id is the whole contract: you answer with a priority per id.
Synthesize from this digest; do not re-review diffs unless the digest is clearly insufficient.

## Priorities

Three priorities exist, and every to-do gets exactly one.

- **P0 — do not ship until resolved.** Evidence of user-visible breakage in shipped
  artifacts, a likely security issue, an *unintended* breaking change (a regression that
  slipped in, as opposed to a deliberate one — deliberate breaks belong in
  `breaking_changes`), or a headline feature whose release packaging or tests are failing with
  the cause not yet understood. Uncertainty about whether release artifacts are broken is
  itself P0: "we don't know if the package works" blocks a release the same way "the package
  is broken" does. A new *shipping surface* is exactly that kind of unknown — when this
  release adds something users install or download, a new OS or distro target, an installer, a
  container image, a wheel for a new platform, and nothing shows the built artifact actually
  installs and runs there, it is P0 until verified. An untested package is indistinguishable
  from a broken one and gates every user on that platform at the door.

- **P1 — verify before shipping.** New user-facing behavior, on a surface that already ships,
  that lacks test evidence and needs a human to confirm it works: a new backend on platforms
  Lemonade already supports, a new command end-to-end. The dividing line from P0 is blast
  radius — one feature on familiar ground is P1; the shipped artifact's basic integrity on new
  ground is P0. When you are unsure between P1 and P2, the question that settles it is whether
  *users* would notice the consequence in this release. If they would, it is P1.

- **P2 — worth doing, not before this release.** Real work whose natural deadline is after
  shipping: code-quality follow-ups, test debt, refactoring, documentation polish unrelated to
  a behavior change, review-process observations, CI flakiness that does not affect shipped
  artifacts. P2 is not a wastebasket and not a verdict on the item's worth — it is a statement
  about *timing*. A tester works P0 and P1 before the release goes out and leaves P2 alone.

The verdict follows from the priorities, so you cannot contradict it: `Blocked` if anything is
P0, `Needs Attention` if anything is P1, `Ready` otherwise. A release whose every open item is
P2 is a release that can ship — which is exactly what P2 means.

Two failure modes matter more than the rest. Rating real pre-release work P2 ships a release
that should have been held. Rating housekeeping P1 buries the two items that mattered in a list
of twenty and costs the tester the ordering that makes the list worth having. Read each item
for what it would cost *users* if this release shipped without it, and let that decide.

## Tester reports

Open `candidate` issues are tester feedback on a build of this bucket: a human installed a
candidate and something went wrong. No commit review wrote these, so they are the one thing you
supply the words for — put each one in `extra_items`. Each says what the report is and which
outcome to pick: **fix later** (ship as is, the issue stays open), **hotfix** (fix on the
release branch before tagging), or **revert** (take the offending change back out). Name the
issue number in the text so a reader can open it, and pick the priority from what the report
describes. A human already decided it was worth filing, so nothing here is P2 without a reason
you can state.

## Breaking changes

**Documenting a breaking change is never a to-do.** Capture every user-facing breaking change
in `breaking_changes`; the release-notes and release-announcement steps read that list and
document each one under an enforced coverage check, so "write the migration note for X" is
already done by the pipeline. The only breaking change that also earns a *priority* is one that
was unintended — a regression to fix or revert before shipping, which is P0 like any other
regression. A deliberate break, however large, goes in `breaking_changes` and is not work.

## Platforms

The caller lists the platforms this project is tested on. Every rating names the platforms its
to-do applies to, so a tester picking up a Fedora box can read only the items that concern
them. Use `["all"]` when it applies everywhere, and name specific platforms only when the item
genuinely does not apply elsewhere — a to-do about Windows installer signing is `["Windows"]`,
a to-do about a server endpoint is `["all"]`.

## Verdict prose

`verdict_reason` is your answer if somebody asked you "can we ship?" in person: one or two
sentences that name what matters in this release and exactly what stands between it and
shipping. It is not a summary of the artifact — no commit statistics, no restating the
checklist, no digest ids, no priority counts. "Two breaking changes still need release-note
coverage and the new Moonshine backend hasn't been verified on its advertised platforms;
nothing else blocks the release." is the register to hit.

Write it fresh, from the priorities you just assigned. The caller shows you the previous run's
priorities so yours do not wobble between candidates, and deliberately does not show you its
prose — a sentence carried over from a shorter list is a sentence that miscounts this one.

If your prose mentions anything that has to happen before shipping, that thing is a P0 or a P1.
Claiming pre-release work in the prose while everything is rated P2 is the worst possible
output: a reader sees `Ready` and ships.

## JSON artifact

Writing the artifact to the caller-provided `.json` path is mandatory before finishing; the
CLI reads that file after the skill exits. Use exactly this shape:

```json
{
  "verdict_reason": "Nothing ships until X is fixed: users hit Y on Z. Everything else is release-note coverage.",
  "ratings": [
    {"id": "a1b2c3d-1", "priority": "P0", "platforms": ["all"]},
    {"id": "a1b2c3d-2", "priority": "P2", "platforms": ["all"]},
    {"id": "e4f5678-1", "priority": "P1", "platforms": ["Windows", "Fedora"]}
  ],
  "extra_items": [
    {"priority": "P0", "platforms": ["Snap"], "text": "Tester report #4120: the Snap build fails to start on 24.04. Decide fix later, hotfix, or revert."}
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

- `ratings` needs one entry for every `id` the caller listed, and nothing else. Do not emit
  `checklist`, `prioritized_todos`, or `tester_plan` — the caller builds the checklist.
- `extra_items` is only for work no commit review wrote, which in practice means `candidate`
  issues. Leave it `[]` when there are none. Never restate a digest to-do here.
- `breaking_changes` is the canonical, deduplicated list of every user-facing breaking change
  shipping in this release — one entry per distinct change, each a single sentence naming the
  change and its migration ("Removed X; use Y instead."). **Write each entry as the user
  experiences it**, because the release page and the Discord post are both built from this
  list and inherit whatever is in it. Name what changed for someone running Lemonade and what
  they must do about it; leave out how it was implemented — build-system internals, action
  names, file paths, and refactors belong in `evidence.breaking_changes`, not here. "Versions
  are now dated, like 2026.39.1 instead of 11.9.0; pin the new format if you pin versions." is
  the register, not a summary of which scripts changed. This list is the source of truth: the
  release-notes and release-announcement steps read it and must surface every entry, so it must
  be complete and must not merge two real breaking changes into one entry or list a
  non-breaking change. Use `[]` when there are none. It must agree with
  `evidence.breaking_changes`: the prose summarizes the same set this list enumerates.
- Every `platforms` entry is one of the platforms the caller listed, or `"all"`.
- `evidence` values are one or two sentences of synthesis each — no PR-by-PR lists, no
  statistics, no commit inventories, no shout-outs.
- Counts only when the digest directly supports them; never claim "all CI passed" from absence
  of evidence.

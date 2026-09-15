---
name: release-review
description: Rate every maintainer to-do in a release bucket by priority, so a tester works a release candidate in the right order. Use when asked to prepare a release bucket's tester checklist.
---

# Release Review

The reader is a tester who has just installed a release candidate and wants to know what to
try, in what order. Everything the commit reviews asked for is already on their checklist. Your
job is to say how much each item matters to *this release*.

**You do not decide whether the release ships.** There is no verdict here and nothing you write
is one. The release admin makes that call, from this checklist and from the testing they have
watched happen, and a one-word answer frozen at the moment you ran could only mislead them.
Rate the work; leave the decision alone.

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

**Everything on this checklist is work to do before the release ships.** The priority says what
a tester does *first*, not whether an item counts. A tester works down from P0, and if the
candidate has to go out before the list is finished, the priority is what tells them where the
damage of stopping is smallest. Nothing here is deferred to after the release — this is a
release checklist, and an item nobody should look at before shipping does not belong on it.

- **P0 — the release does not go out until this is resolved.** Evidence of user-visible
  breakage in shipped artifacts, a likely security issue, an *unintended* breaking change (a
  regression that slipped in, as opposed to a deliberate one — deliberate breaks belong in
  `breaking_changes`), or a headline feature whose release packaging or tests are failing with
  the cause not yet understood. Uncertainty about whether release artifacts are broken is
  itself P0: "we don't know if the package works" blocks a release the same way "the package
  is broken" does. A new *shipping surface* is exactly that kind of unknown — when this
  release adds something users install or download, a new OS or distro target, an installer, a
  container image, a wheel for a new platform, and nothing shows the built artifact actually
  installs and runs there, it is P0 until verified. An untested package is indistinguishable
  from a broken one and gates every user on that platform at the door.

- **P1 — check it before shipping; users would feel it if nobody did.** New user-facing
  behavior, on a surface that already ships, that lacks test evidence and needs a human to
  confirm it works: a new backend on platforms Lemonade already supports, a new command
  end-to-end. The dividing line from P0 is blast radius — one feature on familiar ground is P1;
  the shipped artifact's basic integrity on new ground is P0.

- **P2 — check it before shipping, but do it last.** Work whose consequence is real but
  narrow, or felt by somebody other than a user on release day: an internal document that now
  describes the wrong workflow, a confirmation that a post-approval commit was seen, a
  performance question worth answering before it becomes a habit. P2 is the honest answer to
  "if the candidate has to ship tonight and the list is not finished, what hurts least to
  leave?" — not "this is somebody else's problem".

Two failure modes matter more than the rest. Rating a real blocker P1 or P2 ships a release
that should have been held. Rating everything P0 or P1 buries the two items that mattered in a
list of twenty and costs the tester the ordering that makes the list worth having. Read each
item for what it would cost *users* if this release shipped without it, and let that decide.

## Tester reports

Open `candidate` issues are tester feedback on a build of this bucket: a human installed a
candidate and something went wrong. No commit review wrote these, so they are the one thing you
supply the words for — put each one in `extra_items`, written as the reproduction a tester can
run: what to install or run, what the reporter saw, what to look for. Name the issue number and
pick the priority from what the report describes.

Not the decision. Fix later, hotfix or revert is the release admin's call, made from what the
tester finds; "decide whether to revert" gives the tester nothing to do. These answer to the
same reader as a commit review's to-dos, and the caller rejects a function, source file or SHA
here too. A human already decided it was worth filing, so it is rarely the last thing a
tester should get to.

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

## JSON artifact

Writing the artifact to the caller-provided `.json` path is mandatory before finishing; the
CLI reads that file after the skill exits. Use exactly this shape:

```json
{
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
    "blockers": "Short synthesis of what a tester has to clear before this can ship.",
    "manual_testing": "What human verification this release needs and why.",
    "breaking_changes": "User-facing breaking changes and their migration story.",
    "security": "Security-relevant observations, or 'none observed'."
  }
}
```

- `ratings` needs one entry for every `id` the caller listed, and nothing else. Do not emit
  `checklist`, `prioritized_todos`, or `tester_plan` — the caller builds the checklist. Do not
  emit `verdict` or `verdict_reason`; they do not exist, and anything you put there is
  discarded.
- `extra_items` is only for work no commit review wrote, which in practice means `candidate`
  issues. Leave it `[]` when there are none. Never restate a digest to-do here.
- `breaking_changes` is the canonical, deduplicated list of every user-facing breaking change
  shipping in this release — one entry per distinct change, each a single sentence naming the
  change and its migration ("Removed X; use Y instead."). **Write each entry as the person who
  upgrades meets it**: what used to work, what happens now, what they must change. The release
  page and the Discord post publish this list word for word to people who have never seen this
  repository, so how it was done — a module that moved, a build variable renamed — belongs in
  `evidence.breaking_changes`, not here. The test: can somebody running Lemonade tell from the
  entry whether they are affected and what to do? "Versions are now dated, like 2026.39.1
  instead of 11.9.0; pin the new format if you pin versions." passes. "CMake version extraction
  moved to a Python-based git state derivation system" is true and tells them nothing. Watch
  the parentheses in particular: a sentence that states the impact and then brackets the file
  or symbol it happened in has written for both readers and served neither. Cut the bracket. This list is the source of truth: the
  release-notes and release-announcement steps read it and must surface every entry, so it must
  be complete and must not merge two real breaking changes into one entry or list a
  non-breaking change. Use `[]` when there are none. It must agree with
  `evidence.breaking_changes`: the prose summarizes the same set this list enumerates.
- Every `platforms` entry is one of the platforms the caller listed, or `"all"`.
- `evidence` values are one or two sentences of synthesis each — no PR-by-PR lists, no
  statistics, no commit inventories, no shout-outs.
- Counts only when the digest directly supports them; never claim "all CI passed" from absence
  of evidence.

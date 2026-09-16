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

Three priorities exist, and every to-do gets exactly one. **Everything on this checklist is
work to do before the release ships** — the priority says what a tester reaches for first, not
whether an item counts. Nothing here is deferred to after the release.

Rate each item by **what a user loses if this ships broken and nobody checked**. How new the
code is, how large the diff was, and how thin the test evidence looks all say how *likely* a
problem is — they say nothing about how much it would cost, and a likely problem with a
trivial consequence is still trivial.

- **P0 — a devastating break to new or existing setups.** Somebody cannot install, cannot
  start the server, cannot download a model, cannot reach the API, or a configuration that
  worked before the upgrade stops working. A version or packaging mistake stamped into a
  shipped artifact is P0 because it cannot be taken back once published, and so is anything
  that routes ordinary users to an untested build. Damage the user cannot see — wrong data,
  wrong model, a silently dropped guard — outranks damage that stops them, because it is what
  they do not think to report. A check that something which worked *last release* still works
  guards every existing setup, and outranks a check that a new feature works at all — new is
  not the same as important.

- **P1 — very annoying or incomplete behavior.** It works, but badly, or a feature this
  release advertises does not do what it says. A new endpoint that errors, a relay that drops
  what it was meant to carry, a cache that no longer holds. The user loses the feature, not the
  product, and usually has a way around it.

- **P2 — a minor annoyance.** A stale document, an example that needs a tweak, a diagnostic
  nobody reads, a surface nobody reaches on release day. Internal tooling belongs here by
  default: when a CI workflow or a release script fails, a maintainer does the step by hand,
  and "the release team would be inconvenienced" is not a user-visible consequence. Promote it
  only where its failure reaches a user.

**Most releases carry one or two P0s, and a real spread below them.** If everything is a P1,
nothing is: the tester loses the ordering that made the list worth having, and the two items
that mattered are buried among twenty that did not. Rate each item alone, on consequence, and
let the spread fall out — but a checklist that comes back nearly all one priority is a sign
the rating was skipped, not that the release is uniform.

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

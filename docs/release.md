# Release Artifacts

Job 2: everything a candidate ships with. One command builds all of it.

```bash
repo-manager release build --branch release-v2026.38 --head "$GITHUB_SHA"
```

`release build` is what the release workflow calls, as a `needs:` dependency of the job that
creates the GitHub release. Its exit code matters: a failure fails the release job, because a
published release page with no notes is worse than a candidate that did not publish.

In order, it:

1. Works out the bucket from the branch, the range start from the tag list, and the head from
   `--head` (default: the branch tip).
2. Runs `commit sweep` over the range, so any commit review missed on `main` is done now.
3. Writes `releases/<bucket>/review.json`, then `notes.md`, then `announcement.md`.

Each step is attempted even when an earlier one failed, so one run reports everything that is
wrong rather than only the first thing. The three steps are also available on their own —
`release review`, `release notes`, `release announce` — which is what you want while tuning a
skill.

## review.json

The tester's plan. It carries no verdict: whether the release is ready is the release
admin's call, made from this checklist and from the testing they have watched happen. A stored
one-word answer, frozen at the moment the model ran, would be wrong the first time somebody
worked an item and would invite a reader to ship on it.

- `checklist` holds **every** maintainer to-do in the range, in the words the commit review
  wrote, and the skill never touches those words. Which means the checklist is only ever as
  usable as those to-dos are: the reader is a tester with the candidate installed who has never
  seen the code, and that is enforced where the to-do is written, not here. The digest hands the model an `id` per
  to-do; the model answers with a priority and the platforms it applies to; Python assembles
  the list. That division is the point. A skill that also chose which to-dos survived could
  drop one silently, and the only evidence would be an absence nobody can see. Now an
  unrated id is a validation failure, not a deletion, and it lands in P1 by default.
  Each item carries the `commit` it came from, plus `pr_number` and `author` as fields rather
  than text appended to the sentence — which is what lets the dashboard treat the release
  checklist item and the commit's to-do as one to-do, with one checkbox.
- `priority` is the whole judgement the model contributes, and every item carrying one is work
  to do *before* the release ships, rated by what a user loses if it ships broken: **P0** a
  devastating break to a new or existing setup, **P1** very annoying or incomplete behavior,
  **P2** a minor annoyance. The axis is consequence, not likelihood — how new the code is or
  how thin its tests are says how likely a problem is, not what it would cost. The priority is
  the order a tester works in, and what tells them where the damage is smallest if a candidate
  has to go out before the list is finished. Nothing on a release checklist is deferred to
  after the release.
- `breaking_changes` is the canonical list. `notes.md` and `announcement.md` are reconciled
  against it — one bullet per entry, enforced — so a breaking change cannot reach users
  unannounced. Documenting one is therefore never a to-do.

**Tester reports.** Open issues in the tracked repo carrying the `candidate` label, filed
since the bucket's branch was cut, are folded into the prompt. No commit review wrote these,
so they are the one thing the skill supplies the words for — it returns them in `extra_items`,
each written as the reproduction a tester can run, and the run fails validation if one is
dropped. A human already decided it mattered by filing it.

The item is the reproduction, not the decision. Whether a report is fixed later, hotfixed or
reverted is the release admin's call and they make it from what the tester finds; "decide
whether to revert" gives the tester nothing to do and the admin nothing new to decide with.
Because these are the skill's own prose rather than a commit review's, they are held to the
same test every to-do is — see [commit reviews](commit-review.md#who-the-to-dos-are-for).

## notes.md

Exactly `## Headline` followed by `## Breaking Changes`. This is machine-consumed: the
lemonade release action reads it through the GitHub contents API and puts it on the release
page. Its structure is enforced — the two sections, single-depth bullets, three to five
headline bullets, and a breaking-change bullet count equal to the review's list.

It also shapes the release: the announcement is written from these headline bullets, so the
stories and their order are decided once, here.

## announcement.md

The Discord post, in the maintainer's voice. The CLI checks only what it can — that the post
exists, stays under 45 non-blank lines, pings the right audience, and covers every canonical
breaking change. Voice and story shaping are the skill's job.

**Hotfixes.** When the bucket already has a stable tag, the post covers only the commits
since that tag and opens with `@release` rather than `@everyone`. The people reading it are
running the build that broke.

## Human edits are authoritative

repo-manager records the SHA-256 of every file it writes into a bucket in
`releases/<bucket>/generated.json`. Before writing, it compares. A file whose content no
longer matches — or that has no recorded hash at all — was edited by a human, and
`release build` prints which files are frozen and skips them.

```bash
repo-manager release notes --branch release-v2026.38 --force
```

`--force` writes anyway and re-records the hash. That is the only way to overwrite the
release admin's words.

The tradeoff is real and accepted: a hotfix landing after the admin edited `notes.md` is not
reflected until they edit again or someone passes `--force`. The admin is already in that
file during the candidate week.

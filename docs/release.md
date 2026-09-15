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

The maintainer's verdict and the tester's plan.

- `verdict` is computed from the to-do list, never read from the model: `Blocked` with any
  P0, `Needs Attention` with any P1, `Ready` when the list is empty. The two can never
  disagree, because there is only one of them.
- `prioritized_todos` is the whole worksheet. A to-do earns its place only if the maintainer
  would regret shipping without it *and* users would notice. Each one ends with the PR and
  handle to chase: `(#3456, @someone)`.
- `breaking_changes` is the canonical list. `notes.md` and `announcement.md` are reconciled
  against it — one bullet per entry, enforced — so a breaking change cannot reach users
  unannounced. Documenting one is therefore never a to-do.
- `tester_plan` carries one entry per platform (Windows, Ubuntu PPA, Snap, Docker, macOS,
  Fedora, Debian), each saying what changed there and what a human should exercise. A
  platform nothing touched still gets its smoke check.

**Tester reports.** Open issues in the tracked repo carrying the `candidate` label, filed
since the bucket's branch was cut, are folded into the prompt. Every one of them must appear
in the to-do list naming the outcome to choose — fix later, hotfix, or revert — and the run
fails validation if one is dropped. A human already decided it mattered by filing it.

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

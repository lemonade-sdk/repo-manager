# repo-manager

Commit reviews, PR triage, and release artifacts for GitHub projects, stored as files.

repo-manager reads a repository through the GitHub CLI, runs one of its bundled
[Pi](https://github.com/badlogic/pi-mono) skills against a Lemonade server, validates what
the model wrote, and saves it as a file. That is the whole tool. There is no database
anywhere, and no code path that differs between a laptop and a runner.

## The store

One directory — the **state directory** — holds everything:

```text
commits/<sha>.json            one merged commit, reviewed
prs/<number>.json             one pull request, triaged
releases/<bucket>/review.json         the release verdict, to-dos, and tester plan
releases/<bucket>/notes.md            the release page's Headline and Breaking Changes
releases/<bucket>/announcement.md     the Discord post
releases/<bucket>/candidates/<N>.md   what changed since the previous candidate
releases/<bucket>/generated.json      the SHA-256 of everything repo-manager wrote here
```

In production that directory is a clone of
[`lemonade-sdk/lemonade-testing`](https://github.com/lemonade-sdk/lemonade-testing); on a
laptop it is the same clone, or an empty directory for a scratch run. If it is a git checkout
with an `origin` remote, every write is committed and pushed — a push is atomic, so jobs
writing disjoint files run in parallel safely, and a rejected push rebases and goes again.
`--no-push` commits without pushing. Reviewing a commit locally and pushing puts it into
production; pulling brings production to the laptop.

**A human edit wins.** repo-manager records the SHA-256 of every file it writes into a
bucket. A file whose content no longer matches is frozen: `release build` says so and leaves
it alone. The release admin edits `notes.md` and `announcement.md` directly in the GitHub web
editor, and `--force` is the only way to overwrite them.

## Buckets

A **bucket** is `v<year>.<week>` — the two-component prefix of the versions the build system
produces, like `v2026.38`. A cron cuts `release-v<year>.<week>` from `main` every Wednesday
at 19:00 UTC, so a `release-v*` branch names its own bucket and `main` accumulates toward the
upcoming release week. The final `.number` is unknown until a human tags a candidate, so
nothing stored here depends on it.

Several buckets are live at once: the one on `main`, the one under test, and any older branch
taking a hotfix. A bucket's range starts at the newest `v*` tag belonging to an *earlier*
bucket, so a bucket's own stable tag never truncates its range on a hotfix or a tag build.

## Install

```bash
pip install git+https://github.com/lemonade-sdk/repo-manager.git@v1.0.0
npm install -g pi@$(repo-manager pi version)
```

repo-manager runs `pi` against a Lemonade server, never a hosted model provider. Point it at
one and install the skills:

```bash
export REPO_MANAGER_LEMONADE_URL=http://127.0.0.1:13305/v1
export REPO_MANAGER_PI_MODEL=Lemonade/Qwen3.6-35B-A3B-MTP-GGUF
repo-manager pi setup
```

`gh` must be installed and authenticated for the repository being tracked.

## Commands

Every command takes the same global options *after* the subcommand: `--state DIR` (default:
the current directory), `--repo OWNER/REPO` (default: `lemonade-sdk/lemonade`), `--checkout
DIR` (a clone of the tracked repo used for git and diff operations; one is kept under the
cache directory when this is omitted), and `--no-push`.

**[Commit reviews](docs/commit-review.md)** — one merged commit, one file:

```bash
repo-manager commit review SHA            # write commits/<sha>.json unless it exists
repo-manager commit sweep --branch main   # review every commit in range that has no file
```

**[Release artifacts](docs/release.md)** — everything a candidate ships with:

```bash
repo-manager release build --branch release-v2026.38   # sweep, then review, notes, announce
repo-manager release review --branch release-v2026.38
repo-manager release notes --branch release-v2026.38
repo-manager release announce --branch release-v2026.38
```

**[PR triage](docs/pr-review.md)** — label open pull requests under the spec-driven
development policy:

```bash
repo-manager pr review N
repo-manager pr sweep
repo-manager pr post N                # post or update the triage comment
repo-manager pr label N               # apply the rfc: label
repo-manager pr request-reviewers N
```

**[Dashboard](docs/dashboard.md)** — browse what is stored:

```bash
repo-manager site render --out site   # the static site, from files only
repo-manager site serve               # the same page, live, with the PR action buttons
```

There are no read commands. Browsing stored artifacts is the web UI's job, or `ls` and `jq`
on the files.

Exit code is nonzero if any artifact could not be produced. The release workflow depends on
that: a candidate with no release notes never publishes.

## Skills

- `commit-review` — analyzes one commit and judges whether it was good for the project:
  review quality, tests, release risk, API compatibility, security, documentation, shout-outs.
- `pr-facts` — tier 1 of PR triage: the surfaces a diff changes (closed vocabulary), breaking
  changes with migration cost, body-versus-diff mismatches, maintainer subject areas.
- `pr-cover` — tier 2: for each surface, what covers it under `spec-driven-dev.md`.
- `pr-quality` — tier 3: documentation and testing gaps. The `rfc:` label, the scope, and the
  reviewers are then derived in Python from all three.
- `release-review` — the release verdict, the P0/P1 maintainer to-dos, and the tester plan.
- `release-notes` — the website release highlights, which also shape the release's stories.
- `release-announcement` — the Discord post, in the maintainer's voice.

Every skill is run with `--append-system-prompt` and `--no-skills`, so the skill is the
system prompt for that run and nothing else is offered alongside it.

## Development

```bash
python -m unittest discover tests
```

Design documents live under `docs/design/`. They record the history of the current design and
are not maintained as reference.

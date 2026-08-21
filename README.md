# repo-manager

CLI and Pi skills for managing GitHub projects at scale.

## Install

Create a virtual environment and install the CLI from this checkout:

```bash
uv venv
source .venv/bin/activate
uv pip install -e /home/jfowers/lsdk/repo-manager
```

After activating the virtual environment, `repo-manager` is available directly on `PATH`.

`repo-manager init` installs the Pi skills automatically. To install them manually:

```bash
pi install /home/jfowers/lsdk/repo-manager
```

> **Note:** Pi runs the *installed* copy of each skill, not the `SKILL.md` files in this checkout. After editing any skill under `skills/`, re-run `pi install /home/jfowers/lsdk/repo-manager` (or `repo-manager init`, which reinstalls them automatically) or your changes will not take effect.

The CLI shells out to `pi`, so Pi must be installed and available on `PATH`. The bundled scripts assume `gh` is installed and authenticated with access to the target repository.

## Workspace Setup

Initialize a folder to track a target repository:

```bash
mkdir lemonade-release-review
cd lemonade-release-review
repo-manager init lemonade-sdk/lemonade --branch main --clone
```

After initialization, commands run from that folder do not need `OWNER/REPO`. Pass `--no-pi-install` if you need to initialize without installing Pi skills. If the repo has been managed from another machine before, `init` automatically pulls the published dashboard state — see [Syncing & Publishing](docs/syncing.md#onboarding-and-pulling-published-state).

Workspace state lives under `.repo-manager/` in the initialized folder, including `config.json`, the SQLite database, explicit review artifacts, and the reusable repo checkout.

## Commands

Detailed documentation lives in `docs/`, organized by function.

**[Commit reviews](docs/commit-review.md)** — review merged commits and browse the results:

```bash
repo-manager review-commit SHA     # review one commit
repo-manager sweep [--force]       # review every commit in the release range
repo-manager db-table              # list saved commit reviews
repo-manager db-row N              # print one commit review
```

**[PR reviews](docs/pr-review.md)** — pre-review open pull requests for the human reviewer:

```bash
repo-manager review-pr N                    # review one open PR
repo-manager sweep-prs [--force]            # review every open PR lacking a current review
repo-manager pr-table                       # list saved PR reviews
repo-manager pr-row N                       # print one PR review
repo-manager post-pr-review N [--dry-run]   # post/update the review comment on GitHub
repo-manager request-pr-reviewers N         # request the suggested reviewers
```

**[Release review & announcement](docs/release.md)** — release-readiness verdict and release messaging, with automatic release inference (`vNext` rules):

```bash
repo-manager status                          # show inferred release lifecycle state
repo-manager release-review                  # verdict + prioritized to-do list
repo-manager announce                        # website highlights + Discord announcement
repo-manager override-announcement TAG FILE  # replace a saved announcement
```

**[Syncing & publishing](docs/syncing.md)** — move state between the local database, GitHub issues, and the published dashboard:

```bash
repo-manager sync            # sync release artifacts to GitHub issues
repo-manager publish-pages   # publish the static dashboard to GitHub Pages
repo-manager pull            # merge published dashboard state back into local
repo-manager all             # pull, sweep, release-review, announce, sync, publish
```

**[Web UI](docs/web-ui.md)** — browse everything, check off to-dos, and act on PR reviews:

```bash
repo-manager ui
```

Utility:

```bash
repo-manager wipe-db   # wipe the local SQLite database
```

## Skills

- `commit-review`: analyzes a GitHub commit and judges whether it was good for the project, with attention to review quality, tests, release risk, API compatibility, security, documentation, and shout-outs.
- `pr-triage`: tier 1 of the PR pre-review — checks the author's description against the diff (including whether each `Fixes #N` reference really matches its issue), whether the PR solves one problem, and which of `contribute.md`'s three review rungs it belongs on. Failing either of the first two stops the pipeline before further work.
- `pr-quality`: tier 2 — checks alignment with the contribution and philosophy guides, documentation, and testing, and flags breaking API/UX changes, with an imperative to-do per finding.
- `pr-reviewers`: tier 3 — suggests two to three reviewers from the maintainer table and from `git blame` on the code the PR acts on. Runs only once the earlier tiers are clean and the PR is not a draft.
- `pr-review`: the superseded single-pass version of the above, kept for its bundled `scripts/`.
- `release-review`: analyzes stored commit reviews and produces a release-readiness verdict with P0/P1 maintainer actions.
- `release-announcement`: turns stored commit reviews into Discord-friendly markdown release highlights.

## Direct Pi Usage

Invoke a skill directly:

```text
/skill:commit-review OWNER/REPO COMMIT_SHA
```

Natural-language equivalent:

```text
Review whether commit COMMIT_SHA in OWNER/REPO was good for the project.
```

## Repository Layout

```text
db/
  schema.sql
docs/
  commit-review.md
  pr-review.md
  release.md
  syncing.md
  web-ui.md
repo_manager/
  cli.py
  web.py
scripts/
skills/
  commit-review/
    SKILL.md
    scripts/
  pr-review/
  pr-triage/
  pr-quality/
  pr-reviewers/
    SKILL.md
    scripts/
  release-review/
    SKILL.md
  release-announcement/
    SKILL.md
package.json
setup.py
```

Add future skills as new directories under `skills/`, each with its own `SKILL.md` and optional bundled resources.

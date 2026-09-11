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

**[PR triage](docs/pr-review.md)** — label open pull requests under the spec-driven development policy:

```bash
repo-manager review-pr N                    # triage one PR (label, scope, body, docs/tests, reviewers)
repo-manager sweep-prs [--force]            # triage open PRs opened since the policy landed (--since all for every PR)
repo-manager pr-table                       # list saved triages
repo-manager pr-row N                       # print one triage
repo-manager post-pr-review N [--dry-run]   # post/update the triage comment on GitHub
repo-manager request-pr-reviewers N         # request the suggested reviewers
repo-manager apply-pr-label N [--dry-run]   # apply the rfc: label (rfc:required also drafts + posts the RFC request)
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
- `pr-facts`: tier 1 of PR triage — the surfaces a diff changes (closed vocabulary), breaking changes with migration cost, body-versus-diff mismatches, and maintainer subject areas.
- `pr-cover`: tier 2 — for each surface, what covers it under spec-driven-dev.md: a statement of intent on the base branch (a fix), a working-group charter item, or the linked RFC's design.
- `pr-quality`: tier 3 — documentation and testing gaps per documentation.md and testing.md. The `rfc:` label, scope, and reviewers are derived in Python from these three.
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
  triage.py
  web.py
scripts/
  eval-triage.py
  pr-code-authors.sh
skills/
  commit-review/
    SKILL.md
    scripts/
  pr-facts/
  pr-cover/
  pr-quality/
    SKILL.md
  release-review/
    SKILL.md
  release-announcement/
    SKILL.md
package.json
setup.py
```

Add future skills as new directories under `skills/`, each with its own `SKILL.md` and optional bundled resources.

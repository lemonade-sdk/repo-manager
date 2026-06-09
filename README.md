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

The CLI shells out to `pi`, so Pi must be installed and available on `PATH`. The bundled scripts assume `gh` is installed and authenticated with access to the target repository.

## CLI Workflow

Initialize a folder to track a target repository:

```bash
mkdir lemonade-release-review
cd lemonade-release-review
repo-manager init lemonade-sdk/lemonade --branch main --clone
```

After initialization, commands run from that folder do not need `OWNER/REPO`. Pass `--no-pi-install` if you need to initialize without installing Pi skills.

Review one commit and save the result to SQLite:

```bash
repo-manager review-commit 450bf6c
```

Pi runs normally in your terminal. The skill writes JSON result files under `.repo-manager/reviews/`, then repo-manager reads those artifacts and saves their paths plus parsed fields to SQLite.

Review every commit after a tag:

```bash
repo-manager sweep v10.6.0
```

Re-run existing reviews:

```bash
repo-manager sweep v10.6.0 --force
```

Create a release-readiness review from stored commit reviews:

```bash
repo-manager release-review v10.6.0
```

Generate a Discord-friendly release announcement:

```bash
repo-manager announce v10.6.0
```

Wipe the local SQLite database:

```bash
repo-manager wipe-db
```

Inspect saved commit reviews:

```bash
repo-manager db-table
repo-manager db-row 1
```

Browse saved reviews, the release-level review, and generated announcement in a local web UI:

```bash
repo-manager ui
```

The UI serves the current workspace at `http://127.0.0.1:8765/` by default. Use `--no-open` to print the URL without opening a browser. Commit and release review to-dos can be checked off in the UI, and that state is persisted in SQLite.

Workspace state lives under `.repo-manager/` in the initialized folder, including `config.json`, the SQLite database, explicit review artifacts, and the reusable repo checkout.

## Skills

- `commit-review`: analyzes a GitHub commit and judges whether it was good for the project, with attention to review quality, tests, release risk, API compatibility, security, documentation, and shout-outs.
- `release-review`: analyzes stored commit reviews and produces a release-readiness verdict with P0/P1/P2 maintainer actions.
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
repo_manager/
  cli.py
scripts/
skills/
  commit-review/
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

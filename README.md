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

Review every commit in a release range. The positional value is the release bucket being prepared. repo-manager automatically uses the previous `v*` tag as the lower bound:

```bash
repo-manager sweep v10.7.0
```

Re-run existing reviews:

```bash
repo-manager sweep v10.7.0 --force
```

Create a release-readiness review from stored commit reviews:

```bash
repo-manager release-review v10.7.0
```

The review is one verdict (`Ready`/`Needs Attention`/`Blocked`) plus a tight prioritized to-do list: P0 means do not ship until resolved, P1 means verify before shipping; there is no P2 and no nitpick tier. Pi receives a per-commit digest (summaries, verdicts, open to-dos, test/compatibility/security evidence) rather than the full review payload, and the result is validated (verdict/list agreement, at most 6 items, actionable phrasing) with automatic retry and resumable feedback, like `announce`.

Generate a Discord-friendly release announcement:

```bash
repo-manager announce v10.7.0
```

This writes two artifacts under `.repo-manager/reviews/releases/`: a website release-highlights markdown file containing only `## Headline` and `## Breaking Changes`, plus the Discord announcement markdown.

Generation is staged: Pi first writes a story-plan JSON (3-5 stories, which ones earn sections), then derives both artifacts from it. The plan and artifacts are all validated (plan shape and story coherence, heading/plan consistency, format, voice, length, bullet limits, and an 8-word phrase-overlap check against prior announcements). The announcement prompt feeds Pi an announcement-specific projection of the commit reviews (summaries, authors, credit handles, docs) rather than the full review payload with verdicts and evidence. If validation fails, repo-manager automatically re-runs Pi with the specific errors and the failed drafts, up to 3 attempts. Validation feedback is persisted under `.repo-manager/reviews/releases/.pending/`, so if a run is interrupted, re-running `announce` resumes from the last failed attempt instead of starting over; the feedback file is cleared on success.

When generating these, repo-manager fetches the last three prior GitHub releases and passes their `## Headline` / `## Breaking Changes` sections to Pi as the style reference for the website release-highlights artifact. It separately passes the last three saved local announcements as style references for the Discord announcement.

Replace a saved announcement with Markdown you wrote or already posted:

```bash
repo-manager override-announcement v10.7.0 ./posted-announcement.md
```

You can also pipe Markdown through stdin:

```bash
cat posted-announcement.md | repo-manager override-announcement v10.7.0 -
```

For the next unreleased train, use `vNext`:

```bash
repo-manager sweep vNext
repo-manager release-review vNext
repo-manager announce vNext
```

Use `--since TAG` only when you need to override the inferred previous `v*` tag.

Release reviews and announcements are updated in place for a given release bucket. Re-running either command for the same release replaces the saved database row and rewrites the artifact file under `.repo-manager/reviews/releases/`.

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

Use the tag dropdown to browse `vNext` and historical releases.

The UI keeps the URL updated as you browse, so links can be shared directly to a release bucket and selected review. Deep links use hash parameters and work in both the local and static UI:

```text
#view=commits&tag=v10.7.0&commit=COMMIT_SHA
#view=release&tag=v10.7.0
#view=announcement&tag=v10.7.0
```

## GitHub Pages Publishing

repo-manager can publish a static, read-only copy of the dashboard to the target repository's GitHub Pages site. The local SQLite database remains the source of truth.

```bash
repo-manager publish-pages
```

By default this updates only `docs/repo-manager/` on the `website` branch. The existing website publisher can continue owning the normal generated docs path, while repo-manager owns its dashboard path.

Useful overrides:

```bash
repo-manager publish-pages --website-branch website --target-dir docs/repo-manager
```

Preview the generated static site locally without touching the website branch:

```bash
repo-manager publish-pages --dry-run
```

The dry run writes `.repo-manager/pages-preview/index.html` and opens it in your default browser by default. Use `--no-open` to only write the files, or `--out PATH` to choose a different preview directory.

The published dashboard shows the saved commit reviews, release reviews, announcements, read status, and to-do completion state as of the publish time. To-do and read-state changes should still be made in the local UI, then republished.

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

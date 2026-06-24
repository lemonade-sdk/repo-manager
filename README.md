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

### Onboarding to a repo that already uses repo-manager

If the target repo has already been managed from another machine and its dashboard was published with [`publish-pages`](#github-pages-publishing), you do not need to re-review anything from scratch. `init` automatically pulls the published database so a brand-new machine mirrors the online state:

```bash
mkdir lemonade-release-review
cd lemonade-release-review
repo-manager init lemonade-sdk/lemonade --branch main --clone
```

This reads the published dashboard from the `website` branch (`docs/repo-manager/index.html`) and restores commit reviews, release reviews, announcements, to-do completion, and read state into a fresh local database. Pass `--no-pull` to skip this and start empty, or `--no-clone`/`--no-pi-install` to skip cloning the target repo or installing Pi skills (the pull itself needs neither).

To refresh an existing workspace later — for example after someone else publishes new reviews — re-run the pull anytime:

```bash
repo-manager pull
```

`pull` is a merge: rows from the online copy are inserted or updated by key, and any local-only work you have not published yet is preserved. It is safe to run repeatedly.

**Announcements are special.** repo-manager only ever stores the *proposed* Discord announcement; the *real* one is hand-edited by a maintainer before posting. So `init`/`pull` do not trust the published announcement text. Instead they fetch the real Discord announcement from the repo's wiki page (`Release-Announcements` by default; override with `--wiki-page`) and the website highlights (`## Headline` / `## Breaking Changes`) from each version's GitHub release page. A release with no wiki entry yet (for example an in-progress `vNext`) keeps its proposed draft. Keep the wiki page up to date with the announcements you actually post so onboarding machines get the canonical copy.

Review one commit and save the result to SQLite:

```bash
repo-manager review-commit 450bf6c
```

Pi runs normally in your terminal. The skill writes JSON result files under `.repo-manager/reviews/`, then repo-manager reads those artifacts and saves their paths plus parsed fields to SQLite.

Review every commit in a release range. The positional value is the release bucket being prepared. repo-manager automatically uses the previous `v*` tag as the lower bound:

```bash
repo-manager sweep
```

Re-run existing reviews for the inferred release:

```bash
repo-manager sweep --force
```

Create a release-readiness review from stored commit reviews:

```bash
repo-manager release-review
```

The review is one verdict (`Ready`/`Needs Attention`/`Blocked`) plus a tight prioritized to-do list: P0 means do not ship until resolved, P1 means verify before shipping; there is no P2 and no nitpick tier. Pi receives a per-commit digest (summaries, verdicts, open to-dos, test/compatibility/security evidence) rather than the full review payload, and the result is validated (verdict/list agreement, at most 6 items, actionable phrasing) with automatic retry and resumable feedback, like `announce`.

Sync the saved release artifacts to GitHub issues:

```bash
repo-manager sync
```

This creates or updates `Release v10.7.0 final checklist`, `v10.7.0 release notes`, and `v10.7.0 announcement` when their source artifacts exist locally. New release-review to-dos are appended as checkboxes, existing checklist items are preserved, and checked items on the checklist issue are marked complete in the local repo-manager database. When `release-review` runs again, it looks for the checklist issue and passes checked items plus maintainer issue comments back into the release-review prompt, so definitive notes like "this is not a problem because..." or "the checklist is missing..." can affect the regenerated review. When `announce` runs again, it looks for the release notes and announcement issues and passes maintainer comments on those issues back into the announcement prompt.

Generate a Discord-friendly release announcement:

```bash
repo-manager announce
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

By default, release pipeline commands infer the current release:

```bash
repo-manager status
repo-manager sweep
repo-manager release-review
repo-manager announce
```

This follows the `vNext` resolution rules. When the tracked repo's `CMakeLists.txt` advances past the latest `v*` tag, repo-manager resolves the current release to that concrete tag. For example, if CMake moves from `10.7.0` to `10.8.0`, the inferred release becomes `v10.8.0`; local database rows, saved release to-dos, issue mappings, and synced GitHub issue titles/bodies are migrated from `vNext` to `v10.8.0`. If a matching release branch such as `release-v10.8.0` exists, release commands use that branch instead of the configured default branch unless `--branch` is passed explicitly. Once `v10.8.0` is tagged, inference returns to `vNext` on the configured default branch until CMake advances past the latest tag again.

`status` prints the lifecycle inputs and selected state: CMake release, latest `v*` tag, selected release bucket, release branch presence, selected branch, local review counts, to-do counts, and mapped issue counts.

Use `--since TAG` only when you need to override the inferred previous `v*` tag.

You can still pass an explicit release bucket, such as `repo-manager all v10.8.0`, when you need to override inference.

Run the whole pipeline — `sweep`, `release-review`, `announce`, `sync`, then `publish-pages` — for one release in a single command:

```bash
repo-manager all
```

`all` always runs `sweep` and `publish-pages`. It runs `release-review`, `announce`, and `sync` only after the inferred concrete release branch exists, so release-level artifacts are generated against the branch that will ship rather than a still-moving default branch. `all` accepts the union of the underlying flags: `--repo`, `--branch`, and `--since` apply to the review/announce steps; `--force` re-runs existing commit reviews in the sweep step; and `--website-branch`, `--target-dir`, `--message`, `--dry-run`, and `--out` are passed through to the publish step. Each step prints its own progress, and the run stops at the first failing step.

Release reviews and announcements are updated in place for a given release bucket. Re-running `release-review` for the same release includes the existing release review, to-do completion state, checklist issue state, and issue comments in the prompt so equivalent to-dos are kept stable instead of duplicated. Re-running `announce` includes the existing release notes and announcement artifacts as continuity baselines, plus artifact issue comments, so accepted wording and structure stay stable unless new evidence or maintainer feedback requires a change. `sync` overwrites the generated GitHub issue descriptions from the current saved artifacts while preserving checkbox completion for matching current to-dos.

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

This published copy is also what a new or stale machine syncs from — see [Onboarding to a repo that already uses repo-manager](#onboarding-to-a-repo-that-already-uses-repo-manager) for the `pull` side of this flow.

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

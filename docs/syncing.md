# Syncing & Publishing

repo-manager moves state in three directions: `sync` pushes release artifacts to GitHub issues, `publish-pages` pushes a static dashboard to GitHub Pages, and `pull` brings the published state back into a local database. `all` chains the whole pipeline.

## Syncing release artifacts to GitHub issues

```bash
repo-manager sync
```

This creates or updates `Release v10.7.0 final checklist`, `v10.7.0 release notes`, and `v10.7.0 announcement` when their source artifacts exist locally. New release-review to-dos are appended as checkboxes, existing checklist items are preserved, and checked items on the checklist issue are marked complete in the local repo-manager database. When `release-review` runs again, it looks for the checklist issue and passes checked items plus maintainer issue comments back into the release-review prompt, so definitive notes like "this is not a problem because..." or "the checklist is missing..." can affect the regenerated review. When `announce` runs again, it looks for the release notes and announcement issues and passes maintainer comments on those issues back into the announcement prompt.

## GitHub Pages publishing

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

The published dashboard shows the saved commit reviews, release reviews, announcements, read status, and to-do completion state as of the publish time. To-do and read-state changes should still be made in the local UI, then republished. [PR reviews](pr-review.md) are local-only and excluded from the published dashboard.

## Onboarding and pulling published state

If the target repo has already been managed from another machine and its dashboard was published with `publish-pages`, you do not need to re-review anything from scratch. `init` automatically pulls the published database so a brand-new machine mirrors the online state:

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

## Running the whole pipeline

Run the whole pipeline — `pull`, `sweep`, `release-review`, `announce`, `sync`, then `publish-pages` — for one release in a single command:

```bash
repo-manager all
```

`all` starts by pulling the published database into local, so it is safe to run from any machine without overwriting newer online state when it publishes at the end. Pass `--no-pull` to skip that step. It then always runs `sweep` and `publish-pages`, and runs `release-review`, `announce`, and `sync` only after the inferred concrete release branch exists, so release-level artifacts are generated against the branch that will ship rather than a still-moving default branch. `all` accepts the union of the underlying flags: `--repo`, `--branch`, and `--since` apply to the review/announce steps; `--force` re-runs existing commit reviews in the sweep step; `--wiki-page` applies to the pull step; and `--website-branch`, `--target-dir`, `--message`, `--dry-run`, and `--out` are passed through to the publish step. Each step prints its own progress, and the run stops at the first failing step.

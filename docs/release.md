# Release Review & Announcement

## Release review

Create a release-readiness review from stored [commit reviews](commit-review.md):

```bash
repo-manager release-review
```

The review is one verdict (`Ready`/`Needs Attention`/`Blocked`) plus a tight prioritized to-do list: P0 means do not ship until resolved, P1 means verify before shipping; there is no P2 and no nitpick tier. Pi receives a per-commit digest (summaries, verdicts, open to-dos, test/compatibility/security evidence) rather than the full review payload, and the result is validated (verdict/list agreement, at most 6 items, actionable phrasing) with automatic retry and resumable feedback, like `announce`.

## Announcement

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

## Stable regeneration

Release reviews and announcements are updated in place for a given release bucket. Re-running `release-review` for the same release includes the existing release review, to-do completion state, checklist issue state, and issue comments in the prompt so equivalent to-dos are kept stable instead of duplicated. Re-running `announce` includes the existing release notes and announcement artifacts as continuity baselines, plus artifact issue comments, so accepted wording and structure stay stable unless new evidence or maintainer feedback requires a change. See [Syncing & Publishing](syncing.md) for how `sync` feeds checklist state and maintainer comments back into these prompts.

## Release inference

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

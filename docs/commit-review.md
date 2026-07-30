# Commit Reviews

repo-manager reviews merged commits with the `commit-review` Pi skill, which analyzes a GitHub commit and judges whether it was good for the project, with attention to review quality, tests, release risk, API compatibility, security, documentation, and shout-outs.

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

Use `--since TAG` only when you need to override the inferred previous `v*` tag. See [Release Review & Announcement](release.md) for how the release bucket and branch are inferred.

Inspect saved commit reviews in the terminal:

```bash
repo-manager db-table
repo-manager db-row 1
```

Or browse them in the [web UI](web-ui.md), where commit review to-dos can be checked off and read state is tracked. Commit reviews are part of the published dashboard and round-trip through [`pull` and `publish-pages`](syncing.md).

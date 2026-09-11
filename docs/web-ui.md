# Web UI

Browse saved commit reviews, PR reviews, the release-level review, and generated announcement in a local web UI:

```bash
repo-manager ui
```

The UI serves the current workspace at `http://127.0.0.1:8765/` by default. Use `--no-open` to print the URL without opening a browser. Commit and release review to-dos can be checked off in the UI, and that state is persisted in SQLite.

To reach the UI from another machine — a phone, a laptop on the same network — bind a wildcard address:

```bash
repo-manager ui --host 0.0.0.0 [--port 8765]
```

The startup banner then prints the LAN URL to hand to the other device. There is no authentication: anyone who can reach that address can browse the workspace and use the PR buttons, which act on GitHub with your `gh` credentials. Bind `0.0.0.0` only on networks you trust.

Use the tag dropdown to browse `vNext` and historical releases.

The **PR Reviews** tab has its own controls — the live Status column with a configurable perspective, a per-row read checkbox that unchecks itself when that Status changes, hide toggles, and comment/reviewer-request actions — documented in [PR Reviews](pr-review.md#dashboard).

Every detail pane shows how long its artifact took to generate ("Generated in: NN seconds"), captured for commit reviews, PR reviews, release reviews, and announcements alike.

The UI keeps the URL updated as you browse, so links can be shared directly to a release bucket and selected review. Deep links use hash parameters and work in both the local and static UI:

```text
#view=commits&tag=v10.7.0&commit=COMMIT_SHA
#view=prs&pr=1234
#view=release&tag=v10.7.0
#view=announcement&tag=v10.7.0
```

The published static copy of this dashboard (see [GitHub Pages publishing](syncing.md#github-pages-publishing)) is read-only and excludes PR reviews and the GitHub-acting buttons.

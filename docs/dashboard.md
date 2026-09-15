# Dashboard

Four tabs — Commit DB, PR Reviews, Release Review, Announcement — built from the state
directory.

```bash
repo-manager site render --state . --out site   # the static site
repo-manager site serve  --state .              # the same page, live
```

`site render` needs no network and no checkout of the tracked repo: it reads files and writes
`index.html` with the whole payload embedded. That is deliberate — rendering runs in the state
repo, on a plain `ubuntu-latest` runner with no `pi` installed, so a rendering bug can never
block a lemonade release. The `lemonade-testing` Pages workflow runs it on every push and
deploys to <https://testing.lemonade-server.ai>.

`site serve` reads the same directory on each request, so a review that lands while the page
is open shows up on the next poll.

## The Status column

The PR Reviews tab answers one question per row: does this PR need something from me?
**Review** is your turn, **Merge** means it is approved and waiting on you, **Needs reviewer**
means nobody is on the hook, **In progress** means someone else is, and **Waiting for RFC**
means the PR carries `rfc:required` and is waiting on a discussion rather than a reviewer. The
tooltip carries the reasoning. "Status as" changes whose perspective it is computed from.

That column reads GitHub, so it exists only under `site serve`, which keeps a mirror of PR
state in the serving process's memory. The mirror is a cache, not a record: every field is
re-derivable, nothing you typed lives in it, and dropping it costs one sync. It refreshes by
asking what changed since a watermark — one API point on a quiet repo — and detail-fetches only
the PRs that moved. The freshness label says when it last succeeded, and says so plainly when
it did not.

The published static site has no network, so it renders that column as **not live** rather
than drawing the state a triage saw days ago as though it were current. Everything else on the
tab — label, scope, body-matches-diff, docs and tests, attention, and the comment exactly as
`pr post` would submit it — is in the files and renders either way.

## The Release Review tab

The bucket's verdict and the one-or-two sentences answering "can we ship?", then the
checklist a tester works through — blockers first, each item tagged with the platforms it
applies to — the canonical breaking changes, and the evidence. When a human has edited one of the bucket's files, the tab says which and that
`--force` is what overwrites it.

## What is no longer here

To-do check-off and read state. They were the last two pieces of state the dashboard owned
rather than read, and owning state is what made the old dashboard a second database. A to-do
is resolved by editing the artifact it came from — `review.json`, or the commit review — in the
GitHub web editor or a local clone, and repo-manager will not overwrite a human edit.

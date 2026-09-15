# Dashboard

One page, built from the state directory and nothing else.

```bash
repo-manager site render --state . --out site   # the static site
repo-manager site serve  --state .              # the same page, live
```

`site render` needs no network and no checkout of the tracked repo — it reads files and
writes `index.html` with the whole payload embedded. That is deliberate: rendering runs in
the state repo, on a plain `ubuntu-latest` runner with no `pi` installed, so a rendering bug
can never block a lemonade release. The `lemonade-testing` Pages workflow runs it on every
push and deploys to <https://testing.lemonade-server.ai>.

`site serve` reads the same directory on each request, so a review that lands while the page
is open shows up on the next load.

## What it shows

**Releases** — one card per live bucket, newest first: the verdict, the one-or-two sentences
answering "can we ship?", the P0/P1 to-dos, the canonical breaking changes, the tester
reports folded in, the per-platform tester plan, the evidence, and both artifacts as the
release admin would see them. A bucket whose files a human has edited says so.

**Commits** — every commit review, newest first, filterable by bucket and by free text.
Opening a row shows the verdict reason, the maintainer to-dos, the shout-outs, and the
evidence, with links to the commit and its PR.

**Pull requests** — every triage, filterable by `rfc:` label. Opening a row shows what the
diff changes, the concerns exactly as the posted comment lists them, and why each reviewer is
named.

## Acting on a PR

When the page is served rather than rendered statically, an open PR row carries **Post
comment**, **Apply label**, and **Request reviewers**. Each calls the same function the CLI
calls, so the dashboard, the CLI, and the GitHub Action cannot drift. They act on GitHub with
the operator's own `gh` credentials, so requests that the browser labels as cross-origin are
refused, and `site serve --host 0.0.0.0` says plainly that anyone on the network can act as
you.

## What is not here

To-do check-off and read state are gone. They were the last two pieces of state the dashboard
owned rather than read, and owning state is what made the old dashboard a second database.
The release admin edits `review.json`, `notes.md`, and `announcement.md` directly — in the
GitHub web editor or in a local clone — and repo-manager will not overwrite a human edit.

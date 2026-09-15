# Dashboard

Three tabs — Releases, Commits, PRs — built from the state directory.

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

## Your GitHub ID

The top bar carries one identity box, and the whole page is written from that account's point
of view: which PRs want something from *you*, which commits are yours, and which items you
have worked through. It is typed once and remembered by the browser, so a bookmark opens the
page already pointed at you.

It is also the namespace for everything the page remembers. Two people reading the published
site keep their own check-offs, and retyping the box swaps the whole set rather than merging
it — these are one reader's notes, not a record anyone else inherits.

## Check-off

Every to-do on the page has a box: the tester checklist on a release, the maintainer to-dos on
a commit. Ticking one writes a single key to browser storage and updates the count in the list
beside it — no request, no refetch, no redraw of the checklist you are working through.

**A tick is a note, not a record.** It lives in one browser, under one GitHub login, and never
reaches the state directory. The artifact the item came from is still the only record of what
the release owes, and an item is *resolved* by editing that artifact — `review.json`, or the
commit review — in the GitHub web editor or a local clone. This is the line the file store
draws: the dashboard reads state, it does not own any.

**A to-do has one box, wherever you read it.** A release checklist item and the commit to-do it
came from are the same to-do: the release review carries every to-do through verbatim and
records the commit it came from, so the box is keyed to that commit rather than to whichever
list you happen to be looking at. Tick it on the Releases tab and it is ticked on the Commits
tab, because there was only ever one of it.

An item the model has since reworded arrives unticked, because the words changed and what you
worked through is no longer what the list is asking for.

## The Releases tab

The list is every bucket the directory knows about, newest first — including the one on `main`
that has only been swept, which shows its commit count and `not built`. Four sub-tabs read the
selected release four ways:

**Review** — what this release does to users, then the checklist. Breaking changes come first
because they are the context the checklist is read in; a tester who has to scroll past twenty
to-dos to reach them has already decided what to try. There is no verdict anywhere on the page:
whether the release ships is the release admin's call, and the checklist is what they make it
from.

The checklist is every maintainer to-do in the range, under P0, P1 and P2 headings, and all of
it is work to do before the release ships — the priority is the order to work in, not whether an
item counts. It is the subsection an item sits in rather than a word repeated down the margin
twenty times: the heading says it once, and the eye can find where the work that stops the
release ends. Each item carries the platforms it applies to and the PR and handle to chase. Then
the evidence, and, when a human has edited one of the bucket's files, which one and that
`--force` overwrites it.

**Release notes** and **Announcement** — the website highlights and the Discord post, as
markdown, with a Copy button. They are shown verbatim because they are copied verbatim.

**Stats** — what this release is made of, as one table: what is left under each priority,
commits reviewed and how many in range still are not, how many of the commits are yours,
unique authors and reviewers, the commit-verdict tally and the span of days. Every figure is about
the selected release, because the release is the unit of work.

## The Commits tab

Every reviewed commit, filtered by release from the list's own title bar and searchable beside
it. The release filter opens on the newest bucket; "All releases" is there for a question that
spans more than one.

## The Status column

The PRs tab answers one question per row: does this PR need something from me?
**Review** is your turn, **Merge** means it is approved and waiting on you, **Needs reviewer**
means nobody is on the hook, **In progress** means someone else is, and **Waiting for RFC**
means the PR carries `rfc:required` and is waiting on a discussion rather than a reviewer. The
tooltip carries the reasoning, and the perspective is the GitHub ID in the top bar.

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

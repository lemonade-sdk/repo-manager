# PR Reviews

repo-manager can pre-review open pull requests to flag what a human reviewer should look out for. This is advisory — it never replaces the human review.

## The three tiers

The review runs as three stages, mirroring `contribute.md`'s own division of labour: its Reviewer Expectation lists what an author owes before a human reads the code, and its Review Process decides how much review the change needs. Each tier is a separate skill and a separate `pi` run, so a tier that fails validation re-runs its own judgment instead of the whole review.

**Tier 1 — `pr-triage`** checks the two obligations that decide whether the PR has a reviewable *shape*: does the description honestly map the diff (Reviewer Expectation 1), and does the PR solve one clearly defined problem (Reviewer Expectation 2). Part of the first check is verifying every `Fixes #N` / `Closes #N` reference against the issue it names — those close the issue on merge, so a wrong one silently closes a live bug. Tier 1 also puts the PR on a **review rung** (below).

**Tier 2 — `pr-quality`** checks the rest of the author's obligations: alignment with `philosophy.md` and `contribute.md`, documentation per `documentation.md`, testing per `testing.md`, and any breaking API or UX change.

**Tier 3 — `pr-reviewers`** suggests two to three human reviewers.

**All three run on every PR, drafts included.** The tiers used to be gated — a description that did not match the diff stopped the review before it read the docs, and gaps stopped it before it named a reviewer — and the result was a comment with more `not evaluated` in it than findings. Worse, the author had no way to ask for the rest: editing a PR body does not move the head SHA, so nothing re-ran, and the review that was supposed to save a round trip created one. One run is now the whole answer — every to-do the author owes, and who should look once they are done — so a to-do list is a list to work through rather than the first of three.

## The review rung

There is no `major`/`minor` verdict. `contribute.md`'s Review Process specifies how much review a PR needs, and that is what the artifact records, as `review_requirement`:

| Rung | The guide's words | Reviewers |
| --- | --- | --- |
| `one-reviewer` | minor features and fixes | any 1 |
| `two-with-expert` | major features, refactors, new backends, security-related issues | 2, including 1 subject-area expert |
| `named-approver` | project scope expansion, re-architecture, design language changes | the maintainer the guide names |

Tier 1 reaches the rung by a lookup rather than an impression: it names the surface the diff changes and reads the rung off a table — a flag on an existing command is `one-reviewer`, a new subcommand is `two-with-expert`, a new command that expands what the project does is `named-approver`, and the same three-step ladder applies to endpoints, backends, GUI, code structure, and security. Size is never a rung: a 1,000-line diff adding one flag is `one-reviewer`, while 40 lines registering a new route are not. A low-rung change can be promoted only by naming the capability the project gains, never by calling it significant.

`expert_areas` names the subject areas a reviewer would need in order to count as this PR's expert, in the maintainer table's own vocabulary. `named_approver` is copied out of the guide's Review Process text rather than hardcoded, so it follows the guide if the guide changes.

## Reviewer suggestion

Tier 3 answers who *should* review, which is a different question from who already is. It is not given the PR's reviews, review requests, or review decision — it never calls `get-pr-context.sh` — so a suggestion cannot be a copy of the current assignment. The CLI filters out anyone already requested or reviewing when it acts on the list.

Every PR gets a slate of two or three candidates rather than a single name, so the choice and the fallback stay with the human. Candidates arrive by two routes, recorded in each entry's `basis`:

- `maintainer-table` — their **Subject Areas** cell in `contribute.md` names a surface this PR touches. The `subject_area` is that term copied verbatim, so the match is auditable.
- `code-author` — `git blame` shows they wrote the code this PR acts on, found by `get-pr-code-authors.sh`, which greps the source tree for the identifiers the PR is about and resolves each blamed commit to a GitHub handle through the API. Because every handle comes from a real commit, this route cannot invent a person.

A separate `in_maintainer_table` boolean records table membership independently of why the person was chosen, so a maintainer picked on blame evidence never has to justify themselves with a poorly-fitting table term. A `code-author` who is not in the table is a contributor who knows the code, never a maintainer, and never satisfies a requirement that names one.

A `maintainer-table` citation has to agree with the rung. A cited area that asserts novelty — one whose text carries the word *new*, such as `new endpoints` or `large new features` — contradicts a surface that changes something already there, and the validator rejects the slate when the maintainer had a non-novel area available to cite instead. This is the seam where the tiers can disagree: the rung is a lookup off the surface table in tier 1, and a tier-3 impression that the diff is *substantial* is not allowed to staff the PR above it. Novelty is matched on the word rather than the prefix, because `large new features` is the one term in the table where *new* is not the first word — and it is the term most likely to be reached for.

## Generate

The flow is generate → read → act. Generate and store a review for one PR:

```bash
repo-manager review-pr 1234
```

Or sweep every open PR that lacks a current review. A PR whose head SHA has moved since its stored review is re-reviewed automatically; drafts are skipped unless `--include-drafts` is passed:

```bash
repo-manager sweep-prs
repo-manager sweep-prs --limit 20 --force
```

## Read

Read stored PR reviews in the terminal (or in the [web UI](#pr-reviews-in-the-web-ui)):

```bash
repo-manager pr-table
repo-manager pr-row 1
```

The terminal view leads with the same consolidated **To-Dos** list the posted comment does, then prints one section per question — description accuracy, review suggestion, alignment, documentation, testing, breaking changes, reviewers — as the raw artifact holds them. Sections with findings render them as a checklist with the justification and references as sub-bullets; clean sections instead show a one-line "Checked:" note recording what was inspected to earn the clean bill. The review never re-describes the PR beyond a one-sentence summary: the description-accuracy section judges the author's own description against the diff and only spends words on discrepancies.

## Act

Post the review as a PR comment. Re-posting updates the existing comment in place instead of duplicating it. This is the same text the dashboard previews — see [the detail pane](#pr-reviews-in-the-web-ui).

```bash
repo-manager post-pr-review 1234 --dry-run   # print the comment first
repo-manager post-pr-review 1234
```

**Three things are visible without a click**, and nothing else is:

1. **Whether the PR is ready.** `Ready for review`, or `Not ready for review yet` when the review produced any to-do at all.
2. **One to-do list.** Every finding from all three tiers, as one checklist in the author's own imperative — description discrepancies, a bundling split, alignment flags, documentation and testing gaps, and breaking changes still needing docs or a sign-off. The author works down one list instead of assembling it from six folded sections; the list is the only part of the comment anyone has to act on, so it is the only part that opens on arrival.
3. **The review suggestion.** The attention level, and either the suggested reviewers or who is already covering the PR.

Everything that *justifies* those goes in one of two collapsed **Explanation** folds — the per-check verdicts and their "Checked:" evidence under the to-dos, and the rung's rationale under the review suggestion. An author acting on the to-dos rarely needs the reasoning; a maintainer auditing the review always does, and it is one click away.

This replaced a comment that led with its own AI disclaimer in bold and then put a section-by-section argument between the reader and the list of things to do — with a `not evaluated` paragraph for each check the gate had skipped. The disclaimer is now the last line, in italics, where the policy is satisfied and nobody has to read past it to reach the work. Verdicts are lowercase throughout: half of them — `adequate`, `gaps`, `focused`, `accurate`, `not-applicable` — are the artifact's own schema vocabulary and arrive that way, so capitalising them would misquote the schema, and the verdicts the renderer invents (`none found`, `all cleared`, `2 to resolve`) match those rather than the other way round. A check whose status is `not-evaluated` is dropped from the fold entirely: no tier is skipped any more, so that value only appears on rows stored before the gates came out, and a paragraph explaining that nobody looked is exactly the text this comment stopped spending.

**The attention level is about the reviewer, not the author.** It answers one question — how much scrutiny does a human owe this PR — and it is computed from the rung plus any breaking change nobody has signed off, and from nothing else. It used to fold in what the *author* still owed, which is how a PR whose only problem was an undescribed flag came out labelled `Elevated`: a word that reads like a security review when the fix is one paragraph in the PR body. What the author owes is the to-do list, and the to-do list says it once.

`Routine` is any 1 reviewer with nothing outstanding. `Elevated` is the `two-with-expert` rung. `High` is the `named-approver` rung, or a breaking change that is not yet documented and approved. The level's one-line meaning is derived from the rung rather than from a fixed sentence per level, because level and rung are different questions — how much scrutiny, versus who has to look — and a level maps to more than one rung. That is how a `two-with-expert` PR once came to be labelled "any reviewer can take it". The wording follows contribute.md's Review Process list: `needs any 1 reviewer`, `needs 2 reviewers including 1 subject-area expert`, `needs a review from @handle`.

The rung is stated once. **Why this rung** — the surface and rationale that put the PR there — sits in the review suggestion's Explanation fold, and it carries only what the level's sentence cannot: the expert areas are already folded into the `two-with-expert` clause and the named approver's handle into the `named-approver` one, so each is dropped for the rung that duplicates it and kept for the rungs that do not. A `named-approver` PR's expert areas appear nowhere else, and dropping them wholesale would lose them. Both renderers read this off the same `rung_notes` call, so the two cannot say different things.

The slate is replaced by a sentence when the PR already has the reviewers its rung asks for. Coverage counts everyone on the hook, not only everyone who has finished: a reviewer who has been requested and has not answered yet is staffed on the PR, and naming more candidates will not make them answer sooner — it just suggests people, sometimes the very people already requested. That makes this a wider net than the **Status** column's, which asks whether the review has *happened*; a fully staffed PR can still read `Waiting`, and those are not conflicting answers but answers to different questions. The sentence says which it is: `Already reviewed by` when everyone counted has reviewed, `Already on this PR` when someone is still pending. The rung's other conditions still apply to requested reviewers — a PR whose assignees do not cover its `expert_areas` still gets a slate, because being asked is not the same as being qualified. Anything not knowable counts as *not* covered — `gh` unreachable, the PR closed, no rung on the stored review — because suggesting reviewers a PR turns out not to need is a smaller harm than withholding them from one that does. The "No maintainer listed for" note goes with the slate for the same reason: an unstaffed area is moot once someone qualified is already looking. Handles render without `@` throughout, so nobody is pinged speculatively; the `@` stays in the JSON for the request API. `request-pr-reviewers` is deliberately unaffected — asking for reviewers is an explicit act, not something the tool volunteered.

Request the suggested reviewers on GitHub. The PR author and anyone who already reviewed or was already requested are skipped, and each reviewer is requested individually so one non-collaborator does not abort the rest:

```bash
repo-manager request-pr-reviewers 1234 --dry-run
repo-manager request-pr-reviewers 1234
repo-manager request-pr-reviewers 1234 --reviewers bitgamma,jeremyfowers
```

PR reviews are stored locally (SQLite + `.repo-manager/reviews/prs/`) and are not part of the published dashboard or `pull`.

## Replaying a merged PR

Tuning the skill means asking whether it would have caught what human reviewers caught — and a merged PR's diff already contains the fixes those reviewers asked for, with their comments sitting in the context. `REPO_MANAGER_REPLAY_SHA` removes both: the diff is taken from the PR base to that commit, and the PR's reviews, comments, and check results are withheld, so the review judges the code as it stood before anyone looked at it.

```bash
REPO_MANAGER_REPLAY_SHA=abc1234 repo-manager review-pr 2603
```

The stored artifact still records the PR's live head SHA, so replay reviews are for evaluation rather than posting. One limit to read replays with: only the diff is rewound. Files the PR does not touch — workflows, `CMakeLists.txt`, the docs tree — are still read at the base branch's current state, so a review of a long-merged PR may cite a line that landed after it.

## One derivation

`review_requirement` is the single source of truth for what a PR needs, and `pr_review_facts` is the only thing that derives it: the rung is a surface lookup, then an unapproved breaking change and an expert area whose only owner is the author adjust what the PR actually requires. The attention level, the reviewer count, the **Scope** column and the **Status** column are all summaries of that one object.

They used to be three derivations. The CLI computed the requirement at generation time and wrote answers into database columns; the dashboard's list columns read those frozen copies; its Status column re-derived coverage from the *raw* artifact, which never carried the adjustments; and only the comment preview normalized properly. One PR could therefore show four answers — #2864 read Attention `High`, Scope `one-reviewer`, Status `Handled, 1 required`, and a comment asking for two reviewers. Each was fixed where it was reported, which is precisely what kept producing the next contradiction. The dashboard now runs every stored artifact through the same call the CLI does, and reads everything off the result.

The Scope column shows the rung, because it is the compact sortable form and ties to contribute.md's own vocabulary. A PR whose breaking change raises the requirement reads `two-with-maintainer` — the same slug shape as `two-with-expert`, saying what it needs rather than encoding it. It briefly read `one-reviewer → 2`, which rendered perfectly and communicated nothing: the arrow was a private notation for a number the reader had no way to interpret.

`reviewers_needed` rising to 2 on a breaking change is contribute.md's rule, not the tool's. It was the tool's for one afternoon, and that was a mistake worth recording: the guide implies *one* reviewer who must be a maintainer, since a single maintainer reviewing satisfies the rung and clears the break at once. Turning a constraint on **who** into a constraint on **how many** invented a second reviewer nobody had asked for — and then the Scope column had to invent notation to display the invented number. The two-reviewer rule now lives in the guide, where a tool enforcing it is reading policy instead of making it.

## PR Reviews in the web UI

The **PR Reviews** tab of the [web UI](web-ui.md) lists stored open-PR reviews with their status, attention level, and scope, and its detail pane includes the act half of the PR flow: **Post review comment** and **Request reviewers** buttons that run the same logic as `post-pr-review` and `request-pr-reviewers`. These buttons only work in the local UI (they use your `gh` credentials); the published static dashboard excludes PR reviews entirely.

**The detail pane is split by a horizontal rule, and everything below it is the comment.** Not a preview pane among other sections — the rule is the line between two audiences, and below it the pane renders the exact markdown `post-pr-review` submits: same renderer, same normalized artifact, minus only the HTML marker GitHub does not display either. The button posts what you just read, and nothing follows it.

Above the rule is what only a dashboard reader needs. Some of it is plain on the PR itself and invisible on the dashboard — the number, title, author, base branch, head SHA, and the review's one-sentence **Description**, which is how you tell one dashboard row from another and which anyone reading the comment can already get from the PR body they are looking at. The rest is meaningless to a PR reader: the live **Review Status**, which is computed from *your* perspective and would be a frozen, wrong claim once posted, plus the action buttons and the link to the already-posted comment. That test — obvious on the PR, or true only for the viewer — is what decides which side of the rule a field belongs on.

Below the rule the dashboard styles what it renders, without altering a character of it. Every verdict the review speaks in — `accurate`, `focused`, `adequate`, `gaps`, `discrepancies`, `missing`, `bundled`, `none found`, the attention level, and the ready/not-ready lead — becomes the same colored pill the list columns use, so a check's answer registers before it is read. The comment writes those verdicts as ordinary markdown emphasis, which GitHub renders as bold; only the rendering differs, never the text. "Checked:" evidence is muted, because what was inspected to earn a clean bill is support for a finding rather than one.

This replaced a pane that rendered the review a second way, one section per question, against a comment carrying a deliberately lean subset. The two disagreed about what the review had found, and aligning them section by section only produced new places to disagree; there is now one renderer and nothing left to align. Everything the pane used to show is in the comment, clean bills and their "Checked:" evidence included.

Because the pane below the rule *is* the comment, it inherits the comment's shape: the ready line, the to-do list, and the review suggestion are what you see, and the two **Explanation** folds hold the rest. Nothing in the list marks a row as partial any more, because no review is partial — every tier runs on every PR. The rung slug leads the list's **Scope** column, where it is the compact, sortable form, and the attention level is its own column.

The **Status** column answers one question — **does this PR need something from me?** — in four words, from the perspective of the `Status as` login. It is derived from live GitHub review activity (one batched GraphQL call per refresh, cached for 60 seconds).

| Status | Your move | When |
| --- | --- | --- |
| `Merge` | merge it | Enough qualifying approvals for the requirement, and a listed maintainer among them when a breaking change needs clearing. |
| `Review` | review it | You are a requested reviewer who has not reviewed, the guide names you for this rung, or you requested changes and the author has since **pushed**. |
| `Needs reviewer` | staff it | Even counting everyone already requested, the PR cannot meet its requirement — too few reviewers, no subject expert among them, the named approver absent, or a break with no maintainer to clear it. Waiting does not fix this. |
| `In progress` | nothing | Somebody else is on the hook: another reviewer's change request is with the author, or the reviewers on it cover what it needs. |

It used to speak eleven statuses describing review *activity* — `Handled`, `Waiting`, `In discussion`, `Needs 1 more`, `Needs subject expert`, `Approved (1/2)` — and a maintainer scanning the list still had to work out, row by row, whether any of it meant they should act. The words also failed to line up with action: `Handled` covered PRs nobody had reviewed, and `In discussion` covered eighteen with no reviewer assigned at all, which are precisely the ones needing attention. The axis is now whether anyone is on the hook for the next move, and the detail those eleven labels carried has moved into the tooltip.

The distinction that does the work in `Needs reviewer` is counting **requested** reviewers as well as actual ones. A requested reviewer is a commitment: the PR resolves without you. If even the people already asked could not satisfy the requirement, no amount of waiting closes the gap and somebody has to be assigned.

Colors answer "do I need to act?": green means merge, yellow means you, grey means somebody else.

Status is computed from a perspective — by default the `gh`-authenticated login. The **Status as** input in the panel header switches the whole column to any other GitHub ID's perspective (their "Requests" are your "Waiting"), reusing the same cached data.

Each row leads with a **read checkbox**: check it once you have read the review and acted on it, and the row stops asking for your attention. The check is tied to the Status it was made against, so it clears itself the moment that Status moves — a PR you had handled at `Waiting for me` comes back unchecked when the author pushes and it turns into `Requests`, because what you read is no longer what the PR says. The clear is written down rather than recomputed, so a Status that later returns to its acknowledged value stays unchecked: something happened on that PR while you were not looking. Two things deliberately do not count as a change — a blank Status, which means `gh` could not be reached rather than that anything moved, and retyping **Status as**, which re-labels the whole column without a single thing happening on GitHub. The state lives in SQLite alongside the commit-review read dots, keyed by PR rather than by rubric version, so re-reviewing a PR does not hand you a second checkbox for the same PR.

Two header toggles, both on by default, keep the list focused: **Hide closed PRs** drops merged and closed PRs, and **Hide PRs not into main** drops PRs targeting other branches. Both use the same live GitHub state; when that state is unavailable, rows are shown rather than silently hidden. The detail pane also shows each PR's live `State` (e.g. `OPEN → main`) and how long the review took to generate ("Generated in: NN seconds", also stored as `generation_seconds` in the artifact JSON and database).

# PR Reviews

repo-manager can pre-review open pull requests to flag what a human reviewer should look out for. This is advisory — it never replaces the human review.

## The three tiers

The review runs as three gated stages, mirroring `contribute.md`'s own control flow: its Reviewer Expectation lists what an author owes before a human reads the code, and its Review Process only reaches "evaluate scope, request a subject-area expert" after a PR is ready for review. Each tier is a separate skill and a separate `pi` run, so a tier that fails validation re-runs its own judgment instead of the whole review.

**Tier 1 — `pr-triage`** checks the two obligations that decide whether the PR has a reviewable *shape*: does the description honestly map the diff (Reviewer Expectation 1), and does the PR solve one clearly defined problem (Reviewer Expectation 2). Part of the first check is verifying every `Fixes #N` / `Closes #N` reference against the issue it names — those close the issue on merge, so a wrong one silently closes a live bug. Tier 1 also puts the PR on a **review rung** (below). If the description is inaccurate or missing, or the PR bundles unrelated work, the pipeline **stops here** — the remaining tiers never run, and the posted comment asks the author for exactly one fix.

**Tier 2 — `pr-quality`** checks the rest of the author's obligations: alignment with `philosophy.md` and `contribute.md`, documentation per `documentation.md`, testing per `testing.md`, and any breaking API or UX change. These are batched rather than individually gated, so the author gets one complete list instead of a series of round trips. Gaps here stop the pipeline before a reviewer is assigned.

**Tier 3 — `pr-reviewers`** suggests two to three human reviewers. It runs only once tiers 1 and 2 are clean and the PR is not a draft.

Two deliberate exceptions. A **draft PR** runs tiers 1 and 2 but never tier 3 — Review Process 1 says drafts are for CI and AI review before human review, so a draft gets exactly the author-facing feedback it is for. And an **uncleared breaking change** forces tier 3 to run even when other gaps remain, because only a listed maintainer can sign a break off; withholding the slate there would deadlock the PR.

Sections a tier never reached are recorded as `not-evaluated` rather than left absent, so a skipped check never reads as a clean bill, and the posted comment says plainly what has not been checked yet.

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

A stored review has one section per question — description accuracy, alignment, documentation, testing, scope, breaking changes, reviewers. Sections with findings render them as a to-do checklist with the justification and references as sub-bullets; clean sections instead show a one-line "Checked:" note recording what was inspected to earn the clean bill. The review never re-describes the PR beyond a one-sentence summary: the description-accuracy section judges the author's own description against the diff and only spends words on discrepancies.

## Act

Post the review as a PR comment — the comment is prefixed `[AI-assisted review]` per lemonade's AI policy, and re-posting updates the existing comment in place instead of duplicating it. The comment carries the whole review: the attention line, the description-accuracy check, focus, alignment, documentation, testing, breaking changes, and suggested reviewers (rendered without `@` so nobody is pinged speculatively). This is the same text the dashboard previews — see [the detail pane](#pr-reviews-in-the-web-ui).

**Every section folds into one line.** Each is a `<details>` whose summary carries the section and its whole answer — `Breaking changes: none found`, `Documentation: gaps`, `Alignment issues: 2 to resolve` — with the reasoning and the "Checked:" evidence inside. A PR with nothing wrong is a dozen visible lines rather than the fifty it would otherwise be, and the reasoning that earned each clean bill is still one click away for anyone who wants to audit it.

Which sections start open is decided by the news, not by the section. A clean bill is collapsed, because nobody needs to be told at length that nothing is wrong. A finding is open, because a to-do behind a fold is a to-do nobody does, and the comment exists so the author acts on it. Verdicts are lowercase throughout. Half of them — `adequate`, `gaps`, `focused`, `accurate`, `not-applicable` — are the artifact's own schema vocabulary and arrive lowercase; capitalising them in the comment would misquote the schema, so the verdicts the renderer invents (`none found`, `all cleared`, `2 to resolve`) match those instead of the other way round. A gated section is collapsed under `not evaluated`: it has nothing to report, and the gate is already the headline of the comment.

```bash
repo-manager post-pr-review 1234 --dry-run   # print the comment first
repo-manager post-pr-review 1234
```

The rung is stated once. It used to have a "Review needed" section of its own, which restated what the attention line above it had already said — and said it three ways over, since `attention_requirement_reason` folds the expert areas into the `two-with-expert` clause and the named approver's handle into the `named-approver` one. What survives sits directly under the attention level as **Why this rung**: the surface and rationale, the one part that line cannot carry. Each restatement is dropped only for the rung that actually duplicates it, because a `named-approver` PR's expert areas appear nowhere else and dropping them wholesale would lose them. The web UI merges the same two into one **Attention & Review Needed** section, off the same `rung_notes` call, so the two cannot say different things.

Whether the rung is answered is said on the attention line, where the rung itself is stated — `Elevated — needs 2 reviewers including 1 subject-area expert in llamacpp, ROCm. Covered by: bitgamma, kenvandine.` It used to sit on the readiness line above, which meant a PR read "no reviewers are suggested, these two cover it" and then, on the very next line, "needs 2 reviewers including an expert in X": the requirement and its answer split across two lines that each told half of it. `Covered by` means everyone counted has reviewed; `Already on this PR` means someone is requested and has not answered yet. When the rung is *not* answered nothing is added, because the `Suggested reviewers` fold below already says so.

A gated review goes terse for the same reason. Its lead line enumerates what the author owes, so the attention line drops both the "to-dos below need resolving" clause and the parenthetical counts — `the author owes 3 documentation gap(s), 1 testing gap(s)` followed by `(2 alignment issue(s); 3 documentation gap(s); 1 testing gap(s))` was one fact told three times, in three orders. A PR that is ready but still carries to-dos keeps the clause, where it is the only thing saying so.

The slate is dropped from the comment entirely when the PR already has the reviewers its rung asks for. Coverage counts everyone on the hook, not only everyone who has finished: a reviewer who has been requested and has not answered yet is staffed on the PR, and naming more candidates will not make them answer sooner — it just suggests people, sometimes the very people already requested. That makes this a wider net than the **Status** column's, which asks whether the review has *happened*; a fully staffed PR can still read `Waiting`, and those are not conflicting answers but answers to different questions. The sentence that replaces the slate says which it is: "already has the review its rung asks for" when everyone counted has reviewed, "already on this PR" when someone is still pending. The rung's other conditions still apply to requested reviewers — a PR whose assignees do not cover its `expert_areas` still gets a slate, because being asked is not the same as being qualified. Suggesting reviewers to a PR two maintainers are already reviewing is noise, and it is noise the dashboard contradicts — the Status column would be calling that same PR `Handled`. Both now read coverage off the same cached GraphQL call and the same rules, so the two cannot disagree. The opening line then says so and names who is covering it, instead of promising a slate at the end that never arrives. Anything not knowable counts as *not* covered — `gh` unreachable, the PR closed, no rung on the stored review — because suggesting reviewers a PR turns out not to need is a smaller harm than withholding them from one that does. The "No maintainer listed for" note goes with the slate for the same reason: an unstaffed area is moot once someone qualified is already looking. The dashboard drops the section in exactly the same cases, and its **Review Readiness** line carries the same sentence — a pane that silently omits the slate reads as a reviewer check that failed to run. The one exception, on both sides, is a gated review: the tier never ran, so the section still reports `not evaluated` rather than disappearing. Already covered and never asked are different answers, and the UI does not let them look alike. `request-pr-reviewers` is deliberately unaffected — asking for reviewers is an explicit act, not something the tool volunteered.

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

## PR Reviews in the web UI

The **PR Reviews** tab of the [web UI](web-ui.md) lists stored open-PR reviews with their status, attention level, and scope, and its detail pane includes the act half of the PR flow: **Post review comment** and **Request reviewers** buttons that run the same logic as `post-pr-review` and `request-pr-reviewers`. These buttons only work in the local UI (they use your `gh` credentials); the published static dashboard excludes PR reviews entirely.

**The detail pane is split by a horizontal rule, and everything below it is the comment.** Not a preview pane among other sections — the rule is the line between two audiences, and below it the pane renders the exact markdown `post-pr-review` submits: same renderer, same normalized artifact, minus only the HTML marker GitHub does not display either. The button posts what you just read, and nothing follows it.

Above the rule is what only a dashboard reader needs. Some of it is plain on the PR itself and invisible on the dashboard — the number, title, author, base branch, head SHA, and the review's one-sentence **Description**, which is how you tell one dashboard row from another and which anyone reading the comment can already get from the PR body they are looking at. The rest is meaningless to a PR reader: the live **Review Status**, which is computed from *your* perspective and would be a frozen, wrong claim once posted, plus the action buttons and the link to the already-posted comment. That test — obvious on the PR, or true only for the viewer — is what decides which side of the rule a field belongs on.

Below the rule the dashboard styles what it renders, without altering a character of it. Every verdict the review speaks in — `accurate`, `focused`, `adequate`, `gaps`, `discrepancies`, `missing`, `bundled`, `not evaluated`, `none found`, the attention level, and the ready/not-ready lead — becomes the same colored pill the list columns use, so a section's answer registers before it is read. The comment writes those verdicts as ordinary markdown emphasis, which GitHub renders as bold; only the rendering differs, never the text. "Checked:" evidence is muted, because what was inspected to earn a clean bill is support for a finding rather than one.

This replaced a pane that rendered the review a second way, one section per question, against a comment carrying a deliberately lean subset. The two disagreed about what the review had found, and aligning them section by section only produced new places to disagree; there is now one renderer and nothing left to align. Everything the pane used to show is in the comment, clean bills and their "Checked:" evidence included.

Because a review runs in tiers and a gate can stop it early, the detail pane leads with **Review Readiness**: either every tier ran, or the gate's reason in the same words the posted PR comment uses. Sections whose tier never ran say `not evaluated` and name the gate that stopped them, rather than reporting "None found" for a check nobody performed — a gated review is a partial one, and the UI says which part is missing. Gated rows carry a `gated` marker next to their rung in the list. The **Focus** section reports the triage verdict `focused` or `bundled`, with the split to-do when the diff bundles unrelated work.

The attention level's one-line meaning is derived from the rung, not from a fixed sentence per level. Level and rung are different questions — how much scrutiny, versus who has to look — and a level maps to more than one rung, so a fixed string could only describe one of them. That is how a `two-with-expert` PR came to be labelled "any reviewer can take it". The wording follows contribute.md's Review Process list: `needs any 1 reviewer`, `needs 2 reviewers including 1 subject-area expert`, `needs a review from @handle`. The web UI and the posted PR comment both render the same server-computed sentence, so the two cannot drift.

Because that sentence already carries the rung — in the guide's own words, with the expert areas and the named approver folded into the same clause — the detail pane shows a single **Attention & Review Needed** section rather than an Attention section and a Review needed section that restated it three ways over. What the merged section keeps beneath the level is the one thing the sentence cannot carry: the surface and rationale that put this PR on that rung. The rung slug itself still leads the list's **Scope** column, where it is the compact, sortable form.

The **Status** column tracks where each PR stands in the human review process, derived from live GitHub review activity (one batched GraphQL call per refresh, cached for 60 seconds). The vocabulary, with the full explanation in each badge's tooltip: `Needs triage` (no reviewer assigned, no reviews), `Waiting` (an assigned reviewer hasn't started, or another reviewer's change request is waiting on the author), `Waiting for me` (you are the assigned reviewer, or the author replied to your change request), `Requests` (your change requests are out, waiting on the author), `Handled` (another reviewer has it), and `Approved` (approved but not merged).

When a PR has reviewers but its rung is not yet satisfied, the status says which part is missing rather than asserting that a "core maintainer" is needed: `Needs N more` when the reviewer count is short — naming who already satisfies the expert slot and which area they cover — `Needs subject expert` when nobody reviewing lists any of the PR's `expert_areas` in the maintainer table, and `Needs @handle` when the PR is on the top rung and the maintainer the guide names has not weighed in. A rung that *is* satisfied says so with its arithmetic — who counts, how many the rung asks for, and who fills the expert slot — because the labels that follow name only the people other than you, which left a two-reviewer PR reading as though one reviewer had covered it. These come from parsing the maintainer table in `contribute.md`, so they follow the guide rather than a hardcoded idea of who is senior. The table's **Admin** column is deliberately not consulted: being a repository admin is a permission, not evidence of subject-area expertise. Who counts toward a rung is deliberately wider than GitHub's formal reviewer list: a maintainer who writes their review into the conversation box instead of the review box is in the loop just the same, so their comments count, and so do your own reviews even though the status speaks from your perspective. Comments only earn credit for people in the maintainer table — a passer-by's "+1" is not the review the guide asks for — and the PR author and AI reviewers never count. Colors answer "do I need to act?": green means no, gray means someone else is handling it, yellow means yes, red means urgent.

Status is computed from a perspective — by default the `gh`-authenticated login. The **Status as** input in the panel header switches the whole column to any other GitHub ID's perspective (their "Requests" are your "Waiting"), reusing the same cached data.

Each row leads with a **read checkbox**: check it once you have read the review and acted on it, and the row stops asking for your attention. The check is tied to the Status it was made against, so it clears itself the moment that Status moves — a PR you had handled at `Waiting for me` comes back unchecked when the author pushes and it turns into `Requests`, because what you read is no longer what the PR says. The clear is written down rather than recomputed, so a Status that later returns to its acknowledged value stays unchecked: something happened on that PR while you were not looking. Two things deliberately do not count as a change — a blank Status, which means `gh` could not be reached rather than that anything moved, and retyping **Status as**, which re-labels the whole column without a single thing happening on GitHub. The state lives in SQLite alongside the commit-review read dots, keyed by PR rather than by rubric version, so re-reviewing a PR does not hand you a second checkbox for the same PR.

Two header toggles, both on by default, keep the list focused: **Hide closed PRs** drops merged and closed PRs, and **Hide PRs not into main** drops PRs targeting other branches. Both use the same live GitHub state; when that state is unavailable, rows are shown rather than silently hidden. The detail pane also shows each PR's live `State` (e.g. `OPEN → main`) and how long the review took to generate ("Generated in: NN seconds", also stored as `generation_seconds` in the artifact JSON and database).

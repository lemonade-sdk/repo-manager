# PR Triage

repo-manager triages open pull requests under lemonade's [spec-driven development policy](https://github.com/lemonade-sdk/lemonade/blob/main/docs/dev/spec-driven-dev.md): every PR gets an `rfc:` label, and the triage produces the facts a maintainer needs to apply it. It is advisory. A maintainer applies the label.

## The five lines

The posted comment is five lines, and everything else is in a fold:

```
label: rfc:required
scope: needs-rfc
body matches diff: no
docs and tests: gaps
suggested reviewers: bitgamma, superm1
```

| Key | Values | What it answers |
| --- | --- | --- |
| `label` | `rfc:required`, `rfc:not-required`, `rfc:on-roadmap` | The label to apply. `on-roadmap` means an RFC was needed and an approved one is linked and implemented as written. |
| `scope` | `fix`, `working-group`, `rfc`, `exceeds-working-group`, `exceeds-rfc`, `no-charter`, `needs-rfc` | What covers the change. The first three are clean. `exceeds-` means the PR does charter or RFC things plus something outside them. `no-charter` means the named group has no charter file yet. `needs-rfc` means nothing is linked and the change is not a fix. The group or RFC number follows in parentheses. |
| `body matches diff` | `yes`, `no` | Whether the title, body, template checkbox, Breaking Changes section, and every `Fixes #N` describe the diff. An undisclosed breaking change, a bundled second problem, or a fix claim on a feature makes this `no`. |
| `docs and tests` | `ok`, `gaps` | documentation.md and testing.md, as before. |
| `suggested reviewers` | handles | Two or three, never the author. |

The `<details>` fold under them lists only what a reviewer has to worry about: the surfaces nothing covers and what was looked for, each breaking change with whether the body discloses it, each place the body misstates the diff, the docs and tests to-dos, and why each reviewer is named. A clean PR gets "Nothing to flag." What the PR does well is not in the comment; the model's fuller reasoning stays in the stored artifact for audit.

## How it decides

Three Pi runs read the PR; the labels are derived in Python from what they report, so the rule that turns a surface into a label is written once, in `repo_manager/triage.py`, and can be read.

**`pr-facts`** reads the diff and body and lists the *surfaces* the diff changes, each with a kind from a closed vocabulary (`endpoint-new`, `cli-flag`, `config-key`, `config-default`, `gui`, `backend-new`, `persisted-format`, `docs-structure`, `ci-infra`, `refactor`, `security`, `internal`, and a few more), plus breaking changes with their migration cost, places where the body misdescribes the diff, and the maintainer-table subject areas the diff lands in. Python hands it mechanical leads first — route registrations, `add_option` calls, `defaults.json` keys checked against what the base branch already reads, app files, workflow jobs, charter edits — so a route cannot be missed by a reader who skimmed.

Each surface that changes an existing behavior also says what it replaced, in `was`: `working`, `failing`, `ignored`, or `absent`. This is the single fact that separates a fix from a feature under the policy, and a small model answers a categorical question far more reliably than it applies a prose rule: `failing` and `ignored` are fixes by the existing contract, `absent` is new and can only be covered by a charter or an RFC.

**`pr-cover`** takes those surfaces and, for each non-internal one, says what covers it: a statement of intended behavior already on the base branch (a fix), a ratified charter's scope or roadmap item, or the linked RFC's design. A linked issue is evidence of a deviation, never of intent; if the only thing calling the current behavior wrong is the issue, the change is a feature. Charters are read from the base branch, so a PR that edits a charter to include itself is judged against the charter as it stood. An RFC number resolves as a discussion first and an issue second, and only one labeled `rfc:on-roadmap` satisfies the policy.

**`pr-quality`** is the docs and tests check, rewritten compactly for a small model: the owning reference page for each surface, and a covering test wired into a CI job.

**Derivation.** Anything but `internal` needs cover. A migration, automatic or not, is never charter-covered. `fix` or `working-group` gives `rfc:not-required`; `rfc` gives `rfc:on-roadmap` when the RFC is approved and `rfc:required` otherwise; everything else gives `rfc:required`. A group in the working-groups table with no charter file gives `no-charter`, which is `rfc:required` until a charter lands — this is deliberate for the smart router, which has PRs labeled by hand as not required.

**Mechanical backstops.** Every rule the tuning batches showed the model applying inconsistently is also enforced in code, from the diff and the base branch rather than from the model's prose: a `defaults.json` key the base already reads is not a new key; `ci-infra` needs a changed workflow, action, or shared test helper, and CI that only builds the PR's own feature belongs to that feature; `docs-structure` needs a page added, removed, or moved; a changed default is recorded as a breaking change whether or not the model listed it; a charter covers a surface only through a quoted bullet line, never a heading or a goal sentence; identifiers a surface names that already exist in base-branch source are noted for the cover pass. Merged PRs are judged against the merge commit's first parent, so today's main cannot report a PR's own routes as already there.

**Reviewers** are pure Python: the Code Owner from spec-driven-dev.md for the surfaces touched (CLI, GUI, networking and security, endpoints and backends and breaking changes), the lead of the working group the change sits in, maintainers who took part in the linked RFC, maintainers whose subject areas the facts pass named, then whoever wrote the code by blame, in two passes: the lines the hunks touch, and the identifiers the surfaces name grepped across the tree, which is what reaches the person who built the feature a PR extends. The strongest blame hit ranks ahead of subject-area matches. The author is never a candidate. This replaced a model-driven reviewer tier that twice named contributors as maintainers who were not in the table.

## Model

The three skills are written for Qwen3.6-35B-A3B running under Pi: short prompts with the context inlined, a closed vocabulary, and a JSON schema with an example. Python gathers the context (`gh pr view`, the diff, the guides and charters at the base ref, the RFC, linked issues) and the model reads a brief rather than driving scripts. `REPO_MANAGER_PI_MODEL` names the model; `repo-manager pi setup` writes the provider config that points Pi at a Lemonade server. Runs are sequential — the model is one local server.

## Commands

```bash
repo-manager pr review 1234                    # triage one PR and write prs/1234.json
repo-manager pr review 1234 --no-store         # print the comment instead
repo-manager pr review 1234 --replay SHA       # judge the PR as it stood at SHA
repo-manager pr sweep [--force] [--since D]    # open PRs opened on/after D (default 2026-09-10) whose file is out of date
repo-manager pr post 1234 [--dry-run]          # post or update the comment
repo-manager pr request-reviewers 1234         # request the suggested reviewers
repo-manager pr label 1234 [--dry-run]         # apply the label; for rfc:required also draft + RFC request
```

To read a stored triage, open the dashboard (`repo-manager site serve`) or `jq` the file.

## Acting on the label

`pr label` is the one code path that turns a triage into an act on GitHub, and it is what the dashboard's **Request RFC** / **Apply label** button and the future GitHub Action both call, so the three cannot drift. It applies the triage's `rfc:` label (removing any other `rfc:` label), and when the label is `rfc:required` it also converts the PR to a draft and posts the standard message:

> Thanks for your PR! Please be aware that PRs that change Lemonade's scope, surface area, or user/dev experience need an approved request for comment (RFC) discussion before they can be reviewed. You can learn about the process [here](https://github.com/lemonade-sdk/lemonade/blob/main/docs/dev/contribute.md). If you believe this assessment was made in error, please contact a maintainer on the #dev channel of the Lemonade Discord.

Every step is idempotent: a label already present is not re-added, a draft is not re-drafted, and the message is posted once and found again by its HTML marker. `--label` overrides the stored triage's label, which is how a maintainer corrects one. The Action will run `pr review` on each new PR and then `pr label`; nothing else is needed for it beyond a state directory and `gh` credentials.

Each triage is one file, `prs/<number>.json`, holding the three tiers' output, the derived
five lines, the prose behind them, and what GitHub said about the PR when it was triaged.
Posting a comment records its id and URL in the same file, so the next `pr post` updates the
comment it already owns rather than opening a second one.

## Evaluating against the maintainer's judgment

`scripts/eval-triage.py` runs the triage on a batch of PRs against a scratch state directory and scores the label against a ground-truth file:

```bash
scripts/eval-triage.py --truth scripts/triage-ground-truth.json --out runs/batch-1 3468 3494 3470
```

Ground truth is `{"3468": {"label": "rfc:required", "weight": 2}}`; `--replay pr=sha` judges a merged PR at the commit before its human review, so the question becomes whether the triage would have caught what the reviewer caught. `expected_tool_label` overrides `label` where the tool is meant to disagree with the human label (router PRs with no charter). Each artifact and its rendered comment land in `--out`, and a table of expected versus actual labels prints at the end.

## Dashboard

The **Pull requests** tab lists the five derived lines per PR, filterable by label. Opening a
row shows what the diff changes, the concerns exactly as the comment would list them, and why
each reviewer is named. When the page is being served rather than rendered statically, that
row also carries the **Post comment**, **Apply label**, and **Request reviewers** buttons,
which call the same code paths the CLI does.

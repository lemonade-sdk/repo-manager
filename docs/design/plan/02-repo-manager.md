# Step 2: Agent actions in `lemonade-sdk/repo-manager`

Read `../lemonade-testing.md` first. This step rewrites repo-manager to use a directory of files as its only store and to know the weekly release buckets. Steps 3 and 4 build on the interfaces defined here, so keep them exactly as written or update those docs.

Do the work on a branch. Do not preserve backward compatibility with the SQLite workspace, `vNext`, or the published-dashboard state format; delete rather than migrate.

## Interfaces the later steps depend on

### Store layout

One directory, called the state directory, with this layout:

```
commits/<sha>.json
prs/<number>.json
releases/<bucket>/review.json
releases/<bucket>/notes.md
releases/<bucket>/announcement.md
releases/<bucket>/candidates/<N>.md
releases/<bucket>/generated.json
```

`<bucket>` is `v<year>.<week>`, for example `v2026.38`. `<sha>` is the full 40-character SHA. Files are the same JSON the skills already emit, pretty-printed with sorted keys so diffs are readable.

`commits/<sha>.json` must contain at least `sha`, `committed_at` (ISO 8601), `pr_number`, `author`, `summary`, `verdict`, and `bucket`, so the dashboard renders from files alone with no git checkout.

`review.json` must contain at least `verdict` (`Ready`, `Needs Attention`, or `Blocked`), `branch`, `range_start`, `head_sha`, `prioritized_todos`, `breaking_changes`, and `tester_plan`.

`generated.json` maps each generated filename in the bucket to the SHA-256 of the content repo-manager last wrote. A file whose current content does not match is human-edited and frozen.

`notes.md` is exactly `## Headline` followed by `## Breaking Changes`, the format the lemonade release action already parses. `announcement.md` is the Discord post.

### CLI

Commands are grouped by noun. Every command takes the global options `--state DIR` (default: current directory), `--repo OWNER/REPO`, `--checkout DIR` (a clone of the tracked repo used for git and diff operations), and `--no-push`. If `DIR` is a git checkout with an `origin` remote and `--no-push` is absent, every write is committed and pushed. `--force` regenerates an artifact that already exists, where that applies.

| Command | Job | Behavior |
|---|---|---|
| `commit review SHA` | 1 | Writes `commits/<sha>.json` unless it exists. |
| `commit sweep [--branch B] [--since TAG]` | 1 | Reviews every commit in `last-stable-tag..origin/B` with no file. |
| `pr review N` | 3 | Writes `prs/<n>.json`. |
| `pr sweep [--since]` | 3 | Reviews every open PR with no file. |
| `pr post N` | 3 | Posts or updates the triage comment on the PR. |
| `pr label N` | 3 | Applies the `rfc:` label; `rfc:required` also posts the RFC request. |
| `pr request-reviewers N` | 3 | Requests the suggested reviewers. |
| `release build --branch B [--head SHA]` | 2 | Computes the bucket from the branch (or the cutoff clock on `main`), runs `commit sweep` for the range, then `release review`, `release notes`, and `release announce`. `--head` pins the commit being described (default: `origin/B`); the workflow passes the commit it is building. Regenerates in place on rerun, except frozen files. This is what the workflow calls. |
| `release review --branch B [--head SHA]` | 2 | Writes `releases/<bucket>/review.json`. |
| `release notes --branch B [--head SHA]` | 2 | Writes `releases/<bucket>/notes.md` unless frozen. |
| `release announce --branch B [--head SHA]` | 2 | Writes `releases/<bucket>/announcement.md` unless frozen. |
| `release candidate --branch B` | 4 | Later. Writes `releases/<bucket>/candidates/<N>.md`. Define the command; exit with "not implemented". |
| `site render --out DIR` | dashboard | Writes the static site from the state directory. No network. |
| `site serve` | local | Serves the web UI over the state directory. |
| `pi setup` | setup | Installs the bundled skills into pi's config directory under `$HOME` and writes pi's `models.json` and `settings.json` with a `Lemonade` provider at `REPO_MANAGER_LEMONADE_URL` (OpenAI-compatible, api key `lemonade`) and `REPO_MANAGER_PI_MODEL` as the default model. Idempotent. |
| `pi version` | setup | Prints the pinned `pi` version. |

There are no read commands. Browsing stored artifacts is the web UI's job (`site serve`), or `ls` and `jq` on the files.

Removed: `init`, `pull`, `publish-pages`, `sync`, `override-announcement`, `wipe-db`, `db-table`, `db-row`, `pr-table`, `pr-row`, `status`, `all`, `review-commit`, `sweep`, `review-pr`, `sweep-prs`, `post-pr-review`, `apply-pr-label`, `request-pr-reviewers`, `release-review`, `announce`, `ui`. Every old name is gone; there are no aliases.

Exit code is nonzero if any artifact could not be produced. Step 4 relies on this to fail the release job.

## Tasks

### A. Store module

- New module `repo_manager/store.py`: maps keys to paths, reads and writes files, and commits and pushes. Push does `git push`; on rejection, `git fetch` and `git rebase origin/main`, then push again, up to 5 tries. Commit messages name the key, for example `commits: abc123` or `releases/v2026.38: review, notes`.
- Commit after each artifact, not at the end, so a cancelled run loses at most one in-flight review.
- Delete `db/schema.sql`, `connect_db`, every `ALTER TABLE` migration, and `sync_down.py`. Delete the removed commands listed above and add the new command tree with argparse subparsers, one module per noun (`commands/commit.py`, `commands/pr.py`, `commands/release.py`, `commands/site.py`, `commands/pi.py`).

### B. Buckets replace CMake inference

- Port `upcoming_release_week` and `release_branch_name` from lemonade's `tools/version.py` into repo-manager (copy the functions and their tests; do not import lemonade).
- Bucket for a `release-v<y>.<w>` branch is `v<y>.<w>`. Bucket for `main` is `v<y>.<w>` from `upcoming_release_week(now)`.
- Range start is the newest `v*` tag whose bucket is earlier than the current bucket. A tag's bucket is its first two components. This matters on a tag build or a hotfix: bucket `v2026.38` with stable `v2026.38.1` still ranges from the newest `v2026.37`-or-earlier tag, never from its own tag. Ignore tags that do not match `^v\d+(\.\d+)+$`, which excludes `candidate-v*`.
- Delete `cmake_release_tag`, `cmake_version_from_text`, `release_lifecycle_info`, `migrate_vnext_release_state`, `resolve_release_state`, and every `vNext` string.
- `version_parts` and the dashboard sort must order `v11.9.0` before `v2026.38` and `v2026.38` before `v2026.38.2`.

### C. Release job

- `release build` reviews unreviewed commits in range before generating artifacts. Follow the `(cherry picked from commit <sha>)` trailer so a hotfix on a release branch resolves to the original PR for `pr_number` and `author`.
- Generate `review.json`, `notes.md`, `announcement.md` with the existing skills and validators. Keep the existing retry-with-feedback loop; drop the `.pending` files (retries stay in memory).
- Human edits are authoritative. After writing any file in a bucket, record its SHA-256 in `generated.json`. Before writing, compare: a file whose content does not match its recorded hash is frozen. `release build` prints which files are frozen and skips them; `--force` writes them anyway and re-records the hash.
- Fold open lemonade issues with the `candidate` label, opened after the bucket's branch was cut, into the review prompt. Each becomes a to-do with the RFC's outcome to choose (fix later, hotfix, revert).
- Add a tester plan to `review.json`: for each platform (Windows, Ubuntu PPA, Snap, Docker, macOS, Fedora, Debian), what changed in this bucket and what to exercise, drawn from the commit reviews' manual-release-testing evidence.
- Add a hotfix variant of the announcement: when the bucket already has a stable tag, the post opens with `@release` and covers only the commits since that tag.
- Delete `sync`, `cmd_sync_release_review_issue`, the issue markers, and the checkbox parsing.

### D. Dashboard and UI

- `site render` produces the static site from files only; it must not need `--checkout` or the network. Same content as today's published dashboard plus one row per live bucket with verdict, open blockers, and the tester plan. PR triages are included now.
- `site serve` reads the directory. Build an in-memory index at startup if needed. To-do check-off and read state are removed; the release admin edits files instead.

### E. Releases and pinning

- repo-manager runs `pi` against a Lemonade server, never a hosted model provider. `run_pi` reads `REPO_MANAGER_PI_MODEL` as today; `pi setup` is how the provider gets configured on a fresh machine.
- Pin the `pi` version repo-manager is tested against in `setup.py` metadata (a `PI_VERSION` constant exposed as `repo-manager pi version`) so the consuming workflows can install the matching one.
- Add a `release.yml` workflow: on push of a `v*` tag, run the tests, then create a GitHub release for the tag. Version is the tag. `repo-manager --version` reports it.
- Tag `v1.0.0` when this step is done. Consumers pin to tags.

### F. Tests and docs

- Unit tests for the store (write, commit, push-retry with a simulated rejection), bucket computation, version ordering, cherry-pick trailer resolution, and the human-edit rule.
- Rewrite `README.md` and `docs/` for the file store and buckets. Replace every `v10.7.0`, `vNext`, CMake, and issue-sync mention. Update skill examples to `v2026.38`-style versions.
- Delete `docs/syncing.md`.

## Done when

- `repo-manager commit review SHA --state /tmp/x --no-push` on a fresh directory writes one file.
- `repo-manager release build --branch main --state /tmp/x --no-push` reviews the range since the newest `v*` tag and writes the three artifacts.
- `repo-manager site render --state /tmp/x --out /tmp/site` produces a browsable site.
- `pip install git+https://github.com/lemonade-sdk/repo-manager.git@v1.0.0` on a clean machine works and `repo-manager --version` prints `1.0.0`.
- `grep -ri "sqlite\|vnext\|cmake" repo_manager/ skills/ README.md docs/*.md` returns nothing (`docs/design/` is the history and is exempt).

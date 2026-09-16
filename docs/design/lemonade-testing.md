# Design: repo-manager for the weekly release process

## Proposal Size

Major feature (spans multiple well-scoped PRs across `repo-manager`, `lemonade`, and a new `lemonade-testing` repo).

## User Story

The new release process (RFC #3522, PR stack #3579-#3589) turns releasing into an automated weekly machine: a cron cuts `release-v<year>.<week>` from `main` at lemonade's weekly release cutoff, every push to that branch publishes a numbered candidate, and a human tags one candidate to promote it to stable. repo-manager was built for the old process, where a human decided to release, bumped the version in `CMakeLists.txt`, and had days to read repo-manager's output before tagging. Three things no longer hold:

1. **repo-manager cannot tell which release it is tracking.** It infers the release from the CMake version, which PR #3539 removed. On `main` today, inference never resolves, so `repo-manager all` skips release review, announcement, and issue sync on every run.
2. **Its output is now consumed by automation on a clock, not by a person.** The release action reads the release-notes issue when it creates the release page. If repo-manager has not finished for that commit, the page ships without notes. A commit merged a minute before the cutoff lands in that week's branch, and the current workflow's cancel-in-progress concurrency cancels the run that was reviewing it.
3. **It runs on one self-hosted machine holding the only copy of its state.** A SQLite database under `/opt/lemonade-manager` is the source of truth. If that machine is down, no candidate can get release notes. The same database is also the development store, so any change to storage has to be made twice.

This design makes release notes a guaranteed part of every candidate and stable release, and lets repo-manager run on any of several runners with no state on the runner.

## High-Level Design

### Release buckets

A bucket is `v<year>.<week>`, the two-component prefix of the versions the build system produces. It is computed the same way `tools/version.py` computes it: from the branch name on a `release-v*` branch, or from lemonade's release cutoff on `main`. The final `.number` is unknown until a human tags, so nothing repo-manager stores is keyed on it.

Several buckets are live at once: the one accumulating on `main`, the one under test, and any older branch taking a hotfix. All state is keyed per bucket and per branch. `vNext`, CMake inference, and the `vNext`-to-tag migration code are removed.

### Jobs

Four jobs, each idempotent and each keyed on a natural identifier so reruns are free.

| Job | Trigger | Reads | Writes |
|---|---|---|---|
| 1. Commit review | push to `main` | `commits/<sha>.json` | `commits/<sha>.json` if missing |
| 2. Release artifacts | a job in the release workflow, on every push to `release-v*` and every `v*` tag | all commit files in `last-stable..tip`, the bucket's existing artifacts | `releases/<bucket>/review.json`, `notes.md`, `announcement.md`; missing `commits/<sha>.json` for any unreviewed commit in range |
| 3. PR review (future) | PR opened or synchronized | `prs/<number>.json` | `prs/<number>.json` |
| 4. Candidate delta (future) | job 2, when the bucket already has a candidate | previous candidate's commit list | `releases/<bucket>/candidates/<N>.md` |

Job 2 never assumes earlier runs covered the range. It reviews any commit in `last-stable..tip` that has no file, where `last-stable` is the newest `v*` tag belonging to an earlier bucket, so a bucket's own stable tag never truncates its range on a hotfix or tag build. That closes the race at the cutoff: a cancelled or never-run review on `main` is simply done here. The release workflow's build takes 1.5 to 3 hours, so job 2 normally adds no wall-clock time to a candidate; it only becomes the long pole if job 1 has fallen far behind on `main`.

### Release notes are a required step

Job 2 is a `needs:` dependency of the job that creates the GitHub release, in `cpp_server_build_test_release.yml`. The release page is never created without notes. If the model call fails, the job retries internally; if it still fails, the release job fails and a human reruns it. A failed candidate is preferable to a published page with no notes.

The release-notes action reads `releases/<bucket>/notes.md` from `lemonade-testing` through the GitHub contents API, which is not behind a CDN cache. Candidate and stable pages read the same file.

### State lives in `lemonade-sdk/lemonade-testing`

All repo-manager state is plain files on a branch of a new repo, one file per artifact:

```
commits/<sha>.json
prs/<number>.json
releases/<bucket>/review.json
releases/<bucket>/notes.md
releases/<bucket>/announcement.md
releases/<bucket>/candidates/<N>.md
```

Concurrency comes from git. A push is atomic. Each job commits its files and pushes; on rejection it fetches, rebases, and pushes again. Jobs 1 and 3 write disjoint paths and never conflict, so any number of them run in parallel across the runner pool. Jobs 2 and 4 share a bucket's paths and are serialized by a queued concurrency group named after the bucket. `main` pushes and release-branch pushes use separate groups.

### One store, everywhere

The file directory is the only store. There is no database in development or in production, and no code path that differs between them.

- Every command reads and writes the directory. In production it is a clone of `lemonade-testing`. On a laptop it is the same clone, or an empty directory for a scratch run.
- Pushing is a flag, not a mode. The writer commits after each artifact and pushes with the rebase-retry loop; `--no-push`, or a directory with no remote, skips the push. Nothing else branches on environment.
- The web UI and the dashboard renderer read the directory directly. If a listing over a few thousand JSON files is slow, they build an in-memory index at startup. No cache on disk.
- Local work is real work. Reviewing a commit on a laptop and pushing puts it into production; pulling brings production to the laptop. This replaces `pull`, `publish-pages`, and `init --clone`.
- Isolated experiments use a branch or fork of `lemonade-testing`. Skill tuning runs against a scratch directory and diffs it against the real one.

The SQLite database, its schema and migrations, the JSON-in-a-column round-trips, `sync_down`, and the embedding of state into dashboard HTML are all removed. They are replaced by one small module that maps keys to paths and commits and pushes.

### No machine-owned issues

repo-manager stops creating `final checklist`, `release notes`, and `announcement` issues. Three issues per bucket would be about 150 a year of machine-owned noise in the lemonade tracker.

- **Release notes and announcement** are edited by the release admin directly in `notes.md` and `announcement.md` through the GitHub web editor. A human edit is authoritative: repo-manager records a hash of every file it generates, and a file whose content no longer matches its hash is frozen for that bucket. `release build` logs it and leaves it alone; `--force` regenerates it.
- **Checklist and verdict** are a dashboard page, rendered from `review.json`.
- **Tester feedback** is real issues in lemonade carrying the `candidate` label. Job 2 folds open `candidate` issues for the bucket into the verdict as blockers, each with the RFC's outcome to pick: fix later, hotfix, or revert.

What is lost is the comment thread where a maintainer could write "this to-do is not a problem because..." and have it fed back into the next review. If that is missed, add a single tracking issue per bucket in `lemonade-testing`, not lemonade.

### Dashboard

A workflow in `lemonade-testing` renders the dashboard on every push to the state branch and deploys it to GitHub Pages at `https://testing.lemonade-server.ai`. Rendering runs only in `lemonade-testing`, so a rendering bug never blocks a lemonade release. The dashboard shows one row per live bucket: version, verdict, open blockers, and the tester plan for each platform.

### Runners and tokens

- The jobs run on the four existing Strix Halo runners (`[self-hosted, stx-halo, Linux, X64, lemon-prod]`). Each job installs `gh`, a pinned repo-manager tag, and the `pi` version it declares into the job workspace at the start of the run, without root. The model is served by a `lemond` the job itself starts from a pinned Lemonade embeddable release (`v11.9.0`, model `Qwen3.6-35B-A3B-MTP-GGUF`), with weights cached on the machine across jobs. No hosted model provider is involved. A repo-manager change ships by tagging and bumping the `REPO_MANAGER_VERSION` variable in lemonade.
- Lemonade jobs push to `lemonade-testing` with a GitHub App token (same pattern as the release-branch app), since the default `GITHUB_TOKEN` is scoped to lemonade.
- Job 3 on fork PRs gets no secrets under `pull_request`. It needs `pull_request_target` or a label trigger. Decide before building it.

### Changes by repository

**repo-manager**
- Replace CMake inference with the cutoff-clock bucket; key all state per bucket and branch; delete `vNext` handling.
- Replace SQLite with the file store: one module that maps keys to paths, commits, and pushes with rebase-retry. Delete the schema, migrations, and `sync_down`.
- Make every command idempotent over "what has no file yet".
- Follow `(cherry picked from commit ...)` trailers so hotfix commits on a release branch resolve to their original PR.
- Add the tester plan and `candidate` issue ingestion to the release review; add the hotfix variant of the announcement.
- Remove issue sync, `pull`, `publish-pages`, `init --clone`, and the dashboard state embedding. The web UI and the dashboard renderer read the directory.
- Update docs and skill examples from `v10.7.0`-style versions to `v2026.38`.

**lemonade**
- Add the repo-manager job to `cpp_server_build_test_release.yml` as a dependency of the release job.
- Point `generate-release-notes` at `notes.md` in `lemonade-testing`.
- Keep a `main` push trigger for job 1. Move the snap dispatch out of `repo-manager.yml` into the release workflow. Make concurrency groups per branch and queued.
- Fix `docs/dev/release.md`: the label is `candidate`, the tiers are P0/P1, and references to the three issues are removed.

**lemonade-testing (new)**
- State branch with the layout above. Pages workflow. CNAME for `testing.lemonade-server.ai`.

### Rollout

1. Create `lemonade-testing`, install the App on it, and set up Pages and DNS. No behavior change yet.
2. Land the repo-manager rewrite: bucket inference, file store, idempotent jobs. Run job 1 on `main` in parallel with the existing workflow to validate.
3. Land the lemonade release-workflow job and the notes-action change together with the first release-branch cut that uses them.
4. Retire the old `repo-manager.yml` run, the workspace on the runner, and the three issues.
5. Jobs 3 and 4 as follow-ups.

## Breaking Changes

- The `Release v* final checklist`, `v* release notes`, and `v* announcement` issues are not created. Admins edit files in `lemonade-testing`.
- The dashboard moves from `lemonade-server.ai/repo-manager` to `testing.lemonade-server.ai`.
- A candidate or stable release fails if release notes cannot be generated.

## Maintenance Plan

The runner pool and the App token are the only operated pieces. State is a git branch, so recovery from any failure is a rerun. There is no database anywhere, so nothing to back up and no second code path to keep working.

## Risks

1. Making release notes a required step couples releases to a model call. Mitigated by in-job retries, idempotent reruns, and the human's ability to rerun a single job.
2. A human edit freezes the whole file, so a hotfix landing after the admin edited `notes.md` is not reflected until the admin edits again or runs `--force`. Accepted: the admin is already in that file during the candidate week.
3. A fork-PR trigger for job 3 is a known security tradeoff and is deferred.

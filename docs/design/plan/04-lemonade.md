# Step 4: Agent actions in `lemonade-sdk/lemonade`

Read `../lemonade-testing.md`, `02-repo-manager.md`, and `03-lemonade-testing.md` first. Steps 2 and 3 are done: repo-manager reads and writes a state directory, it is released as `v*` tags, and `lemonade-testing` renders whatever is pushed to it.

This work goes on top of the release-process stack (#3579 through #3589). If the stack is not merged yet, branch from its top (`jfowers/version-without-git`) and note it in the PR. Keep the PRs small; the list below is a reasonable split.

## Shared job definition

Both workflows below run repo-manager the same way. Write it once as a composite action at `.github/actions/repo-manager/action.yml` and call it from both. Inputs: `command`, `app-client-id`, `app-private-key`.

- Every job that uses it has `runs-on: [self-hosted, stx-halo, Linux, X64, lemon-prod]`.
- Tool setup, no root, everything under the workspace:
  - `actions/setup-python@v5` with `python-version: '3.12'`.
  - `actions/setup-node@v4` with `node-version: '22'`.
  - `gh`: download `https://github.com/cli/cli/releases/download/v2.x.y/gh_2.x.y_linux_amd64.tar.gz` (pin the version in the action) with `curl`, extract to `$RUNNER_TEMP/gh`, and append its `bin` to `$GITHUB_PATH`.
  - `python -m venv $RUNNER_TEMP/venv && $RUNNER_TEMP/venv/bin/pip install git+https://github.com/lemonade-sdk/repo-manager.git@${{ vars.REPO_MANAGER_VERSION }}`; append `$RUNNER_TEMP/venv/bin` to `$GITHUB_PATH`.
  - `npm install --prefix $RUNNER_TEMP/npm -g pi@$(repo-manager pi version)`; append `$RUNNER_TEMP/npm/bin` to `$GITHUB_PATH`.
  - `repo-manager pi setup` installs the bundled skills and writes pi's provider config pointing at `REPO_MANAGER_LEMONADE_URL`. Run it with `HOME=$RUNNER_TEMP/home`, and run the repo-manager command with the same `HOME`, so pi's config never touches the shared machine's home directory. The model-server steps above use the real `HOME` for the weight cache.
  - Cache `$RUNNER_TEMP/venv` and `$RUNNER_TEMP/npm` with `actions/cache` keyed on `REPO_MANAGER_VERSION` and the pi version. These are small, so GitHub's cache is fine for them.
- Model server, managed by the job. Pinned versions live at the top of the action as env: `LEMONADE_VERSION: 11.9.0`, `REPO_MANAGER_MODEL: Qwen3.6-35B-A3B-MTP-GGUF`.
  - Everything the server needs persists on the machine under `$HOME/.cache/lemonade-ci/repo-manager/`, where `HOME` is the runner service user's real home directory. Resolve it once at the start of the action with `CI_CACHE=$(getent passwd "$(id -u)" | cut -d: -f6)/.cache/lemonade-ci/repo-manager` rather than reading `$HOME`, because other jobs on these runners override `HOME` into the workspace and the value can leak between steps. Never place it under `$GITHUB_WORKSPACE` or `$RUNNER_TEMP`: the workspace is wiped by `actions/checkout` with `clean: true` and `RUNNER_TEMP` is cleared between jobs.
  - Embeddable: if `$CI_CACHE/lemonade-${LEMONADE_VERSION}/lemond` exists, use it. Otherwise download `https://github.com/lemonade-sdk/lemonade/releases/download/v${LEMONADE_VERSION}/lemonade-embeddable-${LEMONADE_VERSION}-ubuntu-x64.tar.gz` with `curl` and extract it there. Do not use `actions/cache` for this; it would re-download from GitHub's cache on every job.
  - Model cache directory for `lemond`: `$CI_CACHE/models`. Weights downloaded by `lemonade pull` land there and are found by every later job on that machine. Print `du -sh $CI_CACHE/models` after the pull so the log shows whether a download happened.
  - Pick a free port the way `validate_vllm.yml` does, start `lemond $CI_CACHE/models --port <port> --host 127.0.0.1` in the background, and poll `/api/v1/health` until it answers, with the same retry-on-a-new-port loop as that workflow.
  - Pull `${REPO_MANAGER_MODEL}` through the server's HTTP pull endpoint on that port (look up the exact request in the v11.9.0 API docs). The first run on each machine downloads the weights; later runs find them in `$CI_CACHE/models` and the pull returns immediately.
  - Export `REPO_MANAGER_LEMONADE_URL=http://127.0.0.1:<port>/v1` and `REPO_MANAGER_PI_MODEL=Lemonade/${REPO_MANAGER_MODEL}` for the repo-manager step.
  - A final step with `if: always()` posts `/internal/shutdown` to the port and kills the `lemond` PID. Do not `pkill lemond`; another job may be running its own server on the machine.
- Mint an app token with `actions/create-github-app-token`, `client-id`, `private-key`, `owner: lemonade-sdk`, `repositories: lemonade-testing`.
- Clone `lemonade-sdk/lemonade-testing` into `state/` with that token (`actions/checkout` with `repository`, `token`, `path`, `fetch-depth: 1`, `persist-credentials: true`). Set `git config user.name "Lemonade Bot"` and `user.email "lemonade@amd.com"` in that clone.
- Checkout lemonade itself with `fetch-depth: 0` and `fetch-tags: true` into `checkout/` for repo-manager's git operations.
- Env: `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}` for reading lemonade PRs and issues.
- Run `repo-manager <command> --repo lemonade-sdk/lemonade --checkout checkout --state state`.
- `REPO_MANAGER_VERSION` is the repository variable `vars.REPO_MANAGER_VERSION`, initially `v1.0.0`. Jeremy creates it with `gh variable set REPO_MANAGER_VERSION -b v1.0.0 -R lemonade-sdk/lemonade` before PR 1 merges.

The stx-halo runners are shared with benchmark jobs that require exclusive use of the machine for timing, and each repo-manager job loads a 35B model on the GPU. Put every repo-manager job in the same `max-parallel`-style constraint the benchmark jobs use if their timings start to drift; otherwise leave them unconstrained. `timeout-minutes: 45` for job 1 and `timeout-minutes: 180` for job 2, which may have to review a backlog if job 1 fell behind. A repo-manager change in the model or Lemonade version is a one-line edit to the action's env.

## PR 1: commit reviews on `main` (job 1)

Rewrite `.github/workflows/repo-manager.yml`:

- Trigger: push to `main` only. Remove the `release-v*` trigger; job 2 covers those.
- Concurrency group `repo-manager-main-${{ github.sha }}`, no cancel. Distinct SHAs run in parallel across the pool.
- One job calling the composite action with `command: commit review ${{ github.sha }}`. A push that moves `main` by several commits (a merge-queue batch) should review each: list `${{ github.event.before }}..${{ github.sha }}` and call `commit review` for each SHA.
- Remove the snap dispatch steps from this file; they move in PR 3.
- Remove the `/opt/lemonade-manager` steps.

## PR 2: release artifacts inside the release workflow (job 2)

In `.github/workflows/cpp_server_build_test_release.yml`:

- Add a job `repo-manager-release` that runs when the ref is a `release-v*` branch or a `v*` tag. It calls the composite action with `command: release build --branch <branch> --head ${{ github.sha }}`. For a tag, derive the branch from the tag (`v2026.38.1` is on `release-v2026.38`).
- Concurrency for that job only: group `repo-manager-release-<bucket>`, `cancel-in-progress: false`. The workflow-level concurrency is unchanged.
- Add `repo-manager-release` to the `needs` of the job that creates the GitHub release. Its `if` already checks `!contains(needs.*.result, 'failure')`, so a repo-manager failure fails the release. Confirm the check still evaluates with the new dependency.
- In `.github/actions/generate-release-notes/action.yml`, replace the issue search with `gh api repos/lemonade-sdk/lemonade-testing/contents/releases/<bucket>/notes.md --jq .content | base64 -d`, where `<bucket>` is `v${VERSION%.*}`. Use the API rather than `raw.githubusercontent.com`, whose CDN can serve a minutes-old copy. `lemonade-testing` is public, so the job's `GITHUB_TOKEN` suffices. Fail the step if the file is missing or lacks a `## Headline` section; the release must not publish without notes.

## PR 3: move the snap dispatch

Add the two snap `gh workflow run` steps from the old `repo-manager.yml` to the release workflow as a job triggered on `release-v*` pushes, using `SNAP_RC_DISPATCH_TOKEN` as before. It is unrelated to repo-manager and should not depend on it.

## PR 4: docs

In `docs/dev/release.md`:

- The dashboard is `https://testing.lemonade-server.ai`.
- Release notes are edited at `lemonade-testing/releases/<bucket>/notes.md`, the announcement at `announcement.md`, and the checklist is a dashboard page. A human edit is kept, and the release action reads `notes.md` when it creates the release page. Remove all references to the three GitHub issues.
- The label is `candidate`, not `release-candidate`.
- The checklist tiers are P0 and P1.
- Remove the sentence saying repo-manager runs on every push to a release branch and creates issues; replace it with the job-2 description.

## Done when

- A push to `main` produces a run per SHA that adds `commits/<sha>.json` to `lemonade-testing` and the dashboard shows it.
- `workflow_dispatch` of Create Release Branch on a test branch name (or the next real Wednesday cut) produces a candidate whose release page has Headline and Breaking Changes taken from `notes.md`.
- Deleting `notes.md` for a bucket and rerunning the release job makes the release job fail at the notes step.
- The second repo-manager run on any given machine logs that `lemond` and the model were found in `$CI_CACHE` and did not download. Confirm on all four runners.
- The old `repo-manager.yml` steps that used `/opt/lemonade-manager` are gone. Jeremy retires the machine per `01-human.md`.

# Step 3: Agent actions in `lemonade-sdk/lemonade-testing`

Read `../lemonade-testing.md` and `02-repo-manager.md` first. This repo holds repo-manager's state as files on `main` and deploys the dashboard to `https://testing.lemonade-server.ai`. Lemonade's workflows push here; this repo's own workflow only renders.

Jeremy has already created the repo, installed the app, and configured Pages (see `01-human.md`). Work on `main` directly; there is nothing to break yet.

## Tasks

### A. Layout

Create the directories from the store layout with a `.gitkeep` in each:

```
commits/
prs/
releases/
```

`README.md` explains, in a few paragraphs: what this repo is, that files are written by lemonade's workflows using repo-manager, the layout, that `releases/<bucket>/notes.md` and `announcement.md` are meant to be edited by the release admin in the web editor and that repo-manager will not overwrite a human edit, and a link to the dashboard.

Add `CNAME` at the root containing `testing.lemonade-server.ai` so the Pages deploy keeps the domain.

### B. Dashboard workflow

`.github/workflows/pages.yml`:

- Trigger: push to `main`, plus `workflow_dispatch`.
- Concurrency group `pages`, cancel-in-progress true (rendering is stateless, so cancelling is safe here).
- Permissions: `contents: read`, `pages: write`, `id-token: write`.
- Steps: checkout; `pip install git+https://github.com/lemonade-sdk/repo-manager.git@v1.0.0` (bump the tag when repo-manager releases); `repo-manager site render --state . --out site`; copy `CNAME` into `site/`; `actions/upload-pages-artifact` with `path: site`; `actions/deploy-pages`.

Runs on `ubuntu-latest`. `pi` is not installed.

### C. Guardrails

- Branch protection on `main` stays off: the app token pushes directly.
- `.gitattributes`: mark `*.json` and `*.md` as `text eol=lf`.

## Done when

- Pushing a hand-written `commits/<sha>.json` (copy one from a repo-manager scratch run) triggers the workflow and the dashboard shows it at `https://testing.lemonade-server.ai`.
- The site serves over HTTPS at the custom domain after a deploy.

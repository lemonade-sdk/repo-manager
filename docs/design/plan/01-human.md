# Step 1: Actions for Jeremy

Everything here is account, org, or machine setup that an agent cannot do. Do all of it before step 2 starts, except the last section, which comes after step 4. Nothing here changes behavior on its own.

## 1. Create `lemonade-sdk/lemonade-testing`

1. github.com/organizations/lemonade-sdk/repositories/new
2. Name `lemonade-testing`, public, initialize with a README, default branch `main`.
3. Settings, General, Features: turn off Wikis and Projects. Leave Issues on (the optional per-bucket tracking issue lives here if it is ever wanted).
4. Settings, Actions, General: allow all actions; workflow permissions "Read and write".

The state files live on `main`. The dashboard deploys from a workflow, not from a branch, so no `gh-pages` branch is needed.

## 2. Let lemonade's workflows push to `lemonade-testing`

Reuse the app already used to cut release branches. Its client ID and private key are already lemonade secrets (`RELEASE_BRANCH_APP_CLIENT_ID`, `RELEASE_BRANCH_APP_PRIVATE_KEY`), so no new secrets.

1. github.com/organizations/lemonade-sdk/settings/installations, find the release-branch app, Configure.
2. Repository access: add `lemonade-testing`. Save.
3. Open the app's settings (Developer settings, GitHub Apps, the app), Permissions: confirm `Contents: Read and write`. It already needs this to push branches, so nothing should change. If you do change a permission, the installation shows a pending approval banner that you must accept.

## 3. Runner pool

The jobs run on the four existing Strix Halo runners, label set `[self-hosted, stx-halo, Linux, X64, lemon-prod]`. The workflow installs every tool it needs into the job workspace without root, so there is nothing to install on the machines.

## 4. GitHub Pages and the domain

### Porkbun

Nothing, as long as the domain's nameservers already point at Cloudflare. To confirm: porkbun.com, Domain Management, `lemonade-server.ai`, Nameservers. They should be two `*.ns.cloudflare.com` hosts. If they are, Porkbun's own DNS records are ignored and everything below happens in Cloudflare.

### Cloudflare

1. dash.cloudflare.com, select `lemonade-server.ai`, DNS, Records.
2. Confirm there is no existing record named `testing`.
3. Add record:
   - Type: `CNAME`
   - Name: `testing`
   - Target: `lemonade-sdk.github.io`
   - Proxy status: **DNS only** (grey cloud, not orange). With the proxy on, GitHub cannot issue the certificate and the site fails HTTPS.
   - TTL: Auto
4. Save.

### GitHub: repository Pages settings

github.com/lemonade-sdk/lemonade-testing/settings/pages

Under **Build and deployment**:

- **Source** dropdown: choose `GitHub Actions`. The page then offers workflow templates (Jekyll, Static HTML). Ignore them; step 3 adds the workflow.

Under **Custom domain**:

- Text field: `testing.lemonade-server.ai`
- Click **Save**. GitHub shows "DNS check in progress", then "DNS check successful" once it sees the Cloudflare CNAME. If it says the DNS check failed, wait a few minutes and reload; the CNAME can take a moment to propagate.
- **Enforce HTTPS** checkbox, directly below: greyed out until the certificate is issued (minutes, occasionally up to an hour). Reload the page until it is enabled, then check it.

There is nothing else to set on that page.

### GitHub: organization verified domain

github.com/organizations/lemonade-sdk/settings/pages

Under **Verified domains**:

1. Click **Add a domain**.
2. Text field **Domain**: `lemonade-server.ai`. Click **Add domain**.
3. GitHub shows a TXT record to create, with a **name** like `_github-pages-challenge-lemonade-sdk.lemonade-server.ai` and a random **value**. Leave this page open.
4. In Cloudflare, DNS, Records, **Add record**:
   - Type: `TXT`
   - Name: `_github-pages-challenge-lemonade-sdk` (Cloudflare appends `.lemonade-server.ai`)
   - Content: the value GitHub showed, exactly
   - TTL: Auto
   - Save. TXT records have no proxy toggle.
5. Back on GitHub, click **Verify**. It may take a few minutes before it succeeds.

Once verified, `lemonade-server.ai` and all its subdomains can only be used as Pages domains by repos in this org.

The site returns 404 until step 3's workflow exists.

## 5. Confirm what already exists

- lemonade label `candidate`: present.
- `lemonade-sdk/repo-manager`: public, so the other repos can `pip install` it from git with no token.

## After step 4 lands

1. Stop and unregister the old runner at `/opt/lemonade-manager` (`sudo ./svc.sh stop && sudo ./svc.sh uninstall`, then remove it from lemonade's Settings, Actions, Runners).
2. Delete `/opt/lemonade-manager`.
3. Close the open `Release v* final checklist`, `v* release notes`, and `v* announcement` issues in lemonade, if any are open.
4. Update the link in `docs/dev/release.md` and the Discord pins from `lemonade-server.ai/repo-manager` to `testing.lemonade-server.ai` (the agent in step 4 edits the doc; the Discord side is yours).

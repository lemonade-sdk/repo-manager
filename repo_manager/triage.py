"""PR triage under lemonade's spec-driven development policy.

Every open PR gets an `rfc:` label, and this module produces the five facts a maintainer
needs to apply it: the label, what covers the change (a fix, a charter, or an approved
RFC), whether the body tells the truth about the diff, whether docs and tests are in
order, and who should review. Three Pi runs read the diff and the project's guides; the
labels themselves are derived here, in code, from what those runs report, so the rule that
turns a surface into a label is written once and can be read.

Layout of a stored artifact (`.repo-manager/reviews/prs/pr-N.json`):

    meta        what GitHub says about the PR (number, title, author, head, base, body)
    claim       what the PR body claims: the template checkbox, linked WG / RFC / issues
    facts       tier 1 — surfaces the diff changes, breaking changes, body mismatches, areas
    cover       tier 2 — per surface, what covers it: a charter item, an RFC section, or a
                statement of intent already on the base branch
    quality     tier 3 — docs and tests
    reviewers   derived — code owner, group lead, RFC commenters, area maintainers, blame
    outputs     derived — the five lines of the comment
    explanation derived — the prose behind them, one paragraph per section
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from repo_manager import cli

TRIAGE_VERSION = "pr-triage-2026-09-10"
COMMENT_MARKER = "<!-- repo-manager:pr-triage"

LABELS = ("rfc:required", "rfc:not-required", "rfc:on-roadmap")
SCOPES = ("fix", "working-group", "rfc", "exceeds-working-group", "exceeds-rfc", "no-charter", "needs-rfc")

# The surface vocabulary. Anything but `internal` is a change to scope, surface area, or
# experience under spec-driven-dev.md, and needs cover.
SURFACE_KINDS = {
    "endpoint-new": "a new HTTP route or MCP tool",
    "endpoint-args": "a new or changed request parameter or request field on an existing route, or a response field carrying information the server did not previously compute; a response field, metric series, or telemetry attribute that only exposes state already held (an alias, a version, a routing decision) is internal",
    "endpoint-behavior": "an observable change in what an existing route does: status codes, defaults, results; the same result produced faster or cheaper is internal",
    "cli-command": "a new CLI command or subcommand",
    "cli-flag": "a new or changed flag or option on an existing CLI command",
    "config-key": "a new key in defaults.json or a config file, a new policy schema field, or a new environment variable",
    "config-default": "a changed default value that existing installs will pick up",
    "config-syntax": "new syntax accepted by an existing config value",
    "gui": "any user-visible change in the desktop app or web UI",
    "backend-new": "a new inference backend, a new channel or variant of one, or an existing backend brought to a new platform, OS, or CPU architecture",
    "persisted-format": "a change to on-disk layout, to the format of a file users edit or depend on, to model naming, or anything that migrates existing user data; an added optional key in a cache or registry file the server alone owns is internal",
    "docs-structure": "a documentation page added, removed, or moved, or a nav (mkdocs.yml) change; edits inside an existing page are internal",
    "ci-infra": "a change to CI that is the PR's own purpose or alters shared infrastructure: a new workflow file, a restructured matrix, a new composite action, new shared test fixtures; a job or test that only builds or exercises what this same PR adds is part of that surface, and a test file added, moved, or edited is internal",
    "test-refactor": "a PR whose purpose is to restructure tests or test infrastructure that this PR does not otherwise change; tests added, removed, or edited alongside the code they cover are internal",
    "refactor": "a restructuring of working code whose behavior this PR does not otherwise change; restructuring needed to make the change is internal",
    "security": "a change on a security-relevant path: authentication, allowed origins, TLS, sandboxing, data sent to external services",
    "internal": "everything else: a fix inside existing behavior, a dependency bump, build changes, comments, edits inside existing doc pages, tests for the change, or implementing an existing route or field for one more backend or platform",
}
COVER_KINDS = ("fix", "working-group", "rfc", "none")
# What a changed behavior replaces. `failing` and `ignored` are fixes by the policy's own
# definition (the existing contract was not being honored); `working` and `absent` need cover.
WAS_VOCAB = ("working", "failing", "ignored", "absent")
STATUS_VOCAB = ("ok", "gaps", "n/a")
MIGRATION_VOCAB = ("none", "auto", "manual")

# Which Code Owners row (spec-driven-dev.md) a surface kind belongs to, by a word that row's
# Area cell contains. The handles come from the table, so a reassigned area follows the guide.
CODE_OWNER_KEYWORDS = {
    "cli-command": "cli",
    "cli-flag": "cli",
    "gui": "gui",
    "security": "security",
    "endpoint-new": "endpoint",
    "backend-new": "backend",
    "breaking": "breaking",
}

# "Fixes #123", "Closes: #123", "Fixes [#123](.../issues/123)", "Resolves https://.../issues/123".
FIXES_PATTERN = re.compile(
    r"\b(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)\s*:?\s*(?:\[?#(\d+)\]?|\S*/issues/(\d+))", re.IGNORECASE
)
RFC_PATTERN = re.compile(
    r"(?:rfc\b[^\n#]{0,40}#(\d+)|/discussions/(\d+)|discussion\s*#(\d+))", re.IGNORECASE
)
CHECKBOX = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.*)$")


# --- GitHub reads -----------------------------------------------------------------------


def gh_api(args, check=False):
    return cli.gh_json(args, check=check)


def fetch_pr(repo, number):
    fields = (
        "number,title,state,isDraft,author,body,baseRefName,headRefName,headRefOid,url,"
        "labels,files,mergedAt,mergeCommit,additions,deletions"
    )
    data = cli.gh_cli_json(["pr", "view", str(number), "--repo", repo, "--json", fields])
    if not data:
        raise SystemExit(f"Could not fetch PR #{number} from {repo}.")
    return data


def fetch_diff(repo, number, base_ref, replay_sha=""):
    if replay_sha:
        result = cli.run(
            ["gh", "api", "-H", "Accept: application/vnd.github.diff", f"repos/{repo}/compare/{base_ref}...{replay_sha}"],
            check=False,
        )
        files = gh_api([f"repos/{repo}/compare/{base_ref}...{replay_sha}", "--jq", ".files"]) or []
        files = [{"path": f.get("filename"), "additions": f.get("additions"), "deletions": f.get("deletions")} for f in files]
        return result.stdout or "", files
    result = cli.run(["gh", "pr", "diff", str(number), "--repo", repo], check=False)
    return result.stdout or "", None


def fetch_file(workspace, repo, ref, path):
    """A file at `ref`, cached under the workspace. Returns "" when it does not exist."""
    cache = cli.project_docs_cache_dir(workspace) / cli.safe_repo_name(repo) / ref
    cache.mkdir(parents=True, exist_ok=True)
    file = cache / path.replace("/", "__")
    if file.exists():
        return file.read_text(encoding="utf-8", errors="replace")
    result = cli.run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}?ref={ref}"],
        check=False,
    )
    text = result.stdout if result.returncode == 0 else ""
    file.write_text(text, encoding="utf-8")
    return text


def fetch_tree(workspace, repo, ref):
    cache = cli.project_docs_cache_dir(workspace) / cli.safe_repo_name(repo) / ref
    cache.mkdir(parents=True, exist_ok=True)
    file = cache / "repo-tree.txt"
    if file.exists():
        return file.read_text(encoding="utf-8").splitlines()
    result = cli.run(
        ["gh", "api", f"repos/{repo}/git/trees/{ref}?recursive=1", "--jq", '.tree[] | select(.type == "blob") | .path'],
        check=False,
    )
    paths = (result.stdout or "").splitlines()
    file.write_text("\n".join(paths), encoding="utf-8")
    return paths


def refresh_base_cache(workspace, repo, ref):
    """Drop cached files for `ref` so a branch name never serves last week's guides."""
    cache = cli.project_docs_cache_dir(workspace) / cli.safe_repo_name(repo) / ref
    if cache.exists() and not re.fullmatch(r"[0-9a-f]{40}", ref):
        for file in cache.iterdir():
            if file.is_file():
                file.unlink()


def fetch_rfc(repo, number):
    """An RFC by number: a discussion first, an issue second. Returns None when neither exists."""
    owner, name = repo.split("/", 1)
    query = (
        'query{repository(owner:"%s",name:"%s"){discussion(number:%d){title url body author{login} '
        "labels(first:20){nodes{name}} comments(first:100){nodes{author{login}}}}}}" % (owner, name, number)
    )
    result = cli.run(["gh", "api", "graphql", "-f", f"query={query}"], check=False)
    if result.returncode == 0 and result.stdout.strip():
        try:
            node = json.loads(result.stdout)["data"]["repository"]["discussion"]
        except (KeyError, TypeError, json.JSONDecodeError):
            node = None
        if node:
            return {
                "number": number,
                "kind": "discussion",
                "title": node.get("title", ""),
                "url": node.get("url", ""),
                "author": (node.get("author") or {}).get("login", ""),
                "body": node.get("body", ""),
                "labels": [label["name"] for label in (node.get("labels") or {}).get("nodes", [])],
                "commenters": sorted({(c.get("author") or {}).get("login", "") for c in (node.get("comments") or {}).get("nodes", [])} - {""}),
            }
    issue = cli.gh_cli_json(
        ["issue", "view", str(number), "--repo", repo, "--json", "title,url,body,author,labels,comments"], check=False
    )
    if not issue:
        return None
    return {
        "number": number,
        "kind": "issue",
        "title": issue.get("title", ""),
        "url": issue.get("url", ""),
        "author": (issue.get("author") or {}).get("login", ""),
        "body": issue.get("body", ""),
        "labels": [label["name"] for label in issue.get("labels") or []],
        "commenters": sorted({(c.get("author") or {}).get("login", "") for c in issue.get("comments") or []} - {""}),
    }


def fetch_issue(repo, number):
    issue = cli.gh_cli_json(
        ["issue", "view", str(number), "--repo", repo, "--json", "title,body,author,labels,state"], check=False
    )
    if not issue:
        return None
    return {
        "number": number,
        "title": issue.get("title", ""),
        "body": issue.get("body", ""),
        "author": (issue.get("author") or {}).get("login", ""),
        "labels": [label["name"] for label in issue.get("labels") or []],
        "state": issue.get("state", ""),
    }


# --- The guides -------------------------------------------------------------------------


def parse_code_owners(spec_text):
    """{handle: area text} from the Code Owners table in spec-driven-dev.md."""
    owners = {}
    for line in spec_text.splitlines():
        match = re.match(r"^\|\s*@([A-Za-z0-9-]+)\s*\|([^|]*)\|\s*$", line.strip())
        if match:
            owners[match.group(1)] = match.group(2).strip().replace("`", "")
    return owners


WG_ROW = re.compile(r"^\|\s*(?:\[([^\]]+)\]\(\./([^)]+)\)|([^|\[]+?))\s*\|\s*@([A-Za-z0-9-]+)\s*\|[^|]*\|([^|]*)\|\s*$")


def normalize_wg_name(name):
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def parse_working_groups(readme_text):
    """[{name, file, lead, goal, archived}] from the working-groups README tables."""
    groups, archived = [], False
    for line in readme_text.splitlines():
        if line.startswith("#") and "archived" in line.lower():
            archived = True
        match = WG_ROW.match(line.strip())
        if not match:
            continue
        linked, file, plain, lead, goal = match.groups()
        name = (linked or plain or "").strip().replace("\u2011", "-").replace("\u2010", "-")
        if not name or name.lower().startswith("working group"):
            continue
        groups.append({"name": name, "file": file or "", "lead": lead, "goal": goal.strip(), "archived": archived})
    return groups


def load_guides(workspace, repo, ref):
    """Everything the tiers read from the base branch, fetched once per run."""
    refresh_base_cache(workspace, repo, ref)
    contribute = fetch_file(workspace, repo, ref, "docs/dev/contribute.md")
    spec = fetch_file(workspace, repo, ref, "docs/dev/spec-driven-dev.md")
    wg_readme = fetch_file(workspace, repo, ref, "docs/dev/working-groups/README.md")
    groups = parse_working_groups(wg_readme)
    for group in groups:
        group["charter"] = fetch_file(workspace, repo, ref, f"docs/dev/working-groups/{group['file']}") if group["file"] else ""
    return {
        "ref": ref,
        "contribute": contribute,
        "maintainers": cli.parse_maintainer_table(contribute),
        "spec": spec,
        "code_owners": parse_code_owners(spec),
        "working_groups": groups,
        "documentation": fetch_file(workspace, repo, ref, "docs/dev/documentation.md"),
        "testing": fetch_file(workspace, repo, ref, "docs/dev/testing.md"),
        "tree": fetch_tree(workspace, repo, ref),
    }


def maintainer_terms(maintainers):
    terms = []
    for entry in maintainers.values():
        for area in entry.get("areas", []):
            if area.lower() not in {t.lower() for t in terms}:
                terms.append(area)
    return terms


def find_group(guides, name):
    wanted = normalize_wg_name(name)
    if not wanted:
        return None
    for group in guides["working_groups"]:
        have = normalize_wg_name(group["name"])
        if have == wanted or wanted in have or have in wanted:
            return group
    # "smart router" and "cloud hybrid" are the same group; match on the lead's areas is too
    # loose, so a short alias table covers the names people actually write.
    aliases = {"smart router": "cloud hybrid", "router": "cloud hybrid", "cloud": "cloud hybrid", "app": "gui app", "gui": "gui app"}
    for alias, target in aliases.items():
        if alias in wanted:
            return find_group(guides, target)
    return None


# --- The PR body ------------------------------------------------------------------------


def section_text(body, heading):
    """The text under a `## heading` in the PR template, up to the next heading."""
    lines = body.splitlines()
    out, active = [], False
    for line in lines:
        if line.startswith("#"):
            active = heading.lower() in line.lower()
            continue
        if active:
            out.append(line)
    return "\n".join(out).strip()


def parse_claim(body):
    """What the body asserts, so the tool can check the claim against what it found."""
    body = body or ""
    claim = {"kind": "none", "working_group": "", "rfc": 0, "fixes": [], "breaking_declared": None}
    for line in body.splitlines():
        match = CHECKBOX.match(line)
        if not match:
            continue
        checked, text = match.group(1).strip().lower() == "x", match.group(2)
        lowered = text.lower()
        if "introduces breaking changes" in lowered and "not" not in lowered:
            claim["breaking_declared"] = True if checked else claim["breaking_declared"]
        elif "does not introduce breaking changes" in lowered and checked:
            claim["breaking_declared"] = False
        if not checked:
            continue
        if lowered.startswith("fixes something"):
            claim["kind"] = "fix"
        elif "scope of wg" in lowered or "working group" in lowered:
            claim["kind"] = "working-group"
            name = re.sub(r"<!--.*?-->", "", text.split(":", 1)[1] if ":" in text else "").strip()
            claim["working_group"] = name
        elif "approved rfc" in lowered:
            claim["kind"] = "rfc"
            found = re.search(r"#\s*(\d+)", text)
            claim["rfc"] = int(found.group(1)) if found else 0
    stripped = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    if not claim["rfc"]:
        for match in RFC_PATTERN.finditer(stripped):
            number = next((g for g in match.groups() if g), "")
            if number:
                claim["rfc"] = int(number)
                if claim["kind"] == "none":
                    claim["kind"] = "rfc"
                break
    if not claim["working_group"]:
        found = re.search(r"working[ -]group[^\n]*?\b([A-Z][A-Za-z‑ -]{2,40})", stripped)
        if found and claim["kind"] == "none":
            claim["working_group"] = found.group(1).strip()
            claim["kind"] = "working-group"
    claim["fixes"] = sorted({int(a or b) for a, b in FIXES_PATTERN.findall(stripped)})
    # A body written before the template has no checkbox, but "Fixes #N" is the same claim:
    # this is a fix, and it needs no RFC.
    if claim["kind"] == "none" and claim["fixes"]:
        claim["kind"] = "fix"
    claim["breaking_section"] = section_text(stripped, "Breaking Changes")
    return claim


# --- Mechanical leads from the diff -----------------------------------------------------

ROUTE_PATTERN = re.compile(r"^\+.*(?:\.(?:Post|Get|Put|Delete|Patch)\s*\(\s*\"(/[^\"]+)\"|register_route\s*\(\s*\"(/[^\"]+)\")")
CLI_PATTERN = re.compile(r"^\+.*add_(?:option|flag)\s*\(\s*\"([^\"]+)\"")
SUBCOMMAND_PATTERN = re.compile(r"^\+.*add_subcommand\s*\(\s*\"([^\"]+)\"")
ENV_PATTERN = re.compile(r"^[+-].*getenv\s*\(\s*\"(LEMONADE_[A-Z0-9_]+)\"")
MCP_TOOL_PATTERN = re.compile(r"^\+\s*\{?\s*\"name\"\s*:\s*\"([a-z_]+)\"")
JSON_KEY_PATTERN = re.compile(r"^([+-])\s*\"([A-Za-z0-9_.-]+)\"\s*:")
REMOVED_ASSERT = re.compile(r"^-\s*(?:assert|EXPECT_|ASSERT_|self\.assert|REQUIRE|CHECK)")
JOB_PATTERN = re.compile(r"^\+  ([A-Za-z0-9_-]+):\s*$")


def split_diff(diff_text):
    """{path: [lines]} for each file in a unified diff."""
    files, current = {}, None
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            current = line.split(" b/", 1)[-1].strip() if " b/" in line else None
            if current:
                files[current] = []
        elif current is not None:
            files[current].append(line)
    return files


def git_ref(ref):
    return ref if re.fullmatch(r"[0-9a-f]{40}", ref or "") else f"origin/{ref}"


def git_grep_base(checkout, base_ref, term, pathspecs=()):
    if not checkout or not Path(checkout, ".git").exists():
        return []
    cmd = ["git", "-C", str(checkout), "grep", "-n", "-F", "-e", term, git_ref(base_ref), "--", *pathspecs]
    result = cli.run(cmd, check=False)
    return [line.split(":", 1)[-1] for line in (result.stdout or "").splitlines()[:5]]


def surface_hints(diff_text, changed_paths, checkout, base_ref):
    """Leads for the facts tier: things in the diff that usually mean a surface changed.

    Each is a fact about the text of the diff, not a judgment; the model confirms or
    dismisses them. The point is that a route registration or an `add_option` cannot be
    missed by a reader who skimmed, because it is listed here by line.
    """
    leads = []
    files = split_diff(diff_text)
    for path, lines in files.items():
        for line in lines:
            for pattern, label in (
                (ROUTE_PATTERN, "route registration"),
                (CLI_PATTERN, "CLI option"),
                (SUBCOMMAND_PATTERN, "CLI subcommand"),
            ):
                match = pattern.match(line)
                if match:
                    value = next((g for g in match.groups() if g), "")
                    existing = git_grep_base(checkout, base_ref, f'"{value}"', ("src/*",)) if value else []
                    note = "already on the base branch" if existing else "not found on the base branch, so new"
                    leads.append(f"{label} `{value}` in {path} ({note})")
            match = ENV_PATTERN.match(line)
            if match:
                sign = "added" if line.startswith("+") else "removed"
                leads.append(f"environment variable `{match.group(1)}` {sign} in {path}")
            if REMOVED_ASSERT.match(line) and re.search(r"(^|/)tests?/", path):
                leads.append(f"an existing assertion is removed or changed in {path}")
                break
        if path.endswith("defaults.json") or path.endswith("config.json") or "schema" in path:
            for line in lines:
                match = JSON_KEY_PATTERN.match(line)
                if match:
                    sign, key = match.groups()
                    if sign == "+":
                        existing = git_grep_base(checkout, base_ref, f'"{key}"', ("src/*",))
                        note = "already read by code on the base branch" if existing else "not referenced on the base branch"
                        leads.append(f"key `{key}` added in {path} ({note})")
                    else:
                        leads.append(f"key `{key}` removed or changed in {path}")
        if path.startswith(".github/workflows/"):
            jobs = [JOB_PATTERN.match(line).group(1) for line in lines if JOB_PATTERN.match(line)]
            jobs = [job for job in jobs if job not in ("on", "env", "jobs", "permissions", "concurrency", "defaults")]
            leads.append(f"workflow {path} changed" + (f", possibly new jobs: {', '.join(jobs)}" if jobs else ""))
        if "mcp" in path.lower():
            tools = sorted({m.group(1) for m in (MCP_TOOL_PATTERN.match(line) for line in lines) if m})
            if tools:
                leads.append(f"possible MCP tool names added in {path}: {', '.join(tools)}")
    for path in changed_paths:
        if path.startswith("src/app/") and not path.endswith((".md", ".json")):
            leads.append(f"desktop app file changed: {path}")
        if path.startswith("docs/dev/working-groups/"):
            leads.append(f"working-group charter edited in this PR: {path}")
        if path == "mkdocs.yml" or path == "docs/mkdocs.yml":
            leads.append("docs navigation (mkdocs.yml) changed")
        if path.startswith("docs/") and path.endswith(".md") and path not in files_on_base(checkout, base_ref, [path]):
            leads.append(f"new documentation page: {path}")
        if path.endswith(("backend_versions.json", "benchmark_forks.json")):
            leads.append(f"backend registry changed: {path}")
        if re.search(r"path_utils|paths\.cpp|config_dir|cache_dir|migrat", path):
            leads.append(f"on-disk path or migration code changed: {path}")
        if re.search(r"(origin|cors|auth|tls|ssl|sandbox|allowed_hosts|security)", path, re.IGNORECASE):
            leads.append(f"security-relevant path changed: {path}")
    seen, unique = set(), []
    for lead in leads:
        if lead not in seen:
            seen.add(lead)
            unique.append(lead)
    return unique[:60]


def files_on_base(checkout, base_ref, paths):
    if not checkout or not Path(checkout, ".git").exists():
        return set(paths)
    present = set()
    for path in paths:
        result = cli.run(["git", "-C", str(checkout), "cat-file", "-e", f"{git_ref(base_ref)}:{path}"], check=False)
        if result.returncode == 0:
            present.add(path)
    return present


# --- Context assembly -------------------------------------------------------------------


def checkout_path(workspace):
    return Path(workspace) / cli.CONFIG_DIR / "checkout"


def ensure_checkout(workspace, repo, base_ref):
    checkout = checkout_path(workspace)
    if not Path(checkout, ".git").exists():
        return None
    cli.run(["git", "-C", str(checkout), "fetch", "--quiet", "origin", base_ref], check=False)
    return checkout


def gather_context(workspace, repo, number, replay_sha=""):
    """Everything the three tiers will read, fetched once."""
    meta = fetch_pr(repo, number)
    base_ref = meta.get("baseRefName") or cli.load_config(workspace).get("branch") or "main"
    head_sha = replay_sha or meta.get("headRefOid", "")
    diff, replay_files = fetch_diff(repo, number, base_ref, replay_sha)
    files = replay_files if replay_files is not None else [
        {"path": f.get("path"), "additions": f.get("additions"), "deletions": f.get("deletions")} for f in meta.get("files") or []
    ]
    checkout = ensure_checkout(workspace, repo, base_ref)
    # The guides are always read from the base branch: a PR is judged against today's
    # policy. The *code* it is compared with is the base as the PR saw it — for a merged PR
    # that is the merge commit's first parent, because today's main already contains the
    # PR and would report its every route and key as "already there".
    code_ref = base_ref
    merge_sha = (meta.get("mergeCommit") or {}).get("oid", "")
    if merge_sha and checkout:
        cli.run(["git", "-C", str(checkout), "fetch", "--quiet", "origin", merge_sha], check=False)
        parent = cli.run(["git", "-C", str(checkout), "rev-parse", f"{merge_sha}^1"], check=False)
        if parent.returncode == 0 and parent.stdout.strip():
            code_ref = parent.stdout.strip()
    guides = load_guides(workspace, repo, base_ref)
    if code_ref != base_ref:
        guides["tree"] = fetch_tree(workspace, repo, code_ref)
    claim = parse_claim(meta.get("body", ""))
    rfc = fetch_rfc(repo, claim["rfc"]) if claim["rfc"] else None
    issues = [issue for issue in (fetch_issue(repo, n) for n in claim["fixes"][:4]) if issue]
    group = find_group(guides, claim["working_group"]) if claim["working_group"] else None
    return {
        "repo": repo,
        "number": number,
        "meta": {
            "number": number,
            "title": meta.get("title", ""),
            "author": cli.pr_author_handle(meta),
            "url": meta.get("url", ""),
            "state": meta.get("state", ""),
            "is_draft": bool(meta.get("isDraft")),
            "base_ref": base_ref,
            "code_ref": code_ref,
            "head_sha": head_sha,
            "live_head_sha": meta.get("headRefOid", ""),
            "replay_sha": replay_sha,
            "body": meta.get("body", "") or "",
            "labels": [l["name"] for l in meta.get("labels") or [] if not l["name"].startswith("rfc:")],
            "additions": meta.get("additions"),
            "deletions": meta.get("deletions"),
        },
        "files": files,
        "diff": diff,
        "hints": surface_hints(diff, [f["path"] for f in files], checkout, code_ref),
        "guides": guides,
        "claim": claim,
        "rfc": rfc,
        "issues": issues,
        "group": group,
        "checkout": checkout,
    }


MAX_DIFF_CHARS = int(os.environ.get("REPO_MANAGER_MAX_DIFF_CHARS", "160000"))


def diff_for_prompt(context):
    diff = context["diff"]
    if len(diff) <= MAX_DIFF_CHARS:
        return diff
    return diff[:MAX_DIFF_CHARS] + (
        f"\n\n[Diff truncated at {MAX_DIFF_CHARS} of {len(diff)} characters. The changed-file list above is "
        "complete; fetch a file at the head SHA if you need the rest.]"
    )


def files_block(context):
    return "\n".join(f"+{f.get('additions', '?')} -{f.get('deletions', '?')} {f['path']}" for f in context["files"])


def fetch_instructions(context):
    repo, head = context["repo"], context["meta"]["head_sha"]
    base = context["meta"]["code_ref"]
    return (
        "To read a file as this PR leaves it:\n"
        f'  gh api -H "Accept: application/vnd.github.raw" "repos/{repo}/contents/PATH?ref={head}"\n'
        "To read a file as it is on the base branch (the docs and guides the PR is judged against):\n"
        f'  gh api -H "Accept: application/vnd.github.raw" "repos/{repo}/contents/PATH?ref={base}"\n'
    )


def pr_header(context):
    meta = context["meta"]
    judged = f"Judged against the base as the PR saw it: {meta['code_ref']}\n" if meta["code_ref"] != meta["base_ref"] else ""
    return (
        f"PR #{meta['number']}: {meta['title']}\n"
        f"Author: {meta['author']}\n"
        f"Base branch: {meta['base_ref']}    Head SHA: {meta['head_sha']}\n"
        f"{judged}"
        f"Labels: {', '.join(meta['labels']) or 'none'}\n"
    )


def claim_block(context):
    claim = context["claim"]
    lines = [f"Template checkbox: {claim['kind']}"]
    if claim["working_group"]:
        lines.append(f"Working group named: {claim['working_group']}")
    if claim["rfc"]:
        rfc = context["rfc"]
        if rfc:
            lines.append(f"RFC linked: #{claim['rfc']} ({rfc['kind']}, labels: {', '.join(rfc['labels']) or 'none'}) — {rfc['title']}")
        else:
            lines.append(f"RFC linked: #{claim['rfc']} (could not be fetched)")
    if claim["fixes"]:
        lines.append(f"Closing references: {', '.join(f'#{n}' for n in claim['fixes'])}")
    if claim["breaking_declared"] is True:
        lines.append("Breaking Changes section: the author ticked 'introduces breaking changes'")
    elif claim["breaking_declared"] is False:
        lines.append("Breaking Changes section: the author ticked 'does not introduce breaking changes'")
    else:
        lines.append("Breaking Changes section: no box ticked")
    if claim.get("breaking_section"):
        lines.append("Breaking Changes text:\n" + indent(claim["breaking_section"]))
    return "\n".join(lines)


def indent(text, prefix="    "):
    return "\n".join(prefix + line for line in str(text).splitlines())


def issues_block(context):
    if not context["issues"]:
        return "No linked issues."
    parts = []
    for issue in context["issues"]:
        parts.append(
            f"Issue #{issue['number']} ({issue['state'].lower()}, by {issue['author']}): {issue['title']}\n"
            + indent((issue["body"] or "")[:3000])
        )
    return "\n\n".join(parts)


# --- Prompts ----------------------------------------------------------------------------

# The exact shape each tier writes, restated at the end of its prompt. The skill body says
# the same thing, but a small model follows the last thing it read; on #3488 the cover pass
# wrote `surfaces` as an object three times running while the skill showed an array.
OUTPUT_SHAPES = {
    "facts": """## Output shape (write exactly this shape to the path above)
{"surfaces": [{"kind": "<one of the surface kinds>", "what": "...", "where": "path/to/file", "was": "working|failing|ignored|absent"}],
 "breaking_changes": [{"what": "...", "where": "path", "migration": "none|auto|manual", "disclosed": true}],
 "body_mismatches": [{"claim": "...", "actual": "..."}],
 "areas": ["<terms copied from the maintainer table>"],
 "explanation": "two or three sentences"}
`surfaces` is an array with at least one entry. `breaking_changes`, `body_mismatches`, and `areas` are arrays, empty when there is nothing.""",
    "cover": """## Output shape (write exactly this shape to the path above)
{"cover": {"kind": "fix|working-group|rfc|none", "name": "<group name, #N, or empty>",
           "surfaces": [{"index": 0, "covered": true, "by": "document and sentence, or what was looked for"}],
           "explanation": "two or three sentences"}}
`surfaces` is an array with one entry per non-internal surface index listed above. Nothing else goes in the file.""",
    "quality": """## Output shape (write exactly this shape to the path above)
{"docs": {"status": "ok|gaps|n/a", "gaps": [{"what": "...", "where": "path", "action": "one imperative sentence"}]},
 "tests": {"status": "ok|gaps|n/a", "gaps": [{"what": "...", "where": "path", "action": "one imperative sentence"}]},
 "explanation": "two or three sentences"}
`status` is `gaps` exactly when the list is non-empty.""",
}


def facts_prompt(context, out_path):
    guides = context["guides"]
    kinds = "\n".join(f"- `{k}`: {v}" for k, v in SURFACE_KINDS.items())
    terms = ", ".join(f"`{t}`" for t in maintainer_terms(guides["maintainers"]))
    hints = "\n".join(f"- {h}" for h in context["hints"]) or "- none"
    return f"""{pr_header(context)}
Write the JSON result to: {out_path}

## What the PR body claims
{claim_block(context)}

## PR body
{indent(context['meta']['body'] or '(empty)')}

## Linked issues
{issues_block(context)}

## Linked RFC (part of the PR's description: what it states need not be repeated in the body)
{rfc_block(context)}

## Surface kinds (use exactly these)
{kinds}

## Subject-area terms from the maintainer table (use exactly these for `areas`)
{terms}

## Leads found mechanically in the diff
Each is a fact about the diff text. Confirm or dismiss it; do not skip one.
{hints}

## Changed files (+additions -deletions path)
{files_block(context)}

{fetch_instructions(context)}
## Diff
{diff_for_prompt(context)}
"""


def surfaces_block(facts):
    lines = []
    for index, surface in enumerate(facts.get("surfaces", [])):
        was = f"; was: {surface['was']}" if surface.get("was") else ""
        lines.append(f"{index}. [{surface['kind']}] {surface['what']} ({surface.get('where', '')}{was})")
    return "\n".join(lines) or "(none — the diff is internal only)"


def breaking_block(facts):
    lines = []
    for change in facts.get("breaking_changes", []):
        lines.append(f"- {change['what']} (migration: {change.get('migration', 'none')}; {change.get('where', '')})")
    return "\n".join(lines) or "- none"


def charters_block(context):
    guides, group = context["guides"], context["group"]
    groups = [group] if group else [g for g in guides["working_groups"] if not g.get("archived")]
    parts = []
    for g in groups:
        head = f"### Working group: {g['name']} (lead @{g['lead']})\nGoal: {g['goal']}"
        if g.get("charter"):
            parts.append(head + "\nCharter:\n" + indent(g["charter"]))
        else:
            parts.append(head + "\nNo charter file exists for this group yet.")
    return "\n\n".join(parts)


def rfc_block(context):
    rfc = context["rfc"]
    if not rfc:
        return "No RFC is linked." if not context["claim"]["rfc"] else f"RFC #{context['claim']['rfc']} is linked but could not be fetched."
    return (
        f"### RFC #{rfc['number']} ({rfc['kind']}): {rfc['title']}\n"
        f"Author: {rfc['author']}    Labels: {', '.join(rfc['labels']) or 'none'}\n"
        f"Commenters: {', '.join(rfc['commenters']) or 'none'}\n\n" + indent(rfc["body"])
    )


def cover_prompt(context, facts, out_path):
    docs_tree = "\n".join(p for p in context["guides"]["tree"] if p.startswith("docs/") or p == "README.md")
    return f"""{pr_header(context)}
Write the JSON result to: {out_path}

## What the PR body claims
{claim_block(context)}

## Surfaces this PR changes (from the facts pass; judge each by index)
{surfaces_block(facts)}

## Breaking changes found
{breaking_block(facts)}

## Linked issues
{issues_block(context)}

## Linked RFC
{rfc_block(context)}

## Working groups
{charters_block(context)}

## Documentation tree on the base branch (where a statement of intended behavior would live)
{docs_tree}

{fetch_instructions(context)}
## PR body
{indent(context['meta']['body'] or '(empty)')}

## Summary of the diff (from the facts pass)
{facts.get('explanation', '')}
"""


def guide_sections(text, headings):
    """The sections of a guide whose heading contains one of `headings`, in order."""
    out, keep = [], False
    for line in text.splitlines():
        if line.startswith("#"):
            keep = any(h.lower() in line.lower() for h in headings)
        if keep:
            out.append(line)
    return "\n".join(out)


def quality_prompt(context, facts, out_path):
    guides = context["guides"]
    tree = guides["tree"]
    docs_tree = "\n".join(p for p in tree if p.startswith("docs/") or p == "README.md" or p == "mkdocs.yml")
    test_tree = "\n".join(p for p in tree if re.match(r"^(test/|tests/|\.github/workflows/)", p))
    documentation = guide_sections(guides["documentation"], ["completes the feature", "Required sections", "File and Directory", "What belongs"])
    testing = guide_sections(guides["testing"], ["Principles", "Tests ship", "isn't in CI", "must be able to fail", "Extend existing", "mechanism, not the data", "Where Tests Go", "CI Expectations", "What Reviewers Reject"])
    return f"""{pr_header(context)}
Write the JSON result to: {out_path}

## Surfaces this PR changes (from the facts pass)
{surfaces_block(facts)}

## Changed files (+additions -deletions path)
{files_block(context)}

## documentation.md (the rules that apply)
{documentation}

## testing.md (the rules that apply)
{testing}

## Documentation tree on the base branch
{docs_tree}

## Test and CI tree on the base branch
{test_tree}

{fetch_instructions(context)}
## PR body
{indent(context['meta']['body'] or '(empty)')}

## Diff
{diff_for_prompt(context)}
"""


# --- Validation -------------------------------------------------------------------------


def as_list(value):
    return value if isinstance(value, list) else []


def facts_errors(data, guides):
    errors = []
    terms = {t.lower() for t in maintainer_terms(guides["maintainers"])}
    surfaces = as_list(data.get("surfaces"))
    if not surfaces:
        errors.append("surfaces must list at least one entry; use kind `internal` when nothing user-facing changes.")
    for index, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            errors.append(f"surfaces[{index}] must be an object with kind, what, where.")
            continue
        if surface.get("kind") not in SURFACE_KINDS:
            errors.append(f"surfaces[{index}].kind {surface.get('kind')!r} is not one of the surface kinds.")
        if not str(surface.get("what", "")).strip():
            errors.append(f"surfaces[{index}].what is required.")
        if not str(surface.get("where", "")).strip():
            errors.append(f"surfaces[{index}].where is required: the file (and line if you can) that shows it.")
        if surface.get("was") not in (None, "", *WAS_VOCAB):
            errors.append(f"surfaces[{index}].was must be working, failing, ignored, or absent.")
    for index, change in enumerate(as_list(data.get("breaking_changes"))):
        if not isinstance(change, dict) or not str(change.get("what", "")).strip():
            errors.append(f"breaking_changes[{index}] needs `what`.")
            continue
        if change.get("migration") not in MIGRATION_VOCAB:
            errors.append(f"breaking_changes[{index}].migration must be none, auto, or manual.")
        if not isinstance(change.get("disclosed"), bool):
            errors.append(f"breaking_changes[{index}].disclosed must be true or false.")
    for index, item in enumerate(as_list(data.get("body_mismatches"))):
        if not isinstance(item, dict) or not str(item.get("claim", "")).strip() or not str(item.get("actual", "")).strip():
            errors.append(f"body_mismatches[{index}] needs `claim` and `actual`.")
    bad_areas = [a for a in as_list(data.get("areas")) if str(a).lower() not in terms]
    if bad_areas:
        errors.append(f"areas must be copied from the maintainer-table terms; not listed: {', '.join(map(str, bad_areas))}.")
    if not str(data.get("explanation", "")).strip():
        errors.append("explanation is required: a short paragraph on what the diff changes and how the body compares.")
    return errors


def cover_errors(data, facts):
    errors = []
    cover = data.get("cover") if isinstance(data.get("cover"), dict) else {}
    # {"0": {...}} is the array the model meant; the index is the key.
    if isinstance(cover.get("surfaces"), dict):
        cover["surfaces"] = [
            {"index": int(k), **v} for k, v in cover["surfaces"].items()
            if str(k).isdigit() and isinstance(v, dict)
        ]
    if cover.get("kind") not in COVER_KINDS:
        errors.append("cover.kind must be fix, working-group, rfc, or none.")
    judged = {}
    for index, item in enumerate(as_list(cover.get("surfaces"))):
        if not isinstance(item, dict):
            errors.append(f"cover.surfaces[{index}] must be an object.")
            continue
        if not isinstance(item.get("index"), int):
            errors.append(f"cover.surfaces[{index}].index must be the integer index of a surface.")
            continue
        if not isinstance(item.get("covered"), bool):
            errors.append(f"cover.surfaces[{index}].covered must be true or false.")
        if not str(item.get("by", "")).strip():
            errors.append(f"cover.surfaces[{index}].by is required: what covers it, or why nothing does.")
        judged[item["index"]] = item
    expected = [i for i, s in enumerate(facts.get("surfaces", [])) if s.get("kind") != "internal"]
    missing = [i for i in expected if i not in judged]
    if missing:
        errors.append(f"cover.surfaces must judge every non-internal surface; missing indexes: {missing}.")
    if not str(cover.get("explanation", "")).strip():
        errors.append("cover.explanation is required.")
    return errors


def quality_errors(data):
    errors = []
    for key in ("docs", "tests"):
        block = data.get(key) if isinstance(data.get(key), dict) else {}
        if block.get("status") not in STATUS_VOCAB:
            errors.append(f"{key}.status must be ok, gaps, or n/a.")
        gaps = as_list(block.get("gaps"))
        if block.get("status") == "gaps" and not gaps:
            errors.append(f"{key}.status is gaps but {key}.gaps is empty.")
        if block.get("status") != "gaps" and gaps:
            errors.append(f"{key}.gaps is non-empty but {key}.status is not gaps.")
        for index, gap in enumerate(gaps):
            if not isinstance(gap, dict) or not str(gap.get("what", "")).strip() or not str(gap.get("action", "")).strip():
                errors.append(f"{key}.gaps[{index}] needs `what` and an imperative `action`.")
    if not str(data.get("explanation", "")).strip():
        errors.append("explanation is required.")
    return errors


# --- Running a tier ---------------------------------------------------------------------

TIERS = {
    "facts": ("pr-facts", facts_prompt),
    "cover": ("pr-cover", cover_prompt),
    "quality": ("pr-quality", quality_prompt),
}


def run_tier(workspace, context, tier, facts=None, max_attempts=3):
    skill, build = TIERS[tier]
    number = context["number"]
    pending_dir = cli.artifact_dir(workspace, context["repo"], "prs") / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    feedback = ""
    for attempt in range(1, max_attempts + 1):
        out_path = pending_dir / f"pr-{number}.{tier}.{int(time.time() * 1000)}.json"
        prompt = (build(context, out_path) if tier == "facts" else build(context, facts, out_path))
        prompt += "\n" + OUTPUT_SHAPES[tier] + "\n" + feedback
        try:
            output = cli.run_pi(skill, prompt, workspace, base_ref=context["meta"]["base_ref"], model=cli.load_config(workspace).get("model", ""))
        except SystemExit as exc:
            if exc.code in (130, None) or attempt == max_attempts:
                raise
            print(f"Pi run failed (exit {exc.code}); retrying {tier}.", flush=True)
            continue
        if not out_path.exists():
            cli.write_json_artifact_from_output(out_path, output)
        candidate, raw = None, ""
        if out_path.exists():
            raw = out_path.read_text(encoding="utf-8")
            candidate = cli.extract_json_object(raw)
        errors = [] if candidate is not None else [f"No JSON object was written to {out_path}."]
        if candidate is not None:
            if tier == "facts":
                errors = facts_errors(candidate, context["guides"])
            elif tier == "cover":
                errors = cover_errors(candidate, facts)
            else:
                errors = quality_errors(candidate)
        if not errors:
            if tier == "facts":
                settle_known_keys(candidate, context)
                settle_ci_infra(candidate, context)
                settle_feature_ci(candidate, context)
                settle_docs_structure(candidate, context)
                annotate_known_names(candidate, context)
                settle_default_breaks(candidate, context)
                dedupe_breaks(candidate)
            return candidate
        error_list = "\n".join(f"- {e}" for e in errors)
        print(f"\n{tier} attempt {attempt} failed validation:\n{error_list}\n", flush=True)
        if attempt == max_attempts:
            raise SystemExit(f"PR {tier} failed validation after {max_attempts} attempts:\n{error_list}")
        feedback = "\n\n" + cli.build_release_review_feedback(error_list, raw)
    raise SystemExit(f"PR {tier} produced no usable artifact.")


def settle_ci_infra(facts, context):
    """`ci-infra` means CI changed. If no workflow, action, or shared test helper is in the
    changed-file list, the model has called a test file CI; it is internal."""
    paths = [f["path"] for f in context["files"]]
    if any(p.startswith(".github/") or re.match(r"^tests?/utils/", p) for p in paths):
        return
    for surface in facts.get("surfaces", []):
        if surface.get("kind") == "ci-infra":
            surface["kind"] = "internal"
            surface["what"] += " (no workflow, action, or shared test helper changed)"


IDENTIFIER = re.compile(r"`([A-Za-z_][A-Za-z0-9_.-]{3,})`|\b([a-z][a-z0-9]*_[a-z0-9_]+)\b")


def annotate_known_names(facts, context):
    """For each surface, say which identifiers it names already exist in the base branch's
    source. The cover pass reads this: a field the server already stores and reads (from a
    hand-edited file, from another path) is an existing contract, and honoring it on one
    more path is a fix. Without the grep the model can only guess that."""
    checkout, ref = context["checkout"], context["meta"]["code_ref"]
    if not checkout:
        return
    for surface in facts.get("surfaces", []):
        if surface.get("kind") == "internal":
            continue
        names, seen = [], set()
        for a, b in IDENTIFIER.findall(surface.get("what", "")):
            name = a or b
            if name and name not in seen and len(names) < 6:
                seen.add(name)
                names.append(name)
        known = [n for n in names if git_grep_base(checkout, ref, f'"{n}"', ("src/*",))]
        if known:
            surface["what"] += f" [already present in base-branch source: {', '.join(known)}]"


def settle_default_breaks(facts, context):
    """A changed default is a breaking change by the vocabulary's own definition: every
    existing install picks it up without opting in. When the model listed the surface but
    not the break, the break is added from the surface, disclosed only if the body's
    Breaking Changes box was ticked."""
    breaks = facts.setdefault("breaking_changes", [])

    def words(text):
        return {w for w in re.findall(r"[a-z0-9_.]+", str(text).lower()) if len(w) > 3}

    for surface in facts.get("surfaces", []):
        if surface.get("kind") != "config-default":
            continue
        # Already listed if a break names the same key, or says mostly the same words.
        mine = words(surface.get("what", ""))
        names = [a or b for a, b in IDENTIFIER.findall(surface.get("what", ""))]
        listed = any(
            any(n.lower() in str(b.get("what", "")).lower() for n in names)
            or (mine and len(mine & words(b.get("what", ""))) / len(mine) >= 0.6)
            for b in breaks
        )
        if listed:
            continue
        breaks.append({
            "what": surface["what"],
            "where": surface.get("where", ""),
            "migration": "none",
            "disclosed": context["claim"].get("breaking_declared") is True,
        })


def settle_docs_structure(facts, context):
    """`docs-structure` means a page was added, removed, or moved, or the nav changed. When
    every doc in the changed-file list already exists on the base and the nav is untouched,
    the model has called a content edit structure; it is internal."""
    paths = [f["path"] for f in context["files"]]
    docs = [p for p in paths if p.endswith(".md") or p.endswith("mkdocs.yml")]
    if any(p.endswith("mkdocs.yml") for p in docs):
        return
    existing = files_on_base(context["checkout"], context["meta"]["code_ref"], docs)
    if any(p not in existing for p in docs):
        return
    for surface in facts.get("surfaces", []):
        if surface.get("kind") == "docs-structure":
            surface["kind"] = "internal"
            surface["what"] += " (edits inside existing pages)"


# Every surface a CI job could exist to build or exercise: everything but CI itself and
# the kinds that are restructurings rather than things.
FEATURE_KINDS = set(SURFACE_KINDS) - {"internal", "ci-infra", "test-refactor", "refactor", "docs-structure"}


def settle_feature_ci(facts, context):
    """CI jobs that build or test the feature this same PR adds belong to that feature. CI
    stands as its own surface only when the PR adds a workflow file or touches shared
    actions or helpers, or when CI is all the PR does."""
    kinds = surface_kinds_of(facts)
    if not (kinds & FEATURE_KINDS):
        return
    paths = [f["path"] for f in context["files"]]
    shared = any(p.startswith(".github/actions/") or re.match(r"^tests?/utils/", p) for p in paths)
    workflows = [p for p in paths if p.startswith(".github/workflows/")]
    new_workflow = any(p not in files_on_base(context["checkout"], context["meta"]["code_ref"], workflows) for p in workflows)
    if shared or new_workflow:
        return
    for surface in facts.get("surfaces", []):
        if surface.get("kind") == "ci-infra":
            surface["kind"] = "internal"
            surface["what"] += " (CI for the surface this PR adds)"


BREAK_STOPWORDS = {"from", "with", "that", "this", "instead", "uses", "changes", "change", "e.g.", "than", "into", "when", "which", "only", "now", "e.g"}


def dedupe_breaks(facts):
    """The model sometimes states one break twice in different words; keep the first."""
    def words(text):
        return {w for w in re.findall(r"[a-z0-9_.~]+", str(text).lower()) if len(w) > 3 and w not in BREAK_STOPWORDS}
    kept = []
    for change in facts.get("breaking_changes", []):
        mine = words(change.get("what", ""))
        if mine and any(len(mine & words(k.get("what", ""))) / len(mine) >= 0.5 for k in kept):
            continue
        kept.append(change)
    facts["breaking_changes"] = kept


def settle_known_keys(facts, context):
    """A `config-key` surface whose every named key the base branch's code already reads is
    not a new key: writing it into defaults.json at its current value only makes it visible.
    The leads carry that grep; this applies it when the model did not."""
    known = set(re.findall(r"key `([^`]+)` added in [^ ]+ \(already read", "\n".join(context["hints"])))
    unknown = set(re.findall(r"key `([^`]+)` added in [^ ]+ \(not referenced", "\n".join(context["hints"])))
    if not known:
        return
    for surface in facts.get("surfaces", []):
        if surface.get("kind") != "config-key":
            continue
        named_known = [k for k in known if k in surface.get("what", "")]
        named_unknown = [k for k in unknown if k in surface.get("what", "")]
        if named_known and not named_unknown:
            surface["kind"] = "internal"
            surface["what"] += f" (already read by code on the base branch: {', '.join(named_known)})"


# --- Derivation -------------------------------------------------------------------------


QUOTED = re.compile(r"[\"'\u2018\u2019\u201c\u201d]([^\"'\u2018\u2019\u201c\u201d]{12,})[\"'\u2018\u2019\u201c\u201d]")


def squash(text):
    return re.sub(r"\s+", " ", str(text).lower()).strip()


def settle_charter_quotes(cover, group):
    """A charter covers a surface only through a roadmap or scope *item*, which in every
    charter is a bullet line. A quote drawn from a heading or a goal paragraph ("improve
    test coverage while reducing delays") is the model reaching for the broadest sentence
    available; the surface stays uncovered and the note says why."""
    if not group or not group.get("charter"):
        return
    bullets = [squash(line.lstrip("-*[] x")) for line in group["charter"].splitlines() if line.lstrip().startswith(("-", "*"))]
    for item in cover.get("surfaces", []):
        if not item.get("covered"):
            continue
        by = str(item.get("by", ""))
        if not by.lower().startswith(("charter", "roadmap", "scope")) and "charter" not in by.lower():
            continue
        quotes = [squash(q.lstrip("-*[] x")) for q in QUOTED.findall(by)]
        if not quotes:
            item["covered"] = False
            item["by"] += " [no charter item quoted]"
        elif not any(q in b for q in quotes for b in bullets):
            item["covered"] = False
            item["by"] += " [the quoted text is a heading or goal paragraph, not a roadmap item]"


def derive_scope(context, facts, cover):
    """The scope line, from what the tiers found. Returns (scope, name, uncovered, notes)."""
    surfaces = facts.get("surfaces", [])
    kind = cover["cover"].get("kind", "none")
    name = str(cover["cover"].get("name", "") or "")
    if kind == "working-group":
        settle_charter_quotes(cover["cover"], context["group"] or find_group(context["guides"], name))
    judged = {item["index"]: item for item in cover["cover"].get("surfaces", [])}
    external = [i for i, s in enumerate(surfaces) if s.get("kind") != "internal"]
    migrating = [c for c in facts.get("breaking_changes", []) if c.get("migration") in ("auto", "manual")]
    breaking = facts.get("breaking_changes", [])
    notes = []

    # The base already failed or ignored what this surface changes: the existing contract is
    # the intent, and honoring it is a fix whatever the cover pass concluded.
    for i in external:
        if surfaces[i].get("was") in ("failing", "ignored"):
            judged[i] = {"index": i, "covered": True, "by": f"fix: the base branch already {surfaces[i]['was']} this (existing contract)"}
        # The converse: something that was not there before is new, and no doc describing a
        # neighboring feature makes it a fix. Only a charter or an RFC can cover it.
        elif surfaces[i].get("was") == "absent" and kind in ("fix", "none") and judged.get(i, {}).get("covered"):
            judged[i] = {**judged[i], "covered": False, "by": judged[i]["by"] + " [a new surface is not a fix; only a charter or RFC covers it]"}
    uncovered = [i for i in external if not judged.get(i, {}).get("covered")]
    # A cover of kind X only counts for surfaces the model said were covered *by* X or by a
    # fix; a charter cannot cover a surface the model attributed to an unlinked RFC.
    group = context["group"] or (find_group(context["guides"], name) if kind == "working-group" else None)
    rfc = context["rfc"]
    rfc_approved = bool(rfc and "rfc:on-roadmap" in rfc.get("labels", []))

    if not external and not breaking:
        return "fix", "", [], notes
    if migrating and kind != "rfc":
        notes.append("a change that migrates existing installs needs an RFC whatever else covers it")
        uncovered = sorted(set(uncovered) | {i for i in external if surfaces[i].get("kind") in ("persisted-format", "config-default")})
    if kind == "working-group":
        if group is None:
            return "needs-rfc", name, uncovered or external, notes + [f"no working group named {name!r} exists in the table"]
        if not group.get("charter"):
            return "no-charter", group["name"], external, notes
        if uncovered or (migrating):
            return "exceeds-working-group", group["name"], uncovered or external, notes
        return "working-group", group["name"], [], notes
    if kind == "rfc":
        label = f"#{rfc['number']}" if rfc else name
        if not rfc:
            return "needs-rfc", label, external, notes + ["the linked RFC could not be found"]
        if uncovered:
            return "exceeds-rfc", label, uncovered, notes
        if not rfc_approved:
            notes.append(f"RFC {label} is labeled {', '.join(rfc.get('labels')) or 'nothing'}, not rfc:on-roadmap")
        return "rfc", label, [], notes
    if kind in ("fix", "none"):
        if uncovered or migrating:
            return "needs-rfc", "", uncovered or external, notes
        return "fix", "", [], notes
    return "needs-rfc", "", uncovered or external, notes


def derive_label(scope, context):
    if scope in ("fix", "working-group"):
        return "rfc:not-required"
    if scope == "rfc":
        rfc = context["rfc"]
        return "rfc:on-roadmap" if rfc and "rfc:on-roadmap" in rfc.get("labels", []) else "rfc:required"
    return "rfc:required"


def derive_body_match(context, facts, scope):
    """(yes/no, reasons). The body is wrong if it misdescribes the diff, hides a break,
    bundles work it does not mention, or claims a cover the diff does not have."""
    reasons = [f"{brief(m['claim'], 120)} — actually: {brief(m['actual'], 160)}" for m in facts.get("body_mismatches", [])]
    for change in facts.get("breaking_changes", []):
        if not change.get("disclosed"):
            reasons.append(f"undisclosed breaking change: {change['what']}")
    claim = context["claim"]["kind"]
    # A fix claim is wrong only where it was load-bearing: a PR that a charter covers may
    # also close an issue, and "Fixes #N" on it is a link, not a false claim.
    if claim == "fix" and scope in ("needs-rfc", "exceeds-working-group", "exceeds-rfc", "no-charter"):
        fixes = context["claim"].get("fixes") or []
        how = f"presents this as a fix ({', '.join(f'#{n}' for n in fixes)})" if fixes else "ticks 'fixes something'"
        reasons.append(f"the body {how} but the diff changes scope, surface area, or experience")
    if claim == "working-group" and scope in ("needs-rfc",) and not context["group"]:
        reasons.append(f"the body names a working group ({context['claim']['working_group']}) that is not in the table")
    if claim == "rfc" and not context["rfc"]:
        reasons.append(f"the body links RFC #{context['claim']['rfc']}, which could not be found")
    return ("no" if reasons else "yes"), reasons


def surface_kinds_of(facts):
    return {s.get("kind") for s in facts.get("surfaces", [])}


def code_owners_for(context, facts):
    """Every Code Owners row this PR's surfaces land in, as [(handle, area)]."""
    owners = context["guides"]["code_owners"]
    wanted = {CODE_OWNER_KEYWORDS[k] for k in surface_kinds_of(facts) if k in CODE_OWNER_KEYWORDS}
    if facts.get("breaking_changes"):
        wanted.add(CODE_OWNER_KEYWORDS["breaking"])
    return [(handle, area) for handle, area in owners.items() if any(word in area.lower() for word in wanted)]


def blame_authors(workspace, context, facts=None):
    """Who wrote the code this PR acts on, ranked. Two passes: the lines the hunks touch,
    and the identifiers the surfaces name, grepped across the source tree. The second is
    what finds the person who built the feature a PR extends (the sd-cpp image editing an
    image-edit stub for another backend mirrors), whom the hunks alone never reach."""
    script = cli.repo_root() / "scripts" / "pr-code-authors.sh"
    if not script.exists() or not context["checkout"]:
        return []
    env = dict(os.environ)
    env["REPO_MANAGER_CACHE_DIR"] = str(cli.project_docs_cache_dir(workspace))
    env["REPO_MANAGER_CHECKOUT"] = str(context["checkout"])
    env["REPO_MANAGER_BASE_REF"] = context["meta"]["base_ref"]
    terms, seen = [], set()
    for surface in (facts or {}).get("surfaces", []):
        for a, b in IDENTIFIER.findall(surface.get("what", "")):
            name = a or b
            if name and name not in seen and len(terms) < 6:
                seen.add(name)
                terms.append(name)
    # The identifiers the diff itself leans on: snake_case names repeated in added lines
    # (`image_edits`, `resolve_auto_ctx_size`). The surfaces describe a change in words; the
    # code names the function, and the function is what blame can find an author for.
    counts = {}
    for line in context["diff"].splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            for name in re.findall(r"\b[a-z][a-z0-9]*_[a-z0-9_]{4,}\b", line):
                counts[name] = counts.get(name, 0) + 1
    for name, count in sorted(counts.items(), key=lambda t: -t[1]):
        if count >= 2 and name not in seen and len(terms) < 8:
            seen.add(name)
            terms.append(name)
    scores = {}
    for args in ([], terms) if terms else ([],):
        result = subprocess.run(
            ["bash", str(script), context["repo"], str(context["number"]), *args],
            cwd=workspace, env=env, capture_output=True, text=True,
        )
        for line in (result.stdout or "").splitlines():
            match = re.match(r"^- @([A-Za-z0-9-]+) — relevance (\d+)", line)
            if match and match.group(1) != "unknown":
                scores[match.group(1)] = scores.get(match.group(1), 0) + int(match.group(2))
    return sorted(scores.items(), key=lambda t: -t[1])


def suggest_reviewers(workspace, context, facts, scope, scope_name):
    """Two or three reviewers, never the author, each with the reason they are named.

    Order of evidence: the code owner the guide requires for this surface, the lead of the
    working group the change sits in, maintainers who took part in the linked RFC, then
    maintainers whose subject areas the diff touches, then whoever wrote the code.
    """
    author = context["meta"]["author"].lstrip("@").lower()
    maintainers = context["guides"]["maintainers"]
    candidates = []

    def add(handle, basis, detail):
        handle = str(handle).lstrip("@")
        if not handle or handle.lower() == author or handle.lower() in {c["handle"].lower() for c in candidates}:
            return
        candidates.append({"handle": handle, "basis": basis, "detail": detail, "in_maintainer_table": handle.lower() in maintainers})

    for owner, area in code_owners_for(context, facts):
        add(owner, "code-owner", area)
    group = context["group"] or (find_group(context["guides"], scope_name) if scope in ("working-group", "exceeds-working-group", "no-charter") else None)
    if group:
        add(group["lead"], "wg-lead", group["name"])
    rfc = context["rfc"]
    if rfc:
        for login in rfc.get("commenters", []):
            if login.lower() in maintainers:
                add(maintainers[login.lower()].get("handle", login), "rfc-reviewer", f"commented on RFC #{rfc['number']}")
    areas = [str(a).lower() for a in facts.get("areas", [])]
    scored = []
    for login, entry in maintainers.items():
        owned = [a.lower() for a in entry.get("areas", [])]
        # "smart router" and "smart router and orchestration" are the same area written by
        # two maintainers, so a multi-word term that contains the other matches. A single
        # word matches only itself: `cli` is not `new cli commands`.
        hit = sorted({
            o for a in areas for o in owned
            if a == o or (" " in a and a in o) or (" " in o and o in a and " " in a)
        })
        if hit:
            scored.append((len(hit), login, hit))
    # Whoever wrote most of the code this PR touches comes before the area maintainers: the
    # person who built the feature is the reviewer the maintainer would ask for first, and
    # a subject-area term is a weaker match than a blame line.
    blamed = blame_authors(workspace, context, facts)
    if blamed and blamed[0][1] >= 10:
        add(blamed[0][0], "code-author", f"wrote code this PR touches (blame relevance {blamed[0][1]})")
    for _, login, hit in sorted(scored, key=lambda t: (-t[0], t[1])):
        add(maintainers[login].get("handle", login), "area", ", ".join(hit))
    for login, relevance in blamed:
        add(login, "code-author", f"wrote code this PR touches (blame relevance {relevance})")
    # The slate: the required people first, then fill to three from the strongest evidence.
    required = [c for c in candidates if c["basis"] in ("code-owner", "wg-lead")]
    rest = [c for c in candidates if c not in required]
    slate = (required + rest)[:3]
    if len(slate) < 2 and len(candidates) > len(slate):
        slate = candidates[:2]
    for item in slate:
        item["handle"] = "@" + item["handle"]
    return slate


def settle_rfc_disclosure(facts, cover, context):
    """A breaking change the linked RFC describes is disclosed: the RFC is part of the PR's
    description once the body links it, and the body need not repeat it. A break is taken
    as RFC-described when the cover pass matched an RFC-covered surface that says mostly
    the same thing."""
    rfc = context.get("rfc")
    if not rfc or cover.get("kind") != "rfc":
        return
    def words(text):
        return {w for w in re.findall(r"[a-z0-9_.]+", str(text).lower()) if len(w) > 3}
    surfaces = facts.get("surfaces", [])
    covered = [surfaces[i["index"]] for i in cover.get("surfaces", []) if i.get("covered") and isinstance(i.get("index"), int) and i["index"] < len(surfaces)]
    for change in facts.get("breaking_changes", []):
        if change.get("disclosed"):
            continue
        mine = words(change.get("what", ""))
        if mine and any(len(mine & words(s_["what"])) / len(mine) >= 0.5 for s_ in covered):
            change["disclosed"] = True
            change["disclosed_by"] = f"RFC #{rfc['number']}"


def assemble(workspace, context, facts, cover, quality, started):
    scope, scope_name, uncovered, notes = derive_scope(context, facts, cover)
    cover = cover["cover"]
    settle_rfc_disclosure(facts, cover, context)
    label = derive_label(scope, context)
    body_match, body_reasons = derive_body_match(context, facts, scope)
    docs_tests = "gaps" if quality["docs"].get("status") == "gaps" or quality["tests"].get("status") == "gaps" else "ok"
    reviewers = suggest_reviewers(workspace, context, facts, scope, scope_name)
    surfaces = facts.get("surfaces", [])
    scope_display = scope + (f" ({scope_name})" if scope_name else "")
    outputs = {
        "label": label,
        "scope": scope,
        "scope_name": scope_name,
        "scope_display": scope_display,
        "body_matches_diff": body_match,
        "docs_and_tests": docs_tests,
        "suggested_reviewers": [r["handle"] for r in reviewers],
    }
    explanation = {
        "diff": facts.get("explanation", ""),
        "scope": scope_paragraph(context, facts, cover, scope, scope_name, uncovered, notes, label),
        "body": body_paragraph(context, body_match, body_reasons),
        "quality": quality_paragraph(quality),
        "reviewers": reviewers_paragraph(reviewers),
    }
    return {
        "triage_version": TRIAGE_VERSION,
        "repo": context["repo"],
        "pr_number": context["number"],
        "head_sha": context["meta"]["head_sha"],
        "title": context["meta"]["title"],
        "author": context["meta"]["author"],
        "base_ref": context["meta"]["base_ref"],
        "replay_sha": context["meta"]["replay_sha"],
        "claim": context["claim"],
        "facts": facts,
        "cover": cover,
        "quality": quality,
        "reviewers": reviewers,
        "uncovered_surfaces": [surfaces[i] for i in uncovered if i < len(surfaces)],
        "outputs": outputs,
        "explanation": explanation,
        "generation_seconds": round(time.monotonic() - started, 1),
        "reviewed_at": cli.now_iso(),
    }


def brief(text, limit=120):
    """The first clause of a description: up to a semicolon or `limit`, cut on a word, with
    the tool's own bracketed notes removed."""
    text = re.sub(r"\s*\[[^\]]*\]", "", str(text)).strip()
    cut = min([i for i in (text.find(";"), limit) if i > 0] or [len(text)])
    if cut < len(text):
        cut = text.rfind(" ", 0, cut + 1) or cut
        return text[:cut].rstrip(" ,.:;—-") + "…"
    return text.rstrip(" .")


def scope_paragraph(context, facts, cover, scope, name, uncovered, notes, label):
    surfaces = facts.get("surfaces", [])
    parts = [cover.get("explanation", "").strip()]
    if uncovered:
        listed = "; ".join(f"{brief(surfaces[i]['what'])} ({surfaces[i]['kind']})" for i in uncovered if i < len(surfaces))
        parts.append(f"Not covered: {listed}.")
    rfc = context["rfc"]
    if scope == "rfc" and rfc and label == "rfc:required":
        parts.append(f"RFC #{rfc['number']} is labeled {', '.join(rfc['labels']) or 'nothing'}; the PR stays a draft until it is rfc:on-roadmap.")
    if scope == "no-charter":
        parts.append(f"The {name} working group has no charter under docs/dev/working-groups, so its scope cannot be checked; the label is rfc:required until a charter is committed.")
    for note in notes:
        parts.append(note[0].upper() + note[1:] + ".")
    return " ".join(p for p in parts if p)


def body_paragraph(context, body_match, reasons):
    if body_match == "yes":
        return "The title, body, and template claims describe what the diff does."
    return "The body does not match the diff: " + " ".join(r.rstrip(".") + "." for r in reasons)


def quality_paragraph(quality):
    parts = [quality.get("explanation", "").strip()]
    for key, label in (("docs", "Docs"), ("tests", "Tests")):
        for gap in quality[key].get("gaps", []):
            where = gap.get("where", "")
            suffix = f" ({where})" if where and where not in gap["action"] else ""
            parts.append(f"{label} to do: {gap['action'].rstrip('.')}{suffix}.")
    return " ".join(p for p in parts if p)


def reviewers_paragraph(reviewers):
    if not reviewers:
        return "No reviewer could be named: no code owner, group lead, area maintainer, or code author matched."
    names = {
        "code-owner": "code owner for {detail}",
        "wg-lead": "leads the {detail} working group",
        "rfc-reviewer": "{detail}",
        "area": "maintains {detail}",
        "code-author": "{detail}",
    }
    return " ".join(f"{r['handle'].lstrip('@')}: {names[r['basis']].format(detail=r['detail'])}." for r in reviewers)


# --- Storage ----------------------------------------------------------------------------


def artifact_path(workspace, repo, number):
    return cli.artifact_dir(workspace, repo, "prs") / f"pr-{number}.json"


def connect(workspace):
    """The dashboard's connection, which owns the PR tables' schema."""
    from repo_manager.web import connect as web_connect

    return web_connect(cli.db_path(workspace))


def store(workspace, data):
    path = artifact_path(workspace, data["repo"], data["pr_number"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    outputs = data["outputs"]
    with connect(workspace) as conn:
        conn.execute(
            """
            INSERT INTO pr_reviews
            (repo, pr_number, head_sha, pr_title, author, label, scope, body_matches_diff, docs_and_tests,
             suggested_reviewers, raw_output, json_path, reviewed_at, rubric_version, generation_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr_number, rubric_version) DO UPDATE SET
              head_sha=excluded.head_sha, pr_title=excluded.pr_title, author=excluded.author,
              label=excluded.label, scope=excluded.scope, body_matches_diff=excluded.body_matches_diff,
              docs_and_tests=excluded.docs_and_tests, suggested_reviewers=excluded.suggested_reviewers,
              raw_output=excluded.raw_output, json_path=excluded.json_path, reviewed_at=excluded.reviewed_at,
              generation_seconds=excluded.generation_seconds
            """,
            (
                data["repo"], data["pr_number"], data["head_sha"], data["title"], data["author"],
                outputs["label"], outputs["scope_display"], outputs["body_matches_diff"], outputs["docs_and_tests"],
                json.dumps(outputs["suggested_reviewers"]), json.dumps(data), str(path), data["reviewed_at"],
                TRIAGE_VERSION, data["generation_seconds"],
            ),
        )
    return path


def latest(workspace, repo, number):
    with connect(workspace) as conn:
        row = conn.execute(
            "SELECT * FROM pr_reviews WHERE repo=? AND pr_number=? AND rubric_version=?",
            (repo, number, TRIAGE_VERSION),
        ).fetchone()
    return dict(row) if row else None


def data_from_row(row):
    try:
        return json.loads(row["raw_output"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return None


def rows(workspace):
    with connect(workspace) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pr_reviews WHERE rubric_version=? ORDER BY reviewed_at, pr_number", (TRIAGE_VERSION,)
        )]


# --- The whole thing --------------------------------------------------------------------


def triage(workspace, repo, number, replay_sha="", save=True):
    started = time.monotonic()
    context = gather_context(workspace, repo, number, replay_sha)
    facts = run_tier(workspace, context, "facts")
    cover = run_tier(workspace, context, "cover", facts)
    quality = run_tier(workspace, context, "quality", facts)
    data = assemble(workspace, context, facts, cover, quality, started)
    if save:
        store(workspace, data)
        from repo_manager.web import sync_pr_states

        sync_pr_states(workspace, repo, numbers=[number])
    print(summary_line(data), flush=True)
    return data


def summary_line(data):
    o = data["outputs"]
    return (
        f"PR #{data['pr_number']}: {o['label']}, scope {o['scope_display']}, body matches diff {o['body_matches_diff']}, "
        f"docs and tests {o['docs_and_tests']}, reviewers {', '.join(o['suggested_reviewers']) or 'none'}"
    )


# --- Rendering --------------------------------------------------------------------------


def concerns(data):
    """What a reviewer has to worry about, and nothing else: one bullet per problem.

    The fold used to narrate the PR — what the diff does, what the body gets right, which
    docs pages were checked and found fine. None of that helps a reviewer decide where to
    spend their time, so none of it is here. A clean PR gets one line saying so.
    """
    o, facts, cover = data["outputs"], dict(data.get("facts") or {}), data.get("cover") or {}
    facts["breaking_changes"] = list(facts.get("breaking_changes", []))
    dedupe_breaks(facts)
    surfaces = facts.get("surfaces", [])
    judged = {item.get("index"): item for item in cover.get("surfaces", [])}
    items = []
    scope, name = o["scope"], o.get("scope_name", "")
    if scope in ("needs-rfc", "exceeds-working-group", "exceeds-rfc", "no-charter"):
        head = {
            "needs-rfc": "Needs an RFC. Nothing on the base branch, no charter, and no RFC covers:",
            "exceeds-working-group": f"Needs an RFC. The {name} charter does not cover:",
            "exceeds-rfc": f"Needs the RFC updated. RFC {name} does not cover:",
            "no-charter": f"Needs an RFC. The {name} working group has no charter yet, so nothing covers:",
        }[scope]
        uncovered = data.get("uncovered_surfaces") or []
        items.append(head if uncovered else head.split(".")[0] + ".")
        for surface in uncovered:
            index = next((i for i, s_ in enumerate(surfaces) if s_ is surface or s_ == surface), None)
            why = judged.get(index, {}).get("by", "")
            items.append(f"  - **{brief(surface['what'])}** ({surface['kind']})" + (f" — {brief(why, 160)}" if why else ""))
    if scope == "rfc" and o["label"] == "rfc:required":
        items.append(f"RFC {name} is linked but not yet `rfc:on-roadmap`; the PR waits for its approval.")
    for change in facts.get("breaking_changes", []):
        disclosed = ("disclosed in " + change["disclosed_by"]) if change.get("disclosed_by") else ("disclosed" if change.get("disclosed") else "**not disclosed in the body or the linked RFC**")
        migration = f", {change['migration']} migration" if change.get("migration") in ("auto", "manual") else ""
        items.append(f"Breaking change ({disclosed}{migration}): {brief(change['what'], 160)}" + (f" — {change['where']}" if change.get("where") else ""))
    for m in facts.get("body_mismatches", []):
        items.append(f"Body says \"{brief(m['claim'], 110)}\" but the diff shows: {brief(m['actual'], 160)}")
    for reason in derive_body_reasons(data):
        items.append(reason)
    quality = data.get("quality") or {}
    for key, label in (("docs", "Docs"), ("tests", "Tests")):
        for gap in (quality.get(key) or {}).get("gaps", []):
            where = gap.get("where", "")
            suffix = f" ({where})" if where and where not in gap["action"] else ""
            items.append(f"{label}: {gap['action'].rstrip('.')}{suffix}.")
    return items


def derive_body_reasons(data):
    """The claim-versus-scope reasons the body line carries beyond the model's mismatches."""
    reasons = []
    claim = (data.get("claim") or {}).get("kind")
    scope = data["outputs"]["scope"]
    if claim == "fix" and scope in ("needs-rfc", "exceeds-working-group", "exceeds-rfc", "no-charter"):
        fixes = (data.get("claim") or {}).get("fixes") or []
        how = f"presents this as a fix ({', '.join(f'#{n}' for n in fixes)})" if fixes else "ticks 'fixes something'"
        reasons.append(f"The body {how}, but the diff changes scope, surface area, or experience.")
    return reasons


def render_comment(data, head_sha=""):
    o = data["outputs"]
    reviewers = ", ".join(h.lstrip("@") for h in o["suggested_reviewers"]) or "none found"
    items = concerns(data)
    lines = [
        f"{COMMENT_MARKER} {head_sha or data.get('head_sha', '')} -->",
        f"**label:** `{o['label']}`",
        f"**scope:** `{o['scope_display']}`",
        f"**body matches diff:** `{o['body_matches_diff']}`",
        f"**docs and tests:** `{o['docs_and_tests']}`",
        f"**suggested reviewers:** {reviewers}",
        "",
        "<details><summary>Explanation</summary>",
        "",
    ]
    lines += [item if item.startswith("  - ") else f"- {item}" for item in items] or ["- Nothing to flag."]
    lines += ["", f"**Reviewers.** {data.get('explanation', {}).get('reviewers', '')}".rstrip(), "", "</details>", "", "_AI-assisted triage. A maintainer applies the label._"]
    return "\n".join(lines)


def print_review(data):
    print(render_comment(data))


# --- Acting on GitHub -------------------------------------------------------------------

RFC_REQUEST_MARKER = "<!-- repo-manager:rfc-request -->"
RFC_REQUEST_MESSAGE = (
    "Thanks for your PR! Please be aware that PRs that change Lemonade's scope, surface area, "
    "or user/dev experience need an approved request for comment (RFC) discussion before they "
    "can be reviewed. You can learn about the process "
    "[here](https://github.com/lemonade-sdk/lemonade/blob/main/docs/dev/contribute.md). "
    "If you believe this assessment was made in error, please contact a maintainer on the "
    "#dev channel of the Lemonade Discord."
)


def apply_label(workspace, repo, number, label="", dry_run=False):
    """Act on a triage: put the `rfc:` label on the PR, and when it is `rfc:required`, mark
    the PR as a draft and post the standard request for an RFC.

    This is the one code path for that act. The dashboard button calls it, the CLI calls
    it, and the GitHub Action that will run the triage on every new PR calls it, so the
    three cannot drift. Each step is idempotent: a label already present is not re-added,
    a draft is not re-drafted, and the message is posted once, found again by its marker.
    """
    if not label:
        row = latest(workspace, repo, number)
        data = data_from_row(row) if row else None
        if not data:
            return {"ok": False, "error": f"No stored triage for PR #{number}. Run `repo-manager review-pr {number}` first, or pass --label."}
        label = data["outputs"]["label"]
    if label not in LABELS:
        return {"ok": False, "error": f"{label!r} is not one of {', '.join(LABELS)}."}
    meta = cli.gh_cli_json(["pr", "view", str(number), "--repo", repo, "--json", "labels,isDraft,state"])
    if not meta:
        return {"ok": False, "error": f"Could not fetch PR #{number} from {repo}."}
    present = {item["name"] for item in meta.get("labels") or []}
    actions, warnings = [], []
    if meta.get("state", "").upper() != "OPEN":
        warnings.append(f"PR #{number} is {meta.get('state', '').lower()}, not open.")
    to_remove = sorted(present & (set(LABELS) - {label}))
    if label not in present or to_remove:
        args = ["pr", "edit", str(number), "--repo", repo]
        if label not in present:
            args += ["--add-label", label]
        if to_remove:
            args += ["--remove-label", ",".join(to_remove)]
        actions.append({"step": "label", "add": label if label not in present else "", "remove": to_remove, "cmd": args})
    if label == "rfc:required":
        if not meta.get("isDraft"):
            actions.append({"step": "draft", "cmd": ["pr", "ready", str(number), "--repo", repo, "--undo"]})
        if not find_marked_comment(repo, number, RFC_REQUEST_MARKER):
            actions.append({"step": "message", "cmd": [
                "api", "--method", "POST", f"repos/{repo}/issues/{number}/comments",
                "-f", f"body={RFC_REQUEST_MARKER}\n{RFC_REQUEST_MESSAGE}",
            ]})
    if dry_run:
        return {"ok": True, "dry_run": True, "label": label, "actions": actions, "warnings": warnings}
    done, failed = [], []
    for action in actions:
        result = cli.run(["gh", *action["cmd"]], check=False)
        if result.returncode == 0:
            done.append(action["step"])
        else:
            failed.append({"step": action["step"], "error": (result.stderr or "").strip()[:300]})
    if done:
        from repo_manager.web import sync_pr_states

        sync_pr_states(workspace, repo, numbers=[number])
    return {"ok": not failed, "label": label, "done": done, "failed": failed, "warnings": warnings,
            "error": "; ".join(f"{f['step']}: {f['error']}" for f in failed) if failed else ""}


def find_marked_comment(repo, number, marker):
    comments = gh_api(["--method", "GET", f"repos/{repo}/issues/{number}/comments", "-f", "per_page=100"]) or []
    for comment in comments:
        if str(comment.get("body", "")).startswith(marker):
            return comment
    return None



def find_comment(repo, number):
    return find_marked_comment(repo, number, COMMENT_MARKER)


def post_comment(workspace, repo, number, dry_run=False):
    row = latest(workspace, repo, number)
    data = data_from_row(row) if row else None
    if not data:
        return {"ok": False, "error": f"No stored triage for PR #{number}. Run `repo-manager review-pr {number}` first."}
    warnings = []
    meta = cli.fetch_pr_metadata(repo, number)
    live_head = meta.get("headRefOid", "")
    if live_head and data.get("head_sha") and live_head != data["head_sha"]:
        warnings.append(f"PR head has moved since the triage ({data['head_sha'][:7]} -> {live_head[:7]}); consider re-running review-pr.")
    body = render_comment(data)
    existing = find_comment(repo, number)
    if dry_run:
        return {"ok": True, "action": "update" if existing else "create", "dry_run": True, "body": body, "warnings": warnings}
    if existing:
        comment = gh_api(["--method", "PATCH", f"repos/{repo}/issues/comments/{existing['id']}", "-f", f"body={body}"], check=True)
        action = "updated"
    else:
        comment = gh_api(["--method", "POST", f"repos/{repo}/issues/{number}/comments", "-f", f"body={body}"], check=True)
        action = "created"
    if not comment or not comment.get("id"):
        return {"ok": False, "error": "GitHub did not return the posted comment.", "warnings": warnings}
    with connect(workspace) as conn:
        conn.execute(
            """
            INSERT INTO pr_review_comments (comment_key, repo, pr_number, comment_id, comment_url, posted_head_sha, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comment_key) DO UPDATE SET comment_id=excluded.comment_id, comment_url=excluded.comment_url,
              posted_head_sha=excluded.posted_head_sha, synced_at=excluded.synced_at
            """,
            (f"{repo}|{number}", repo, number, comment["id"], comment.get("html_url", ""), data.get("head_sha", ""), cli.now_iso()),
        )
    return {"ok": True, "action": action, "url": comment.get("html_url", ""), "warnings": warnings}


def request_reviewers(workspace, repo, number, handles=None, dry_run=False):
    warnings = []
    if handles:
        suggestions = [{"handle": cli.clean_github_handle(h), "basis": "explicit", "detail": "requested explicitly"} for h in handles if cli.clean_github_handle(h)]
    else:
        row = latest(workspace, repo, number)
        data = data_from_row(row) if row else None
        if not data:
            return {"ok": False, "error": f"No stored triage for PR #{number}. Run `repo-manager review-pr {number}` first, or pass --reviewers."}
        suggestions = data.get("reviewers", [])
    meta = cli.gh_cli_json(["pr", "view", str(number), "--repo", repo, "--json", "author,reviewRequests,reviews"])
    if not meta:
        return {"ok": False, "error": f"Could not fetch PR #{number} from {repo}.", "warnings": warnings}
    author = cli.pr_author_handle(meta).lower()
    involved = set()
    for request in meta.get("reviewRequests") or []:
        login = request.get("login") or request.get("slug") or ""
        if login:
            involved.add(login.lower())
    for review in meta.get("reviews") or []:
        login = (review.get("author") or {}).get("login") or ""
        if login:
            involved.add(login.lower())
    to_request, skipped = [], []
    for item in suggestions:
        handle = item.get("handle", "")
        if not cli.VALID_HANDLE.match(handle):
            skipped.append({"handle": handle, "reason": "not a plain GitHub handle"})
        elif handle.lower() == author:
            skipped.append({"handle": handle, "reason": "PR author"})
        elif handle.lstrip("@").lower() in involved:
            skipped.append({"handle": handle, "reason": "already requested or reviewed"})
        else:
            to_request.append(item)
    if dry_run:
        return {"ok": True, "dry_run": True, "to_request": to_request, "skipped": skipped, "warnings": warnings}
    requested, failed = [], []
    for item in to_request:
        login = item["handle"].lstrip("@")
        result = gh_api(["--method", "POST", f"repos/{repo}/pulls/{number}/requested_reviewers", "-f", f"reviewers[]={login}"])
        if result:
            requested.append(item["handle"])
        else:
            failed.append({"handle": item["handle"], "reason": "GitHub rejected the request (not a collaborator?)"})
    return {"ok": True, "requested": requested, "skipped": skipped, "failed": failed, "warnings": warnings}

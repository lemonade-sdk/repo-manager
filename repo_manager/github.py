"""Everything repo-manager asks GitHub, through `gh`.

`gh` carries the token — a GitHub App token in the workflows, the maintainer's own login on
a laptop — so nothing here handles credentials.
"""

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone


VALID_HANDLE = re.compile(r"^@[A-Za-z0-9-]+$")
DISCORD_ANNOTATION = re.compile(r"\s*\(discord:[^)]*\)", re.IGNORECASE)
MAINTAINER_ROW = re.compile(r"^\|\s*@([A-Za-z0-9-]+)\s*\|([^|]*)\|(.*)\|\s*$")

PR_METADATA_FIELDS = "number,title,state,isDraft,headRefOid,author,url,baseRefName"


def run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise SystemExit(
            f"{' '.join(cmd[:3])} failed:\n{(result.stderr or result.stdout or '').strip()}"
        )
    return result


def require_gh():
    if not shutil.which("gh"):
        raise SystemExit("`gh` was not found on PATH; install and authenticate the GitHub CLI.")


def gh_json(args, check=True):
    """`gh api ...`, parsed. Returns None when the call fails or says nothing."""
    require_gh()
    result = run(["gh", "api", *args], check=check)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GitHub API returned invalid JSON for `gh api {' '.join(args)}`: {exc}")


def gh_cli_json(args, check=True):
    """`gh <subcommand> --json ...`, parsed."""
    require_gh()
    result = run(["gh", *args], check=check)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GitHub CLI returned invalid JSON for `gh {' '.join(args)}`: {exc}")


def associated_pr(repo, sha):
    """(number, author handle) for the PR a commit came from, or (None, "")."""
    data = gh_json([f"repos/{repo}/commits/{sha}/pulls"], check=False)
    if not isinstance(data, list) or not data:
        return None, ""
    first = data[0]
    return first.get("number"), handle_of(first.get("user"))


def handle_of(user):
    login = user.get("login") if isinstance(user, dict) else str(user or "")
    return f"@{login}" if login else ""


def pr_author_handle(meta):
    return handle_of(meta.get("author") or {})


def fetch_pr_metadata(repo, number):
    data = gh_cli_json(["pr", "view", str(number), "--repo", repo, "--json", PR_METADATA_FIELDS])
    if not data:
        raise SystemExit(f"Could not fetch PR #{number} from {repo}.")
    return data


def list_open_prs(repo, limit):
    data = gh_cli_json([
        "pr", "list", "--repo", repo, "--state", "open", "--limit", str(limit),
        "--json", "number,title,isDraft,headRefOid,author,createdAt",
    ])
    return data or []


def parse_time(value):
    """An instant from either clock. git writes `2026-09-01T17:22:59-04:00` and GitHub writes
    `2026-09-12T10:00:00Z`, so these can only be compared once they are both datetimes."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def open_issues_with_label(repo, label, since=""):
    """Open issues carrying `label`, optionally only those opened on or after `since`.

    These are the tester-feedback issues the release review folds in as blockers: a human
    found something on a candidate and filed it against the repo being released, which is
    where testers already live.
    """
    args = ["issue", "list", "--repo", repo, "--state", "open", "--label", label,
            "--limit", "100", "--json", "number,title,body,author,createdAt,url,labels"]
    issues = gh_cli_json(args, check=False) or []
    cutoff = parse_time(since)
    if cutoff:
        issues = [
            issue for issue in issues
            if (parse_time(issue.get("createdAt")) or cutoff) >= cutoff
        ]
    return sorted(issues, key=lambda issue: issue.get("number", 0))


def release_body(repo, tag):
    result = run(["gh", "api", f"repos/{repo}/releases/tags/{tag}", "--jq", '.body // ""'], check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def clean_handle(value):
    text = DISCORD_ANNOTATION.sub("", str(value or "")).strip().lstrip("@").strip()
    return f"@{text}" if text else ""


def fetch_file(repo, ref, path):
    """A file at a ref, or "" when it does not exist."""
    result = run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw",
         f"repos/{repo}/contents/{path}?ref={ref}"],
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def parse_maintainer_table(text):
    """{handle: {"handle", "admin", "areas"}} from the Maintainers table in contribute.md.

    Parsing the guide beats hardcoding: a reassigned subject area follows the document that
    assigned it, which is how the tool stays right without anyone remembering to edit it.
    """
    table = {}
    for line in (text or "").splitlines():
        match = MAINTAINER_ROW.match(line.strip())
        if not match:
            continue
        handle, admin_cell, areas_cell = match.groups()
        areas = [area.strip() for area in areas_cell.split(",") if area.strip()]
        if not areas:
            continue
        table[handle.lower()] = {
            "handle": f"@{handle}",
            "admin": admin_cell.strip().lower() in ("yes", "y", "true", "x"),
            "areas": areas,
        }
    return table

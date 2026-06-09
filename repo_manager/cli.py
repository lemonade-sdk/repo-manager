import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from repo_manager import __version__


CONFIG_DIR = ".repo-manager"
CONFIG_FILE = "config.json"
DB_FILE = "repo-manager.sqlite"
RUBRIC_VERSION = "commit-review-2026-06-09"
RELEASE_RUBRIC_VERSION = "release-review-2026-06-09"
ANNOUNCEMENT_VERSION = "release-announcement-2026-06-09"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def repo_root():
    override = os.environ.get("REPO_MANAGER_HOME")
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve().parent.parent
    if (here / "skills").exists():
        return here
    installed = Path(sys.prefix) / "share" / "repo-manager"
    if (installed / "skills").exists():
        return installed
    return Path.cwd()


def skill_path(name):
    path = repo_root() / "skills" / name / "SKILL.md"
    if not path.exists():
        raise SystemExit(f"Skill not found: {path}")
    return path


def schema_path():
    path = repo_root() / "db" / "schema.sql"
    if not path.exists():
        raise SystemExit(f"Schema not found: {path}")
    return path


def find_workspace(start=None):
    cur = Path(start or Path.cwd()).resolve()
    for path in [cur, *cur.parents]:
        if (path / CONFIG_DIR / CONFIG_FILE).exists():
            return path
    raise SystemExit("No repo-manager workspace found. Run `repo-manager init OWNER/REPO` first.")


def load_config(workspace=None):
    workspace = workspace or find_workspace()
    with (workspace / CONFIG_DIR / CONFIG_FILE).open("r", encoding="utf-8") as f:
        return json.load(f)


def db_path(workspace=None):
    workspace = workspace or find_workspace()
    return workspace / CONFIG_DIR / DB_FILE


def connect_db(workspace=None):
    conn = sqlite3.connect(db_path(workspace))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(workspace):
    (workspace / CONFIG_DIR).mkdir(parents=True, exist_ok=True)
    with connect_db(workspace) as conn:
        conn.executescript(schema_path().read_text(encoding="utf-8"))


def run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


def clone_or_fetch(repo, workspace):
    checkout = workspace / CONFIG_DIR / "checkout"
    script = repo_root() / "scripts" / "clone-repo.sh"
    if not script.exists():
        script = repo_root() / "skills" / "commit-review" / "scripts" / "clone-repo.sh"
    run([str(script), repo, str(checkout)])
    return checkout


def resolve_repo(args, config):
    return args.repo or config["repo"]


def run_pi(skill_name, prompt, cwd):
    pi = shutil.which("pi")
    if not pi:
        raise SystemExit("`pi` was not found on PATH.")
    cmd = [pi, "--mode", "json", "--skill", str(skill_path(skill_name)), prompt]
    saw_text = False
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        print(f"Running Pi skill: {skill_name}", flush=True)
        for line in proc.stdout:
            if render_pi_event(line):
                saw_text = True
        returncode = proc.wait()
    except KeyboardInterrupt:
        if "proc" in locals() and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        raise SystemExit(130)
    if saw_text:
        print()
    if returncode != 0:
        raise SystemExit(returncode)


def truncate_pi_detail(value, limit=180):
    text = str(value).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def summarize_tool_args(tool_name, args):
    if not isinstance(args, dict):
        return ""
    for key in ("cmd", "command", "path", "file_path", "pattern", "query"):
        if key in args and args[key]:
            return truncate_pi_detail(args[key])
    if tool_name == "bash" and "args" in args:
        return truncate_pi_detail(args["args"])
    return ""


def render_pi_event(line):
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        text = line.strip()
        if text:
            print(text, flush=True)
            return True
        return False

    event_type = event.get("type")
    if event_type == "agent_start":
        print("Pi started.", flush=True)
    elif event_type == "agent_end":
        print("\nPi finished.", flush=True)
    elif event_type == "tool_execution_start":
        tool_name = event.get("toolName", "tool")
        detail = summarize_tool_args(tool_name, event.get("args"))
        if detail:
            print(f"\nRunning {tool_name}: {detail}", flush=True)
        else:
            print(f"\nRunning {tool_name}", flush=True)
    elif event_type == "tool_execution_end":
        tool_name = event.get("toolName", "tool")
        status = "failed" if event.get("isError") else "done"
        print(f"{tool_name} {status}.", flush=True)
    elif event_type == "message_update":
        assistant_event = event.get("assistantMessageEvent") or {}
        if assistant_event.get("type") == "text_delta":
            print(assistant_event.get("delta", ""), end="", flush=True)
            return True
        if assistant_event.get("type") == "error":
            message = assistant_event.get("error", {}).get("errorMessage")
            if message:
                print(f"\nPi error: {message}", flush=True)
    return False


def parse_pr_number(raw):
    try:
        prs = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(prs, list) and prs:
        return prs[0].get("number")
    return None


def associated_pr(repo, commit):
    script = repo_root() / "scripts" / "get-associated-prs.sh"
    result = run([str(script), repo, commit])
    return parse_pr_number(result.stdout)


def safe_repo_name(repo):
    return repo.replace("/", "__")


def artifact_dir(workspace, repo, kind):
    path = workspace / CONFIG_DIR / "reviews" / kind / safe_repo_name(repo)
    path.mkdir(parents=True, exist_ok=True)
    return path


def commit_artifact_paths(workspace, repo, commit):
    base = artifact_dir(workspace, repo, "commits")
    return base / f"{commit}.commit-review.json"


def release_artifact_paths(workspace, repo, tag_start, head, kind):
    base = artifact_dir(workspace, repo, "releases")
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag_start)
    if kind == "review":
        return base / f"{safe_tag}..{head}.release-review.json", None
    return None, base / f"{safe_tag}..{head}.release-announcement.md"


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def require_files(*paths):
    missing = [str(path) for path in paths if not Path(path).exists()]
    if missing:
        raise SystemExit("Pi completed, but expected artifact file(s) were not created:\n" + "\n".join(missing))


def store_commit_review(workspace, repo, branch, tag_start, commit, json_file):
    data = read_json(json_file)
    raw = json.dumps(data, indent=2)
    try:
        pr_number = associated_pr(repo, commit)
    except SystemExit:
        pr_number = None
    with connect_db(workspace) as conn:
        conn.execute(
            """
            INSERT INTO commit_reviews (
              repo, commit_sha, branch, tag_start, pr_number, author, summary,
              verdict, verdict_reason, maintainer_todos, shout_outs, raw_output, json_path,
              reviewed_at, skill_version, rubric_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, commit_sha, rubric_version) DO UPDATE SET
              branch=excluded.branch,
              tag_start=excluded.tag_start,
              pr_number=excluded.pr_number,
              author=excluded.author,
              summary=excluded.summary,
              verdict=excluded.verdict,
              verdict_reason=excluded.verdict_reason,
              maintainer_todos=excluded.maintainer_todos,
              shout_outs=excluded.shout_outs,
              raw_output=excluded.raw_output,
              json_path=excluded.json_path,
              reviewed_at=excluded.reviewed_at,
              skill_version=excluded.skill_version
            """,
            (
                repo,
                commit,
                branch,
                tag_start,
                pr_number,
                data.get("author", ""),
                data.get("summary", ""),
                data.get("verdict", ""),
                data.get("verdict_reason", ""),
                json.dumps(data.get("maintainer_todos", [])),
                json.dumps(data.get("shout_outs", [])),
                raw,
                str(json_file),
                now_iso(),
                __version__,
                RUBRIC_VERSION,
            ),
        )


def review_exists(workspace, repo, commit):
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT 1 FROM commit_reviews WHERE repo=? AND commit_sha=? AND rubric_version=?",
            (repo, commit, RUBRIC_VERSION),
        ).fetchone()
    return row is not None


def list_commits(repo, workspace, branch, tag_start):
    checkout = clone_or_fetch(repo, workspace)
    run(["git", "-C", str(checkout), "fetch", "origin", branch, "--tags", "--prune"])
    rev = f"{tag_start}..origin/{branch}"
    result = run(["git", "-C", str(checkout), "rev-list", "--reverse", rev])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def head_sha(repo, workspace, branch):
    checkout = clone_or_fetch(repo, workspace)
    result = run(["git", "-C", str(checkout), "rev-parse", f"origin/{branch}"])
    return result.stdout.strip()


def load_reviews(workspace, repo, branch=None, tag_start=None):
    where = ["repo=?"]
    values = [repo]
    if branch:
        where.append("branch=?")
        values.append(branch)
    if tag_start:
        where.append("tag_start=?")
        values.append(tag_start)
    query = "SELECT * FROM commit_reviews WHERE " + " AND ".join(where) + " ORDER BY reviewed_at, commit_sha"
    with connect_db(workspace) as conn:
        return [dict(row) for row in conn.execute(query, values).fetchall()]


def compact_reviews(reviews):
    rows = []
    for review in reviews:
        data = read_json(review["json_path"]) if review.get("json_path") else {}
        rows.append(
            {
                "commit_sha": review["commit_sha"],
                "pr_number": review["pr_number"],
                "author": data.get("author", review["author"]),
                "summary": data.get("summary", review["summary"]),
                "verdict": data.get("verdict", review["verdict"]),
                "verdict_reason": data.get("verdict_reason", review["verdict_reason"]),
                "maintainer_todos": data.get("maintainer_todos", []),
                "shout_outs": data.get("shout_outs", []),
                "evidence": data.get("evidence", {}),
            }
        )
    return json.dumps(rows, indent=2)


def cmd_init(args):
    workspace = Path.cwd().resolve()
    manager_dir = workspace / CONFIG_DIR
    manager_dir.mkdir(exist_ok=True)
    config = {
        "repo": args.repo,
        "branch": args.branch,
        "created_at": now_iso(),
    }
    (manager_dir / CONFIG_FILE).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    init_db(workspace)
    if args.clone:
        clone_or_fetch(args.repo, workspace)
    if not args.no_pi_install:
        pi = shutil.which("pi")
        if not pi:
            raise SystemExit("`pi` was not found on PATH. Re-run with --no-pi-install to skip skill installation.")
        run([pi, "install", str(repo_root())])
        print(f"Installed Pi skills from {repo_root()}")
    else:
        print("Skipped Pi skill installation")
    print(f"Initialized repo-manager workspace for {args.repo} on {args.branch}")


def cmd_review_commit(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    json_file = commit_artifact_paths(workspace, repo, args.commit)
    prompt = (
        f"/skill:commit-review {repo} {args.commit}\n\n"
        f"Write the machine-readable JSON result to: {json_file}\n"
        "The JSON must match the schema required by the skill."
    )
    run_pi("commit-review", prompt, workspace)
    require_files(json_file)
    store_commit_review(workspace, repo, branch, args.tag_start or "", args.commit, json_file)


def cmd_sweep(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    commits = list_commits(repo, workspace, branch, args.tag)
    print(f"Found {len(commits)} commits in {args.tag}..origin/{branch}")
    for commit in commits:
        if review_exists(workspace, repo, commit) and not args.force:
            print(f"Skipping existing review for {commit}")
            continue
        print(f"Reviewing {commit}")
        json_file = commit_artifact_paths(workspace, repo, commit)
        prompt = (
        f"/skill:commit-review {repo} {commit}\n\n"
            f"This commit is part of the release range {args.tag}..{branch}.\n"
            f"Write the machine-readable JSON result to: {json_file}\n"
            "The JSON must match the schema required by the skill."
        )
        run_pi("commit-review", prompt, workspace)
        require_files(json_file)
        store_commit_review(workspace, repo, branch, args.tag, commit, json_file)


def cmd_release_review(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    reviews = load_reviews(workspace, repo, branch, args.tag)
    if not reviews:
        raise SystemExit("No commit reviews found for that repo/branch/tag.")
    head = head_sha(repo, workspace, branch)
    json_file, _ = release_artifact_paths(workspace, repo, args.tag, head, "review")
    prompt = (
        f"/skill:release-review\n\nRepo: {repo}\nBranch: {branch}\n"
        f"Starting tag: {args.tag}\nHead SHA: {head}\n\n"
        f"Write the machine-readable JSON result to: {json_file}\n"
        "Stored commit reviews:\n"
        f"{compact_reviews(reviews)}"
    )
    run_pi("release-review", prompt, workspace)
    require_files(json_file)
    data = read_json(json_file)
    raw = json.dumps(data, indent=2)
    verdict = data.get("verdict", "")
    with connect_db(workspace) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO release_reviews
            (repo, branch, tag_start, head_sha, verdict, raw_output, json_path, reviewed_at, skill_version, rubric_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo, branch, args.tag, head, verdict, raw, str(json_file), now_iso(), __version__, RELEASE_RUBRIC_VERSION),
        )


def cmd_announce(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    reviews = load_reviews(workspace, repo, branch, args.tag)
    if not reviews:
        raise SystemExit("No commit reviews found for that repo/branch/tag.")
    head = head_sha(repo, workspace, branch)
    _, markdown_file = release_artifact_paths(workspace, repo, args.tag, head, "announcement")
    prompt = (
        f"/skill:release-announcement\n\nRepo: {repo}\nBranch: {branch}\n"
        f"Starting tag: {args.tag}\nHead SHA: {head}\n\n"
        f"Write the Discord-friendly Markdown announcement to: {markdown_file}\n\n"
        "Stored commit reviews:\n"
        f"{compact_reviews(reviews)}"
    )
    run_pi("release-announcement", prompt, workspace)
    require_files(markdown_file)
    raw = Path(markdown_file).read_text(encoding="utf-8")
    with connect_db(workspace) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO release_announcements
            (repo, branch, tag_start, head_sha, raw_output, markdown_path, generated_at, skill_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo, branch, args.tag, head, raw, str(markdown_file), now_iso(), ANNOUNCEMENT_VERSION),
        )


def cmd_wipe_db(args):
    workspace = find_workspace()
    path = db_path(workspace)
    if path.exists():
        path.unlink()
    init_db(workspace)
    print(f"Wiped database: {path}")


def review_rows(workspace):
    with connect_db(workspace) as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT rowid, *
                FROM commit_reviews
                ORDER BY reviewed_at, commit_sha
                """
            ).fetchall()
        ]


def truncate(text, width):
    text = " ".join((text or "").split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


def cmd_db_table(args):
    workspace = find_workspace()
    rows = review_rows(workspace)
    if not rows:
        print("No commit reviews in the database.")
        return
    headers = ("#", "Commit", "Verdict", "Description")
    widths = (5, 12, 8, 90)
    print(f"{headers[0]:>{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]:<{widths[2]}}  {headers[3]}")
    print(f"{'-' * widths[0]}  {'-' * widths[1]}  {'-' * widths[2]}  {'-' * widths[3]}")
    for index, row in enumerate(rows, start=1):
        commit = truncate(row["commit_sha"], widths[1])
        verdict = truncate(row["verdict"], widths[2])
        description = truncate(row["summary"], widths[3])
        print(f"{index:>{widths[0]}}  {commit:<{widths[1]}}  {verdict:<{widths[2]}}  {description}")


def cmd_db_row(args):
    workspace = find_workspace()
    rows = review_rows(workspace)
    if args.index < 1 or args.index > len(rows):
        raise SystemExit(f"Row index out of range. Use `repo-manager db-table` to see valid indexes.")
    row = rows[args.index - 1]
    print(f"Row: {args.index}")
    print(f"Repo: {row['repo']}")
    print(f"Commit: {row['commit_sha']}")
    print(f"PR: {row['pr_number'] or ''}")
    print(f"Author: {row['author'] or ''}")
    print(f"Summary: {row['summary'] or ''}")
    print(f"Verdict: {row['verdict'] or ''}")
    print(f"Explanation: {row['verdict_reason'] or ''}")
    print(f"Audited At: {row['reviewed_at']}")
    print(f"JSON: {row['json_path'] or ''}")
    print()
    if row["json_path"] and Path(row["json_path"]).exists():
        print_pretty_review(read_json(row["json_path"]))
    elif row["raw_output"]:
        try:
            print_pretty_review(json.loads(row["raw_output"]))
        except json.JSONDecodeError:
            print(row["raw_output"])


def cmd_ui(args):
    from repo_manager.web import serve

    workspace = find_workspace()
    serve(workspace, args.host, args.port, not args.no_open)


def print_pretty_review(data):
    print("Description")
    print(data.get("summary", ""))
    print()

    shout_outs = data.get("shout_outs") or []
    if shout_outs:
        print("Shout Outs")
        for item in shout_outs:
            if isinstance(item, dict):
                print(f"- {item.get('handle', '')}: {item.get('reason', '')}".rstrip())
            else:
                print(f"- {item}")
        print()

    print("Verdict")
    print(data.get("verdict", ""))
    reason = data.get("verdict_reason", "")
    if reason:
        print(reason)
    print()

    todos = data.get("maintainer_todos") or []
    if todos:
        print("Maintainer To-Do")
        for item in todos:
            if isinstance(item, dict):
                print(f"- {item.get('text', '')}".rstrip())
            else:
                print(f"- {item}")
        print()

    evidence = data.get("evidence") or {}
    if evidence:
        print("Evidence")
        labels = {
            "review": "Review",
            "post_approval_commits": "Post-Approval Commits",
            "tests": "Tests",
            "manual_release_testing": "Manual Release Testing",
            "api_compatibility": "API Compatibility",
            "security": "Security",
            "documentation": "Documentation",
        }
        for key, label in labels.items():
            value = evidence.get(key)
            if value:
                print(f"- {label}: {value}")


def build_parser():
    parser = argparse.ArgumentParser(prog="repo-manager")
    parser.add_argument("--version", action="version", version=f"repo-manager {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize this folder to track a target repo.")
    init.add_argument("repo", help="Target GitHub repo, e.g. OWNER/REPO.")
    init.add_argument("--branch", default="main")
    init.add_argument("--clone", action="store_true", help="Clone/fetch the target repo immediately.")
    init.add_argument("--no-pi-install", action="store_true", help="Skip installing this package's Pi skills.")
    init.set_defaults(func=cmd_init)

    review = sub.add_parser("review-commit", help="Run commit-review for one commit and save it.")
    review.add_argument("commit")
    review.add_argument("--repo")
    review.add_argument("--branch")
    review.add_argument("--tag-start", default="")
    review.set_defaults(func=cmd_review_commit)

    sweep = sub.add_parser("sweep", help="Review every commit after a tag on the tracked branch.")
    sweep.add_argument("tag")
    sweep.add_argument("--repo")
    sweep.add_argument("--branch")
    sweep.add_argument("--force", action="store_true", help="Re-run reviews that already exist.")
    sweep.set_defaults(func=cmd_sweep)

    release = sub.add_parser("release-review", help="Run release-level review from stored commit reviews.")
    release.add_argument("tag")
    release.add_argument("--repo")
    release.add_argument("--branch")
    release.set_defaults(func=cmd_release_review)

    announce = sub.add_parser("announce", help="Generate a Discord-friendly release announcement.")
    announce.add_argument("tag")
    announce.add_argument("--repo")
    announce.add_argument("--branch")
    announce.set_defaults(func=cmd_announce)

    wipe = sub.add_parser("wipe-db", help="Delete and recreate the local SQLite database.")
    wipe.set_defaults(func=cmd_wipe_db)

    db_table = sub.add_parser("db-table", help="Print saved commit reviews as a table.")
    db_table.set_defaults(func=cmd_db_table)

    db_row = sub.add_parser("db-row", help="Print saved commit review contents by table row index.")
    db_row.add_argument("index", type=int)
    db_row.set_defaults(func=cmd_db_row)

    ui = sub.add_parser("ui", help="Serve a local web UI for saved reviews and announcements.")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--no-open", action="store_true", help="Print the URL without opening a browser.")
    ui.set_defaults(func=cmd_ui)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

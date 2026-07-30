import json
import hashlib
import re
import sqlite3
import subprocess
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone


def connect(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    ensure_range_schema(conn)
    ensure_pr_schema(conn)
    ensure_generation_schema(conn)
    return conn


def ensure_generation_schema(conn):
    for table in ("commit_reviews", "release_reviews", "release_announcements", "pr_reviews"):
        try:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            continue
        if columns and "generation_seconds" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN generation_seconds REAL NOT NULL DEFAULT 0")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_file(workspace):
    return workspace / ".repo-manager" / "repo-manager.sqlite"


def ensure_range_schema(conn):
    for table in ("commit_reviews", "release_reviews", "release_announcements"):
        try:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            continue
        if columns and "range_start" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN range_start TEXT NOT NULL DEFAULT ''")
        if table == "release_announcements" and columns:
            if "release_highlights_output" not in columns:
                conn.execute(
                    "ALTER TABLE release_announcements ADD COLUMN release_highlights_output TEXT NOT NULL DEFAULT ''"
                )
                if "release_notes_output" in columns:
                    conn.execute(
                        """
                        UPDATE release_announcements
                        SET release_highlights_output=release_notes_output
                        WHERE release_highlights_output=''
                        """
                    )
            if "release_highlights_path" not in columns:
                conn.execute(
                    "ALTER TABLE release_announcements ADD COLUMN release_highlights_path TEXT NOT NULL DEFAULT ''"
                )
                if "release_notes_path" in columns:
                    conn.execute(
                        """
                        UPDATE release_announcements
                        SET release_highlights_path=release_notes_path
                        WHERE release_highlights_path=''
                        """
                    )


def ensure_pr_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_reviews (
          repo TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          head_sha TEXT NOT NULL DEFAULT '',
          pr_title TEXT NOT NULL DEFAULT '',
          author TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL DEFAULT '',
          attention_level TEXT NOT NULL DEFAULT '',
          scope_verdict TEXT NOT NULL DEFAULT '',
          second_review_required INTEGER NOT NULL DEFAULT 0,
          documentation_status TEXT NOT NULL DEFAULT '',
          alignment_flags TEXT NOT NULL DEFAULT '[]',
          breaking_changes TEXT NOT NULL DEFAULT '[]',
          suggested_reviewers TEXT NOT NULL DEFAULT '[]',
          maintainer_needed_areas TEXT NOT NULL DEFAULT '[]',
          raw_output TEXT NOT NULL,
          json_path TEXT NOT NULL DEFAULT '',
          reviewed_at TEXT NOT NULL,
          skill_version TEXT NOT NULL,
          rubric_version TEXT NOT NULL,
          generation_seconds REAL NOT NULL DEFAULT 0,
          PRIMARY KEY (repo, pr_number, rubric_version)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_review_comments (
          comment_key TEXT PRIMARY KEY,
          repo TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          comment_id INTEGER NOT NULL,
          comment_url TEXT NOT NULL DEFAULT '',
          posted_head_sha TEXT NOT NULL DEFAULT '',
          synced_at TEXT NOT NULL
        )
        """
    )


def ensure_todo_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_todos (
          todo_id TEXT PRIMARY KEY,
          review_kind TEXT NOT NULL,
          review_key TEXT NOT NULL,
          todo_index INTEGER NOT NULL,
          todo_text TEXT NOT NULL,
          completed INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_review_todos_review
        ON review_todos (review_kind, review_key)
        """
    )


def ensure_read_schema(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS review_read_states (
          review_key TEXT PRIMARY KEY,
          is_read INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL
        )
        """
    )


def read_json_file(path):
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.exists():
        return {}
    try:
        with file_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def read_text_file(path):
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    try:
        return file_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def markdown_heading_level(line):
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None, ""
    return len(match.group(1)), match.group(2).strip()


def extract_release_note_sections(markdown):
    wanted = {"headline", "breaking changes"}
    sections = {}
    current = None
    current_level = None
    for line in (markdown or "").splitlines():
        level, title = markdown_heading_level(line)
        normalized = title.lower() if title else ""
        if level is not None:
            if normalized in wanted:
                current = normalized
                current_level = level
                sections[current] = [line]
                continue
            if current and level <= current_level:
                current = None
                current_level = None
        if current:
            sections[current].append(line)
    ordered = []
    for key in ("headline", "breaking changes"):
        text = "\n".join(sections.get(key, [])).strip()
        if text:
            ordered.append(text)
    return "\n\n".join(ordered)


def parse_json_text(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def todo_display_text(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return item.get("text") or item.get("reason") or json.dumps(item, sort_keys=True)
    return str(item)


def todo_id(review_kind, review_key, index, item):
    payload = json.dumps(
        {"kind": review_kind, "review": review_key, "index": index, "todo": item},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_todos(conn, review_kind, review_key, todos):
    ensure_todo_schema(conn)
    normalized = []
    existing = {
        row["todo_id"]: row
        for row in conn.execute(
            "SELECT * FROM review_todos WHERE review_kind=? AND review_key=?",
            (review_kind, review_key),
        )
    }
    for index, item in enumerate(todos or []):
        item_id = todo_id(review_kind, review_key, index, item)
        text = todo_display_text(item)
        row = existing.get(item_id)
        if row is None:
            conn.execute(
                """
                INSERT INTO review_todos
                (todo_id, review_kind, review_key, todo_index, todo_text, completed, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?)
                """,
                (item_id, review_kind, review_key, index, text, now_iso()),
            )
            completed = False
        else:
            completed = bool(row["completed"])
        normalized.append(
            {
                "id": item_id,
                "text": text,
                "priority": item.get("priority") if isinstance(item, dict) else "",
                "completed": completed,
            }
        )
    return normalized


def read_state(conn, review_key):
    ensure_read_schema(conn)
    row = conn.execute(
        "SELECT is_read FROM review_read_states WHERE review_key=?",
        (review_key,),
    ).fetchone()
    return bool(row["is_read"]) if row else False


def review_data(row):
    data = read_json_file(row.get("json_path")) or parse_json_text(row.get("raw_output"), {})
    return data if isinstance(data, dict) else {}


def normalize_handle(value):
    if not value:
        return ""
    handle = str(value).strip()
    if not handle:
        return ""
    return handle if handle.startswith("@") else f"@{handle}"


def is_ai_reviewer(handle):
    lowered = str(handle or "").lower()
    return any(token in lowered for token in ("chatgpt", "claude", "copilot"))


def reviewer_handles(item, data):
    reviewers = data.get("reviewers")
    if isinstance(reviewers, list):
        handles = {
            normalize_handle(entry.get("handle") if isinstance(entry, dict) else entry)
            for entry in reviewers
        }
    else:
        review_text = ""
        evidence = data.get("evidence")
        if isinstance(evidence, dict):
            review_text = str(evidence.get("review") or "")
        handles = {normalize_handle(match) for match in re.findall(r"@([A-Za-z0-9-]+)", review_text)}
    author = normalize_handle(data.get("author") or item.get("author"))
    return sorted(handle for handle in handles if handle and handle != author and not is_ai_reviewer(handle))


def parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def format_date_range(rows):
    dates = [parse_iso_datetime(row.get("merge_date") or row.get("commit_date") or row.get("reviewed_at")) for row in rows]
    dates = [date for date in dates if date is not None]
    if not dates:
        return {"start": "", "end": "", "days": 0}
    start = min(dates).date()
    end = max(dates).date()
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": (end - start).days + 1,
    }


def tag_sort_key(tag):
    if tag == "vNext":
        return (1, ())
    parts = tuple(int(part) for part in re.findall(r"\d+", str(tag or "")))
    return (0, parts)


def commit_dates(workspace, shas):
    checkout = workspace / ".repo-manager" / "checkout"
    if not checkout.exists():
        return {}
    unique_shas = [sha for sha in dict.fromkeys(shas) if sha]
    if not unique_shas:
        return {}
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), "show", "-s", "--format=%H%x00%cI", *unique_shas],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return {}
    if result.returncode != 0:
        return {}
    dates = {}
    for line in result.stdout.splitlines():
        if "\x00" not in line:
            continue
        sha, commit_date = line.split("\x00", 1)
        dates[sha] = commit_date.strip()
    return dates


def commit_reviews(workspace):
    rows = []
    with connect(db_file(workspace)) as conn:
        ensure_todo_schema(conn)
        ensure_read_schema(conn)
        for row in conn.execute(
            """
            SELECT rowid, *
            FROM commit_reviews
            ORDER BY reviewed_at DESC, commit_sha
            """
        ):
            item = dict(row)
            data = review_data(item)
            item["details"] = data
            item["maintainer_todos"] = data.get(
                "maintainer_todos",
                parse_json_text(item.get("maintainer_todos"), []),
            )
            item["shout_outs"] = data.get("shout_outs", parse_json_text(item.get("shout_outs"), []))
            item["evidence"] = data.get("evidence", {})
            item["summary"] = data.get("summary", item.get("summary") or "")
            item["verdict_reason"] = data.get("verdict_reason", item.get("verdict_reason") or "")
            item["merge_date"] = data.get("merge_date") or data.get("merged_at") or ""
            item["reviewers"] = reviewer_handles(item, data)
            review_key = f"{item['repo']}|{item['commit_sha']}|{item['rubric_version']}"
            item["review_key"] = review_key
            item["is_read"] = read_state(conn, review_key)
            item["todo_items"] = normalize_todos(conn, "commit", review_key, item["maintainer_todos"])
            item["outstanding_todos"] = sum(1 for todo in item["todo_items"] if not todo["completed"])
            rows.append(item)
    dates = commit_dates(workspace, [row.get("commit_sha") for row in rows])
    for row in rows:
        row["commit_date"] = dates.get(row.get("commit_sha")) or row.get("reviewed_at")
    return rows


def release_reviews(workspace):
    rows = []
    seen = set()
    with connect(db_file(workspace)) as conn:
        ensure_todo_schema(conn)
        for row in conn.execute(
            """
            SELECT rowid, *
            FROM release_reviews
            ORDER BY reviewed_at DESC
            """
        ):
            item = dict(row)
            key = (item["repo"], item["branch"], item["tag_start"], item["rubric_version"])
            if key in seen:
                continue
            seen.add(key)
            data = review_data(item)
            item["details"] = data
            item["verdict_reason"] = data.get("verdict_reason", "")
            item["prioritized_todos"] = data.get("prioritized_todos", [])
            item["evidence"] = data.get("evidence", {})
            review_key = (
                f"{item['repo']}|{item['branch']}|{item['tag_start']}|"
                f"{item['rubric_version']}"
            )
            item["todo_items"] = normalize_todos(conn, "release", review_key, item["prioritized_todos"])
            item["outstanding_todos"] = sum(1 for todo in item["todo_items"] if not todo["completed"])
            rows.append(item)
    return rows


_PR_STATE_CACHE = {}
PR_STATE_TTL_SECONDS = 60


def live_pr_states(repo, numbers):
    """Current state, base branch, and review activity for the given PRs, in one cached GraphQL call.

    Returns {number: {"state", "base", "review_decision", "last_commit_at", "reviews",
    "comments", "viewer"}}. Empty on any gh failure so callers treat state as unknown
    instead of hiding rows or inventing a status.
    """
    numbers = sorted({int(number) for number in numbers})
    if not repo or "/" not in repo or not numbers:
        return {}
    now = time.time()
    cached = _PR_STATE_CACHE.get(repo)
    if cached and now - cached[0] < PR_STATE_TTL_SECONDS and cached[2] == numbers:
        return cached[1]
    owner, _, name = repo.partition("/")
    fields = " ".join(
        f"pr{number}: pullRequest(number: {number}) {{ state baseRefName reviewDecision "
        "commits(last: 1) { nodes { commit { committedDate } } } "
        "reviews(last: 50) { nodes { author { login } state submittedAt } } "
        "comments(last: 50) { nodes { author { login } createdAt } } "
        "reviewThreads(last: 30) { nodes { comments(last: 15) { nodes { author { login } createdAt } } } } "
        "reviewRequests(first: 20) { nodes { requestedReviewer { ... on User { login } ... on Team { name } } } } "
        "}"
        for number in numbers
    )
    query = f'query {{ viewer {{ login }} repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(proc.stdout) if proc.stdout else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return cached[1] if cached else {}
    data = payload.get("data") or {}
    viewer = (data.get("viewer") or {}).get("login", "")
    repository = data.get("repository") or {}

    def login_of(node):
        return ((node or {}).get("author") or {}).get("login", "")

    states = {}
    for number in numbers:
        entry = repository.get(f"pr{number}")
        if not isinstance(entry, dict):
            continue
        commits = ((entry.get("commits") or {}).get("nodes")) or []
        last_commit_at = ((commits[0].get("commit") or {}).get("committedDate", "")) if commits else ""
        reviews = [
            {"login": login_of(node), "state": node.get("state", ""), "at": node.get("submittedAt", "")}
            for node in ((entry.get("reviews") or {}).get("nodes")) or []
            if login_of(node)
        ]
        comments = [
            {"login": login_of(node), "at": node.get("createdAt", "")}
            for node in ((entry.get("comments") or {}).get("nodes")) or []
            if login_of(node)
        ]
        for thread in ((entry.get("reviewThreads") or {}).get("nodes")) or []:
            comments += [
                {"login": login_of(node), "at": node.get("createdAt", "")}
                for node in ((thread.get("comments") or {}).get("nodes")) or []
                if login_of(node)
            ]
        requested = []
        for node in ((entry.get("reviewRequests") or {}).get("nodes")) or []:
            reviewer = (node or {}).get("requestedReviewer") or {}
            handle = reviewer.get("login") or reviewer.get("name") or ""
            if handle:
                requested.append(handle)
        states[number] = {
            "state": entry.get("state", ""),
            "base": entry.get("baseRefName", ""),
            "review_decision": entry.get("reviewDecision") or "",
            "last_commit_at": last_commit_at,
            "reviews": reviews,
            "comments": comments,
            "requested": requested,
            "viewer": viewer,
        }
    if states:
        _PR_STATE_CACHE[repo] = (now, states, numbers)
        return states
    return cached[1] if cached else {}


def derive_review_status(info, author, attention_level, viewer_override=""):
    """Map live review activity to a (short label, tooltip detail) pair.

    The perspective is the gh-authenticated viewer unless viewer_override names another
    GitHub login: "Waiting for me" means the ball is in that person's court. Returns
    ('', '') when the PR is not open or live data is missing.
    """
    if not info or info.get("state") != "OPEN":
        return "", ""
    viewer = str(viewer_override or info.get("viewer") or "").lstrip("@").lower()
    author_login = str(author or "").lstrip("@").lower()
    latest = {}
    for review in sorted(info.get("reviews", []), key=lambda review: review.get("at") or ""):
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"):
            latest[review["login"].lower()] = review
    mine = latest.get(viewer)
    if mine and mine.get("state") == "CHANGES_REQUESTED":
        my_time = mine.get("at") or ""
        activity = [
            comment.get("at") or ""
            for comment in info.get("comments", [])
            if (comment.get("login") or "").lower() == author_login
        ]
        if info.get("last_commit_at"):
            activity.append(info["last_commit_at"])
        if any(at > my_time for at in activity if at):
            return "Waiting for me", "The author replied to my change request — my turn to re-review."
        return "Requests", "I requested changes — waiting for the author to respond."
    if info.get("review_decision") == "APPROVED":
        return "Approved", "Approved but not merged yet."
    others = {
        login: review
        for login, review in latest.items()
        if login not in (viewer, author_login)
        and not is_ai_reviewer(login)
        and review.get("state") != "DISMISSED"
    }
    if others:
        names = ", ".join(sorted(others))
        if attention_level == "High":
            return "Needs core", f"In review by {names}, but the pre-review flags High attention — needs a core maintainer."
        blocking = sorted(login for login, review in others.items() if review.get("state") == "CHANGES_REQUESTED")
        if blocking:
            return "Waiting", f"{', '.join(blocking)} requested changes — waiting for the author."
        return "Handled", f"Being handled by another reviewer: {names}."
    requested = [handle for handle in info.get("requested", []) if not is_ai_reviewer(handle)]
    if any(handle.lower() == viewer for handle in requested):
        return "Waiting for me", "I am assigned as a reviewer and have not reviewed yet."
    requested_others = [handle for handle in requested if handle.lower() != viewer]
    if requested_others:
        return "Waiting", f"Reviewer assigned but no review yet: {', '.join(sorted(requested_others))}."
    return "Needs triage", "No reviewer assigned and no reviews yet — needs triage by me."


def pr_reviews(workspace, pr_viewer=""):
    rows = []
    effective_viewer = str(pr_viewer or "").lstrip("@").strip()
    with connect(db_file(workspace)) as conn:
        comment_urls = {
            (row["repo"], row["pr_number"]): row["comment_url"]
            for row in conn.execute("SELECT repo, pr_number, comment_url FROM pr_review_comments")
        }
        seen = set()
        for row in conn.execute(
            """
            SELECT rowid, *
            FROM pr_reviews
            ORDER BY reviewed_at DESC, pr_number DESC
            """
        ):
            item = dict(row)
            key = (item["repo"], item["pr_number"])
            if key in seen:
                continue
            seen.add(key)
            data = review_data(item)
            item["details"] = data
            item["summary"] = data.get("summary", item.get("summary") or "")
            item["alignment_flags"] = data.get("alignment_flags", parse_json_text(item.get("alignment_flags"), []))
            item["breaking_changes"] = data.get("breaking_changes", parse_json_text(item.get("breaking_changes"), []))
            item["suggested_reviewers"] = data.get(
                "suggested_reviewers", parse_json_text(item.get("suggested_reviewers"), [])
            )
            item["maintainer_needed_areas"] = data.get(
                "maintainer_needed_areas", parse_json_text(item.get("maintainer_needed_areas"), [])
            )
            item["documentation"] = data.get("documentation", {})
            item["scope"] = data.get("scope", {})
            item["evidence"] = data.get("evidence", {})
            item["description_check"] = data.get("description_check", {})
            item["attention_reasons"] = data.get("attention_reasons", [])
            item["comment_url"] = comment_urls.get((item["repo"], item["pr_number"]), "")
            rows.append(item)
    by_repo = {}
    for item in rows:
        by_repo.setdefault(item["repo"], set()).add(item["pr_number"])
    authenticated = ""
    for repo, numbers in by_repo.items():
        states = live_pr_states(repo, numbers)
        for item in rows:
            if item["repo"] == repo:
                info = states.get(item["pr_number"], {})
                authenticated = authenticated or info.get("viewer", "")
                item["pr_state"] = info.get("state", "")
                item["base_ref"] = info.get("base", "")
                status, detail = derive_review_status(
                    info, item.get("author"), item.get("attention_level"), effective_viewer
                )
                item["review_status"] = status
                item["review_status_detail"] = detail
    return rows, (effective_viewer or authenticated)


def release_announcements(workspace):
    rows = []
    seen = set()
    with connect(db_file(workspace)) as conn:
        for row in conn.execute(
            """
            SELECT rowid, *
            FROM release_announcements
            ORDER BY generated_at DESC
            """
        ):
            item = dict(row)
            key = (item["repo"], item["branch"], item["tag_start"], item["skill_version"])
            if key in seen:
                continue
            seen.add(key)
            item["markdown"] = read_text_file(item.get("markdown_path")) or item.get("raw_output") or ""
            release_highlights = (
                read_text_file(item.get("release_highlights_path"))
                or item.get("release_highlights_output")
                or read_text_file(item.get("release_notes_path"))
                or item.get("release_notes_output")
                or ""
            )
            item["release_highlights_markdown"] = (
                extract_release_note_sections(release_highlights) or release_highlights
            )
            rows.append(item)
    return rows


def verdict_counts(rows):
    counts = {}
    for row in rows:
        verdict = row.get("verdict") or "Unknown"
        counts[verdict] = counts.get(verdict, 0) + 1
    return counts


def load_config(workspace):
    with (workspace / ".repo-manager" / "config.json").open("r", encoding="utf-8") as f:
        return json.load(f)


def app_data(workspace, pr_viewer=""):
    commits = commit_reviews(workspace)
    releases = release_reviews(workspace)
    announcements = release_announcements(workspace)
    prs, effective_viewer = pr_reviews(workspace, pr_viewer)
    authors = {normalize_handle(row.get("author")) for row in commits if normalize_handle(row.get("author"))}
    reviewers = {reviewer for row in commits for reviewer in row.get("reviewers", [])}
    tags = sorted(
        {
            row.get("tag_start")
            for row in [*commits, *releases, *announcements]
            if row.get("tag_start")
        },
        key=tag_sort_key,
        reverse=True,
    )
    return {
        "config": load_config(workspace),
        "tags": tags,
        "counts": {
            "commits": len(commits),
            "release_reviews": len(releases),
            "announcements": len(announcements),
            "pr_reviews": len(prs),
            "unread_reviews": sum(1 for row in commits if not row.get("is_read")),
            "outstanding_todos": sum(row.get("outstanding_todos", 0) for row in commits)
            + sum(row.get("outstanding_todos", 0) for row in releases),
            "authors": len(authors),
            "reviewers": len(reviewers),
            "date_range": format_date_range(commits),
            "verdicts": verdict_counts(commits),
        },
        "commit_reviews": commits,
        "release_reviews": releases,
        "release_announcements": announcements,
        "pr_reviews": prs,
        "pr_viewer": effective_viewer,
    }


def public_app_data(workspace):
    data = app_data(workspace)
    # PR reviews are transient pre-merge advisories and are not round-tripped by
    # sync_down, so they stay out of the published dashboard entirely.
    data.pop("pr_reviews", None)
    data.pop("pr_viewer", None)
    data.get("counts", {}).pop("pr_reviews", None)
    for key in ("commit_reviews", "release_reviews", "release_announcements"):
        cleaned = []
        for row in data.get(key, []):
            item = dict(row)
            for private_key in (
                "rowid",
                "raw_output",
                "json_path",
                "markdown_path",
                "release_highlights_output",
                "release_highlights_path",
                "release_notes_output",
                "release_notes_path",
            ):
                item.pop(private_key, None)
            cleaned.append(item)
        data[key] = cleaned
    return data


def script_safe_json(value):
    # Inside JSON text, `<` only occurs within strings, so escaping it cannot change the
    # parsed value — but it prevents review/PR-controlled content (e.g. a literal
    # `</script>`) from terminating the embedding <script> element. U+2028/U+2029 are
    # valid in JSON strings but historically illegal in JS source.
    payload = json.dumps(value, ensure_ascii=False)
    return (
        payload.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def static_index_html(workspace):
    payload = script_safe_json(public_app_data(workspace))
    bootstrap = (
        "<script>"
        "window.REPO_MANAGER_STATIC = true;"
        f"window.REPO_MANAGER_STATIC_DATA = {payload};"
        "</script>"
    )
    return INDEX_HTML.replace("</head>", f"{bootstrap}\n</head>", 1)


def export_static_site(workspace, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(static_index_html(workspace), encoding="utf-8")


def update_todo(workspace, payload):
    todo = payload.get("todo_id")
    completed = 1 if payload.get("completed") else 0
    if not todo:
        return {"ok": False, "error": "Missing todo_id"}
    with connect(db_file(workspace)) as conn:
        ensure_todo_schema(conn)
        result = conn.execute(
            "UPDATE review_todos SET completed=?, updated_at=? WHERE todo_id=?",
            (completed, now_iso(), todo),
        )
        if result.rowcount == 0:
            return {"ok": False, "error": "Unknown todo_id"}
    return {"ok": True}


def update_read_state(workspace, payload):
    review_key = payload.get("review_key")
    is_read = 1 if payload.get("is_read") else 0
    if not review_key:
        return {"ok": False, "error": "Missing review_key"}
    with connect(db_file(workspace)) as conn:
        ensure_read_schema(conn)
        conn.execute(
            """
            INSERT INTO review_read_states (review_key, is_read, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(review_key) DO UPDATE SET
              is_read=excluded.is_read,
              updated_at=excluded.updated_at
            """,
            (review_key, is_read, now_iso()),
        )
    return {"ok": True}


def run_pr_action(workspace, action, payload):
    """Run a gh-backed PR action from a request thread.

    cli.py signals every failure with SystemExit, which would otherwise kill the handler
    thread with no HTTP response — so it must be caught and turned into a JSON error here.
    """
    pr_number = payload.get("pr_number")
    if not isinstance(pr_number, int):
        return {"ok": False, "error": "Missing pr_number"}
    from repo_manager import cli

    try:
        repo = load_config(workspace)["repo"]
        if action == "comment":
            return cli.post_pr_review(workspace, repo, pr_number)
        return cli.request_pr_reviewers(workspace, repo, pr_number)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc) or "Command failed"}


def make_handler(workspace):
    class RepoManagerHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.send_text(INDEX_HTML, "text/html; charset=utf-8")
            elif path == "/api/data":
                params = parse_qs(urlparse(self.path).query)
                pr_viewer = (params.get("viewer") or [""])[0][:64]
                self.send_json(app_data(workspace, pr_viewer))
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/todo", "/api/read", "/api/pr-comment", "/api/pr-reviewers"):
                self.send_error(404)
                return
            if path in ("/api/pr-comment", "/api/pr-reviewers"):
                # These act on GitHub with the user's gh credentials, so reject
                # cross-origin requests (browser-set Origin that isn't this server).
                origin = self.headers.get("Origin", "")
                host = self.headers.get("Host", "")
                if origin and urlparse(origin).netloc != host:
                    self.send_json({"ok": False, "error": "Cross-origin request rejected"}, status=403)
                    return
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
                return
            if path == "/api/todo":
                result = update_todo(workspace, payload)
            elif path == "/api/read":
                result = update_read_state(workspace, payload)
            elif path == "/api/pr-comment":
                result = run_pr_action(workspace, "comment", payload)
            else:
                result = run_pr_action(workspace, "reviewers", payload)
            self.send_json(result, status=200 if result.get("ok") else 400)

        def send_json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_text(self, text, content_type):
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return RepoManagerHandler


def serve(workspace, host, port, open_browser):
    server = ThreadingHTTPServer((host, port), make_handler(workspace))
    actual_host, actual_port = server.server_address
    display_host = "127.0.0.1" if actual_host in ("0.0.0.0", "") else actual_host
    url = f"http://{display_host}:{actual_port}/"
    print(f"Serving repo-manager UI at {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped repo-manager UI.")
    finally:
        server.server_close()


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>repo-manager</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-2: #f1f4f7;
      --text: #17202a;
      --muted: #647385;
      --line: #d7dde5;
      --accent: #176b87;
      --accent-2: #0f766e;
      --danger: #b42318;
      --warn: #9a5b00;
      --ok: #14743f;
      --shadow: 0 1px 2px rgba(16, 24, 40, 0.06);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    * { box-sizing: border-box; }
    html {
      height: 100%;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
      height: 100%;
      overflow: hidden;
    }
    button, input, select {
      font: inherit;
    }
    .shell {
      height: 100vh;
      display: grid;
      grid-template-columns: 280px 1fr;
      overflow: hidden;
    }
    aside {
      background: #15202b;
      color: #eef4f8;
      padding: 20px 16px;
      border-right: 1px solid #0c141c;
      display: flex;
      flex-direction: column;
      min-height: 0;
      overflow: hidden;
    }
    .brand {
      font-weight: 700;
      font-size: 18px;
      margin-bottom: 4px;
    }
    .repo {
      color: #b9c7d3;
      font-size: 13px;
      word-break: break-word;
    }
    .metrics {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin: 18px 0;
    }
    .metric {
      background: rgba(255,255,255,0.08);
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 6px;
      padding: 10px;
    }
    .metric strong {
      display: block;
      font-size: 20px;
      line-height: 1.1;
    }
    .metric span {
      color: #b9c7d3;
      font-size: 12px;
    }
    nav {
      display: grid;
      gap: 6px;
      margin-top: 14px;
    }
    .sidebar-stats {
      margin-top: auto;
      padding-top: 18px;
      display: grid;
      gap: 10px;
    }
    .sidebar-stat {
      border-top: 1px solid rgba(255,255,255,0.12);
      padding-top: 10px;
    }
    .sidebar-stat span {
      display: block;
      color: #b9c7d3;
      font-size: 12px;
      margin-bottom: 3px;
    }
    .sidebar-stat strong {
      color: #ffffff;
      font-size: 14px;
      font-weight: 650;
    }
    .sidebar-stat small {
      display: block;
      color: #b9c7d3;
      font-size: 12px;
      margin-top: 3px;
    }
    .nav-button {
      border: 0;
      width: 100%;
      text-align: left;
      color: #dce7ee;
      background: transparent;
      border-radius: 6px;
      padding: 10px 12px;
      cursor: pointer;
    }
    .nav-button.active {
      color: #ffffff;
      background: rgba(255,255,255,0.14);
    }
    main {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }
    header {
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 14px 20px;
      display: flex;
      align-items: center;
      gap: 12px;
      justify-content: space-between;
    }
    h1 {
      font-size: 20px;
      margin: 0;
      letter-spacing: 0;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .search {
      width: min(420px, 42vw);
      min-width: 220px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: #fff;
    }
    .content {
      min-width: 0;
      min-height: 0;
      padding: 18px 20px 28px;
      overflow: hidden;
    }
    .split {
      height: 100%;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(420px, 0.92fr) minmax(360px, 1.08fr);
      gap: 16px;
      align-items: stretch;
    }
    .single {
      height: 100%;
      min-height: 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      align-items: stretch;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }
    .panel-head {
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .panel-head h2 {
      font-size: 15px;
      margin: 0;
    }
    .pr-toggle {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      white-space: nowrap;
      cursor: pointer;
    }
    .pr-toggle input {
      accent-color: var(--accent, #6b8afd);
      margin: 0;
    }
    .pr-toggle input[type="text"] {
      width: 110px;
      font: inherit;
      font-size: 12px;
      padding: 2px 6px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: transparent;
      color: inherit;
    }
    .table-wrap {
      overflow: auto;
      min-height: 0;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th {
      position: sticky;
      top: 0;
      background: var(--panel-2);
      color: #435366;
      font-size: 12px;
      text-transform: uppercase;
      z-index: 1;
    }
    tr {
      cursor: pointer;
    }
    tbody tr:hover {
      background: #f6fafb;
    }
    tbody tr.selected {
      background: #e8f3f6;
    }
    .commit-cell {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      color: #1d5268;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .description {
      overflow: hidden;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
    }
    .read-toggle {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      border: 1px solid #1f77b4;
      background: transparent;
      padding: 0;
      cursor: pointer;
      vertical-align: middle;
    }
    .read-toggle.unread {
      background: #1f77b4;
    }
    .read-toggle:focus-visible {
      outline: 2px solid #83c5f3;
      outline-offset: 2px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      height: 22px;
      border-radius: 999px;
      padding: 0 8px;
      font-size: 12px;
      font-weight: 650;
      border: 1px solid transparent;
      white-space: nowrap;
    }
    .badge.clean, .badge.ready {
      color: var(--ok);
      background: #e9f7ef;
      border-color: #bfe7d0;
    }
    .badge.resolved {
      color: var(--accent-2);
      background: #e6f6f4;
      border-color: #b9e4df;
    }
    .badge.needs-attention {
      color: var(--warn);
      background: #fff5df;
      border-color: #f4d79a;
    }
    .badge.blocker, .badge.blocked, .badge.high {
      color: var(--danger);
      background: #fff0ee;
      border-color: #f4c4bd;
    }
    .pr-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .badge.routine {
      color: var(--ok);
      background: #e9f7ef;
      border-color: #bfe7d0;
    }
    .badge.elevated {
      color: var(--warn);
      background: #fff5df;
      border-color: #f4d79a;
    }
    /* Status colors answer "do I need to act?":
       green = no, gray = someone else is handling it, yellow = yes, red = urgent. */
    .badge.approved, .badge.requests {
      color: var(--ok);
      background: #e9f7ef;
      border-color: #bfe7d0;
    }
    .badge.handled, .badge.waiting {
      color: #5c6470;
      background: #f0f1f3;
      border-color: #d8dbe0;
    }
    .badge.needs-triage, .badge.waiting-for-me {
      color: var(--warn);
      background: #fff5df;
      border-color: #f4d79a;
    }
    .badge.needs-core {
      color: var(--danger);
      background: #fff0ee;
      border-color: #f4c4bd;
    }
    .detail {
      padding: 16px;
      display: grid;
      gap: 14px;
      align-content: start;
      min-height: 0;
      overflow: auto;
    }
    .kv {
      display: grid;
      grid-template-columns: 92px 1fr;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .kv strong {
      color: var(--text);
      font-weight: 600;
    }
    a {
      color: var(--accent);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    .section h3 {
      font-size: 13px;
      text-transform: uppercase;
      color: #435366;
      margin: 0 0 8px;
      letter-spacing: 0;
    }
    .section p {
      margin: 0;
      line-height: 1.5;
    }
    ul {
      margin: 0;
      padding-left: 18px;
    }
    li {
      margin: 4px 0;
      line-height: 1.45;
    }
    .evidence-list {
      display: grid;
      gap: 10px;
    }
    .evidence-item {
      border-left: 3px solid var(--line);
      padding-left: 10px;
    }
    .evidence-item h4 {
      margin: 0 0 4px;
      color: #435366;
      font-size: 12px;
      font-weight: 700;
      text-transform: capitalize;
    }
    .evidence-item p {
      margin: 0;
      line-height: 1.5;
    }
    .muted {
      color: var(--muted);
      font-size: 12px;
    }
    .todo-count {
      color: #27384a;
      font-weight: 650;
      white-space: nowrap;
    }
    .todo-count.done {
      color: var(--muted);
      font-weight: 500;
    }
    .todo-list {
      display: grid;
      gap: 8px;
    }
    .todo-item {
      display: grid;
      grid-template-columns: 18px 1fr;
      gap: 9px;
      align-items: start;
      line-height: 1.45;
    }
    .todo-item input {
      margin-top: 2px;
    }
    .todo-item.done span {
      color: var(--muted);
      text-decoration: line-through;
    }
    .priority {
      color: var(--warn);
      font-weight: 700;
      margin-right: 4px;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 13px;
      line-height: 1.5;
      background: #0f1720;
      color: #e7eef5;
      border-radius: 8px;
      padding: 14px;
      max-height: 100%;
      overflow: auto;
    }
    .empty {
      padding: 18px;
      color: var(--muted);
    }
    .copy {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      padding: 7px 10px;
      cursor: pointer;
    }
    .hidden {
      display: none;
    }
    @media (max-width: 980px) {
      body {
        overflow: auto;
      }
      .shell {
        grid-template-columns: 1fr;
        height: auto;
        min-height: 100vh;
        overflow: visible;
      }
      aside {
        position: static;
        overflow: visible;
      }
      main,
      .content {
        overflow: visible;
      }
      .split {
        grid-template-columns: 1fr;
        height: auto;
      }
      .single {
        height: auto;
      }
      .search {
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <div class="brand">repo-manager</div>
      <div class="repo" id="repo-name"></div>
      <div class="metrics">
        <div class="metric"><strong id="metric-commits">0</strong><span>commit reviews</span></div>
        <div class="metric"><strong id="metric-unread">0</strong><span>unread reviews</span></div>
        <div class="metric"><strong id="metric-todos">0</strong><span>open to-dos</span></div>
      </div>
      <nav>
        <button class="nav-button active" data-view="commits">Commit DB</button>
        <button class="nav-button" data-view="prs">PR Reviews</button>
        <button class="nav-button" data-view="release">Release Review</button>
        <button class="nav-button" data-view="announcement">Announcement</button>
      </nav>
      <div class="sidebar-stats">
        <div class="sidebar-stat"><span>Contributions</span><strong id="stat-contributions">0</strong></div>
        <div class="sidebar-stat"><span>Unique authors</span><strong id="stat-authors">0</strong></div>
        <div class="sidebar-stat"><span>Unique reviewers</span><strong id="stat-reviewers">0</strong></div>
        <div class="sidebar-stat"><span>Date range</span><strong id="stat-days">No reviews</strong><small id="stat-dates"></small></div>
      </div>
    </aside>
    <main>
      <header>
        <h1 id="view-title">Commit DB</h1>
        <div class="toolbar">
          <select class="search" id="tag-select" aria-label="Release tag"></select>
          <input class="search" id="search" placeholder="Filter commits, verdicts, authors, descriptions">
          <button class="copy hidden" id="copy-announcement">Copy announcement</button>
        </div>
      </header>
      <section class="content">
        <div id="view-commits" class="split">
          <section class="panel">
            <div class="panel-head"><h2>Saved Commit Reviews</h2><span class="muted" id="commit-count"></span></div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style="width: 42px;"></th>
                    <th style="width: 48px;">#</th>
                    <th style="width: 132px;">Commit</th>
                    <th style="width: 150px;">Verdict</th>
                    <th style="width: 92px;">To-Dos</th>
                    <th>Description</th>
                    <th style="width: 130px;">Author</th>
                  </tr>
                </thead>
                <tbody id="commit-rows"></tbody>
              </table>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>Commit Review</h2><span class="muted" id="commit-selected"></span></div>
            <div class="detail" id="commit-detail"></div>
          </section>
        </div>

        <div id="view-prs" class="split hidden">
          <section class="panel">
            <div class="panel-head">
              <h2>Saved PR Reviews</h2>
              <label class="muted pr-toggle" title="GitHub login whose perspective the Status column reflects">Status as
                <input type="text" id="pr-viewer" spellcheck="false"></label>
              <label class="muted pr-toggle"><input type="checkbox" id="pr-hide-closed" checked> Hide closed PRs</label>
              <label class="muted pr-toggle"><input type="checkbox" id="pr-hide-non-main" checked> Hide PRs not into main</label>
              <span class="muted" id="pr-count"></span>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style="width: 48px;">#</th>
                    <th style="width: 72px;">PR</th>
                    <th style="width: 118px;">Status</th>
                    <th style="width: 110px;">Attention</th>
                    <th style="width: 70px;">Scope</th>
                    <th>Description</th>
                    <th style="width: 130px;">Author</th>
                  </tr>
                </thead>
                <tbody id="pr-rows"></tbody>
              </table>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>PR Review</h2><span class="muted" id="pr-selected"></span></div>
            <div class="detail" id="pr-detail"></div>
          </section>
        </div>

        <div id="view-release" class="single hidden">
          <section class="panel">
            <div class="panel-head"><h2>Release-Level Review</h2></div>
            <div class="detail" id="release-detail"></div>
          </section>
        </div>

        <div id="view-announcement" class="single hidden">
          <section class="panel">
            <div class="panel-head"><h2>Announcement</h2></div>
            <div class="detail" id="announcement-detail"></div>
          </section>
        </div>
      </section>
    </main>
  </div>

  <script>
    const isStatic = Boolean(window.REPO_MANAGER_STATIC);
    const state = {
      data: null,
      view: "commits",
      selectedTag: "",
      selectedCommit: 0,
      selectedPr: 0,
      selectedRelease: 0,
      selectedAnnouncement: 0,
      filter: "",
      route: {},
      prActionMessage: null,
      hideClosedPrs: true,
      hideNonMainPrs: true,
      prViewer: ""
    };
    let suppressRouteUpdate = false;
    const $ = (id) => document.getElementById(id);

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    function badge(value) {
      const cls = String(value || "unknown").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
      return `<span class="badge ${cls}">${esc(value || "Unknown")}</span>`;
    }

    function statusBadge(row) {
      if (!row.review_status) return `<span class="muted">—</span>`;
      const cls = row.review_status.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
      return `<span class="badge ${cls}" title="${esc(row.review_status_detail || "")}">${esc(row.review_status)}</span>`;
    }

    function shortSha(value) {
      return String(value || "").slice(0, 10);
    }

    function parseRoute() {
      const rawHash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
      const params = new URLSearchParams(rawHash || window.location.search.slice(1));
      const view = params.get("view");
      return {
        view: ["commits", "prs", "release", "announcement"].includes(view) ? view : "",
        tag: params.get("tag") || "",
        commit: params.get("commit") || "",
        pr: params.get("pr") || "",
        releaseHead: params.get("releaseHead") || "",
        announcementHead: params.get("announcementHead") || ""
      };
    }

    function applyRouteFromUrl() {
      const route = parseRoute();
      state.route = route;
      if (!route.view && route.commit) route.view = "commits";
      if (!route.view && route.pr) route.view = "prs";
      if (!route.view && route.releaseHead) route.view = "release";
      if (!route.view && route.announcementHead) route.view = "announcement";
      if (state.data && route.commit && !route.tag) {
        const row = (state.data.commit_reviews || []).find((item) => matchSha(item.commit_sha, route.commit));
        if (row && row.tag_start) route.tag = row.tag_start;
      }
      if (state.data && route.releaseHead && !route.tag) {
        const row = (state.data.release_reviews || []).find((item) => matchSha(item.head_sha, route.releaseHead));
        if (row && row.tag_start) route.tag = row.tag_start;
      }
      if (state.data && route.announcementHead && !route.tag) {
        const row = (state.data.release_announcements || []).find((item) => matchSha(item.head_sha, route.announcementHead));
        if (row && row.tag_start) route.tag = row.tag_start;
      }
      if (route.view) state.view = route.view;
      if (route.tag) state.selectedTag = route.tag;
    }

    function updateRoute() {
      if (suppressRouteUpdate || !state.data) return;
      const params = new URLSearchParams();
      params.set("view", state.view);
      if (state.selectedTag) params.set("tag", state.selectedTag);
      if (state.view === "commits") {
        const row = filteredCommits()[state.selectedCommit] || filteredCommits()[0];
        if (row && row.commit_sha) params.set("commit", row.commit_sha);
      } else if (state.view === "prs") {
        const row = filteredPrs()[state.selectedPr] || filteredPrs()[0];
        if (row && row.pr_number) params.set("pr", String(row.pr_number));
      } else if (state.view === "release") {
        const row = filteredReleases()[state.selectedRelease] || filteredReleases()[0];
        if (row && row.head_sha) params.set("releaseHead", row.head_sha);
      } else if (state.view === "announcement") {
        const row = filteredAnnouncements()[state.selectedAnnouncement] || filteredAnnouncements()[0];
        if (row && row.head_sha) params.set("announcementHead", row.head_sha);
      }
      const next = `#${params.toString()}`;
      if (window.location.hash !== next) {
        history.replaceState(null, "", next);
      }
    }

    function matchSha(value, target) {
      if (!value || !target) return false;
      const full = String(value).toLowerCase();
      const wanted = String(target).toLowerCase();
      return full === wanted || full.startsWith(wanted);
    }

    function scrollSelected(root) {
      requestAnimationFrame(() => {
        const selected = root.querySelector(".selected");
        if (selected) selected.scrollIntoView({ block: "nearest" });
      });
    }

    function asList(items) {
      if (!items || !items.length) return `<p class="muted">None.</p>`;
      return `<ul>${items.map((item) => {
        if (typeof item === "string") return `<li>${esc(item)}</li>`;
        const priority = item.priority ? `${esc(item.priority)}: ` : "";
        const handle = item.handle ? `${esc(item.handle)}: ` : "";
        return `<li>${priority}${handle}${esc(item.text || item.reason || JSON.stringify(item))}</li>`;
      }).join("")}</ul>`;
    }

    function evidenceList(evidence) {
      const entries = Object.entries(evidence || {}).filter(([, value]) => value);
      if (!entries.length) return `<p class="muted">No evidence recorded.</p>`;
      return `<div class="evidence-list">${entries.map(([key, value]) => `
        <div class="evidence-item">
          <h4>${esc(key.replaceAll("_", " "))}</h4>
          <p>${esc(value)}</p>
        </div>
      `).join("")}</div>`;
    }

    function todoCount(row) {
      const total = (row.todo_items || []).length;
      const completed = total - (row.outstanding_todos || 0);
      if (!total) return `<span class="todo-count done">0</span>`;
      return `<span class="todo-count ${completed === total ? "done" : ""}">${completed}/${total}</span>`;
    }

    function displayVerdict(row) {
      const total = (row.todo_items || []).length;
      if (total > 0 && (row.outstanding_todos || 0) === 0) {
        return "Resolved";
      }
      return row.verdict;
    }

    function todoList(items) {
      if (!items || !items.length) return `<p class="muted">None.</p>`;
      return `<div class="todo-list">${items.map((item) => `
        <label class="todo-item ${item.completed ? "done" : ""}">
          <input type="checkbox" data-todo-id="${esc(item.id)}" ${item.completed ? "checked" : ""} ${isStatic ? "disabled" : ""}>
          <span>${item.priority ? `<span class="priority">${esc(item.priority)}</span>` : ""}${esc(item.text)}</span>
        </label>
      `).join("")}</div>`;
    }

    async function setTodo(todoId, completed) {
      if (isStatic) return;
      const response = await fetch("/api/todo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ todo_id: todoId, completed })
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "Failed to update to-do");
      }
      await reloadData();
    }

    async function setReadState(reviewKey, isRead) {
      if (isStatic) return;
      const response = await fetch("/api/read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ review_key: reviewKey, is_read: isRead })
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "Failed to update read status");
      }
      await reloadData();
    }

    async function reloadData() {
      if (isStatic) {
        state.data = window.REPO_MANAGER_STATIC_DATA || {};
        applyRouteFromUrl();
        renderAll();
        return;
      }
      const viewerParam = state.prViewer ? `?viewer=${encodeURIComponent(state.prViewer)}` : "";
      const response = await fetch(`/api/data${viewerParam}`);
      state.data = await response.json();
      const viewerInput = $("pr-viewer");
      if (viewerInput && document.activeElement !== viewerInput) {
        viewerInput.value = state.prViewer || state.data.pr_viewer || "";
      }
      applyRouteFromUrl();
      renderAll();
    }

    function attachTodoHandlers(root) {
      if (isStatic) return;
      root.querySelectorAll("input[data-todo-id]").forEach((checkbox) => {
        checkbox.addEventListener("change", async () => {
          checkbox.disabled = true;
          try {
            await setTodo(checkbox.dataset.todoId, checkbox.checked);
          } catch (error) {
            checkbox.checked = !checkbox.checked;
            checkbox.disabled = false;
            alert(error.message);
          }
        });
      });
    }

    function section(title, body) {
      return `<div class="section"><h3>${esc(title)}</h3>${body}</div>`;
    }

    function field(label, value) {
      return `<div class="kv"><span>${esc(label)}</span><strong>${esc(value || "")}</strong></div>`;
    }

    function linkedField(label, href, value) {
      if (!href || !value) return field(label, value);
      return `<div class="kv"><span>${esc(label)}</span><strong><a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(value)}</a></strong></div>`;
    }

    function filteredCommits() {
      const rows = state.data.commit_reviews || [];
      const query = state.filter.trim().toLowerCase();
      const tagged = state.selectedTag ? rows.filter((row) => row.tag_start === state.selectedTag) : rows;
      if (!query) return tagged;
      return tagged.filter((row) => [
        row.commit_sha, row.verdict, row.summary, row.author, row.verdict_reason, row.tag_start, row.branch
      ].join(" ").toLowerCase().includes(query));
    }

    function prVisible(row) {
      if (state.hideClosedPrs && row.pr_state && row.pr_state !== "OPEN") return false;
      if (state.hideNonMainPrs && row.base_ref && row.base_ref !== "main") return false;
      return true;
    }

    function filteredPrs() {
      const rows = (state.data.pr_reviews || []).filter(prVisible);
      const query = state.filter.trim().toLowerCase();
      if (!query) return rows;
      return rows.filter((row) => [
        String(row.pr_number), row.pr_title, row.summary, row.author, row.attention_level, row.scope_verdict, row.review_status
      ].join(" ").toLowerCase().includes(query));
    }

    function filteredReleases() {
      const rows = state.data.release_reviews || [];
      return state.selectedTag ? rows.filter((row) => row.tag_start === state.selectedTag) : rows;
    }

    function filteredAnnouncements() {
      const rows = state.data.release_announcements || [];
      return state.selectedTag ? rows.filter((row) => row.tag_start === state.selectedTag) : rows;
    }

    function renderShell() {
      const data = state.data;
      const tags = data.tags || [];
      if (state.selectedTag && !tags.includes(state.selectedTag)) {
        state.selectedTag = "";
      }
      if (!state.selectedTag && tags.length) {
        state.selectedTag = tags[0];
      }
      $("tag-select").innerHTML = tags.length ? tags.map((tag) => `<option value="${esc(tag)}" ${tag === state.selectedTag ? "selected" : ""}>${esc(tag)}</option>`).join("") : `<option value="">No tags</option>`;
      const commits = filteredCommits();
      const releases = filteredReleases();
      const announcements = filteredAnnouncements();
      $("repo-name").textContent = `${data.config.repo} · ${data.config.branch || "main"}`;
      $("metric-commits").textContent = commits.length;
      $("metric-unread").textContent = commits.filter((row) => !row.is_read).length;
      $("metric-todos").textContent = commits.reduce((sum, row) => sum + (row.outstanding_todos || 0), 0) + releases.reduce((sum, row) => sum + (row.outstanding_todos || 0), 0);
      $("stat-contributions").textContent = commits.length;
      $("stat-authors").textContent = new Set(commits.map((row) => row.author).filter(Boolean)).size;
      $("stat-reviewers").textContent = new Set(commits.flatMap((row) => row.reviewers || [])).size;
      const range = dateRange(commits);
      $("stat-days").textContent = range.start && range.end ? `${range.days} days` : "No reviews";
      $("stat-dates").textContent = range.start && range.end ? `${range.start} to ${range.end}` : "";
    }

    function dateRange(rows) {
      const dates = rows.map((row) => row.merge_date || row.commit_date || row.reviewed_at).filter(Boolean).map((value) => new Date(value)).filter((date) => !Number.isNaN(date.getTime()));
      if (!dates.length) return {};
      const start = new Date(Math.min(...dates));
      const end = new Date(Math.max(...dates));
      const dayMs = 24 * 60 * 60 * 1000;
      return {
        start: start.toISOString().slice(0, 10),
        end: end.toISOString().slice(0, 10),
        days: Math.floor((Date.UTC(end.getUTCFullYear(), end.getUTCMonth(), end.getUTCDate()) - Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), start.getUTCDate())) / dayMs) + 1
      };
    }

    function renderCommits() {
      const rows = filteredCommits();
      if (state.route.commit) {
        const routedIndex = rows.findIndex((row) => matchSha(row.commit_sha, state.route.commit));
        if (routedIndex !== -1) {
          state.selectedCommit = routedIndex;
        }
      }
      if (rows.length) {
        state.selectedCommit = Math.min(state.selectedCommit, rows.length - 1);
      }
      $("commit-count").textContent = `${rows.length} shown`;
      $("commit-rows").innerHTML = rows.map((row, index) => `
        <tr data-index="${index}" class="${index === state.selectedCommit ? "selected" : ""}">
          <td><button class="read-toggle ${row.is_read ? "read" : "unread"}" data-review-key="${esc(row.review_key)}" data-is-read="${row.is_read ? "true" : "false"}" title="${row.is_read ? "Read" : "Unread"}" aria-label="${row.is_read ? "Read" : "Unread"}" ${isStatic ? "disabled" : ""}></button></td>
          <td>${index + 1}</td>
          <td class="commit-cell" title="${esc(row.commit_sha)}">${esc(shortSha(row.commit_sha))}</td>
          <td>${badge(displayVerdict(row))}</td>
          <td>${todoCount(row)}</td>
          <td><div class="description">${esc(row.summary)}</div></td>
          <td>${esc(row.author || "")}</td>
        </tr>
      `).join("");
      scrollSelected($("commit-rows"));
      $("commit-rows").querySelectorAll("tr").forEach((tr) => {
        tr.addEventListener("click", async () => {
          const index = Number(tr.dataset.index);
          state.selectedCommit = index;
          state.route = {};
          const selected = rows[index];
          if (!isStatic && selected && !selected.is_read) {
            updateRoute();
            await setReadState(selected.review_key, true);
            return;
          }
          renderCommits();
          updateRoute();
        });
      });
      if (!isStatic) {
        $("commit-rows").querySelectorAll(".read-toggle").forEach((button) => {
          button.addEventListener("click", async (event) => {
            event.stopPropagation();
            button.disabled = true;
            try {
              await setReadState(button.dataset.reviewKey, button.dataset.isRead !== "true");
            } catch (error) {
              button.disabled = false;
              alert(error.message);
            }
          });
        });
      }
      const row = rows[state.selectedCommit] || rows[0];
      if (!row) {
        $("commit-detail").innerHTML = `<div class="empty">No commit reviews saved yet.</div>`;
        $("commit-selected").textContent = "";
        return;
      }
      $("commit-selected").textContent = shortSha(row.commit_sha);
      const evidence = row.evidence || {};
      const prUrl = row.pr_number ? `https://github.com/${state.data.config.repo}/pull/${row.pr_number}` : "";
      $("commit-detail").innerHTML = [
        field("Commit", row.commit_sha),
        linkedField("PR", prUrl, row.pr_number ? `#${row.pr_number}` : ""),
        field("Author", row.author),
        row.merge_date ? field("Merged", row.merge_date) : "",
        field("Audited", row.reviewed_at),
        generatedInField(row),
        section("Description", `<p>${esc(row.summary)}</p>`),
        section("Verdict", `<p>${badge(displayVerdict(row))} ${esc(row.verdict_reason || "")}</p>`),
        section("Maintainer To-Do", todoList(row.todo_items)),
        section("Shout Outs", asList(row.shout_outs)),
        section("Evidence", evidenceList(evidence))
      ].join("");
      attachTodoHandlers($("commit-detail"));
    }

    async function runPrAction(path, prNumber) {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pr_number: prNumber })
      });
      let result = {};
      try {
        result = await response.json();
      } catch (error) {
        result = {};
      }
      if (!response.ok || !result.ok) {
        throw new Error(result.error || "Action failed");
      }
      return result;
    }

    function describePrActionResult(kind, result) {
      if (kind === "comment") {
        const action = result.action === "updated" ? "Updated" : "Posted";
        return `${action} review comment${result.url ? `: ${result.url}` : "."}`;
      }
      const parts = [];
      if ((result.requested || []).length) parts.push(`Requested: ${result.requested.join(", ")}`);
      (result.skipped || []).forEach((item) => parts.push(`Skipped ${item.handle} (${item.reason})`));
      (result.failed || []).forEach((item) => parts.push(`Failed ${item.handle}: ${item.reason}`));
      (result.warnings || []).forEach((warning) => parts.push(warning));
      return parts.join(" · ") || "No reviewers left to request.";
    }

    function prActions(row) {
      const disabled = isStatic ? "disabled" : "";
      const message = state.prActionMessage && state.prActionMessage.pr === row.pr_number
        ? `<p class="muted" id="pr-action-result">${esc(state.prActionMessage.text)}</p>`
        : `<p class="muted hidden" id="pr-action-result"></p>`;
      const commentLink = row.comment_url
        ? `<p class="muted">Posted comment: <a href="${esc(row.comment_url)}" target="_blank" rel="noopener noreferrer">${esc(row.comment_url)}</a></p>`
        : "";
      return `
        <div class="pr-actions">
          <button class="copy" id="pr-post-comment" data-pr="${row.pr_number}" ${disabled}>Post review comment</button>
          <button class="copy" id="pr-request-reviewers" data-pr="${row.pr_number}" ${disabled}>Request reviewers</button>
        </div>
        ${message}
        ${commentLink}`;
    }

    function attachPrActionHandlers(row) {
      if (isStatic) return;
      const bindings = [
        ["pr-post-comment", "/api/pr-comment", "comment"],
        ["pr-request-reviewers", "/api/pr-reviewers", "reviewers"]
      ];
      bindings.forEach(([id, path, kind]) => {
        const button = $(id);
        if (!button) return;
        button.addEventListener("click", async () => {
          button.disabled = true;
          const original = button.textContent;
          button.textContent = "Working...";
          try {
            const result = await runPrAction(path, Number(button.dataset.pr));
            const warnings = (result.warnings || []).length && kind === "comment"
              ? ` (${result.warnings.join("; ")})` : "";
            state.prActionMessage = {
              pr: Number(button.dataset.pr),
              text: describePrActionResult(kind, result) + warnings
            };
            await reloadData();
          } catch (error) {
            button.disabled = false;
            button.textContent = original;
            alert(error.message);
          }
        });
      });
    }

    const ATTENTION_MEANINGS = {
      "High": "a core maintainer should look at this before it merges",
      "Elevated": "any reviewer can take it, but the to-dos below need resolving before approval",
      "Routine": "nothing flagged; a standard review pass is enough"
    };

    function attentionSection(row) {
      const meaning = ATTENTION_MEANINGS[row.attention_level] || "";
      const reasons = (row.attention_reasons || []).join("; ");
      return `<p>${badge(row.attention_level)} ${esc(meaning)}${reasons ? ` <span class="muted">(${esc(reasons)})</span>` : ""}</p>`;
    }

    function generatedInField(row) {
      const seconds = Number(row.generation_seconds || (row.details || {}).generation_seconds || 0);
      if (!seconds || seconds <= 0) return "";
      return field("Generated in", `${Math.round(seconds)} seconds`);
    }

    function basisNote(evidence, key) {
      const value = (evidence || {})[key];
      return value ? `<p class="muted">Checked: ${esc(value)}</p>` : "";
    }

    function todoItems(items) {
      return `<ul class="todo-issues">${items.map((item) => {
        const subs = (item.subs || []).filter(([, value]) => value)
          .map(([label, value]) => `<li><span class="muted">${esc(label)}:</span> ${esc(value)}</li>`).join("");
        return `<li>☐ <strong>${esc(item.action || item.fallback || "")}</strong>${subs ? `<ul>${subs}</ul>` : ""}</li>`;
      }).join("")}</ul>`;
    }

    function descriptionCheckSection(check, evidence) {
      if (!check || !check.verdict) return `<p class="muted">Not assessed by this review.</p>`;
      const missingTodo = check.verdict === "missing" && !(check.discrepancies || []).length
        ? todoItems([{ action: "Ask the author to describe the change — the PR has no usable description.", subs: [] }])
        : "";
      const todos = (check.discrepancies || []).length ? todoItems(check.discrepancies.map((item) => ({
        action: item.action,
        fallback: "Reconcile the description with the diff.",
        subs: [["Described", item.described], ["In the diff", item.actual], ["Reference", item.evidence]]
      }))) : "";
      return `<p>${badge(check.verdict)} ${esc(check.notes || "")}</p>${missingTodo}${todos}`;
    }

    function alignmentList(flags, evidence) {
      if (!flags || !flags.length) return `<p class="muted">None found.</p>` + basisNote(evidence, "alignment");
      return todoItems(flags.map((flag) => {
        const where = [flag.doc, flag.section].filter(Boolean).join(" — ");
        const why = where ? `${where}: ${flag.concern || ""}` : (flag.concern || "");
        return {
          action: flag.action,
          fallback: flag.concern,
          subs: [["Why", why], ["Reference", flag.evidence]]
        };
      }));
    }

    function documentationSection(documentation, evidence) {
      const status = (documentation || {}).status || "";
      const gaps = (documentation || {}).gaps || [];
      if (!gaps.length) return `<p>${badge(status || "unknown")}</p>${basisNote(evidence, "documentation")}`;
      return `<p>${badge(status || "unknown")}</p>${todoItems(gaps.map((gap) => ({
        action: gap.action,
        fallback: gap.what,
        subs: [["Gap", gap.what], ["Where", gap.where], ["Why", gap.policy]]
      })))}`;
    }

    function breakingStatus(change) {
      const documented = change.documented ? "documented in this PR" : "not documented in this PR";
      const approval = change.maintainer_approval === "approved"
        ? "maintainer-approved"
        : change.maintainer_approval === "not-approved" ? "not maintainer-approved" : "needs maintainer confirmation";
      return `${documented}; ${approval}`;
    }

    function breakingList(changes, evidence) {
      if (!changes || !changes.length) return `<p class="muted">None found.</p>` + basisNote(evidence, "breaking_changes");
      const items = changes.map((change) => {
        const summary = `(${change.surface || ""}) ${change.change || ""}`;
        if (change.documented === true && change.maintainer_approval === "approved") {
          return `<li>${esc(summary)} — ${esc(breakingStatus(change))}</li>`;
        }
        return `<li>☐ <strong>${esc(change.action || "Document this break and get a maintainer sign-off.")}</strong>
          <ul><li>${esc(summary)}</li><li>${esc(breakingStatus(change))}</li></ul></li>`;
      }).join("");
      return `<ul class="todo-issues">${items}</ul>`;
    }

    function reviewerList(reviewers, areas, evidence) {
      const items = [];
      (reviewers || []).forEach((item) => {
        const handle = String(item.handle || "").replace(/^@/, "");
        const area = item.subject_area ? ` (${esc(item.subject_area)})` : "";
        const reason = item.reason ? ` — ${esc(item.reason)}` : "";
        items.push(`<li><strong>${esc(handle)}</strong>${area}${reason}</li>`);
      });
      (areas || []).forEach((area) => {
        items.push(`<li class="muted">No maintainer listed for: ${esc(area)}</li>`);
      });
      if (!items.length) return `<p class="muted">None.</p>`;
      return `<ul>${items.join("")}</ul>`;
    }

    function renderPrReviews() {
      const rows = filteredPrs();
      if (state.route.pr) {
        const routedIndex = rows.findIndex((row) => String(row.pr_number) === String(state.route.pr));
        if (routedIndex !== -1) {
          state.selectedPr = routedIndex;
        }
      }
      if (rows.length) {
        state.selectedPr = Math.min(state.selectedPr, rows.length - 1);
      }
      $("pr-count").textContent = `${rows.length} shown`;
      $("pr-rows").innerHTML = rows.map((row, index) => `
        <tr data-index="${index}" class="${index === state.selectedPr ? "selected" : ""}">
          <td>${index + 1}</td>
          <td>#${row.pr_number}</td>
          <td>${statusBadge(row)}</td>
          <td>${badge(row.attention_level)}</td>
          <td>${esc(row.scope_verdict || "")}</td>
          <td><div class="description">${esc(row.pr_title || row.summary || "")}</div></td>
          <td>${esc(row.author || "")}</td>
        </tr>
      `).join("");
      scrollSelected($("pr-rows"));
      $("pr-rows").querySelectorAll("tr").forEach((tr) => {
        tr.addEventListener("click", () => {
          state.selectedPr = Number(tr.dataset.index);
          state.route = {};
          state.prActionMessage = null;
          renderPrReviews();
          updateRoute();
        });
      });
      const row = rows[state.selectedPr] || rows[0];
      if (!row) {
        $("pr-detail").innerHTML = `<div class="empty">Run <code>repo-manager review-pr N</code> or <code>repo-manager sweep-prs</code> to review open PRs.</div>`;
        $("pr-selected").textContent = "";
        return;
      }
      $("pr-selected").textContent = `#${row.pr_number}`;
      const prUrl = `https://github.com/${state.data.config.repo}/pull/${row.pr_number}`;
      const scope = row.scope || {};
      $("pr-detail").innerHTML = [
        linkedField("PR", prUrl, `#${row.pr_number} — ${row.pr_title || ""}`),
        field("Author", row.author),
        row.pr_state ? field("State", `${row.pr_state}${row.base_ref ? ` → ${row.base_ref}` : ""}`) : "",
        field("Head", shortSha(row.head_sha)),
        field("Reviewed", row.reviewed_at),
        generatedInField(row),
        row.review_status ? section("Review Status", `<p>${statusBadge(row)} ${esc(row.review_status_detail || "")}</p>`) : "",
        section("Description", `<p>${esc(row.summary)}</p>`),
        section("Author's Description vs. the Diff", descriptionCheckSection(row.description_check, row.evidence)),
        section("Attention", attentionSection(row)),
        section("Scope", `<p><strong>${esc(scope.verdict || row.scope_verdict || "")}</strong> — ${esc(scope.rationale || "")}</p>`),
        section("Alignment Issues", alignmentList(row.alignment_flags, row.evidence)),
        section("Documentation", documentationSection(row.documentation, row.evidence)),
        section("Breaking Changes", breakingList(row.breaking_changes, row.evidence)),
        section("Suggested Reviewers", reviewerList(row.suggested_reviewers, row.maintainer_needed_areas, row.evidence)),
        section("Actions", prActions(row))
      ].join("");
      attachPrActionHandlers(row);
    }

    function renderRelease() {
      const rows = filteredReleases();
      if (state.route.releaseHead) {
        const routedIndex = rows.findIndex((row) => matchSha(row.head_sha, state.route.releaseHead));
        if (routedIndex !== -1) {
          state.selectedRelease = routedIndex;
        }
      }
      if (rows.length) {
        state.selectedRelease = Math.min(state.selectedRelease, rows.length - 1);
      }
      const row = rows[state.selectedRelease] || rows[0];
      if (!row) {
        $("release-detail").innerHTML = `<div class="empty">Run <code>repo-manager release-review TAG</code> to create one.</div>`;
        return;
      }
      const details = row.details || {};
      $("release-detail").innerHTML = [
        field("Repo", row.repo),
        field("Release", row.tag_start),
        row.range_start ? field("Since", row.range_start) : "",
        field("Head", row.head_sha),
        field("Audited", row.reviewed_at),
        generatedInField(row),
        section("Verdict", `<p>${badge(displayVerdict(row))} ${esc(details.verdict_reason || row.verdict_reason || "")}</p>`),
        section("Prioritized To-Do", todoList(row.todo_items)),
        section("Evidence", evidenceList(details.evidence))
      ].join("");
      attachTodoHandlers($("release-detail"));
    }

    function renderAnnouncement() {
      const rows = filteredAnnouncements();
      if (state.route.announcementHead) {
        const routedIndex = rows.findIndex((row) => matchSha(row.head_sha, state.route.announcementHead));
        if (routedIndex !== -1) {
          state.selectedAnnouncement = routedIndex;
        }
      }
      if (rows.length) {
        state.selectedAnnouncement = Math.min(state.selectedAnnouncement, rows.length - 1);
      }
      const row = rows[state.selectedAnnouncement] || rows[0];
      if (!row) {
        $("announcement-detail").innerHTML = `<div class="empty">Run <code>repo-manager announce TAG</code> to create an announcement.</div>`;
        return;
      }
      $("announcement-detail").innerHTML = [
        field("Release", row.tag_start),
        row.range_start ? field("Since", row.range_start) : "",
        field("Head", row.head_sha),
        field("Generated", row.generated_at),
        generatedInField(row),
        section("Website Release Highlights", `<pre id="release-highlights-markdown">${esc(row.release_highlights_markdown || "No website release highlights artifact saved.")}</pre>`),
        section("Discord Markdown", `<pre id="announcement-markdown">${esc(row.markdown || "")}</pre>`)
      ].join("");
    }

    function setView(view) {
      state.view = view;
      document.querySelectorAll(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
      $("view-commits").classList.toggle("hidden", view !== "commits");
      $("view-prs").classList.toggle("hidden", view !== "prs");
      $("view-release").classList.toggle("hidden", view !== "release");
      $("view-announcement").classList.toggle("hidden", view !== "announcement");
      $("search").classList.toggle("hidden", view !== "commits" && view !== "prs");
      $("copy-announcement").classList.toggle("hidden", view !== "announcement");
      $("view-title").textContent = view === "commits" ? "Commit DB" : view === "prs" ? "PR Reviews" : view === "release" ? "Release Review" : "Announcement";
      updateRoute();
    }

    function renderAll() {
      renderShell();
      renderCommits();
      renderPrReviews();
      renderRelease();
      renderAnnouncement();
      setView(state.view);
    }

    document.querySelectorAll(".nav-button").forEach((button) => {
      button.addEventListener("click", () => {
        state.route = {};
        setView(button.dataset.view);
      });
    });
    $("search").addEventListener("input", (event) => {
      state.filter = event.target.value;
      state.selectedCommit = 0;
      state.selectedPr = 0;
      state.route = {};
      renderCommits();
      renderPrReviews();
      updateRoute();
    });
    $("pr-viewer").addEventListener("change", async (event) => {
      state.prViewer = event.target.value.trim().replace(/^@/, "");
      await reloadData();
    });
    $("pr-hide-closed").addEventListener("change", (event) => {
      state.hideClosedPrs = event.target.checked;
      state.selectedPr = 0;
      renderPrReviews();
      updateRoute();
    });
    $("pr-hide-non-main").addEventListener("change", (event) => {
      state.hideNonMainPrs = event.target.checked;
      state.selectedPr = 0;
      renderPrReviews();
      updateRoute();
    });
    $("tag-select").addEventListener("change", (event) => {
      state.selectedTag = event.target.value;
      state.selectedCommit = 0;
      state.selectedPr = 0;
      state.selectedRelease = 0;
      state.selectedAnnouncement = 0;
      state.route = {};
      renderAll();
    });
    $("copy-announcement").addEventListener("click", async () => {
      const markdown = $("announcement-markdown");
      await navigator.clipboard.writeText(markdown ? markdown.textContent : "");
      $("copy-announcement").textContent = "Copied";
      setTimeout(() => $("copy-announcement").textContent = "Copy announcement", 1000);
    });
    window.addEventListener("hashchange", () => {
      if (!state.data) return;
      suppressRouteUpdate = true;
      applyRouteFromUrl();
      renderAll();
      suppressRouteUpdate = false;
    });

    reloadData()
      .catch((error) => {
        document.body.innerHTML = `<div class="empty">Failed to load repo-manager data: ${esc(error.message)}</div>`;
      });
  </script>
</body>
</html>
"""

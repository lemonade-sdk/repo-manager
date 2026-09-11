import json
import hashlib
import re
import socket
import sqlite3
import subprocess
import threading
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

@contextmanager
def connect(db_file):
    """A connection that commits on the way out and then actually closes.

    sqlite3's own context manager ends the transaction but leaves the connection open, so
    `with connect(...)` leaked a file descriptor per call. That was survivable when the
    dashboard was loaded by hand; a page that polls every thirty seconds and a sync that
    runs behind it reach the process fd limit in hours.
    """
    conn = sqlite3.connect(db_file)
    try:
        conn.row_factory = sqlite3.Row
        ensure_range_schema(conn)
        ensure_pr_schema(conn)
        ensure_pr_state_schema(conn)
        ensure_generation_schema(conn)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

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
    """The triage table. A table from the pre-policy tool (no `label` column) is dropped,
    not migrated: every review in it was judged against a contribution guide that no longer
    exists, and the artifacts on disk keep the history."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(pr_reviews)")}
    if columns and "label" not in columns:
        conn.execute("DROP TABLE pr_reviews")
        conn.execute("DELETE FROM pr_review_comments")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_reviews (
          repo TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          head_sha TEXT NOT NULL DEFAULT '',
          pr_title TEXT NOT NULL DEFAULT '',
          author TEXT NOT NULL DEFAULT '',
          label TEXT NOT NULL DEFAULT '',
          scope TEXT NOT NULL DEFAULT '',
          body_matches_diff TEXT NOT NULL DEFAULT '',
          docs_and_tests TEXT NOT NULL DEFAULT '',
          suggested_reviewers TEXT NOT NULL DEFAULT '[]',
          raw_output TEXT NOT NULL,
          json_path TEXT NOT NULL DEFAULT '',
          reviewed_at TEXT NOT NULL,
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

def ensure_pr_state_schema(conn):
    """A local mirror of what GitHub says about each reviewed PR.

    This table is a cache, not a record: every column is re-derivable from GitHub, nothing
    the reviewer typed lives here, and dropping it costs one sync. That is what lets the
    dashboard read it without hedging — a row is either present and dated, or absent and
    the page says so.

    Kept apart from pr_reviews because the two answer to different clocks. A review is
    written once and stays true; a PR's state is true only as of `fetched_at`, and the
    column that reports it has to be able to say when.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_state (
          repo TEXT NOT NULL,
          pr_number INTEGER NOT NULL,
          state TEXT NOT NULL DEFAULT '',
          base_ref TEXT NOT NULL DEFAULT '',
          head_sha TEXT NOT NULL DEFAULT '',
          is_draft INTEGER NOT NULL DEFAULT 0,
          labels TEXT NOT NULL DEFAULT '[]',
          review_decision TEXT NOT NULL DEFAULT '',
          last_commit_at TEXT NOT NULL DEFAULT '',
          in_merge_queue INTEGER NOT NULL DEFAULT 0,
          auto_merge_by TEXT NOT NULL DEFAULT '',
          reviews TEXT NOT NULL DEFAULT '[]',
          comments TEXT NOT NULL DEFAULT '[]',
          requested TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL DEFAULT '',
          fetched_at TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (repo, pr_number)
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(pr_state)")}
    if columns and "labels" not in columns:
        conn.execute("ALTER TABLE pr_state ADD COLUMN labels TEXT NOT NULL DEFAULT '[]'")
    # One row per repo, holding the two things a sync needs to know before it starts: how
    # far the last one got, and whether it worked. `error` is kept rather than logged
    # because a failed sync has to reach the reader — a dashboard that silently serves
    # week-old state is the failure mode this whole table exists to end.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pr_sync (
          repo TEXT PRIMARY KEY,
          synced_at TEXT NOT NULL DEFAULT '',
          attempted_at TEXT NOT NULL DEFAULT '',
          watermark TEXT NOT NULL DEFAULT '',
          viewer TEXT NOT NULL DEFAULT '',
          error TEXT NOT NULL DEFAULT ''
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

# --- GitHub access ------------------------------------------------------------------
#
# Two shapes of query, and the split between them is the whole point. Asking "what moved?"
# is a connection ordered by update time and costs one point; asking "what is PR #3350's
# review state?" is expensive per PR. The old dashboard only had the second kind and ran it
# over every PR it had ever reviewed on every page load, which at 123 PRs was 74,000 nodes
# and ten seconds — past the ten-second ceiling GitHub enforces, so it failed about half the
# time and took the whole Status column down with it. Detecting change first means the
# expensive query runs over the handful of PRs that actually moved, and usually over none.

# Everything the Status column and the coverage rules read, conversation included. The
# conversation connections are the expensive part — 450 of the 571 nodes per PR — and
# dropping them was tempting, but they are what catches a reply buried in a review thread,
# and chunking turns out to be the real fix: forty PRs with the full set is 24,000 nodes
# and 2.3 seconds, against 123 PRs at 74,000 nodes and a coin-flip failure. The cost was
# never per PR. It was per PR times every review ever written, on every page load.
PR_DETAIL_FIELDS = (
    "state baseRefName reviewDecision isDraft headRefOid updatedAt labels(first: 20) { nodes { name } } "
    "isInMergeQueue autoMergeRequest { enabledBy { login } } "
    "commits(last: 1) { nodes { commit { committedDate } } } "
    "reviews(last: 50) { nodes { author { login } state submittedAt } } "
    "comments(last: 50) { nodes { author { login } createdAt } } "
    "reviewThreads(last: 30) { nodes { comments(last: 15) { nodes { author { login } createdAt } } } } "
    "reviewRequests(first: 20) { nodes { requestedReviewer { ... on User { login } ... on Team { name } } } }"
)

GH_TIMEOUT_SECONDS = 30

class GitHubError(Exception):
    """A sync could not read GitHub. Carries the sentence the dashboard will show."""

def gh_graphql(query):
    """Run one GraphQL query, or raise GitHubError with something a person can read.

    Every failure mode gets checked, because the one this replaces checked none of them:
    a non-zero exit, an unparseable body, and a 200 carrying an `errors` array all used to
    land in the same silent `return {}` that blanked the column. HTTP 502 and 504 are the
    common ones here and they arrive as a non-zero exit with a plain-text body.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise GitHubError("`gh` is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitHubError(f"GitHub did not answer within {GH_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise GitHubError(f"Could not run gh: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise GitHubError(detail[0][:200] if detail else f"gh exited {proc.returncode}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubError(f"GitHub returned a non-JSON response: {exc}") from exc
    if payload.get("errors"):
        first = payload["errors"][0] or {}
        raise GitHubError(str(first.get("message") or "GitHub rejected the query")[:200])
    data = payload.get("data")
    if not isinstance(data, dict):
        raise GitHubError("GitHub returned no data")
    return data

def _login_of(node):
    return ((node or {}).get("author") or {}).get("login", "")

def parse_pr_detail(entry):
    """One GraphQL pullRequest node as the row shape the rest of this module reads."""
    commits = ((entry.get("commits") or {}).get("nodes")) or []
    auto_merge = entry.get("autoMergeRequest") or {}
    reviews = [
        {"login": _login_of(node), "state": node.get("state", ""), "at": node.get("submittedAt", "")}
        for node in ((entry.get("reviews") or {}).get("nodes")) or []
        if _login_of(node)
    ]
    comments = [
        {"login": _login_of(node), "at": node.get("createdAt", ""), "kind": "comment"}
        for node in ((entry.get("comments") or {}).get("nodes")) or []
        if _login_of(node)
    ]
    # Kept apart from conversation comments: a reply inside a review thread is easy to
    # miss on the PR page, so a status that turns on one has to say where to look.
    for thread in ((entry.get("reviewThreads") or {}).get("nodes")) or []:
        comments += [
            {"login": _login_of(node), "at": node.get("createdAt", ""), "kind": "thread"}
            for node in ((thread.get("comments") or {}).get("nodes")) or []
            if _login_of(node)
        ]
    requested = []
    for node in ((entry.get("reviewRequests") or {}).get("nodes")) or []:
        reviewer = (node or {}).get("requestedReviewer") or {}
        handle = reviewer.get("login") or reviewer.get("name") or ""
        if handle:
            requested.append(handle)
    return {
        "state": entry.get("state", ""),
        "base": entry.get("baseRefName", ""),
        "head_sha": entry.get("headRefOid", "") or "",
        "is_draft": bool(entry.get("isDraft")),
        "labels": [node.get("name", "") for node in ((entry.get("labels") or {}).get("nodes")) or [] if node.get("name")],
        "review_decision": entry.get("reviewDecision") or "",
        "last_commit_at": ((commits[0].get("commit") or {}).get("committedDate", "")) if commits else "",
        "in_merge_queue": bool(entry.get("isInMergeQueue")),
        "auto_merge_by": ((auto_merge.get("enabledBy") or {}).get("login") or "") if auto_merge else "",
        "reviews": reviews,
        "comments": comments,
        "requested": requested,
        "updated_at": entry.get("updatedAt", "") or "",
    }

def fetch_pr_details(repo, numbers, chunk=40):
    """Full detail for specific PRs: {number: state dict}, plus the authenticated login.

    Chunked because this is the query that outgrew the timeout. Forty PRs is ~1.3s against
    a ceiling of ten, and a cold rebuild of several hundred reviews walks it in batches
    rather than betting the whole sync on one oversized request.
    """
    owner, _, name = repo.partition("/")
    numbers = sorted({int(number) for number in numbers})
    states, viewer = {}, ""
    for index in range(0, len(numbers), chunk):
        batch = numbers[index:index + chunk]
        fields = " ".join(
            f"pr{number}: pullRequest(number: {number}) {{ {PR_DETAIL_FIELDS} }}" for number in batch
        )
        data = gh_graphql(
            f'query {{ viewer {{ login }} repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'
        )
        viewer = viewer or (data.get("viewer") or {}).get("login", "")
        repository = data.get("repository") or {}
        for number in batch:
            entry = repository.get(f"pr{number}")
            # A null entry is a PR number this repo does not have. Skipping it leaves the
            # stored row alone rather than overwriting a good one with nothing.
            if isinstance(entry, dict):
                states[number] = parse_pr_detail(entry)
    return states, viewer

def fetch_changed_prs(repo, since, page_limit=10):
    """Every PR touched since `since`, newest first — the cheap half of a sync.

    Ordering by UPDATED_AT descending over all states makes this a change log: page until
    a PR older than the watermark shows up and everything after it is older still, so the
    walk stops after one page on a quiet repo. Costs one point per page and returns numbers
    only; deciding which of them are worth a detail fetch is the caller's business.

    Returns (changed, complete). `complete` is False when the walk hit the page limit
    without reaching the watermark, which means the caller should fall back to a full sync
    rather than trust a partial change log.
    """
    owner, _, name = repo.partition("/")
    changed, cursor = {}, None
    for _ in range(page_limit):
        after = f', after: "{cursor}"' if cursor else ""
        data = gh_graphql(
            f'query {{ repository(owner: "{owner}", name: "{name}") {{ pullRequests('
            f"first: 100, orderBy: {{field: UPDATED_AT, direction: DESC}}{after}"
            ") { pageInfo { hasNextPage endCursor } nodes { number updatedAt state } } } }"
        )
        connection = ((data.get("repository") or {}).get("pullRequests")) or {}
        nodes = connection.get("nodes") or []
        for node in nodes:
            updated = node.get("updatedAt") or ""
            if since and updated <= since:
                return changed, True
            changed[int(node["number"])] = updated
        page = connection.get("pageInfo") or {}
        if not page.get("hasNextPage") or not nodes:
            return changed, True
        # With no watermark at all, one page of "what moved recently" is the whole useful
        # answer; paging to the beginning of the repo would just be a slow full sync.
        if not since:
            return changed, True
        cursor = page.get("endCursor")
    return changed, False

# --- The mirror ---------------------------------------------------------------------

TERMINAL_STATES = ("MERGED", "CLOSED")

def read_pr_states(conn, repo, viewer=""):
    """Every mirrored PR for this repo, in the shape derive_review_status expects."""
    states = {}
    for row in conn.execute("SELECT * FROM pr_state WHERE repo=?", (repo,)):
        states[row["pr_number"]] = {
            "state": row["state"],
            "base": row["base_ref"],
            "head_sha": row["head_sha"],
            "is_draft": bool(row["is_draft"]),
            "labels": parse_json_text(row["labels"], []),
            "review_decision": row["review_decision"],
            "last_commit_at": row["last_commit_at"],
            "in_merge_queue": bool(row["in_merge_queue"]),
            "auto_merge_by": row["auto_merge_by"],
            "reviews": parse_json_text(row["reviews"], []),
            "comments": parse_json_text(row["comments"], []),
            "requested": parse_json_text(row["requested"], []),
            "updated_at": row["updated_at"],
            "fetched_at": row["fetched_at"],
            "viewer": viewer,
        }
    return states

def write_pr_states(conn, repo, states, fetched_at=None):
    fetched_at = fetched_at or now_iso()
    for number, info in states.items():
        conn.execute(
            """
            INSERT INTO pr_state (
              repo, pr_number, state, base_ref, head_sha, is_draft, labels, review_decision,
              last_commit_at, in_merge_queue, auto_merge_by, reviews, comments,
              requested, updated_at, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr_number) DO UPDATE SET
              state=excluded.state, base_ref=excluded.base_ref, head_sha=excluded.head_sha,
              is_draft=excluded.is_draft, labels=excluded.labels, review_decision=excluded.review_decision,
              last_commit_at=excluded.last_commit_at, in_merge_queue=excluded.in_merge_queue,
              auto_merge_by=excluded.auto_merge_by, reviews=excluded.reviews,
              comments=excluded.comments, requested=excluded.requested,
              updated_at=excluded.updated_at,
              fetched_at=excluded.fetched_at
            """,
            (
                repo, int(number), info.get("state", ""), info.get("base", ""),
                info.get("head_sha", ""), 1 if info.get("is_draft") else 0,
                json.dumps(info.get("labels", [])),
                info.get("review_decision", ""), info.get("last_commit_at", ""),
                1 if info.get("in_merge_queue") else 0, info.get("auto_merge_by", ""),
                json.dumps(info.get("reviews", [])), json.dumps(info.get("comments", [])),
                json.dumps(info.get("requested", [])),
                info.get("updated_at", ""), fetched_at,
            ),
        )

def read_sync_state(conn, repo):
    row = conn.execute("SELECT * FROM pr_sync WHERE repo=?", (repo,)).fetchone()
    if not row:
        return {"repo": repo, "synced_at": "", "attempted_at": "", "watermark": "", "viewer": "", "error": ""}
    return dict(row)

def write_sync_state(conn, repo, **fields):
    current = read_sync_state(conn, repo)
    current.update({key: value for key, value in fields.items() if value is not None})
    conn.execute(
        """
        INSERT INTO pr_sync (repo, synced_at, attempted_at, watermark, viewer, error)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo) DO UPDATE SET
          synced_at=excluded.synced_at, attempted_at=excluded.attempted_at,
          watermark=excluded.watermark, viewer=excluded.viewer, error=excluded.error
        """,
        (
            repo, current["synced_at"], current["attempted_at"],
            current["watermark"], current["viewer"], current["error"],
        ),
    )

def reviewed_pr_numbers(conn, repo):
    return {
        row["pr_number"]
        for row in conn.execute("SELECT DISTINCT pr_number FROM pr_reviews WHERE repo=?", (repo,))
    }

# One sync per repo at a time. Three open tabs and a Refresh click are one GitHub call,
# not four, and a slow sync never stacks behind itself.
_SYNC_LOCKS = {}
_SYNC_LOCKS_GUARD = threading.Lock()

def sync_lock(repo):
    with _SYNC_LOCKS_GUARD:
        return _SYNC_LOCKS.setdefault(repo, threading.Lock())

PR_SYNC_WAIT_SECONDS = 45

def sync_pr_states(workspace, repo, full=False, numbers=None, wait=False):
    """Bring the mirror up to date with GitHub. Returns a summary of what moved.

    Three passes, cheapest first:

      1. Ask what changed since the watermark. One point, a third of a second, and on a
         quiet repo the answer is nothing and the sync stops here.
      2. Detail-fetch the changed PRs that we hold reviews for — usually none to a handful.
      3. Take in any reviewed PR the mirror has never seen, which is how a review written
         by `sweep-prs` five minutes ago gets its state.

    Terminal PRs are not refreshed on their own account: merged is forever. They still get
    picked up by pass 1 if they move, so a reopened PR is not stranded.

    Cost scales with what changed, not with how many reviews have accumulated — the whole
    reason for the rewrite. A full sync is the fallback when there is no watermark to work
    from, or when the change log was too long to walk.
    """
    if not repo or "/" not in repo:
        return {"ok": False, "error": "No repo configured", "fetched": 0}
    lock = sync_lock(repo)
    # Callers who have something to render do not queue behind a sync in flight; they are
    # already being served from the mirror and the next poll will pick up whatever it
    # brings back. `wait` is for the caller that has nothing to show at all.
    acquired = lock.acquire(timeout=PR_SYNC_WAIT_SECONDS) if wait else lock.acquire(blocking=False)
    if not acquired:
        return {"ok": True, "skipped": "A sync is already running", "fetched": 0}
    started = now_iso()
    try:
        with connect(db_file(workspace)) as conn:
            reviewed = reviewed_pr_numbers(conn, repo)
            sync_state = read_sync_state(conn, repo)
            # Having waited for someone else's sync, the thing we were waiting for has
            # happened. Running a second one back to back would only delay the page that
            # is already able to render.
            if wait and sync_state.get("synced_at"):
                return {"ok": True, "waited": True, "fetched": 0, "changed": 0}
            known = {
                row["pr_number"]: dict(row)
                for row in conn.execute("SELECT * FROM pr_state WHERE repo=?", (repo,))
            }
        if not reviewed:
            with connect(db_file(workspace)) as conn:
                write_sync_state(conn, repo, synced_at=started, attempted_at=started, error="")
            return {"ok": True, "fetched": 0, "changed": 0, "reason": "no saved reviews"}

        watermark = sync_state.get("watermark") or ""
        def stale_reviewed():
            # Everything not already known to be terminal. A merged PR that is already
            # mirrored as merged has nothing left to tell us.
            return {
                number for number in reviewed
                if (known.get(number) or {}).get("state") not in TERMINAL_STATES
            }

        targets, surveyed = set(numbers or ()), ""
        if numbers:
            mode = "targeted"
        elif full or not watermark:
            mode = "full"
            targets = stale_reviewed()
        else:
            mode = "incremental"
            changed, complete = fetch_changed_prs(repo, watermark)
            if complete:
                targets = {number for number in changed if number in reviewed}
                # Everything up to the newest entry in the change log has now been looked
                # at, including the PRs we chose not to fetch because no review names them.
                # Recording that is what keeps the next walk short: without it the same
                # window is re-scanned every time, and it only grows.
                surveyed = max(changed.values(), default=watermark)
            else:
                # The change log ran longer than we are willing to walk, so it cannot be
                # trusted to be complete and the watermark it would imply would be a lie.
                mode = "full"
                targets = stale_reviewed()
        # A review saved since the last sync has no mirrored state at all, whatever the
        # change log said — it may not have been touched on GitHub since it was opened.
        targets |= {number for number in reviewed if number not in known}

        states, viewer = ({}, "")
        if targets:
            states, viewer = fetch_pr_details(repo, targets)
        fetched_at = now_iso()
        # The watermark is a claim that nothing before it went unseen, so only a pass that
        # actually surveyed the repo may move it. A targeted sync — one PR, on its way to
        # writing a comment — proves nothing about any other PR, and advancing the mark on
        # its behalf would strand every change that landed alongside it.
        if mode == "targeted":
            high_water = watermark
        elif mode == "full":
            high_water = max(
                [watermark] + [info.get("updated_at", "") for info in states.values() if info.get("updated_at")]
            )
        else:
            high_water = max(watermark, surveyed)
        with connect(db_file(workspace)) as conn:
            write_pr_states(conn, repo, states, fetched_at)
            write_sync_state(
                conn,
                repo,
                # For the same reason the watermark does not move: `synced_at` is what the
                # freshness label reports about the whole column, and one PR refreshed on
                # its way to a comment does not make the other hundred any newer.
                synced_at=sync_state.get("synced_at") if mode == "targeted" else fetched_at,
                attempted_at=fetched_at,
                watermark=high_water,
                viewer=viewer or sync_state.get("viewer") or "",
                error="",
            )
        return {"ok": True, "mode": mode, "fetched": len(states), "changed": len(targets)}
    except GitHubError as exc:
        # The attempt is recorded and the mirror is left exactly as it was. Stale state
        # with a date on it is worth more than the blank column this replaces.
        with connect(db_file(workspace)) as conn:
            write_sync_state(conn, repo, attempted_at=now_iso(), error=str(exc))
        return {"ok": False, "error": str(exc), "fetched": 0}
    finally:
        lock.release()

PR_SYNC_STALE_SECONDS = 60

def sync_is_stale(sync_state, stale_seconds=PR_SYNC_STALE_SECONDS):
    synced_at = parse_iso_datetime(sync_state.get("synced_at"))
    if not synced_at:
        return True
    return (datetime.now(timezone.utc) - synced_at).total_seconds() >= stale_seconds

def sync_pr_states_async(workspace, repo):
    """Start a sync and do not wait for it. The page is already being served from SQLite."""
    thread = threading.Thread(
        target=sync_pr_states, args=(workspace, repo), name=f"pr-sync:{repo}", daemon=True
    )
    thread.start()
    return thread

def review_coverage(reviewer_logins, requirement, maintainers, approvals=()):
    """Does the review this PR has satisfy what the guide asks: one reviewer, and where
    possible one who lists a subject area the diff lands in.

    The areas come from the triage's facts pass, copied from the maintainer table, so an
    expert here is a table lookup and never an inference. Admin status is not consulted; it
    is a repo permission, not evidence that a person knows this code.
    """
    areas = [str(a).strip() for a in (requirement or {}).get("areas") or [] if str(a).strip()]
    reviewers = [login.lower() for login in reviewer_logins]
    experts = {}
    for login in reviewers:
        covered = covers_any_area(maintainers.get(login), areas)
        if covered:
            experts[login] = covered
    approving_maintainers = sorted(
        login for login in {str(login).lstrip("@").lower() for login in approvals} if login in maintainers
    )
    return {
        "count": len(reviewers),
        "needed": 1,
        "experts": experts,
        "areas": areas,
        "approving_maintainers": approving_maintainers,
    }

def expert_names(coverage):
    """Who covers the subject area, as "login (area, area)", or "" if nobody."""
    return ", ".join(
        f"{login} ({', '.join(areas)})" for login, areas in sorted(coverage["experts"].items())
    )

STANDING_VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")

def latest_reviews(info):
    """{login: where each person currently stands} — their verdict if they gave one.

    A comment-verdict review says a person spoke, not what they decided: it fills a slot
    only for someone who has not decided anything yet, and never overwrites one. This is
    GitHub's own model — its reviewDecision survives a later comment too.
    """
    verdicts, remarks = {}, {}
    for review in sorted(info.get("reviews", []), key=lambda review: review.get("at") or ""):
        login = review["login"].lower()
        if review.get("state") in STANDING_VERDICTS:
            verdicts[login] = review
        elif review.get("state") == "COMMENTED":
            remarks[login] = review
    latest = dict(remarks)
    latest.update(verdicts)
    return latest

def derive_review_status(info, author, viewer_override="", requirement=None, maintainers=None):
    """Does this PR need something from me? Four answers, from my perspective.

    **Merge** (it is done, I merge it), **Review** (my turn), **Needs reviewer** (nobody is
    on the hook), and **In progress** (someone else is on the hook — another reviewer, or
    the author). The tooltip keeps the detail.

    Returns ('', '') when the PR is not open or live data is missing.
    """
    if not info or info.get("state") != "OPEN":
        return "", ""
    viewer = str(viewer_override or info.get("viewer") or "").lstrip("@").lower()
    author_login = str(author or "").lstrip("@").lower()
    table = maintainers or {}
    latest = latest_reviews(info)

    # A PR that carries rfc:required is waiting on a discussion, not on a reviewer. Nothing
    # about who has or has not reviewed it changes that, so it is answered before any of
    # the reviewer arithmetic below.
    if "rfc:required" in (info.get("labels") or []):
        return "Waiting for RFC", "Labeled rfc:required; review waits for an approved RFC."
    if info.get("in_merge_queue"):
        return "In progress", "In the merge queue."
    if info.get("auto_merge_by"):
        return "In progress", f"Auto-merge enabled by {info['auto_merge_by']}; merges when checks pass."

    def counts(login):
        return login != author_login and not is_ai_reviewer(login)

    reviewed = {
        login for login, review in latest.items()
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED") and counts(login)
    }
    approvals = sorted(
        login for login, review in latest.items()
        if review.get("state") == "APPROVED" and counts(login)
    )
    requested = {
        str(handle).lstrip("@").lower() for handle in info.get("requested") or []
        if handle and counts(str(handle).lstrip("@").lower())
    }
    engaged = {
        login for login, review in latest.items()
        if review.get("state") == "COMMENTED" and counts(login)
    }

    # --- 1. My turn.
    mine = latest.get(viewer)
    if mine and mine.get("state") == "CHANGES_REQUESTED":
        my_time = mine.get("at") or ""
        pushed_at = info.get("last_commit_at") or ""
        if pushed_at and pushed_at > my_time:
            return "Review", f"Author pushed {pushed_at.replace('T', ' ')[:16]} UTC, after your change request."
        replies = [
            comment for comment in info.get("comments", [])
            if (comment.get("login") or "").lower() == author_login
            and (comment.get("at") or "") > my_time
        ]
        detail = "Your change request is out; no push since."
        if replies:
            detail += f" {len(replies)} repl{'y' if len(replies) == 1 else 'ies'}, no code change."
        return "In progress", detail
    if viewer and viewer in requested and viewer not in reviewed:
        return "Review", "You are a requested reviewer."

    # --- 2. Blocked on the author.
    blocking = sorted(
        login for login in reviewed
        if (latest.get(login) or {}).get("state") == "CHANGES_REQUESTED"
    )
    if blocking:
        return "In progress", f"{', '.join(blocking)} requested changes."

    # --- 3. Done: an approval. The expert is noted, not required — the guide says
    # "whenever possible", which is a wish for the reviewer to weigh, not a gate.
    coverage = review_coverage(sorted(reviewed), requirement, table, approvals)
    if approvals:
        experts = expert_names(coverage)
        note = f" Subject area covered by {experts}." if experts else (
            f" Nobody approving lists {', '.join(coverage['areas'])}." if coverage["areas"] else ""
        )
        return "Merge", f"Approved by {', '.join(approvals)}.{note}"

    # --- 4. Is anyone on the hook?
    prospective = sorted(reviewed | requested | engaged)
    if prospective:
        if all(login in engaged and login not in requested for login in prospective):
            return "In progress", f"Reviewed with comments by {', '.join(prospective)}; no verdict yet."
        return "In progress", f"On it: {', '.join(prospective)}."
    return "Needs reviewer", "Nobody is reviewing this PR."


def attention_of(item):
    """One word for the list: `routine` when every answer is the good one, `elevated` when
    any is not. The five answers themselves stay in the detail pane."""
    reasons = []
    if item.get("label") == "rfc:required":
        reasons.append("needs an RFC")
    if item.get("body_matches_diff") == "no":
        reasons.append("body does not match the diff")
    if item.get("docs_and_tests") == "gaps":
        reasons.append("docs or tests have gaps")
    return ("elevated" if reasons else "routine"), "; ".join(reasons) or "label, body, docs and tests all clean"

def requirement_of(data):
    """What the Status column checks a PR's reviewers against: the areas the diff lands in."""
    facts = (data or {}).get("facts") or {}
    return {"areas": facts.get("areas") or []}

def reviewer_coverage(workspace, repo, pr_number, author, requirement):
    """Whether this PR already has a reviewer, and who. Refreshes before it reads, because
    the answer is about to be written into a comment naming people."""
    sync_pr_states(workspace, repo, numbers=[int(pr_number)])
    with connect(db_file(workspace)) as conn:
        info = read_pr_states(conn, repo).get(int(pr_number)) or {}
    maintainers = load_maintainer_context(workspace, repo).get("table", {})
    return coverage_verdict(info, author, requirement, maintainers)

def pr_comment_preview(item):
    """The comment this PR's triage would post, for the dashboard to show verbatim."""
    data = item.get("details") or {}
    if not data:
        return ""
    body = render_comment(data, item.get("head_sha", ""))
    return "\n".join(line for line in body.splitlines() if not line.startswith(COMMENT_MARKER)).strip()

def coverage_verdict(info, author, requirement, maintainers):
    """Counts everyone on the hook, not only everyone who has finished: a requested
    reviewer is staffed on this PR, and naming more candidates will not make them answer."""
    if info.get("state") != "OPEN":
        return {"adequate": False, "who": [], "pending": []}
    author_login = str(author or "").lstrip("@").lower()
    latest = latest_reviews(info)
    reviewing = {
        login for login, review in latest.items()
        if review.get("state") in ("APPROVED", "CHANGES_REQUESTED")
        and login != author_login and not is_ai_reviewer(login)
    }
    requested = {
        str(handle).lstrip("@").lower()
        for handle in info.get("requested") or []
        if handle and not is_ai_reviewer(handle) and str(handle).lstrip("@").lower() != author_login
    }
    who = sorted(set(reviewing) | requested)
    pending = sorted(requested - set(reviewing))
    return {"adequate": bool(who), "who": who, "pending": pending}

def pr_reviews(workspace, pr_viewer=""):
    rows = []
    effective_viewer = str(pr_viewer or "").lstrip("@").strip()
    with connect(db_file(workspace)) as conn:
        comment_urls = {
            (row["repo"], row["pr_number"]): row["comment_url"]
            for row in conn.execute("SELECT repo, pr_number, comment_url FROM pr_review_comments")
        }
        seen = set()
        for row in conn.execute("SELECT rowid, * FROM pr_reviews ORDER BY reviewed_at DESC, pr_number DESC"):
            item = dict(row)
            key = (item["repo"], item["pr_number"])
            if key in seen:
                continue
            seen.add(key)
            data = data_from_row(item) or {}
            item["details"] = data
            item["suggested_reviewers"] = parse_json_text(item.get("suggested_reviewers"), [])
            item["requirement"] = requirement_of(data)
            item["attention"], item["attention_detail"] = attention_of(item)
            item["coverage"] = {"adequate": False, "who": [], "pending": []}
            item["comment_markdown"] = pr_comment_preview(item)
            item["comment_url"] = comment_urls.get(key, "")
            rows.append(item)
    repos = {item["repo"] for item in rows}
    authenticated = ""
    sync_states = {}
    with connect(db_file(workspace)) as conn:
        for repo in repos:
            sync_states[repo] = read_sync_state(conn, repo)
            authenticated = authenticated or sync_states[repo].get("viewer") or ""
        viewer = effective_viewer or authenticated
        for repo in repos:
            states = read_pr_states(conn, repo, viewer)
            maintainers = load_maintainer_context(workspace, repo).get("table", {})
            for item in rows:
                if item["repo"] != repo:
                    continue
                info = states.get(item["pr_number"], {})
                item["pr_state"] = info.get("state", "")
                item["base_ref"] = info.get("base", "")
                item["state_fetched_at"] = info.get("fetched_at", "")
                item["state_known"] = bool(info)
                status, detail = derive_review_status(
                    info, item.get("author"), viewer, requirement=item["requirement"], maintainers=maintainers,
                )
                item["review_status"] = status
                item["review_status_detail"] = detail
                item["pr_labels"] = info.get("labels") or []
                item["pr_is_draft"] = bool(info.get("is_draft"))
                item["coverage"] = coverage_verdict(info, item.get("author"), item["requirement"], maintainers)
    return rows, viewer, sync_states

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

def pr_sync_summary(sync_states):
    """What the page needs to say how fresh it is, and what to do if it is not.

    Collapsed across repos to the worst case, because the reader is being told whether to
    trust the column in front of them and an average would let one stalled repo hide.
    """
    if not sync_states:
        return {"synced_at": "", "error": "", "stale": True, "never": True}
    synced = [state.get("synced_at") or "" for state in sync_states.values()]
    errors = [state.get("error") or "" for state in sync_states.values() if state.get("error")]
    oldest = min(synced) if synced else ""
    return {
        "synced_at": oldest,
        "attempted_at": max((state.get("attempted_at") or "" for state in sync_states.values()), default=""),
        "error": errors[0] if errors else "",
        "stale": any(sync_is_stale(state) for state in sync_states.values()),
        "never": not all(synced),
    }

def app_data(workspace, pr_viewer=""):
    commits = commit_reviews(workspace)
    releases = release_reviews(workspace)
    announcements = release_announcements(workspace)
    prs, effective_viewer, sync_states = pr_reviews(workspace, pr_viewer)
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
    payload = {
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
        "pr_sync": pr_sync_summary(sync_states),
    }
    # A fingerprint of everything except the freshness block, so a poll that finds nothing
    # new can say so and leave the page alone. Without it every poll would redraw the table
    # under the reader's cursor thirty seconds after they stopped touching it, and the only
    # difference between the two payloads would be the clock.
    payload["digest"] = hashlib.sha256(
        json.dumps({key: value for key, value in payload.items() if key != "pr_sync"},
                   sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return payload

def public_app_data(workspace):
    data = app_data(workspace)
    # PR reviews are transient pre-merge advisories and are not round-tripped by
    # sync_down, so they stay out of the published dashboard entirely.
    data.pop("pr_reviews", None)
    data.pop("pr_viewer", None)
    data.pop("pr_sync", None)
    data.pop("digest", None)
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
    from repo_manager import triage

    try:
        repo = load_config(workspace)["repo"]
        if action == "comment":
            return triage.post_comment(workspace, repo, pr_number)
        if action == "label":
            return triage.apply_label(workspace, repo, pr_number)
        return triage.request_reviewers(workspace, repo, pr_number)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc) or "Command failed"}

def configured_repo(workspace):
    try:
        return load_config(workspace).get("repo", "")
    except (OSError, ValueError, KeyError):
        return ""

def serve_app_data(workspace, pr_viewer=""):
    """The dashboard payload, and the decision about whether to go and get fresh facts.

    Stale-while-revalidate, with one exception at the bottom. Reading SQLite takes about
    five milliseconds and always works, so the answer goes out immediately and a sync — if
    the mirror has aged past a minute — runs behind it for the next poll to pick up. The
    reader is never made to wait on GitHub to find out what they already knew.

    The exception is a mirror that has never been filled. There is nothing honest to render
    then, so that one request waits: a first run that flashes an empty dashboard looks
    broken in a way that a first run saying "Syncing…" does not.
    """
    repo = configured_repo(workspace)
    if repo:
        with connect(db_file(workspace)) as conn:
            sync_state = read_sync_state(conn, repo)
            has_mirror = bool(conn.execute(
                "SELECT 1 FROM pr_state WHERE repo=? LIMIT 1", (repo,)
            ).fetchone())
        if not has_mirror and not sync_state.get("synced_at"):
            sync_pr_states(workspace, repo, wait=True)
        elif sync_is_stale(sync_state):
            sync_pr_states_async(workspace, repo)
    return app_data(workspace, pr_viewer)

def run_sync_action(workspace, payload):
    """The Refresh button: the one path that waits for GitHub, because someone asked it to.

    A sync is roughly a second, and the person who clicked is watching the button. Handing
    them the freshly synced payload in the same response is the difference between "it
    refreshed" and "did that do anything?".
    """
    repo = configured_repo(workspace)
    if not repo:
        return {"ok": False, "error": "No repo configured for this workspace"}
    result = sync_pr_states(workspace, repo, full=bool(payload.get("full")))
    viewer = str(payload.get("viewer") or "")[:64]
    result["data"] = app_data(workspace, viewer)
    # A failed sync still answers with data — the mirror it could not refresh is exactly
    # what the reader should keep looking at, now labelled with why it did not move.
    result["ok"] = True
    return result

def make_handler(workspace):
    class RepoManagerHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.send_text(INDEX_HTML, "text/html; charset=utf-8")
            elif path == "/api/data":
                params = parse_qs(urlparse(self.path).query)
                pr_viewer = (params.get("viewer") or [""])[0][:64]
                self.send_json(serve_app_data(workspace, pr_viewer))
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/todo", "/api/read", "/api/pr-comment", "/api/pr-reviewers", "/api/pr-apply-label", "/api/sync"):
                self.send_error(404)
                return
            if path in ("/api/pr-comment", "/api/pr-reviewers", "/api/pr-apply-label"):
                # These act on GitHub with the user's gh credentials, so reject
                # cross-origin requests (browser-set Origin that isn't this server).
                origin = self.headers.get("Origin", "")
                host = self.headers.get("Host", "")
                if origin and urlparse(origin).netloc != host:
                    self.send_json({"ok": False, "error": "Cross-origin request rejected"}, status=403)
                    return
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8") if length else ""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
                return
            if path == "/api/sync":
                result = run_sync_action(workspace, payload)
            elif path == "/api/todo":
                result = update_todo(workspace, payload)
            elif path == "/api/read":
                result = update_read_state(workspace, payload)
            elif path == "/api/pr-comment":
                result = run_pr_action(workspace, "comment", payload)
            elif path == "/api/pr-apply-label":
                result = run_pr_action(workspace, "label", payload)
            else:
                result = run_pr_action(workspace, "reviewers", payload)
            self.send_json(result, status=200 if result.get("ok") else 400)

        # Nothing this server returns is ever safe to reuse: the page embeds the app itself,
        # so a cached copy survives a restart and hides every code change, and the JSON is a
        # live view whose Status column is computed per request. A response cached during a
        # slow `gh` call freezes an empty Status for every PR, and restarting cannot dislodge
        # it — which is exactly how a working dashboard came to show no status at all.
        def send_no_store(self):
            self.send_header("Cache-Control", "no-store, max-age=0")

        def send_json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_no_store()
            self.end_headers()
            self.wfile.write(body)

        def send_text(self, text, content_type):
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_no_store()
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return RepoManagerHandler

def lan_address():
    """Best guess at this machine's LAN address, for the URL printed when binding a wildcard."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No packets are sent; connect() on UDP just picks the outbound interface.
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()

def serve(workspace, host, port, open_browser):
    server = ThreadingHTTPServer((host, port), make_handler(workspace))
    # Started before the browser is, so the walk from `repo-manager ui` to a rendered page
    # usually overlaps the sync rather than following it. Nothing waits on this thread: if
    # the browser wins the race it is served from the mirror as it stands, and the poll a
    # few seconds later picks up whatever this brought back.
    repo = configured_repo(workspace)
    if repo:
        sync_pr_states_async(workspace, repo)
    actual_host, actual_port = server.server_address
    wildcard = actual_host in ("0.0.0.0", "", "::")
    display_host = "127.0.0.1" if wildcard else actual_host
    url = f"http://{display_host}:{actual_port}/"
    print(f"Serving repo-manager UI at {url}")
    if wildcard:
        lan = lan_address()
        if lan:
            print(f"Reachable on the LAN at http://{lan}:{actual_port}/")
        print("Anyone on this network can browse this workspace and act on GitHub as you.")
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
    /* The PR list is the scanning surface and needs room for a description; the review
       beside it is read one at a time and has been sitting on slack. The old split left
       the list 535px against 558px of fixed columns, which squeezed Description to 35px
       and then, once the head stopped overflowing and forcing the pane wider, to nothing. */
    #view-prs.split {
      grid-template-columns: minmax(560px, 1.2fr) minmax(340px, 0.8fr);
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
      /* The PR head carries a title, a perspective, two filters, a count and the sync
         controls, which is more than fits across a split pane. Without wrapping the row
         simply overflows and the panel clips whatever is furthest right — which silently
         ate the freshness label and the Refresh button. */
      flex-wrap: wrap;
      gap: 8px 12px;
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
    /* Freshness sits at the right end of the PR panel head, pushed there so it reads as a
       property of the whole column rather than of any one control beside it. */
    /* Held together on one line and pushed to the right end of whichever row they land
       on, so the freshness reads as a property of the column rather than another filter. */
    .sync-group {
      margin-left: auto;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }
    .sync-state {
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }
    /* A sync that failed is the one thing here allowed to raise its voice: the column is
       still showing its last known answer, and the reader has to know it is not live. */
    .sync-state.stale {
      color: #9a6b00;
    }
    .sync-state.failed {
      color: #b4232a;
      font-weight: 600;
    }
    .sync-button {
      font-size: 12px;
      padding: 4px 9px;
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
    /* Fixed column widths and a fixed layout mean a pane narrower than their sum does not
       shrink the columns — it silently starves whichever column has no width of its own.
       A floor plus the scroll box the wrap already provides is what keeps Description
       readable instead of letting it vanish. */
    #view-prs table {
      min-width: 690px;
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
    .pr-check {
      cursor: pointer;
      vertical-align: middle;
      accent-color: #1f77b4;
    }
    /* A checked-off PR stays in the list but has stopped asking for anything. */
    #pr-rows tr.checked-off td:not(:first-child) {
      opacity: 0.5;
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
    .badge.merge, .badge.approved, .badge.requests {
      color: var(--ok);
      background: #e9f7ef;
      border-color: #bfe7d0;
    }
    .badge.in-progress, .badge.handled, .badge.waiting, .badge.in-discussion, .badge.waiting-for-rfc {
      color: #5c6470;
      background: #f0f1f3;
      border-color: #d8dbe0;
    }
    .badge.review, .badge.needs-reviewer, .badge.needs-triage, .badge.waiting-for-me {
      color: var(--warn);
      background: #fff5df;
      border-color: #f4d79a;
    }
    .badge.needs-core {
      color: var(--danger);
      background: #fff0ee;
      border-color: #f4c4bd;
    }
    /* A PR the author still owes work on, and the check that says why. */
    .badge.not-ready, .badge.bundled {
      color: var(--warn);
      background: #fff5df;
      border-color: #f4d79a;
    }
    .badge.focused, .badge.adequate, .badge.accurate, .badge.none-found {
      color: var(--ok);
      background: #e9f7ef;
      border-color: #bfe7d0;
    }
    .badge.gaps, .badge.discrepancies {
      color: var(--warn);
      background: #fff5df;
      border-color: #f4d79a;
    }
    .badge.missing {
      color: var(--danger);
      background: #fff0ee;
      border-color: #f4c4bd;
    }
    .badge.not-applicable, .badge.unknown, .badge.tone-neutral {
      color: #5c6470;
      background: #f0f1f3;
      border-color: #d8dbe0;
    }
    .badge.tone-ok {
      color: var(--ok);
      background: #e9f7ef;
      border-color: #bfe7d0;
    }
    .badge.tone-warn {
      color: var(--warn);
      background: #fff5df;
      border-color: #f4d79a;
    }
    .badge.tone-road {
      background: #dbe7f5;
      color: #24507f;
    }
    .badge.tone-danger {
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
    .pr-split {
      border: 0;
      border-top: 1px solid var(--line);
      margin: 20px 0 18px;
    }
    .md > * + * {
      margin-top: 10px;
    }
    .md > h4:first-child {
      margin-top: 0;
    }
    .md h4 {
      font-size: 13px;
      text-transform: uppercase;
      color: #435366;
      margin: 18px 0 0;
      letter-spacing: 0;
    }
    .md ul {
      margin: 0;
      padding-left: 20px;
    }
    .md ul ul {
      margin-top: 4px;
      color: #5a6b7f;
    }
    .md li {
      line-height: 1.5;
    }
    /* A checkbox is the marker. The list sits at the text margin, as a to-do list does. */
    .md li.task {
      list-style: none;
      margin-left: -20px;
    }
    /* The comment reads at three weights, and they follow the markup's own nesting rather
       than a list of known headings: the lead verdict, the sections under it, and the blocks
       inside a fold. A fold is subordinate to the section it explains, so it carries no rule
       of its own — a border-top there read as a divider between top-level parts and made
       "Explanation" outrank the "Review suggestion" it belongs to. */
    th.sortable {
      cursor: pointer;
      user-select: none;
    }
    th.sortable:hover {
      color: #1f2a37;
    }
    th.sorted {
      color: #1f2a37;
      font-weight: 700;
    }
    .md-verdict {
      margin-bottom: 14px;
    }
    .md-section {
      font-size: 15px;
      font-weight: 700;
      color: #1f2a37;
      margin-top: 22px;
    }
    .md-subhead {
      font-size: 13px;
      font-weight: 650;
      color: #435366;
    }
    .md details {
      margin-top: 12px;
    }
    .md summary {
      cursor: pointer;
      display: flex;
      align-items: baseline;
      gap: 6px;
      list-style: none;
      width: fit-content;
    }
    .md summary::-webkit-details-marker {
      display: none;
    }
    /* The caret leads the label. It used to be pushed to margin-left:auto, which on a pane
       this wide left it a screen away from the word it discloses. */
    .md summary::before {
      content: "\25b8";
      color: #98a2ae;
      font-size: 10px;
    }
    .md details[open] > summary::before {
      content: "\25be";
    }
    .md details[open] > summary {
      margin-bottom: 8px;
    }
    /* Open, the fold's contents are railed off so they read as inside something. */
    .md details[open] {
      border-left: 2px solid var(--line);
      padding-left: 12px;
      margin-left: 2px;
    }
    .md-fold-label {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #8794a4;
      font-weight: 600;
    }
    .md summary:hover .md-fold-label {
      color: #5a6b7f;
    }
    .md-label {
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.02em;
      color: #5a6b7f;
      font-weight: 650;
    }
    .md-footer {
      border-top: 1px solid var(--line);
      padding-top: 10px;
      margin-top: 18px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    .md code {
      background: rgba(31, 119, 180, 0.08);
      border-radius: 4px;
      padding: 1px 4px;
      font-size: 12px;
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
    .copy:disabled {
      color: #98a2ae;
      background: #f6f7f9;
      cursor: not-allowed;
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
              <label class="muted pr-toggle"><input type="checkbox" id="pr-hide-drafts" checked> Hide draft PRs</label>
              <span class="muted" id="pr-count"></span>
              <span class="sync-group">
                <span class="sync-state" id="pr-sync"></span>
                <button class="copy sync-button" id="pr-refresh" title="Fetch the current state of every reviewed PR from GitHub now">Refresh</button>
              </span>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style="width: 34px;" title="Checked off: stays checked until the PR's Status changes"></th>
                    <th style="width: 72px;" class="sortable" data-sort="pr" title="Sort by PR number">PR</th>
                    <th style="width: 128px;" class="sortable" data-sort="status" title="Sort by how much this needs from you">Status</th>
                    <th style="width: 104px;" class="sortable" data-sort="attention" title="Elevated when the label, body, or docs and tests need attention">Attention</th>
                    <th class="sortable" data-sort="description">Title</th>
                    <th style="width: 130px;" class="sortable" data-sort="author">Author</th>
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

    // What this browser remembers about how I left the dashboard: which PRs I have checked
    // off, and how I had the list sorted. Neither is a fact about the repo, so neither is
    // worth a round trip to the server or a row in the workspace database.
    const PR_CHECKS_KEY = "repo-manager:pr-checks";
    const PR_SORT_KEY = "repo-manager:pr-sort";

    function readStored(key, fallback) {
      try {
        const stored = JSON.parse(window.localStorage.getItem(key));
        return stored && typeof stored === "object" ? stored : fallback;
      } catch (error) {
        return fallback;
      }
    }

    function writeStored(key, value) {
      try {
        window.localStorage.setItem(key, JSON.stringify(value));
      } catch (error) {
        // Storage blocked or full. The page still remembers while it is open.
      }
    }

    // Read once, at startup, and authoritative from then on: a browser that refuses to
    // store any of this must still behave normally for as long as the page is open.
    const prChecks = readStored(PR_CHECKS_KEY, {});

    // A column the table no longer offers is not a sort order, so it is forgotten along
    // with its direction rather than leaving the list sorted by nothing in particular.
    function readPrSort() {
      const stored = readStored(PR_SORT_KEY, {});
      const columns = Array.from(
        document.querySelectorAll("#view-prs th.sortable"), (th) => th.dataset.sort
      );
      return columns.includes(stored.key)
        ? { key: stored.key, desc: Boolean(stored.desc) }
        : { key: "status", desc: false };
    }

    const rememberedSort = readPrSort();

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
      hideDraftPrs: true,
      prSort: rememberedSort.key,
      prSortDesc: rememberedSort.desc,
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

    // The five answers are one-word verdicts; colour says which way each one points.
    function verdictBadge(value, kind) {
      const v = String(value || "");
      let tone = "neutral";
      if (kind === "label") tone = v === "rfc:required" ? "danger" : v === "rfc:on-roadmap" ? "road" : v ? "ok" : "neutral";
      if (kind === "scope") tone = /^(fix|working-group|rfc)\b/.test(v) ? "ok" : v ? "warn" : "neutral";
      if (kind === "yesno") tone = v === "yes" ? "ok" : v === "no" ? "warn" : "neutral";
      if (kind === "quality") tone = v === "ok" ? "ok" : v === "gaps" ? "warn" : "neutral";
      if (kind === "attention") tone = v === "routine" ? "ok" : v === "elevated" ? "warn" : "neutral";
      return `<span class="badge tone-${tone}">${esc(v || "—")}</span>`;
    }

    function statusBadge(row) {
      if (!row.review_status) {
        // Three different silences, and only one of them is a dash. A merged or closed PR
        // genuinely needs nothing. A PR the mirror has not reached yet needs an unknown
        // amount, and drawing that as "nothing needed" is the exact lie this rewrite was
        // written to stop telling.
        if (row.state_known === false) {
          return `<span class="badge tone-neutral" title="Not fetched from GitHub yet">syncing…</span>`;
        }
        const settled = row.pr_state ? `${row.pr_state.toLowerCase()} — nothing needed` : "";
        return `<span class="muted" title="${esc(settled)}">—</span>`;
      }
      // "Approved (1/2)" is not the green "Approved": the rung still wants someone.
      if (/\(\d+\/\d+\)/.test(row.review_status)) {
        return `<span class="badge tone-neutral" title="${esc(row.review_status_detail || "")}">${esc(row.review_status)}</span>`;
      }
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

    // --- Freshness ------------------------------------------------------------------
    //
    // The page reads a local mirror of GitHub, so it always has an answer and the only
    // open question is how old that answer is. Everything below exists to keep that
    // question answered on screen, and to keep asking it while someone is looking.

    // Polling is a local read; the server decides whether a poll is worth a GitHub call.
    // Thirty seconds is short enough that a review landing while you watch shows up on its
    // own, and the digest means a poll that changes nothing costs a request and no redraw.
    const PR_POLL_MS = 30000;
    let pollTimer = null;
    let syncing = false;

    function agoLabel(iso) {
      const then = Date.parse(iso || "");
      if (!Number.isFinite(then)) return "";
      const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
      if (seconds < 60) return `${seconds}s ago`;
      if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
      if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
      return `${Math.round(seconds / 86400)}d ago`;
    }

    // Redrawn on a one-second timer as well as on every load, because "synced 4s ago" that
    // stays 4s ago is worse than no label at all — it is a claim about right now that
    // quietly stops being true.
    function renderSyncState() {
      const el = $("pr-sync");
      if (!el || isStatic) return;
      const sync = (state.data && state.data.pr_sync) || {};
      el.classList.remove("stale", "failed");
      if (syncing) {
        el.textContent = "syncing…";
        el.title = "Fetching the current state of every reviewed PR from GitHub";
        return;
      }
      if (!sync.synced_at) {
        el.classList.add("stale");
        el.textContent = "not synced yet";
        el.title = "No PR state has been fetched from GitHub yet.";
        return;
      }
      const ago = agoLabel(sync.synced_at);
      if (sync.error) {
        el.classList.add("failed");
        el.textContent = `synced ${ago} · GitHub unreachable`;
        el.title = `The last sync attempt failed: ${sync.error}\nThe Status column is showing the last state fetched successfully.`;
        return;
      }
      el.textContent = `synced ${ago}`;
      el.title = `PR state fetched from GitHub at ${sync.synced_at}`;
    }

    // A poll that finds the same digest touches nothing. That is what makes it safe to
    // poll at all: the reader's selection, scroll position and half-typed search box are
    // not disturbed thirty seconds after they stopped interacting with the page.
    function applyData(payload, options) {
      const quiet = Boolean(options && options.quiet);
      const unchanged = quiet && state.data && payload.digest && payload.digest === state.data.digest;
      state.data = payload;
      if (unchanged) {
        renderSyncState();
        return false;
      }
      const viewerInput = $("pr-viewer");
      if (viewerInput && document.activeElement !== viewerInput) {
        viewerInput.value = state.prViewer || state.data.pr_viewer || "";
      }
      // Redrawing the list rewrites its innerHTML, which resets the scroll box to the top.
      // On a poll nobody asked for, that would drag the reader back up the table.
      const wrap = document.querySelector("#view-prs .table-wrap");
      const scrollTop = wrap ? wrap.scrollTop : 0;
      applyRouteFromUrl();
      renderAll();
      if (wrap && quiet) wrap.scrollTop = scrollTop;
      renderSyncState();
      return true;
    }

    async function reloadData(options) {
      if (isStatic) {
        state.data = window.REPO_MANAGER_STATIC_DATA || {};
        applyRouteFromUrl();
        renderAll();
        return;
      }
      const viewerParam = state.prViewer ? `?viewer=${encodeURIComponent(state.prViewer)}` : "";
      const response = await fetch(`/api/data${viewerParam}`);
      applyData(await response.json(), options);
    }

    // Chained rather than an interval, so a slow response can never stack polls behind
    // itself, and hidden tabs stop entirely: nobody is reading, so nothing needs fetching
    // and GitHub does not get asked.
    function schedulePoll() {
      if (isStatic) return;
      clearTimeout(pollTimer);
      if (document.visibilityState !== "visible") return;
      pollTimer = setTimeout(async () => {
        try {
          await reloadData({ quiet: true });
        } catch (error) {
          // Keep showing the last good render. The label already carries the sync's own
          // verdict, and a failed poll is not news worth clearing the screen over.
        }
        schedulePoll();
      }, PR_POLL_MS);
    }

    async function runSync() {
      if (isStatic || syncing) return;
      const button = $("pr-refresh");
      syncing = true;
      if (button) button.disabled = true;
      renderSyncState();
      try {
        const response = await fetch("/api/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ viewer: state.prViewer || "" })
        });
        const result = await response.json();
        syncing = false;
        if (result.data) applyData(result.data, { quiet: true });
      } catch (error) {
        syncing = false;
        await reloadData({ quiet: true }).catch(() => {});
      } finally {
        syncing = false;
        if (button) button.disabled = false;
        renderSyncState();
        schedulePoll();
      }
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
      // Only a mirrored draft is hidden; a PR the sync has not reached is shown, like the
      // other two toggles, rather than silently dropped.
      if (state.hideDraftPrs && row.state_known && row.pr_is_draft) return false;
      return true;
    }

    // Ranked by how much the PR wants from *me*, which is the order the Status column is
    // for: my own turn first, then the ones only I can staff, then everything already in
    // somebody else's hands. Ascending is most-action-first, because that is what a
    // maintainer opening the dashboard is looking for.
    const STATUS_ACTION_RANK = {
      "Review": 0,
      "Merge": 1,
      "Needs reviewer": 2,
      "In progress": 3,
      "Waiting for RFC": 4
    };
    function prSortValue(row, key) {
      switch (key) {
        case "pr": return Number(row.pr_number) || 0;
        case "status": {
          const rank = STATUS_ACTION_RANK[row.review_status];
          // An unknown or absent status sorts with the quiet end rather than the top.
          return rank === undefined ? 90 : rank;
        }
        case "attention": return row.attention === "elevated" ? 0 : 1;
        case "author": return String(row.author || "").toLowerCase();
        case "description": return String(row.pr_title || "").toLowerCase();
        default: return 0;
      }
    }

    function sortPrs(rows) {
      const key = state.prSort;
      if (!key) return rows;
      const dir = state.prSortDesc ? -1 : 1;
      // Sorted on a copy: state.data.pr_reviews is the cached payload, and reordering it
      // in place would make the next render depend on the previous one.
      return rows.slice().sort((a, b) => {
        const av = prSortValue(a, key), bv = prSortValue(b, key);
        if (av < bv) return -1 * dir;
        if (av > bv) return 1 * dir;
        // Ties settle on PR number so the order never wobbles between refreshes.
        return (Number(a.pr_number) || 0) - (Number(b.pr_number) || 0);
      });
    }

    function filteredPrs() {
      const rows = (state.data.pr_reviews || []).filter(prVisible);
      const query = state.filter.trim().toLowerCase();
      if (!query) return sortPrs(rows);
      return sortPrs(rows.filter((row) => [
        String(row.pr_number), row.pr_title, row.author, row.label, row.scope, row.attention, row.review_status
      ].join(" ").toLowerCase().includes(query)));
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
        // Not the URL: the reload that follows puts the same link in the "Posted comment"
        // line right below this one, and the two together read as two comments.
        return result.action === "updated" ? "Updated the review comment." : "Posted the review comment.";
      }
      if (kind === "label") {
        const steps = { label: "applied the label", draft: "marked as draft", message: "posted the RFC request" };
        const done = (result.done || []).map((step) => steps[step] || step);
        return `${result.label}: ${done.join(", ") || "already applied, nothing to do"}.`;
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
        ? `<p class="muted">Comment on GitHub: <a href="${esc(row.comment_url)}" target="_blank" rel="noopener noreferrer">${esc(row.comment_url)}</a></p>`
        : "";
      // A covered rung has no slate below the rule, so the button would be acting on names
      // that are not on screen — and on #3210 one of those names was a reviewer already
      // requested on the PR. The tooltip says who is covering it, so the button explains
      // itself rather than just refusing. `repo-manager request-pr-reviewers N` still works:
      // typing the command is an explicit override, clicking a button you cannot read is not.
      // The label button is the same act the GitHub Action will perform on every new PR:
      // apply the triage's rfc: label, and for rfc:required also draft the PR and post the
      // standard request. Once GitHub already shows the label there is nothing left to do.
      // For rfc:required the button stays live: the message step is not visible from the
      // mirror, and every step is idempotent, so a click on a PR that is done says so.
      const labelApplied = row.label !== "rfc:required" && (row.pr_labels || []).includes(row.label);
      const labelButton = row.label === "rfc:required" ? "Request RFC" : `Apply ${row.label}`;
      const labelTitle = labelApplied
        ? `${row.label} is already applied on GitHub`
        : row.label === "rfc:required"
          ? "Apply rfc:required, mark the PR as a draft, and post the standard RFC request (steps already done are skipped)"
          : `Apply the ${row.label} label on GitHub`;
      const covered = (row.coverage || {}).adequate;
      const who = ((row.coverage || {}).who || []).join(", ");
      const reviewersTitle = covered
        ? `Not needed — this PR already has the reviewers its rung asks for${who ? ` (${who})` : ""}`
        : "Request the suggested reviewers on GitHub";
      return `
        <div class="pr-actions">
          <button class="copy" id="pr-post-comment" data-pr="${row.pr_number}" ${disabled}>Post review comment</button>
          <button class="copy" id="pr-request-reviewers" data-pr="${row.pr_number}" title="${esc(reviewersTitle)}" ${disabled || (covered ? "disabled" : "")}>Request reviewers</button>
          <button class="copy" id="pr-apply-label" data-pr="${row.pr_number}" title="${esc(labelTitle)}" ${disabled || (labelApplied ? "disabled" : "")}>${esc(labelButton)}</button>
        </div>
        ${message}
        ${commentLink}`;
    }

    function attachPrActionHandlers(row) {
      if (isStatic) return;
      const bindings = [
        ["pr-post-comment", "/api/pr-comment", "comment"],
        ["pr-request-reviewers", "/api/pr-reviewers", "reviewers"],
        ["pr-apply-label", "/api/pr-apply-label", "label"]
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

    function generatedInField(row) {
      const seconds = Number(row.generation_seconds || (row.details || {}).generation_seconds || 0);
      if (!seconds || seconds <= 0) return "";
      return field("Generated in", `${Math.round(seconds)} seconds`);
    }

    // The comment is markdown, and the pane is a preview of it, so the pane has to render
    // markdown. Only the subset render_pr_review_comment actually emits is handled —
    // headings, checklists, nested notes, and inline emphasis. Anything else falls through
    // as a paragraph, which is the right failure for a preview: it shows the literal text.
    function mdInline(text) {
      return esc(text)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[\s(])\*([^*]+)\*/g, "$1<em>$2</em>")
        .replace(/(^|[\s(])_([^_]+)_/g, "$1<em>$2</em>");
    }

    // The line that leads the comment, and the only pill that keeps its full sentence: a
    // chip reading "not ready" alone, floating above the to-dos, is a status nobody can
    // read without the markdown behind it. The value here is the badge's colour class.
    const MD_VERDICTS = {
      "ready for review": "ready",
      "not ready for review yet": "not-ready"
    };

    // "Focus: <b>focused</b>" — the label stays a label, the verdict becomes the pill the
    // list columns use. On GitHub the same line is bold text; only the rendering differs.
    function mdSummary(text) {
      const parts = text.match(/^(.*?):\s*<b>(.*?)<\/b>(.*)$/);
      // A bare summary ("Explanation") is a control, not a heading — the section it belongs
      // to is the line above it, and a fold that outweighs its own section inverts the page.
      if (!parts) return `<span class="md-fold-label">${mdInline(text)}</span>`;
      return `<span class="md-label">${mdInline(parts[1])}</span> ${mdBadge(parts[2])}${mdInline(parts[3])}`;
    }

    // Section verdicts carry counts ("2 to resolve"), so they cannot each have a class of
    // their own. The pill is coloured by what the verdict means instead: green when there is
    // nothing to do, yellow when there is, red when something is absent that should not be,
    // grey when nobody looked.
    function mdBadge(verdict) {
      const value = verdict.trim().toLowerCase();
      let tone = "neutral";
      if (/^(none found|none|adequate|accurate|focused|all cleared|not-applicable)$/.test(value)) {
        tone = "ok";
      } else if (/(to resolve|^gaps$|discrepancies|bundled|suggested|no maintainer)/.test(value)) {
        tone = "warn";
      } else if (/^missing$/.test(value)) {
        tone = "danger";
      }
      return `<span class="badge tone-${tone}">${esc(verdict)}</span>`;
    }

    // `depth` is how many <details> deep this line sits. Emphasis follows nesting: a bold
    // line at the top level is a section of the comment, the same line inside a fold is a
    // block within one. Reading weight off the markup rather than off a list of known
    // titles is what keeps "Why this rung" from ever outranking "Review suggestion".
    function mdParagraph(line, depth) {
      // The lead verdict keeps its whole sentence inside the pill; every other pill is a
      // one-word answer to a labelled question and does not need one.
      const lead = line.match(/^\*\*([^*]+)\*\*(?:\s*—\s*(.*))?$/);
      if (lead) {
        const tone = MD_VERDICTS[lead[1].trim().toLowerCase()];
        if (tone) {
          return `<p class="md-verdict"><span class="badge ${tone}">${esc(lead[1])}</span> ${mdInline(lead[2] || "")}</p>`;
        }
      }
      // The five triage lines: "**label:** `rfc:required`". The key stays a label and the
      // value becomes the same pill the list columns use.
      const triageLine = line.match(/^\*\*(label|scope|body matches diff|docs and tests):\*\*\s*`([^`]*)`\s*$/);
      if (triageLine) {
        const kind = { "label": "label", "scope": "scope", "body matches diff": "yesno", "docs and tests": "quality" }[triageLine[1]];
        return `<p><span class="md-label">${esc(triageLine[1])}</span> ${verdictBadge(triageLine[2], kind)}</p>`;
      }
      const reviewersLine = line.match(/^\*\*(suggested reviewers):\*\*\s*(.*)$/);
      if (reviewersLine) {
        return `<p><span class="md-label">${esc(reviewersLine[1])}</span> ${mdInline(reviewersLine[2])}</p>`;
      }
      // Inside the Explanation fold each check heads its block as "**Name: verdict**".
      // The verdict is the pill, the name is the label.
      const verdict = line.match(/^\*\*(.*):\s*(.+?)\*\*$/);
      if (verdict) {
        return `<p><span class="md-label">${mdInline(verdict[1])}</span> ${mdBadge(verdict[2])}</p>`;
      }
      // A bold line standing on its own is a heading, and how loud it is depends on where.
      if (lead && !lead[2]) {
        return `<p class="${depth ? "md-subhead" : "md-section"}">${mdInline(lead[1])}</p>`;
      }
      // What was inspected to earn a clean bill is supporting evidence, not a finding.
      if (/^Checked:/.test(line)) {
        return `<p class="muted">${mdInline(line)}</p>`;
      }
      // The AI-assistance footer is end matter: below the review, and below its rule.
      if (/^_AI-assisted triage/.test(line)) {
        return `<p class="md-footer">${mdInline(line)}</p>`;
      }
      return `<p>${mdInline(line)}</p>`;
    }

    function markdown(text) {
      const out = [];
      let depth = 0;        // how many <ul> are open
      let folds = 0;        // how many <details> are open
      let itemOpen = false; // the <li> at the deepest level is not closed yet
      // A nested list belongs inside its parent <li>, so the parent stays open across the
      // deeper level and closes only when that level does.
      const setDepth = (want) => {
        while (depth > want) {
          if (itemOpen) out.push("</li>");
          out.push("</ul>");
          depth--;
          itemOpen = depth > 0;
        }
        while (depth < want) { out.push("<ul>"); depth++; itemOpen = false; }
      };
      const pushItem = (html, cls) => {
        if (itemOpen) out.push("</li>");
        out.push(`<li${cls ? ` class="${cls}"` : ""}>${html}`);
        itemOpen = true;
      };
      for (const raw of String(text || "").split("\n")) {
        const line = raw.replace(/\s+$/, "");
        if (!line.trim()) { setDepth(0); continue; }
        // The comment folds each section into <details>, so the preview does too — same
        // defaults, so a section open on GitHub is open here. These are the only raw HTML
        // tags the comment emits; everything else is still escaped on the way through.
        if (line === "</details>") { setDepth(0); folds = Math.max(0, folds - 1); out.push("</details>"); continue; }
        const fold = line.match(/^<details( open)?><summary>(.*?)<\/summary>(<\/details>)?$/);
        if (fold) {
          setDepth(0);
          out.push(`<details${fold[1] || ""}><summary>${mdSummary(fold[2])}</summary>${fold[3] || ""}`);
          if (!fold[3]) folds++;
          continue;
        }
        const heading = line.match(/^#{1,6}\s+(.*)$/);
        if (heading) { setDepth(0); out.push(`<h4>${mdInline(heading[1])}</h4>`); continue; }
        const item = line.match(/^(\s*)[-*]\s+(.*)$/);
        if (item) {
          setDepth(item[1].length >= 2 ? 2 : 1);
          const task = item[2].match(/^\[([ xX])\]\s+(.*)$/);
          // A checkbox is already a marker; the bullet beside it is a second one.
          pushItem(task
            ? `${task[1].toLowerCase() === "x" ? "\u2611" : "\u2610"} ${mdInline(task[2])}`
            : mdInline(item[2]), task ? "task" : "");
          continue;
        }
        setDepth(0);
        out.push(mdParagraph(line, folds));
      }
      setDepth(0);
      return out.join("");
    }

    // The arrow is drawn in the header text rather than by CSS, so it survives the innerHTML
    // rewrite the table does on every render.
    function markPrSortHeader() {
      document.querySelectorAll('#view-prs th.sortable').forEach((th) => {
        const active = th.dataset.sort === state.prSort;
        th.classList.toggle("sorted", active);
        const label = th.dataset.label || (th.dataset.label = th.textContent.trim());
        th.textContent = active ? `${label} ${state.prSortDesc ? "\u25be" : "\u25b4"}` : label;
      });
    }

    function attachPrSortHandlers() {
      document.querySelectorAll('#view-prs th.sortable').forEach((th) => {
        th.addEventListener("click", () => {
          const key = th.dataset.sort;
          // Same column toggles direction; a new column starts in its natural order, which
          // for status and attention means most-urgent-first.
          if (state.prSort === key) {
            state.prSortDesc = !state.prSortDesc;
          } else {
            state.prSort = key;
            state.prSortDesc = false;
          }
          writeStored(PR_SORT_KEY, { key: state.prSort, desc: state.prSortDesc });
          state.selectedPr = 0;
          renderPrReviews();
        });
      });
    }

    // Checking a box writes one key to the store and draws the checkmark, and that is the
    // entire transaction — no request, no refetch, no redraw.
    //
    // Every workspace on this machine serves its dashboard from the same localhost origin
    // and so shares one store, which is why the repo belongs in the key.
    function prCheckKey(row) {
      return `${row.repo}|${row.pr_number}`;
    }

    // A check-off also goes stale by sitting there: after a week I have stopped carrying
    // the PR around in my head, so the box stops claiming I have, whatever GitHub has been
    // doing meanwhile.
    const PR_CHECK_TTL_MS = 7 * 24 * 60 * 60 * 1000;

    // A check-off says "I have dealt with this PR as it stands", so it is stored with the
    // Status it was made against and the time it was made, and lasts only as long as both
    // hold. Two things that look like a Status change are not: a blank Status is gh
    // telling us nothing rather than telling us something new, and a Status written from
    // another perspective is the "Status as" box being retyped, which relabels the whole
    // column without anything having happened on GitHub. Neither may sweep the checkmarks
    // away; the week runs regardless.
    function reconcilePrChecks(rows) {
      const viewer = state.data.pr_viewer || "";
      const now = Date.now();
      const live = new Set();
      let changed = false;
      for (const row of rows) {
        const key = prCheckKey(row);
        live.add(key);
        const entry = prChecks[key];
        if (!entry) continue;
        const status = row.review_status || "";
        const moved = status && entry.viewer === viewer && entry.status !== status;
        // Written as a failure to be recent, so an entry with no readable time on it —
        // one stored before check-offs were timed — is expired rather than immortal.
        const expired = !(now - Date.parse(entry.at) < PR_CHECK_TTL_MS);
        if (moved || expired) {
          delete prChecks[key];
          changed = true;
        }
      }
      // A review this dashboard no longer holds has nothing left to check off. Another
      // workspace's rows are simply absent here, so only these repos are pruned.
      const repos = new Set(rows.map((row) => row.repo));
      for (const key of Object.keys(prChecks)) {
        if (repos.has(key.slice(0, key.lastIndexOf("|"))) && !live.has(key)) {
          delete prChecks[key];
          changed = true;
        }
      }
      if (changed) writeStored(PR_CHECKS_KEY, prChecks);
    }

    function setPrCheck(row, checked) {
      if (checked) {
        prChecks[prCheckKey(row)] = {
          status: row.review_status || "",
          viewer: state.data.pr_viewer || "",
          at: new Date().toISOString()
        };
      } else {
        delete prChecks[prCheckKey(row)];
      }
      writeStored(PR_CHECKS_KEY, prChecks);
    }

    // The date is in the tooltip because the box now disappears on its own: without it,
    // a check that expires overnight looks like the dashboard losing my work.
    function prCheckTitle(entry) {
      if (!entry) return "Check off this PR";
      return `Checked off ${entry.at.slice(0, 10)} \u2014 clears when the Status changes, or a week after checking`;
    }

    function prCheckBox(entry) {
      const title = prCheckTitle(entry);
      return `<input type="checkbox" class="pr-check" ${entry ? "checked" : ""} title="${title}" aria-label="${title}">`;
    }

    let prSortWired = false;

    function renderPrReviews() {
      if (!prSortWired) { attachPrSortHandlers(); prSortWired = true; }
      // Against every saved review, not just the visible ones: a PR hidden by a filter
      // moves on GitHub too, and its box must already be clear when the filter comes off.
      reconcilePrChecks(state.data.pr_reviews || []);
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
      $("pr-rows").innerHTML = rows.map((row, index) => {
        const entry = prChecks[prCheckKey(row)];
        return `
        <tr data-index="${index}" class="${index === state.selectedPr ? "selected" : ""}${entry ? " checked-off" : ""}">
          <td>${prCheckBox(entry)}</td>
          <td>#${row.pr_number}</td>
          <td>${statusBadge(row)}</td>
          <td title="${esc(row.attention_detail || "")}">${verdictBadge(row.attention, "attention")}</td>
          <td><div class="description">${esc(row.pr_title || "")}</div></td>
          <td>${esc(row.author || "")}</td>
        </tr>
      `;
      }).join("");
      markPrSortHeader();
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
      $("pr-rows").querySelectorAll(".pr-check").forEach((box) => {
        // Checking a box is not picking a row, so the click stops before the <tr>.
        box.addEventListener("click", (event) => event.stopPropagation());
        box.addEventListener("change", () => {
          const tr = box.closest("tr");
          const row = rows[Number(tr.dataset.index)];
          setPrCheck(row, box.checked);
          // The browser has already drawn the checkmark; the row catches up to it, and
          // nothing else on the page has learned anything that would change what it shows.
          tr.classList.toggle("checked-off", box.checked);
          box.title = prCheckTitle(prChecks[prCheckKey(row)]);
          box.setAttribute("aria-label", box.title);
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
      $("pr-detail").innerHTML = [
        linkedField("PR", prUrl, `#${row.pr_number} — ${row.pr_title || ""}`),
        // Dashboard-only by the same test as the rest of this block: a reader of the comment
        // is already on the PR, whose own body says what it does. A reader skimming the
        // dashboard has no other way to tell one row from another.
        field("Author", row.author),
        row.pr_state ? field("State", `${row.pr_state}${row.base_ref ? ` → ${row.base_ref}` : ""}`) : "",
        field("Head", shortSha(row.head_sha)),
        field("Reviewed", row.reviewed_at),
        generatedInField(row),
        row.review_status ? section("Review Status", `<p>${statusBadge(row)} ${esc(row.review_status_detail || "")}</p>`) : "",
        section("Actions", prActions(row)),
        // The rule is the line between the two audiences. Above it is the dashboard's own:
        // things GitHub already shows on the PR itself (number, author, state) but that a
        // dashboard reader cannot see, plus the Status this viewer's perspective computes,
        // plus the buttons. Below it is the review, and the review is the comment — what is
        // rendered there is exactly what Post review comment submits, with nothing after it.
        `<hr class="pr-split">`,
        `<div class="md">${markdown(row.comment_markdown)}</div>`
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
    $("pr-hide-drafts").addEventListener("change", (event) => {
      state.hideDraftPrs = event.target.checked;
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

    // The published dashboard is a file, not a server: there is nothing behind these two
    // to press, and offering a Refresh that cannot refresh is worse than offering none.
    if (isStatic) {
      $("pr-refresh").classList.add("hidden");
      $("pr-sync").classList.add("hidden");
    }
    $("pr-refresh").addEventListener("click", runSync);

    // Coming back to the tab is the strongest signal there is that someone wants current
    // information, so it does not wait out the poll interval. Leaving stops the clock: a
    // dashboard left open on a hidden tab overnight makes no requests at all.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") {
        clearTimeout(pollTimer);
        return;
      }
      reloadData({ quiet: true }).catch(() => {});
      schedulePoll();
    });

    // Only ever rewrites the one span, so it is free to run while the reader works.
    setInterval(renderSyncState, 1000);

    reloadData()
      .then(() => schedulePoll())
      .catch((error) => {
        document.body.innerHTML = `<div class="empty">Failed to load repo-manager data: ${esc(error.message)}</div>`;
      });
  </script>
</body>
</html>
"""

# The maintainer table parser lives in cli.py so tier-1 validation and the dashboard agree
# on what a valid subject area is.
from repo_manager.cli import covers_any_area, load_maintainer_context  # noqa: E402
from repo_manager.triage import COMMENT_MARKER, data_from_row, render_comment  # noqa: E402

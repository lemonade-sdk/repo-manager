import json
import hashlib
import re
import sqlite3
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timezone


def connect(db_file):
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    ensure_range_schema(conn)
    return conn


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


def app_data(workspace):
    commits = commit_reviews(workspace)
    releases = release_reviews(workspace)
    announcements = release_announcements(workspace)
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
    }


def public_app_data(workspace):
    data = app_data(workspace)
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


def make_handler(workspace):
    class RepoManagerHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.send_text(INDEX_HTML, "text/html; charset=utf-8")
            elif path == "/api/data":
                self.send_json(app_data(workspace))
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/api/todo", "/api/read"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
                return
            if path == "/api/todo":
                result = update_todo(workspace, payload)
            else:
                result = update_read_state(workspace, payload)
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
    .badge.blocker, .badge.blocked {
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
      selectedRelease: 0,
      selectedAnnouncement: 0,
      filter: "",
      route: {}
    };
    let suppressRouteUpdate = false;
    const $ = (id) => document.getElementById(id);

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    function badge(value) {
      const cls = String(value || "unknown").toLowerCase().replace(/\s+/g, "-");
      return `<span class="badge ${cls}">${esc(value || "Unknown")}</span>`;
    }

    function shortSha(value) {
      return String(value || "").slice(0, 10);
    }

    function parseRoute() {
      const rawHash = window.location.hash.startsWith("#") ? window.location.hash.slice(1) : "";
      const params = new URLSearchParams(rawHash || window.location.search.slice(1));
      const view = params.get("view");
      return {
        view: ["commits", "release", "announcement"].includes(view) ? view : "",
        tag: params.get("tag") || "",
        commit: params.get("commit") || "",
        releaseHead: params.get("releaseHead") || "",
        announcementHead: params.get("announcementHead") || ""
      };
    }

    function applyRouteFromUrl() {
      const route = parseRoute();
      state.route = route;
      if (!route.view && route.commit) route.view = "commits";
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
      const response = await fetch("/api/data");
      state.data = await response.json();
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
        section("Description", `<p>${esc(row.summary)}</p>`),
        section("Verdict", `<p>${badge(displayVerdict(row))} ${esc(row.verdict_reason || "")}</p>`),
        section("Maintainer To-Do", todoList(row.todo_items)),
        section("Shout Outs", asList(row.shout_outs)),
        section("Evidence", evidenceList(evidence))
      ].join("");
      attachTodoHandlers($("commit-detail"));
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
        section("Website Release Highlights", `<pre id="release-highlights-markdown">${esc(row.release_highlights_markdown || "No website release highlights artifact saved.")}</pre>`),
        section("Discord Markdown", `<pre id="announcement-markdown">${esc(row.markdown || "")}</pre>`)
      ].join("");
    }

    function setView(view) {
      state.view = view;
      document.querySelectorAll(".nav-button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
      $("view-commits").classList.toggle("hidden", view !== "commits");
      $("view-release").classList.toggle("hidden", view !== "release");
      $("view-announcement").classList.toggle("hidden", view !== "announcement");
      $("search").classList.toggle("hidden", view !== "commits");
      $("copy-announcement").classList.toggle("hidden", view !== "announcement");
      $("view-title").textContent = view === "commits" ? "Commit DB" : view === "release" ? "Release Review" : "Announcement";
      updateRoute();
    }

    function renderAll() {
      renderShell();
      renderCommits();
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
      state.route = {};
      renderCommits();
      updateRoute();
    });
    $("tag-select").addEventListener("change", (event) => {
      state.selectedTag = event.target.value;
      state.selectedCommit = 0;
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

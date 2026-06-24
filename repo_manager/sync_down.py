"""Import an online (published) repo-manager dashboard back into a local SQLite DB.

`publish-pages` exports the database as a self-contained ``index.html`` whose
``window.REPO_MANAGER_STATIC_DATA`` global holds the public app data (see
``repo_manager.web.public_app_data``). This module reverses that flow: it parses
the embedded JSON and upserts it back into the local tables so a fresh or stale
machine can mirror the online copy.

The published JSON strips ``raw_output``/``*_path`` from each row but keeps the
parsed ``details`` (commit/release reviews) and ``markdown`` (announcements), so
those columns are reconstructed losslessly. Announcement *text* is intentionally
overwritten from authoritative sources by the caller (wiki + GitHub release
pages); the rows seeded here are just the proposed baseline that preserves
metadata and any vNext draft.
"""

import json
import re
from datetime import datetime, timezone


STATIC_DATA_RE = re.compile(r"REPO_MANAGER_STATIC_DATA\s*=\s*(.*?);\s*</script>", re.DOTALL)
WIKI_TAG_RE = re.compile(r"^#\s+(v\d+\.\d+\.\d+\S*)\s*$")


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_static_data(html):
    """Pull the embedded public app-data JSON out of a published index.html."""
    match = STATIC_DATA_RE.search(html or "")
    if not match:
        raise ValueError(
            "Could not find embedded REPO_MANAGER_STATIC_DATA in the published page. "
            "Is this a repo-manager dashboard produced by publish-pages?"
        )
    # The payload is script-safe JSON (``<`` escaped as \\u003c etc.), which is
    # still valid JSON, so no un-escaping is needed before json.loads.
    return json.loads(match.group(1))


def _json_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _restore_todos(conn, review_kind, review_key, todo_items):
    """Recreate review_todos rows so checkbox state survives the round-trip.

    The published ``todo_items`` carry the deterministic ``id`` computed on the
    source machine. Reconstructed ``raw_output`` yields the same ids when the web
    layer re-derives them, so writing rows keyed by those ids preserves which
    items were marked complete.
    """
    for index, item in enumerate(todo_items or []):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not item_id:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO review_todos
            (todo_id, review_kind, review_key, todo_index, todo_text, completed, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                review_kind,
                review_key,
                index,
                item.get("text") or "",
                1 if item.get("completed") else 0,
                _now_iso(),
            ),
        )


def upsert_commit_reviews(conn, rows):
    count = 0
    for row in rows or []:
        repo = row.get("repo")
        commit_sha = row.get("commit_sha")
        rubric_version = row.get("rubric_version") or ""
        if not repo or not commit_sha:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO commit_reviews
            (repo, commit_sha, branch, tag_start, range_start, pr_number, author, summary,
             verdict, verdict_reason, maintainer_todos, shout_outs, raw_output, json_path,
             reviewed_at, skill_version, rubric_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                repo,
                commit_sha,
                row.get("branch"),
                row.get("tag_start"),
                row.get("range_start") or "",
                row.get("pr_number"),
                row.get("author"),
                row.get("summary") or "",
                row.get("verdict"),
                row.get("verdict_reason") or "",
                _json_text(row.get("maintainer_todos")),
                _json_text(row.get("shout_outs")),
                _json_text(row.get("details")),
                row.get("reviewed_at") or "",
                row.get("skill_version") or "",
                rubric_version,
            ),
        )
        review_key = row.get("review_key") or f"{repo}|{commit_sha}|{rubric_version}"
        _restore_todos(conn, "commit", review_key, row.get("todo_items"))
        if row.get("is_read"):
            conn.execute(
                """
                INSERT OR REPLACE INTO review_read_states (review_key, is_read, updated_at)
                VALUES (?, 1, ?)
                """,
                (review_key, _now_iso()),
            )
        count += 1
    return count


def upsert_release_reviews(conn, rows):
    count = 0
    for row in rows or []:
        repo = row.get("repo")
        branch = row.get("branch")
        tag_start = row.get("tag_start")
        rubric_version = row.get("rubric_version") or ""
        if not repo or not tag_start:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO release_reviews
            (repo, branch, tag_start, range_start, head_sha, verdict, raw_output, json_path,
             reviewed_at, skill_version, rubric_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
            """,
            (
                repo,
                branch or "",
                tag_start,
                row.get("range_start") or "",
                row.get("head_sha") or "",
                row.get("verdict"),
                _json_text(row.get("details")),
                row.get("reviewed_at") or "",
                row.get("skill_version") or "",
                rubric_version,
            ),
        )
        review_key = f"{repo}|{branch or ''}|{tag_start}|{rubric_version}"
        _restore_todos(conn, "release", review_key, row.get("todo_items"))
        count += 1
    return count


def upsert_release_announcements(conn, rows):
    """Seed announcement rows from the published (proposed) data.

    Text fields are placeholders that the caller overwrites from the wiki and
    GitHub release pages; this preserves metadata (range_start/head_sha) and any
    vNext draft that has no authoritative source yet.
    """
    count = 0
    for row in rows or []:
        repo = row.get("repo")
        branch = row.get("branch")
        tag_start = row.get("tag_start")
        skill_version = row.get("skill_version") or ""
        if not repo or not tag_start:
            continue
        conn.execute(
            """
            INSERT OR REPLACE INTO release_announcements
            (repo, branch, tag_start, range_start, head_sha, raw_output, markdown_path,
             release_highlights_output, release_highlights_path, generated_at, skill_version)
            VALUES (?, ?, ?, ?, ?, ?, '', ?, '', ?, ?)
            """,
            (
                repo,
                branch or "",
                tag_start,
                row.get("range_start") or "",
                row.get("head_sha") or "",
                row.get("markdown") or "",
                row.get("release_highlights_markdown") or "",
                row.get("generated_at") or "",
                skill_version,
            ),
        )
        count += 1
    return count


def announcement_metadata(rows):
    """Map tag_start -> {branch, range_start, head_sha} from published announcements."""
    meta = {}
    for row in rows or []:
        tag = row.get("tag_start")
        if not tag:
            continue
        meta[tag] = {
            "branch": row.get("branch") or "",
            "range_start": row.get("range_start") or "",
            "head_sha": row.get("head_sha") or "",
        }
    return meta


def parse_wiki_announcements(markdown):
    """Split the Release-Announcements wiki page into {tag: discord_markdown}.

    Each release is delimited by a level-1 heading holding only the tag
    (``# v10.8.0``); the body is everything up to the next such heading.
    """
    sections = {}
    current_tag = None
    current_lines = []
    for line in (markdown or "").splitlines():
        match = WIKI_TAG_RE.match(line)
        if match:
            if current_tag is not None:
                sections[current_tag] = "\n".join(current_lines).strip()
            current_tag = match.group(1)
            current_lines = []
            continue
        if current_tag is not None:
            current_lines.append(line)
    if current_tag is not None:
        sections[current_tag] = "\n".join(current_lines).strip()
    return sections

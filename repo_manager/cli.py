import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from repo_manager import __version__


CONFIG_DIR = ".repo-manager"
CONFIG_FILE = "config.json"
DB_FILE = "repo-manager.sqlite"
RUBRIC_VERSION = "commit-review-2026-06-09"
RELEASE_RUBRIC_VERSION = "release-review-2026-06-09"
ANNOUNCEMENT_VERSION = "release-announcement-2026-06-09"
PR_RUBRIC_VERSION = "pr-review-tiered-2026-08-20"
PR_REVIEW_COMMENT_MARKER = "<!-- repo-manager:pr-review"


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
    ensure_db_schema(conn)
    return conn


def ensure_db_schema(conn):
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
    for table in ("commit_reviews", "release_reviews", "release_announcements", "pr_reviews"):
        try:
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            continue
        if columns and "generation_seconds" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN generation_seconds REAL NOT NULL DEFAULT 0")
    pr_columns = {row["name"] for row in conn.execute("PRAGMA table_info(pr_reviews)")}
    if pr_columns and "testing_status" not in pr_columns:
        conn.execute("ALTER TABLE pr_reviews ADD COLUMN testing_status TEXT NOT NULL DEFAULT ''")
    # The major/minor scope taxonomy predates contribute.md's Review Process rungs and is
    # gone, not shimmed: drop the columns so nothing can read a stale verdict.
    for dead in ("scope_verdict", "second_review_required"):
        if dead in pr_columns:
            try:
                conn.execute(f"ALTER TABLE pr_reviews DROP COLUMN {dead}")
            except sqlite3.OperationalError:
                pass
    for column, decl in (
        ("review_rung", "TEXT NOT NULL DEFAULT ''"),
        ("reviewers_needed", "INTEGER NOT NULL DEFAULT 1"),
        ("tier_reached", "TEXT NOT NULL DEFAULT ''"),
        ("gate_stopped_at", "TEXT NOT NULL DEFAULT ''"),
    ):
        if pr_columns and column not in pr_columns:
            conn.execute(f"ALTER TABLE pr_reviews ADD COLUMN {column} {decl}")
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
        CREATE TABLE IF NOT EXISTS release_review_issues (
          review_key TEXT PRIMARY KEY,
          repo TEXT NOT NULL,
          branch TEXT NOT NULL,
          tag_start TEXT NOT NULL,
          issue_number INTEGER NOT NULL,
          issue_url TEXT NOT NULL DEFAULT '',
          synced_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS release_announcement_issues (
          issue_key TEXT PRIMARY KEY,
          repo TEXT NOT NULL,
          branch TEXT NOT NULL,
          tag_start TEXT NOT NULL,
          issue_kind TEXT NOT NULL,
          issue_number INTEGER NOT NULL,
          issue_url TEXT NOT NULL DEFAULT '',
          synced_at TEXT NOT NULL
        )
        """
    )
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
          review_rung TEXT NOT NULL DEFAULT '',
          reviewers_needed INTEGER NOT NULL DEFAULT 1,
          tier_reached TEXT NOT NULL DEFAULT '',
          gate_stopped_at TEXT NOT NULL DEFAULT '',
          documentation_status TEXT NOT NULL DEFAULT '',
          testing_status TEXT NOT NULL DEFAULT '',
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


def project_docs_cache_dir(workspace):
    """Absolute cache root for fetched project docs.

    The fetch script defaults this to a path relative to its cwd, which is how a skill run
    from somewhere else ends up reading — or writing — a different repository's cache.
    """
    return Path(workspace).resolve() / CONFIG_DIR / "cache" / "project-docs"


PR_TIERS_WITHOUT_REVIEW_STATE = ("pr-triage", "pr-reviewers")


def run_pi(skill_name, prompt, cwd, base_ref=""):
    pi = shutil.which("pi")
    if not pi:
        raise SystemExit("`pi` was not found on PATH.")
    env = dict(os.environ)
    env["REPO_MANAGER_CACHE_DIR"] = str(project_docs_cache_dir(cwd))
    env["REPO_MANAGER_CHECKOUT"] = str(Path(cwd).resolve() / CONFIG_DIR / "checkout")
    if base_ref:
        env["REPO_MANAGER_BASE_REF"] = base_ref
    if skill_name in PR_TIERS_WITHOUT_REVIEW_STATE:
        env["REPO_MANAGER_WITHHOLD_REVIEWS"] = "1"
    cmd = [pi, "--mode", "json", "--skill", str(skill_path(skill_name))]
    saw_text = False
    assistant_text = []

    def feed_prompt(proc):
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        writer = threading.Thread(target=feed_prompt, args=(proc,), daemon=True)
        writer.start()
        print(f"Running Pi skill: {skill_name}", flush=True)
        for line in proc.stdout:
            rendered = render_pi_event(line)
            if rendered:
                saw_text = True
                assistant_text.append(rendered)
        returncode = proc.wait()
        writer.join(timeout=1)
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
        print(f"Pi exited with code {returncode} before completing the skill.", file=sys.stderr, flush=True)
        raise SystemExit(returncode)
    return "".join(assistant_text)


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
            return text + "\n"
        return ""

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
            delta = assistant_event.get("delta", "")
            print(delta, end="", flush=True)
            return delta
        if assistant_event.get("type") == "error":
            message = assistant_event.get("error", {}).get("errorMessage")
            if message:
                print(f"\nPi error: {message}", flush=True)
    return ""


def extract_json_object(text):
    stripped = (text or "").strip()
    if not stripped:
        return None
    candidates = [stripped]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(stripped[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def write_json_artifact_from_output(path, output):
    parsed = extract_json_object(output)
    if parsed is None:
        return False
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
    return True


HIGHLIGHTS_HORIZONTAL_RULE = re.compile(r"^\s*([-*_])\1{2,}\s*$")


def release_highlights_validation_errors(markdown):
    """Structural contract for the machine-parsed website highlights artifact — nothing more.

    lemonade-server.ai parses this file by its `## Headline` and `## Breaking Changes` sections,
    so the real contract is: those two `##` sections, 3-5 single-depth headline bullets, and
    breaking changes as bullets or empty. Be liberal about cosmetic noise the parser ignores —
    a stray document title or a `---` separator — and enforce only what the parser needs. Wording
    and casualness are the skill's job, not a hard gate.
    """
    text = (markdown or "").strip()
    if not text:
        return ["Release highlights artifact is empty."]
    lines = [line for line in text.splitlines() if not HIGHLIGHTS_HORIZONTAL_RULE.match(line)]
    section_headings = [(index, line.strip()) for index, line in enumerate(lines) if re.match(r"^##\s+", line)]
    if [title for _, title in section_headings] != ["## Headline", "## Breaking Changes"]:
        return [
            "Release highlights artifact must contain exactly `## Headline` then `## Breaking Changes` "
            "as its only `##` sections, and nothing else but their bullets."
        ]

    errors = []
    headline_index, breaking_index = section_headings[0][0], section_headings[1][0]
    headline_lines = [line for line in lines[headline_index + 1 : breaking_index] if line.strip()]
    breaking_lines = [line for line in lines[breaking_index + 1 :] if line.strip()]
    headline_bullets = [line for line in headline_lines if line.strip().startswith(("- ", "* "))]
    if len(headline_bullets) != len(headline_lines):
        errors.append("Release highlights Headline section must contain only single-depth bullets.")
    if not 3 <= len(headline_bullets) <= 5:
        errors.append("Release highlights Headline section must contain 3-5 bullets.")

    for line in breaking_lines:
        if not line.strip().startswith(("- ", "* ")):
            errors.append("Release highlights Breaking Changes section must contain only bullets or be empty.")
    return errors


def announcement_nonempty_errors(markdown):
    """The Discord post is prose for humans; the only hard requirement is that it exists.

    Voice, length, credits, no-PR-numbers, no-canned-phrases — all of it lives in the skill,
    which teaches it far better than a regex can police it. A style regex must never be able to
    block a postable announcement.
    """
    if not (markdown or "").strip():
        return ["Discord announcement is empty."]
    return []


MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+")
BREAKING_HEADING = re.compile(r"^#{1,6}\s+.*breaking\s+changes", re.IGNORECASE)


def count_breaking_change_bullets(markdown):
    """Count the bullets under a `Breaking Changes` heading at any heading depth.

    Both artifacts label the section "Breaking Changes" — the website highlights at `##`, the
    Discord post often at `###` — so match the heading by its text and count `-`/`*` bullets
    until the next heading. Returns -1 when no such section exists, so callers can tell an absent
    section from a present-but-empty one.
    """
    in_section = False
    found = False
    count = 0
    for line in (markdown or "").splitlines():
        if MARKDOWN_HEADING.match(line):
            if BREAKING_HEADING.match(line):
                in_section = True
                found = True
            elif in_section:
                break
        elif in_section and line.strip().startswith(("- ", "* ")):
            count += 1
    return count if found else -1


def canonical_breaking_changes_context(canonical):
    """Prompt block that hands the announcement its breaking changes from the release review."""
    if canonical is None:
        return ""
    if not canonical:
        return (
            "Breaking changes (from the release review, the source of truth): none. Leave the website "
            "highlights `## Breaking Changes` section present with no bullets, and omit the Discord "
            "Breaking Changes section entirely.\n\n"
        )
    bullets = "\n".join(f"- {item}" for item in canonical)
    return (
        f"Breaking changes (from the release review, the source of truth): exactly {len(canonical)} "
        f"user-facing breaking change(s) ship in this release:\n{bullets}\n\n"
        "Surface every one of them in BOTH artifacts — one bullet each in the website highlights "
        "`## Breaking Changes` section and one bullet each in the Discord `Breaking Changes` section. "
        "Reword them in the right register, but do not drop, merge, or invent any: each section's bullet "
        "count must equal the number above.\n\n"
    )


def announcement_breaking_changes_errors(release_highlights_markdown, discord_markdown, canonical):
    """Reconcile both artifacts against the release review's canonical breaking-change set.

    The release review is the single source of truth for what counts as a breaking change. When a
    review exists, the website highlights and the Discord post must each surface exactly that set —
    one bullet per change — so a dropped or merged breaking change can no longer slip through. When
    no review is available (`canonical is None`) there is nothing to reconcile against, so skip.
    """
    if canonical is None:
        return []
    errors = []
    expected = len(canonical)
    highlights_count = count_breaking_change_bullets(release_highlights_markdown)
    highlights_bullets = max(highlights_count, 0)
    if highlights_bullets != expected:
        if expected:
            errors.append(
                f"Website highlights Breaking Changes section has {highlights_bullets} bullet(s) but the "
                f"release review identified {expected} breaking change(s); cover exactly these, one bullet "
                "each: " + "; ".join(canonical)
            )
        else:
            errors.append(
                f"Website highlights Breaking Changes section has {highlights_bullets} bullet(s) but the "
                "release review identified no breaking changes; the section must be present with no bullets."
            )
    if expected:
        discord_count = count_breaking_change_bullets(discord_markdown)
        if discord_count == -1:
            errors.append(
                f"Discord announcement is missing a Breaking Changes section; the release review identified "
                f"{expected} breaking change(s) that must appear, one bullet each: " + "; ".join(canonical)
            )
        elif discord_count != expected:
            errors.append(
                f"Discord announcement Breaking Changes section has {discord_count} bullet(s) but the release "
                f"review identified {expected} breaking change(s); cover exactly these, one bullet each: "
                + "; ".join(canonical)
            )
    return errors


def normalize_release_priority(value):
    text = str(value or "").strip().upper()
    if text in ("P0", "BLOCKING", "BLOCKER", "HIGH"):
        return "P0"
    return "P1"


# Pi does not reliably emit the documented `prioritized_todos`/`text` keys: across runs it has
# filed the same list under `open_todos` (text under `text`) and under `todos` (text under `todo`).
# Rather than chase an ever-growing allowlist, recognize the to-do list by what its key name
# implies and read each item's text liberally. The verdict is derived from that list, so a
# misnamed-but-present list can never silently collapse into a false "Ready".
TODO_LIST_KEY_HINTS = ("todo", "action", "risk", "recommend", "blocker", "attention")
TODO_TEXT_KEYS = ("text", "todo", "task", "action", "description", "item")


def extract_todo_list(data):
    documented = data.get("prioritized_todos")
    if isinstance(documented, list) and documented:
        return documented
    for key, value in data.items():
        if isinstance(value, list) and value and any(hint in key.lower() for hint in TODO_LIST_KEY_HINTS):
            return value
    return documented if isinstance(documented, list) else []


def todo_text_value(item):
    for key in TODO_TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_breaking_changes(value):
    """Coerce Pi's `breaking_changes` field into a clean list of one-line statements.

    This list is the canonical record of user-facing breaking changes for the release; the
    announcement step reads it and must surface every entry. Pi files each entry as either a
    bare string or a small object, so read both shapes liberally and fold any migration pointer
    into the sentence so a single string fully describes the change.
    """
    items = []
    if isinstance(value, list):
        entries = value
    elif isinstance(value, str):
        entries = [value]
    else:
        entries = []
    for entry in entries:
        if isinstance(entry, str):
            text = entry.strip()
        elif isinstance(entry, dict):
            text = str(
                entry.get("change") or entry.get("text") or entry.get("summary") or entry.get("description") or ""
            ).strip()
            migration = str(entry.get("migration") or entry.get("action") or "").strip()
            if text and migration and migration.lower() not in text.lower():
                text = f"{text} — {migration}"
        else:
            text = str(entry).strip()
        if text:
            items.append(text)
    return items


def release_review_breaking_changes(data):
    breaking = normalize_breaking_changes(data.get("breaking_changes"))
    if breaking:
        return breaking
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    evidence_breaking = evidence.get("breaking_changes")
    if isinstance(evidence_breaking, list):
        return normalize_breaking_changes(evidence_breaking)
    return []


def normalize_release_review_data(data):
    """Coerce Pi's output into the single-ledger shape and derive the verdict from it.

    The to-do list is the only source of truth. The verdict is computed from it here, so the
    two can never disagree — there is no separate triage ledger to reconcile against.
    """
    normalized = []
    for item in extract_todo_list(data):
        if isinstance(item, dict):
            text = todo_text_value(item)
            if text:
                normalized.append({"priority": normalize_release_priority(item.get("priority")), "text": text})
        elif str(item).strip():
            normalized.append({"priority": "P1", "text": str(item).strip()})
    data["prioritized_todos"] = normalized

    data["breaking_changes"] = release_review_breaking_changes(data)

    data["evidence"] = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}

    if any(todo["priority"] == "P0" for todo in normalized):
        data["verdict"] = "Blocked"
    elif normalized:
        data["verdict"] = "Needs Attention"
    else:
        data["verdict"] = "Ready"
    return data


FALSE_GREEN_PATTERN = re.compile(
    r"\bP[01]\b|\bblock(?:s|er|ers|ing)?\b|before (?:shipping|release|releasing|tagging)",
    re.IGNORECASE,
)

# Mirrors the false-green guard above, for the canonical breaking-changes list. The list is the
# source of truth the announcement reconciles against, so an empty list whose prose still
# describes breaking changes is a silent under-count waiting to happen downstream.
HAS_BREAKING_PATTERN = re.compile(r"breaking change", re.IGNORECASE)
NO_BREAKING_PATTERN = re.compile(
    r"\b(no|none|zero|without|not any|aren['’]?t any|no user-facing)\b[^.]{0,40}breaking change",
    re.IGNORECASE,
)


def release_review_validation_errors(data):
    """Structural checks that protect the three maintainer-facing panels — nothing more.

    The verdict and the to-do priorities are guaranteed by normalize_release_review_data, so
    the only things that can be missing are the human-facing prose fields. Style, length, and
    word choice are the skill's job, not a hard gate that can deadlock a release review. The one
    exception is a false green: an empty list whose reason still describes blocking work is the
    worst possible output, so that single contradiction is caught here.
    """
    errors = []
    todos = data.get("prioritized_todos")
    if not isinstance(todos, list):
        errors.append("prioritized_todos must be a list (empty for Ready).")
    elif not todos and FALSE_GREEN_PATTERN.search(str(data.get("verdict_reason", ""))):
        errors.append(
            "prioritized_todos is empty but verdict_reason still describes blocking or to-verify work — "
            "put each such item in prioritized_todos so the verdict reflects it."
        )
    if not str(data.get("verdict_reason", "")).strip():
        errors.append("verdict_reason is required: one or two sentences answering 'can we ship?'.")
    evidence = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    breaking = data.get("breaking_changes")
    breaking_prose = f"{evidence.get('breaking_changes', '')} {data.get('verdict_reason', '')}"
    if isinstance(breaking, list) and not breaking and HAS_BREAKING_PATTERN.search(breaking_prose) and not NO_BREAKING_PATTERN.search(breaking_prose):
        errors.append(
            "breaking_changes is empty but the evidence or verdict_reason describes breaking changes — "
            "enumerate every user-facing breaking change in the breaking_changes list (one entry each, with "
            "its migration), since the announcement is reconciled against it."
        )
    for key in ("coverage", "blockers", "manual_testing", "breaking_changes", "security"):
        if not str(evidence.get(key, "")).strip():
            errors.append(
                f"evidence.{key} is required: one or two sentences of synthesis for the maintainer dashboard "
                "(or 'none observed' when that is the honest answer)."
            )
    return errors


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
        return base / f"{safe_tag}.release-review.json", None
    return None, base / f"{safe_tag}.release-announcement.md"


def release_announcement_artifact_paths(workspace, repo, tag_start, head):
    base = artifact_dir(workspace, repo, "releases")
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag_start)
    return (
        base / f"{safe_tag}.release-highlights.md",
        base / f"{safe_tag}.release-announcement.md",
    )


def pending_release_announcement_artifact_paths(workspace, repo, tag_start, head):
    release_highlights_file, markdown_file = release_announcement_artifact_paths(workspace, repo, tag_start, head)
    pending_dir = release_highlights_file.parent / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(int(time.time() * 1000))
    return (
        pending_dir / f"{release_highlights_file.stem}.{stamp}.pending.md",
        pending_dir / f"{markdown_file.stem}.{stamp}.pending.md",
    )


def read_json(path):
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def stamp_generation_seconds(json_file, started):
    """Record wall-clock generation time in the JSON artifact; returns the seconds."""
    seconds = round(time.monotonic() - started, 1)
    path = Path(json_file)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return seconds
    if isinstance(data, dict):
        data["generation_seconds"] = seconds
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return seconds


def write_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def require_files(*paths):
    missing = [str(path) for path in paths if not Path(path).exists()]
    if missing:
        raise SystemExit("Pi completed, but expected artifact file(s) were not created:\n" + "\n".join(missing))


def store_commit_review(workspace, repo, branch, release_tag, range_start, commit, json_file):
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
              repo, commit_sha, branch, tag_start, range_start, pr_number, author, summary,
              verdict, verdict_reason, maintainer_todos, shout_outs, raw_output, json_path,
              reviewed_at, skill_version, rubric_version, generation_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, commit_sha, rubric_version) DO UPDATE SET
              generation_seconds=excluded.generation_seconds,
              branch=excluded.branch,
              tag_start=excluded.tag_start,
              range_start=excluded.range_start,
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
                release_tag,
                range_start,
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
                float(data.get("generation_seconds") or 0),
            ),
        )


def review_exists(workspace, repo, commit):
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT 1 FROM commit_reviews WHERE repo=? AND commit_sha=? AND rubric_version=?",
            (repo, commit, RUBRIC_VERSION),
        ).fetchone()
    return row is not None


def pr_artifact_path(workspace, repo, pr_number):
    base = artifact_dir(workspace, repo, "prs")
    return base / f"pr-{pr_number}.pr-review.json"


def pr_review_feedback_file(workspace, repo, pr_number):
    base = artifact_dir(workspace, repo, "prs")
    pending_dir = base / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    return pending_dir / f"pr-{pr_number}.pr-review-feedback.json"


def load_pr_review_feedback(workspace, repo, pr_number):
    path = pr_review_feedback_file(workspace, repo, pr_number)
    if not path.exists():
        return ""
    try:
        data = read_json(path)
    except (json.JSONDecodeError, OSError):
        return ""
    return build_release_review_feedback(data.get("errors", ""), data.get("artifact", ""))


def save_pr_review_feedback(workspace, repo, pr_number, error_list, artifact_raw):
    path = pr_review_feedback_file(workspace, repo, pr_number)
    path.write_text(
        json.dumps({"errors": error_list, "artifact": artifact_raw, "saved_at": now_iso()}, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_pr_review_feedback(workspace, repo, pr_number):
    path = pr_review_feedback_file(workspace, repo, pr_number)
    if path.exists():
        path.unlink()


def gh_cli_json(args, check=True):
    if not shutil.which("gh"):
        raise SystemExit("`gh` was not found on PATH; install and authenticate GitHub CLI.")
    result = run(["gh", *args], check=check)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GitHub CLI returned invalid JSON for gh {' '.join(args)}: {exc}")


PR_METADATA_FIELDS = "number,title,state,isDraft,headRefOid,author,url,baseRefName"


def pr_author_handle(meta):
    author = meta.get("author") or {}
    login = author.get("login") if isinstance(author, dict) else str(author)
    return f"@{login}" if login else ""


def fetch_pr_metadata(repo, pr_number):
    data = gh_cli_json(["pr", "view", str(pr_number), "--repo", repo, "--json", PR_METADATA_FIELDS])
    if not data:
        raise SystemExit(f"Could not fetch PR #{pr_number} from {repo}.")
    return data


def list_open_prs(repo, limit):
    data = gh_cli_json(
        [
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,isDraft,headRefOid,author",
        ]
    )
    return data or []


DISCORD_ANNOTATION = re.compile(r"\s*\(discord:[^)]*\)", re.IGNORECASE)
VALID_HANDLE = re.compile(r"^@[A-Za-z0-9-]+$")

PR_DOC_VOCAB = ("contribute.md", "philosophy.md")
PR_DOC_STATUS_VOCAB = ("adequate", "gaps", "not-applicable", "not-evaluated")
PR_TEST_STATUS_VOCAB = ("adequate", "gaps", "not-applicable", "not-evaluated")
PR_FOCUS_VOCAB = ("focused", "bundled")
# contribute.md's Review Process rungs. These replace the old major/minor scope verdict,
# which predated the guide and encoded a size taxonomy the guide does not use.
PR_RUNG_VOCAB = ("one-reviewer", "two-with-expert", "named-approver")
PR_RUNG_REVIEWERS = {"one-reviewer": 1, "two-with-expert": 2, "named-approver": 2}
PR_RUNG_LABEL = {
    "one-reviewer": "any 1 reviewer",
    "two-with-expert": "2 reviewers including 1 subject-area expert",
    "named-approver": "review from the maintainer the guide names",
}
PR_SURFACE_VOCAB = ("api", "ux")
PR_APPROVAL_VOCAB = ("approved", "not-approved", "unclear")
PR_DESCRIPTION_VOCAB = ("accurate", "discrepancies", "missing")
# Why a reviewer is on the list. 'code-author' is a contributor who wrote the code under
# review, which is a different claim from maintainership and never satisfies it.
PR_BASIS_VOCAB = ("maintainer-table", "code-author")
def clean_github_handle(value):
    text = DISCORD_ANNOTATION.sub("", str(value or "")).strip().lstrip("@").strip()
    return f"@{text}" if text else ""


def coerce_bool(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("true", "yes", "y", "1"):
        return True
    if text in ("false", "no", "n", "0"):
        return False
    return None


def text_of(entry, *keys):
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def normalize_pr_review_data(data):
    """Coerce Pi's output into the documented shape and derive the computed fields.

    `reviewers_needed` and `attention_level` are always computed here from the
    structured lists, never trusted from Pi, so the skill's prose can never contradict them.
    """
    description = data.get("description_check") if isinstance(data.get("description_check"), dict) else {}
    discrepancies = []
    for entry in description.get("discrepancies") or []:
        if isinstance(entry, dict):
            discrepancies.append(
                {
                    "described": text_of(entry, "described", "claim", "claimed"),
                    "actual": text_of(entry, "actual", "reality", "found"),
                    "action": text_of(entry, "action", "todo", "fix"),
                    "evidence": text_of(entry, "evidence"),
                }
            )
        elif str(entry).strip():
            discrepancies.append({"described": "", "actual": str(entry).strip(), "action": "", "evidence": ""})
    data["description_check"] = {
        "verdict": text_of(description, "verdict").lower(),
        "notes": text_of(description, "notes", "assessment"),
        "discrepancies": discrepancies,
    }

    flags = []
    for entry in data.get("alignment_flags") or []:
        if isinstance(entry, dict):
            flags.append(
                {
                    "doc": text_of(entry, "doc", "document", "guide").lower(),
                    "section": text_of(entry, "section", "heading"),
                    "concern": text_of(entry, "concern", "text", "issue", "description"),
                    "action": text_of(entry, "action", "todo", "fix"),
                    "evidence": text_of(entry, "evidence"),
                }
            )
        elif str(entry).strip():
            flags.append({"doc": "", "section": "", "concern": str(entry).strip(), "action": "", "evidence": ""})
    data["alignment_flags"] = flags

    documentation = data.get("documentation") if isinstance(data.get("documentation"), dict) else {}
    gaps = []
    for entry in documentation.get("gaps") or []:
        if isinstance(entry, dict):
            gaps.append(
                {
                    "what": text_of(entry, "what", "text", "gap", "description"),
                    "where": text_of(entry, "where", "path", "file"),
                    "policy": text_of(entry, "policy", "rule"),
                    "action": text_of(entry, "action", "todo", "fix"),
                }
            )
        elif str(entry).strip():
            gaps.append({"what": str(entry).strip(), "where": "", "policy": "", "action": ""})
    data["documentation"] = {
        "status": text_of(documentation, "status").lower(),
        "gaps": gaps,
    }

    testing = data.get("testing") if isinstance(data.get("testing"), dict) else {}
    test_gaps = []
    for entry in testing.get("gaps") or []:
        if isinstance(entry, dict):
            test_gaps.append(
                {
                    "what": text_of(entry, "what", "text", "gap", "description"),
                    "where": text_of(entry, "where", "path", "file", "suite"),
                    "policy": text_of(entry, "policy", "rule"),
                    "action": text_of(entry, "action", "todo", "fix"),
                }
            )
        elif str(entry).strip():
            test_gaps.append({"what": str(entry).strip(), "where": "", "policy": "", "action": ""})
    data["testing"] = {
        "status": text_of(testing, "status").lower(),
        "gaps": test_gaps,
    }

    focus = data.get("focus") if isinstance(data.get("focus"), dict) else {}
    data["focus"] = {
        "verdict": text_of(focus, "verdict").lower(),
        "rationale": text_of(focus, "rationale", "reason"),
        "action": text_of(focus, "action", "todo", "fix"),
    }

    requirement = data.get("review_requirement") if isinstance(data.get("review_requirement"), dict) else {}
    rung = text_of(requirement, "rung", "verdict").lower()
    areas = requirement.get("expert_areas")
    if isinstance(areas, str):
        areas = [areas]
    data["review_requirement"] = {
        "rung": rung,
        "surface": text_of(requirement, "surface"),
        "expert_areas": [str(area).strip() for area in (areas or []) if str(area).strip()],
        "named_approver": clean_github_handle(text_of(requirement, "named_approver")) if rung == "named-approver" else "",
        "rationale": text_of(requirement, "rationale", "reason"),
        "reviewers_needed": PR_RUNG_REVIEWERS.get(rung, 1),
    }


    breaking = []
    for entry in data.get("breaking_changes") or []:
        if isinstance(entry, dict):
            breaking.append(
                {
                    "change": text_of(entry, "change", "text", "summary", "description"),
                    "surface": text_of(entry, "surface").lower(),
                    "documented": coerce_bool(entry.get("documented")),
                    "documentation_evidence": text_of(entry, "documentation_evidence"),
                    "maintainer_approval": text_of(entry, "maintainer_approval", "approval").lower(),
                    "approval_evidence": text_of(entry, "approval_evidence"),
                    "action": text_of(entry, "action", "todo", "fix"),
                }
            )
        elif str(entry).strip():
            breaking.append(
                {
                    "change": str(entry).strip(),
                    "surface": "",
                    "documented": None,
                    "documentation_evidence": "",
                    "maintainer_approval": "",
                    "approval_evidence": "",
                    "action": "",
                }
            )
    data["breaking_changes"] = breaking

    reviewers = []
    seen = set()
    for entry in data.get("suggested_reviewers") or []:
        if isinstance(entry, dict):
            item = {
                "handle": clean_github_handle(text_of(entry, "handle", "reviewer", "login", "maintainer")),
                "basis": (text_of(entry, "basis", "source") or "maintainer-table").lower(),
                "in_maintainer_table": coerce_bool(entry.get("in_maintainer_table")),
                "subject_area": text_of(entry, "subject_area", "area"),
                "reason": text_of(entry, "reason", "why"),
            }
        else:
            item = {
                "handle": clean_github_handle(entry),
                "basis": "maintainer-table",
                "in_maintainer_table": None,
                "subject_area": "",
                "reason": "",
            }
        if not item["handle"] or item["handle"].lower() in seen:
            continue
        seen.add(item["handle"].lower())
        reviewers.append(item)
    data["suggested_reviewers"] = reviewers

    areas = data.get("maintainer_needed_areas")
    if isinstance(areas, str):
        areas = [areas]
    data["maintainer_needed_areas"] = [str(area).strip() for area in (areas or []) if str(area).strip()]

    data["evidence"] = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}

    uncleared = [
        change for change in breaking
        if change["documented"] is not True or change["maintainer_approval"] != "approved"
    ]
    reasons = []
    rung_now = data["review_requirement"]["rung"]
    if rung_now in ("named-approver", "two-with-expert"):
        reasons.append(attention_requirement_reason(data["review_requirement"]))
    if uncleared:
        reasons.append(f"{len(uncleared)} breaking change(s) without docs or maintainer sign-off")
    elif breaking:
        reasons.append(f"{len(breaking)} documented, maintainer-approved breaking change(s)")
    if flags:
        reasons.append(f"{len(flags)} alignment issue(s)")
    if data["documentation"]["status"] == "gaps":
        reasons.append(f"{len(gaps)} documentation gap(s)")
    if data["testing"]["status"] == "gaps":
        reasons.append(f"{len(test_gaps)} testing gap(s)")
    verdict = data["description_check"]["verdict"]
    # Bundling outranks a description omission — see pr_gate_after_triage. Reporting both
    # gives the reader two reasons for one problem.
    if data["focus"]["verdict"] == "bundled":
        reasons.append("unrelated changes bundled together")
    elif verdict == "discrepancies":
        reasons.append("PR description does not match the diff")
    elif verdict == "missing":
        reasons.append("PR description is missing")
    data["attention_reasons"] = reasons

    if rung_now == "named-approver" or uncleared:
        data["attention_level"] = "High"
    elif rung_now == "two-with-expert":
        data["attention_level"] = "Elevated"
    elif (
        breaking
        or flags
        or data["focus"]["verdict"] == "bundled"
        or data["documentation"]["status"] == "gaps"
        or data["testing"]["status"] == "gaps"
        or verdict in ("discrepancies", "missing")
    ):
        data["attention_level"] = "Elevated"
    else:
        data["attention_level"] = "Routine"
    return data


def store_pr_review(workspace, repo, meta, data, json_file):
    raw = json.dumps(data, indent=2)
    with connect_db(workspace) as conn:
        conn.execute(
            """
            INSERT INTO pr_reviews (
              repo, pr_number, head_sha, pr_title, author, summary, attention_level,
              review_rung, reviewers_needed, documentation_status, testing_status,
              alignment_flags, breaking_changes, suggested_reviewers, maintainer_needed_areas,
              raw_output, json_path, reviewed_at, skill_version, rubric_version, generation_seconds,
              tier_reached, gate_stopped_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr_number, rubric_version) DO UPDATE SET
              generation_seconds=excluded.generation_seconds,
              head_sha=excluded.head_sha,
              pr_title=excluded.pr_title,
              author=excluded.author,
              summary=excluded.summary,
              attention_level=excluded.attention_level,
              review_rung=excluded.review_rung,
              reviewers_needed=excluded.reviewers_needed,
              documentation_status=excluded.documentation_status,
              testing_status=excluded.testing_status,
              alignment_flags=excluded.alignment_flags,
              breaking_changes=excluded.breaking_changes,
              suggested_reviewers=excluded.suggested_reviewers,
              maintainer_needed_areas=excluded.maintainer_needed_areas,
              raw_output=excluded.raw_output,
              json_path=excluded.json_path,
              reviewed_at=excluded.reviewed_at,
              skill_version=excluded.skill_version,
              tier_reached=excluded.tier_reached,
              gate_stopped_at=excluded.gate_stopped_at
            """,
            (
                repo,
                meta["number"],
                meta.get("headRefOid", ""),
                meta.get("title", ""),
                pr_author_handle(meta),
                data.get("summary", ""),
                data.get("attention_level", ""),
                data.get("review_requirement", {}).get("rung", ""),
                int(data.get("review_requirement", {}).get("reviewers_needed") or 1),
                data.get("documentation", {}).get("status", ""),
                data.get("testing", {}).get("status", ""),
                json.dumps(data.get("alignment_flags", [])),
                json.dumps(data.get("breaking_changes", [])),
                json.dumps(data.get("suggested_reviewers", [])),
                json.dumps(data.get("maintainer_needed_areas", [])),
                raw,
                str(json_file),
                now_iso(),
                __version__,
                PR_RUBRIC_VERSION,
                float(data.get("generation_seconds") or 0),
                data.get("tier_reached", ""),
                (data.get("gate") or {}).get("stopped_at") or "",
            ),
        )


def latest_pr_review(workspace, repo, pr_number):
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT * FROM pr_reviews WHERE repo=? AND pr_number=? AND rubric_version=?",
            (repo, pr_number, PR_RUBRIC_VERSION),
        ).fetchone()
    return dict(row) if row else None


_REFRESHED_DOCS = set()


def refresh_project_docs(workspace, repo, ref):
    """Re-fetch the project docs at `ref` before the skill runs.

    Two failures this closes. The cache never expires on its own — the fetch script only
    downloads a file it does not already have — so a warm cache serves whatever vintage it
    was filled with. And a skill that skips the fetch reads the docs from whatever working
    tree is lying around, which for a PR review is the PR's own branch: its contribution
    guide is the one from the branch point, not the one the PR will merge into.
    """
    if (str(workspace), repo, ref) in _REFRESHED_DOCS:
        return
    script = repo_root() / "skills" / "pr-review" / "scripts" / "get-pr-review-docs.sh"
    if not script.exists():
        print(f"Warning: doc fetch script not found at {script}; the skill will fetch its own.")
        return
    env = dict(os.environ)
    env["REPO_MANAGER_CACHE_DIR"] = str(project_docs_cache_dir(workspace))
    env["REPO_MANAGER_REFRESH_DOCS"] = "1"
    print(f"Refreshing project docs from {repo}@{ref}...", flush=True)
    result = subprocess.run(
        ["bash", str(script), repo, ref],
        cwd=workspace,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode == 0:
        _REFRESHED_DOCS.add((str(workspace), repo, ref))
    else:
        print(
            f"Warning: could not refresh project docs at {ref} "
            f"({(result.stderr or '').strip() or 'unknown error'}). "
            "The review may be judged against a stale contribution guide."
        )


PR_TIER_SKILLS = {"triage": "pr-triage", "quality": "pr-quality", "reviewers": "pr-reviewers"}


# --- contribute.md maintainer table -------------------------------------------------
#
# The guide is the source of truth for who maintains what and who the escalation rung
# names. Parsing it beats hardcoding, which is how the old "core maintainer" wording
# outlived the concept it described.

MAINTAINER_ROW = re.compile(r"^\|\s*@([A-Za-z0-9-]+)\s*\|([^|]*)\|(.*)\|\s*$")


def contribute_doc_path(workspace, repo, ref="main"):
    return project_docs_cache_dir(workspace) / safe_repo_name(repo) / ref / "docs__dev__contribute.md"


def parse_maintainer_table(text):
    """{handle: {"admin": bool, "areas": [term, ...]}} from the Maintainers table."""
    table = {}
    for line in text.splitlines():
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


def load_maintainer_context(workspace, repo, ref="main"):
    path = contribute_doc_path(workspace, repo, ref)
    if not path.exists():
        return {"table": {}}
    return {"table": parse_maintainer_table(path.read_text(encoding="utf-8", errors="replace"))}


def covers_any_area(entry, expert_areas):
    """Does this maintainer's Subject Areas cell name any area the PR needs an expert for?"""
    if not entry or not expert_areas:
        return []
    owned = {area.lower() for area in entry.get("areas", [])}
    return [area for area in expert_areas if area.strip().lower() in owned]

def pr_triage_validation_errors(data, pr_author, maintainer_areas=None):
    """Tier 1: the shape checks. Their verdicts gate the rest of the pipeline."""
    errors = []
    if not str(data.get("summary", "")).strip():
        errors.append("summary is required: one sentence on what the PR does.")

    description = data.get("description_check", {})
    if description.get("verdict") not in PR_DESCRIPTION_VOCAB:
        errors.append("description_check.verdict must be exactly 'accurate', 'discrepancies', or 'missing'.")
    if not description.get("notes"):
        errors.append("description_check.notes is required: how the author's description compares to the diff.")
    discrepancies = description.get("discrepancies", [])
    if description.get("verdict") == "discrepancies" and not discrepancies:
        errors.append("description_check.verdict is 'discrepancies' but the discrepancies list is empty.")
    elif description.get("verdict") != "discrepancies" and discrepancies:
        errors.append(
            "description_check.discrepancies is non-empty but the verdict is not 'discrepancies' — "
            "set the verdict or drop the list."
        )
    for item in discrepancies:
        if not (item.get("described") or item.get("actual")):
            errors.append("Every description discrepancy needs 'described' and/or 'actual' filled in.")
            break
    for item in discrepancies:
        if not item.get("action"):
            errors.append("Every description discrepancy needs an imperative 'action' that resolves it.")
            break
    for item in discrepancies:
        if not item.get("evidence"):
            errors.append(
                "Every description discrepancy needs 'evidence' — the file, hunk, or quoted description "
                "text a reader can check it against."
            )
            break

    focus = data.get("focus", {})
    if focus.get("verdict") not in PR_FOCUS_VOCAB:
        errors.append("focus.verdict must be exactly 'focused' or 'bundled'.")
    if not focus.get("rationale"):
        errors.append("focus.rationale is required: whether the diff solves one problem.")
    if focus.get("verdict") == "bundled" and not focus.get("action"):
        errors.append("focus.verdict is 'bundled' but focus.action is empty — name the split.")
    if focus.get("verdict") == "focused" and focus.get("action"):
        errors.append("focus.action is set but the verdict is 'focused' — drop the action or change the verdict.")

    requirement = data.get("review_requirement", {})
    rung = requirement.get("rung")
    if rung not in PR_RUNG_VOCAB:
        errors.append(
            "review_requirement.rung must be exactly 'one-reviewer', 'two-with-expert', or "
            "'named-approver' — read it off the surface table in the skill."
        )
    if not requirement.get("surface"):
        errors.append(
            "review_requirement.surface is required: the changed surface in the table's own words "
            "(for example 'flag on an existing CLI command')."
        )
    if not requirement.get("rationale"):
        errors.append("review_requirement.rationale is required: the surface and the rung it maps to.")
    if rung == "named-approver" and not VALID_HANDLE.match(requirement.get("named_approver") or ""):
        errors.append(
            "review_requirement.rung is 'named-approver' but named_approver is not a bare '@handle' — "
            "copy it from contribute.md's Review Process text."
        )
    if rung == "one-reviewer" and requirement.get("expert_areas"):
        errors.append(
            "review_requirement.expert_areas must be empty for 'one-reviewer' — that rung asks for "
            "any one reviewer, not a subject-area expert."
        )
    if rung in ("two-with-expert", "named-approver") and not requirement.get("expert_areas"):
        errors.append(
            "review_requirement.expert_areas is empty but the rung needs a subject-area expert — "
            "name the areas a reviewer would have to cover, in maintainer-table vocabulary."
        )
    if maintainer_areas:
        unknown = [
            area for area in requirement.get("expert_areas") or []
            if area.strip().lower() not in maintainer_areas
        ]
        if unknown:
            errors.append(
                f"review_requirement.expert_areas contains {unknown!r}, which no maintainer's "
                "Subject Areas cell names. Copy the terms verbatim from the contribute.md table — "
                "an area nobody lists can never be matched to a reviewer, so it reads as "
                "'no expert available' forever. Available terms include: "
                + ", ".join(sorted(maintainer_areas)[:14]) + "."
            )

    evidence = data.get("evidence", {})
    for key in ("description", "focus", "review_requirement"):
        if not str(evidence.get(key, "")).strip():
            errors.append(
                f"evidence.{key} is required: one or two sentences of synthesis "
                "(or 'none observed' when that is the honest answer)."
            )
    return errors


def pr_quality_validation_errors(data, pr_author):
    """Tier 2: the author-obligation checks."""
    errors = []
    for section, vocab in (("documentation", PR_DOC_STATUS_VOCAB), ("testing", PR_TEST_STATUS_VOCAB)):
        block = data.get(section, {})
        status = block.get("status")
        gaps = block.get("gaps", [])
        if status not in vocab or status == "not-evaluated":
            errors.append(f"{section}.status must be 'adequate', 'gaps', or 'not-applicable'.")
        elif status == "gaps" and not gaps:
            errors.append(f"{section}.status is 'gaps' but {section}.gaps is empty — list each gap.")
        elif status != "gaps" and gaps:
            errors.append(
                f"{section}.gaps is non-empty but status is not 'gaps' — set status to 'gaps' or drop the list."
            )
        for gap in gaps:
            if not gap.get("what"):
                errors.append(f"Every {section} gap needs a non-empty 'what'.")
                break
        for gap in gaps:
            if not gap.get("action"):
                errors.append(f"Every {section} gap needs an imperative 'action' that closes it.")
                break
    for gap in data.get("testing", {}).get("gaps", []):
        if not gap.get("where"):
            errors.append(
                "Every testing gap needs a 'where' naming the suite, workflow, or CMake registration "
                "that has to change — a gap you cannot route is a gap you have not established."
            )
            break

    for flag in data.get("alignment_flags", []):
        if flag.get("doc") not in PR_DOC_VOCAB:
            errors.append("Every alignment flag needs doc set to 'contribute.md' or 'philosophy.md'.")
            break
    for flag in data.get("alignment_flags", []):
        if not flag.get("concern") or not flag.get("evidence"):
            errors.append("Every alignment flag needs a non-empty concern and evidence.")
            break
    for flag in data.get("alignment_flags", []):
        if not flag.get("action"):
            errors.append("Every alignment flag needs an imperative 'action' that resolves it.")
            break
    author_handle = str(pr_author or "").lstrip("@").lower()
    if author_handle:
        for flag in data.get("alignment_flags", []):
            text = f"{flag.get('action', '')} {flag.get('concern', '')}".lower()
            if f"@{author_handle}" in text and "approv" in text:
                errors.append(
                    f"An alignment flag names the PR author (@{author_handle}) as the maintainer who "
                    "approved the change. The pre-agreement is between the contributor and a different "
                    "maintainer — name another maintainer of this area, or ask for the agreement without "
                    "naming who gave it."
                )
                break

    breaking = data.get("breaking_changes", [])
    for change in breaking:
        if not change.get("change"):
            errors.append("Every breaking change needs a non-empty 'change' statement.")
            break
    for change in breaking:
        if change.get("surface") not in PR_SURFACE_VOCAB:
            errors.append("Every breaking change needs surface set to exactly 'api' or 'ux'.")
            break
    for change in breaking:
        if change.get("documented") is None:
            errors.append("Every breaking change needs documented set to true or false.")
            break
    for change in breaking:
        if change.get("maintainer_approval") not in PR_APPROVAL_VOCAB:
            errors.append(
                "Every breaking change needs maintainer_approval set to 'approved', 'not-approved', or 'unclear'."
            )
            break
    for change in breaking:
        if not breaking_change_cleared(change) and not change.get("action"):
            errors.append(
                "Every breaking change that is undocumented or lacks maintainer approval needs an "
                "imperative 'action' that clears it."
            )
            break

    evidence = data.get("evidence", {})
    for key in ("alignment", "documentation", "testing", "breaking_changes"):
        if not str(evidence.get(key, "")).strip():
            errors.append(
                f"evidence.{key} is required: one or two sentences of synthesis "
                "(or 'none observed' when that is the honest answer)."
            )
    return errors


def cited_areas(reviewer):
    """The subject-area terms a suggestion cites, as a list."""
    return [part.strip() for part in str(reviewer.get("subject_area", "")).split(",") if part.strip()]


def asserts_novelty(area):
    """Does this subject area describe work that did not exist before the PR?

    Matched on the word rather than the prefix. "large new features" asserts novelty
    exactly as "new endpoints" does, and it is the one term in the table where "new" is
    not the first word — which is how a PR whose surface was "a field on an existing
    route" came to be staffed, and reported, as a large new feature.
    """
    return "new" in re.split(r"[^a-z0-9]+", str(area).lower())


def pr_reviewers_validation_errors(data, pr_author, requirement=None, maintainers=None):
    """Tier 3: the slate."""
    errors = []
    author_key = str(pr_author or "").lstrip("@").lower()
    for reviewer in data.get("suggested_reviewers", []):
        if not VALID_HANDLE.match(reviewer.get("handle", "")):
            errors.append(
                f"Suggested reviewer handle {reviewer.get('handle', '')!r} is not a plain GitHub handle — "
                "use '@handle' only; a surface no row covers belongs in maintainer_needed_areas."
            )
        elif author_key and reviewer["handle"].lstrip("@").lower() == author_key:
            errors.append(
                f"Suggested reviewer {reviewer['handle']} is the PR author — pick another candidate, or "
                "say in evidence.reviewers that the author is the sole candidate for that surface."
            )
        elif reviewer.get("basis") not in PR_BASIS_VOCAB:
            errors.append(
                f"Suggested reviewer {reviewer['handle']} needs basis set to 'maintainer-table' or 'code-author'."
            )
        elif reviewer.get("in_maintainer_table") is None:
            errors.append(
                f"Suggested reviewer {reviewer['handle']} needs in_maintainer_table set to true or false — "
                "it records whether their handle appears in the contribute.md table."
            )
        elif not reviewer.get("subject_area") or not reviewer.get("reason"):
            errors.append("Every suggested reviewer needs a subject_area and a reason.")
    if not data.get("suggested_reviewers") and not data.get("maintainer_needed_areas"):
        errors.append(
            "suggested_reviewers and maintainer_needed_areas are both empty — every surface the PR touches "
            "either has a candidate to suggest or is a recorded maintainer gap."
        )
    # The rung named the expertise this PR needs; a slate covering none of it staffs the PR
    # with people the dashboard will immediately report as insufficient.
    expert_areas = [a for a in (requirement or {}).get("expert_areas") or [] if str(a).strip()]
    if expert_areas and maintainers:
        # An area whose only maintainers are the PR author cannot be covered by any slate,
        # because the author is never suggestable. Demanding it would make this check
        # unsatisfiable by construction, so those areas are excluded and belong in
        # maintainer_needed_areas instead.
        coverable = []
        for area in expert_areas:
            owners = {
                handle for handle, entry in maintainers.items()
                if area.strip().lower() in {a.lower() for a in entry.get("areas", [])}
            }
            if owners - {author_key}:
                coverable.append(area)
        expert_areas = coverable
    if expert_areas and maintainers:
        slate = [item.get("handle", "").lstrip("@").lower() for item in data.get("suggested_reviewers", [])]
        wanted = {a.strip().lower() for a in expert_areas}
        covered = [
            handle for handle in slate
            if wanted & {area.lower() for area in (maintainers.get(handle) or {}).get("areas", [])}
        ]
        if not covered:
            errors.append(
                f"This PR's rung needs a subject-area expert in {', '.join(expert_areas)}, but nobody "
                "on the slate lists any of those areas in the maintainer table. Add a maintainer who "
                "does, or record the area in maintainer_needed_areas if nobody covers it."
            )

    # One pass over the slate, so every rule below reads the same derived facts instead of
    # re-deriving them — and so no rule can demand a term another rule forbids.
    rung = (requirement or {}).get("rung")
    surface = str((requirement or {}).get("surface", "")).lower()
    non_novel_rung = "existing" in surface or rung == "one-reviewer"
    table_entries = 0
    for item in data.get("suggested_reviewers", []):
        handle = item.get("handle", "")
        cited = cited_areas(item)
        if len(cited) > 3:
            errors.append(
                f"Suggested reviewer {handle} cites {len(cited)} subject areas. Name only the ones that "
                "make them relevant to this diff — quoting the whole cell says nothing about why they "
                "were chosen."
            )
        if item.get("basis") != "maintainer-table":
            continue
        table_entries += 1
        entry = (maintainers or {}).get(handle.lstrip("@").lower())
        if not entry:
            continue
        owned = {area.lower() for area in entry.get("areas", [])}
        unknown = [part for part in cited if part.lower() not in owned]
        if unknown:
            errors.append(
                f"Suggested reviewer {handle} cites subject area(s) {unknown!r} that do not appear in "
                f"their Subject Areas cell. Quote terms verbatim from it "
                f"({', '.join(sorted(entry.get('areas', []))[:6])}...) — citing more than one is fine when "
                "each is genuinely listed — or use basis 'code-author' if blame is the real reason."
            )
            continue
        # A term asserting novelty contradicts a rung that describes changing something which
        # already exists. Only complain when they had a non-novel term to cite instead:
        # a maintainer whose cell offers nothing else would otherwise be uncitable, which is
        # the same unsatisfiable-by-construction trap as an author-owned expert area.
        novel = [part for part in cited if asserts_novelty(part)]
        if non_novel_rung and novel:
            if any(not asserts_novelty(area) for area in owned):
                errors.append(
                    f"Suggested reviewer {handle} is justified by {', '.join(repr(a) for a in novel)}, "
                    f"which describes work that did not exist before — but this PR's surface is "
                    f"{(requirement or {}).get('surface')!r}, which changes something that already does. "
                    "The rung is a lookup off the surface table, not an impression of how large the diff "
                    "is, so do not re-argue it here: cite one of their other listed areas, or use basis "
                    "'code-author'. If the surface itself is wrong, that is a triage finding, not a "
                    "reason to staff the PR above its rung."
                )

    # A slate with no maintainer-table entry means no row matched any surface this PR touches.
    # That is a maintainer gap by definition, and saying so is the useful part for the reader.
    if data.get("suggested_reviewers") and not table_entries and not data.get("maintainer_needed_areas"):
        errors.append(
            "Every suggested reviewer is a code-author, which means no maintainer's Subject Areas "
            "cell covers this PR — record those surfaces in maintainer_needed_areas. A reader who "
            "learns nobody owns a surface can act on that; a slate that omits it hides the gap."
        )

    basis_text = str(data.get("evidence", {}).get("reviewers", "")).strip()
    if not basis_text:
        errors.append("evidence.reviewers is required: the surfaces you identified and how you got the slate.")
    elif "blame" not in basis_text.lower() and not any(
        item.get("basis") == "code-author" for item in data.get("suggested_reviewers", [])
    ):
        errors.append(
            "evidence.reviewers does not mention the code-authorship pass. Run "
            "get-pr-code-authors.sh and record what it returned, even when nothing useful came "
            "back — a docs-only PR is the case where it matters most, not the case to skip."
        )
    return errors


PR_TIER_VALIDATORS = {
    "triage": pr_triage_validation_errors,
    "quality": pr_quality_validation_errors,
    "reviewers": pr_reviewers_validation_errors,
}


def rung_brief(requirement):
    """The line tier 3 needs: how many reviewers, what expertise, who is named."""
    rung = (requirement or {}).get("rung") or ""
    if not rung:
        return ""
    needed = PR_RUNG_REVIEWERS.get(rung, 1)
    areas = ", ".join((requirement or {}).get("expert_areas") or [])
    parts = [
        f"Review requirement: {rung} — contribute.md asks for {PR_RUNG_LABEL.get(rung, rung)}.",
        f"Surface: {(requirement or {}).get('surface', '')}.",
        f"Name at least {needed} reviewer(s).",
    ]
    if areas:
        parts.append(f"At least one must cover a subject area among: {areas}.")
    named = (requirement or {}).get("named_approver") or ""
    if named:
        parts.append(f"The guide names {named} for this rung; include them unless they authored the PR.")
    return " ".join(parts)


def run_pr_tier(workspace, repo, meta, tier, base_ref, requirement=None):
    """Run one tier's skill and return its validated, normalized fragment.

    Each tier validates only its own fields, so a bounced tier re-runs that tier's judgment
    rather than the whole review — the monolith re-ran everything for one bad field.
    """
    number = meta["number"]
    author = pr_author_handle(meta)
    skill = PR_TIER_SKILLS[tier]
    validator = PR_TIER_VALIDATORS[tier]
    maintainer_areas, maintainer_table = None, {}
    if tier in ("triage", "reviewers"):
        maintainer_table = load_maintainer_context(workspace, repo, base_ref).get("table", {})
        maintainer_areas = {area.lower() for entry in maintainer_table.values() for area in entry.get("areas", [])}
    pending_dir = pr_artifact_path(workspace, repo, number).parent / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    feedback = ""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        pending_json = pending_dir / f"pr-{number}.{tier}.{int(time.time() * 1000)}.pending.json"
        prompt = (
            f"/skill:{skill} {repo} {number}\n\n"
            f"PR: #{number} — {meta.get('title', '')}\n"
            f"Head SHA: {meta.get('headRefOid', '')}\n"
            f"Base branch: {base_ref} (project docs have been refreshed at this ref; "
            f"read them only via scripts/get-pr-review-docs.sh, never from a working tree)\n"
            f"Author: {author} (never suggest the author as a reviewer)\n"
            + (f"{rung_brief(requirement)}\n" if tier == "reviewers" and requirement else "")
            + f"\nWrite the machine-readable JSON result to: {pending_json}\n\n"
            f"{feedback}"
        )
        try:
            output = run_pi(skill, prompt, workspace, base_ref=base_ref)
        except SystemExit as exc:
            if exc.code in (130, None) or attempt == max_attempts:
                raise
            print(f"Pi run failed (exit {exc.code}); retrying tier {tier}.", flush=True)
            continue
        if not pending_json.exists() and write_json_artifact_from_output(pending_json, output):
            print(f"Wrote {tier} artifact from Pi output: {pending_json}")
        errors, candidate, artifact_raw = [], None, ""
        if pending_json.exists():
            artifact_raw = pending_json.read_text(encoding="utf-8")
            candidate = extract_json_object(artifact_raw)
            if candidate is None:
                errors.append("Artifact file must contain a valid JSON object.")
            else:
                candidate = normalize_pr_review_data(candidate)
                if tier == "triage":
                    errors = validator(candidate, author, maintainer_areas)
                elif tier == "reviewers":
                    errors = validator(candidate, author, requirement, maintainer_table)
                else:
                    errors = validator(candidate, author)
        else:
            errors.append(f"Expected {tier} JSON file was not created: {pending_json}")
        if not errors:
            return candidate
        error_list = "\n".join(f"- {error}" for error in errors)
        if attempt == max_attempts:
            raise SystemExit(f"PR {tier} failed validation after {max_attempts} attempts:\n{error_list}")
        print(f"\nTier {tier} attempt {attempt} failed validation; asking Pi to revise:\n{error_list}\n", flush=True)
        feedback = build_release_review_feedback(error_list, artifact_raw)
    raise SystemExit(f"PR {tier} produced no usable artifact.")


def pr_gate_after_triage(triage):
    """contribute.md Reviewer Expectation 1-2: is this PR in a shape worth reviewing?

    Bundling outranks a description omission. Both checks can see the same undescribed
    extra work, and asking an author to both describe it and split it is two contradictory
    to-dos for one problem — so when the PR is going to be split, the split is the finding
    and the descriptions of the resulting PRs are a question for those PRs.
    """
    if triage["focus"]["verdict"] == "bundled":
        return "the PR bundles unrelated changes"
    if triage["description_check"]["verdict"] != "accurate":
        verdict = triage["description_check"]["verdict"]
        return (
            "the PR description is missing"
            if verdict == "missing"
            else "the PR description does not match the diff"
        )
    return None


def pr_gate_after_quality(quality):
    """Reviewer Expectation 3-5: work the author owes before a human should read the code."""
    reasons = []
    if quality["documentation"]["status"] == "gaps":
        reasons.append(f"{len(quality['documentation']['gaps'])} documentation gap(s)")
    if quality["testing"]["status"] == "gaps":
        reasons.append(f"{len(quality['testing']['gaps'])} testing gap(s)")
    if quality["alignment_flags"]:
        reasons.append(f"{len(quality['alignment_flags'])} alignment issue(s)")
    return ", ".join(reasons) or None


def generate_pr_review(workspace, repo, meta):
    number = meta["number"]
    base_ref = meta.get("baseRefName") or load_config(workspace).get("branch") or "main"
    refresh_project_docs(workspace, repo, base_ref)
    started = time.monotonic()

    triage = run_pr_tier(workspace, repo, meta, "triage", base_ref)
    data = dict(triage)
    tier_reached, stopped_at, gate_reason = "triage", None, None

    blocked = pr_gate_after_triage(triage)
    if blocked:
        stopped_at, gate_reason = "triage", blocked
        print(f"PR #{number}: stopped after triage — {blocked}.", flush=True)
    else:
        quality = run_pr_tier(workspace, repo, meta, "quality", base_ref)
        for key in ("alignment_flags", "documentation", "testing", "breaking_changes"):
            data[key] = quality[key]
        data["evidence"].update(quality.get("evidence", {}))
        tier_reached = "quality"
        owed = pr_gate_after_quality(quality)
        # An uncleared break is the one author obligation that still needs a maintainer:
        # only a listed maintainer can sign it off, so withholding the slate would deadlock.
        uncleared = [c for c in quality["breaking_changes"] if not breaking_change_cleared(c)]
        if meta.get("isDraft"):
            reason = "this PR is still a draft, so no human reviewer is assigned yet"
            if owed:
                reason += f", and the author owes {owed}"
            stopped_at, gate_reason = "quality", reason
            print(f"PR #{number}: draft — skipping reviewer assignment.", flush=True)
        elif owed and not uncleared:
            stopped_at, gate_reason = "quality", f"the author owes {owed}"
            print(f"PR #{number}: stopped after quality — author owes {owed}.", flush=True)
        else:
            if owed and uncleared:
                print(
                    f"PR #{number}: {owed} outstanding, but an uncleared breaking change needs a "
                    "maintainer sign-off — assigning reviewers anyway.",
                    flush=True,
                )
            reviewers = run_pr_tier(
                workspace, repo, meta, "reviewers", base_ref, requirement=data.get("review_requirement")
            )
            data["suggested_reviewers"] = reviewers["suggested_reviewers"]
            data["maintainer_needed_areas"] = reviewers["maintainer_needed_areas"]
            data["evidence"].update(reviewers.get("evidence", {}))
            tier_reached = "reviewers"

    if tier_reached == "triage":
        data["documentation"] = {"status": "not-evaluated", "gaps": []}
        data["testing"] = {"status": "not-evaluated", "gaps": []}
        data["alignment_flags"] = []
        data["breaking_changes"] = []
    data = normalize_pr_review_data(data)
    data["tier_reached"] = tier_reached
    data["gate"] = {"stopped_at": stopped_at, "reason": gate_reason or ""}
    data["repo"] = repo
    data["pr_number"] = number
    data["head_sha"] = meta.get("headRefOid", "")
    data["title"] = meta.get("title", "")
    data["author"] = pr_author_handle(meta)
    data["generation_seconds"] = round(time.monotonic() - started, 1)

    json_file = pr_artifact_path(workspace, repo, number)
    json_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    store_pr_review(workspace, repo, meta, data, json_file)
    reviewers_line = ", ".join(item["handle"] for item in data.get("suggested_reviewers", [])) or "none"
    print(
        f"PR #{number}: tier {tier_reached}, attention {data['attention_level']}, "
        f"rung {data['review_requirement']['rung'] or 'n/a'}, suggested reviewers: {reviewers_line}",
        flush=True,
    )
    return data


def pr_review_data_from_row(row):
    if row.get("json_path") and Path(row["json_path"]).exists():
        parsed = extract_json_object(Path(row["json_path"]).read_text(encoding="utf-8"))
        if parsed is not None:
            return normalize_pr_review_data(parsed)
    parsed = extract_json_object(row.get("raw_output") or "")
    if parsed is not None:
        return normalize_pr_review_data(parsed)
    return None


def attention_requirement_reason(requirement):
    """The who-must-review clause for a rung, generated in exactly one place.

    Both the attention reasons and the attention meaning are built from this, because the
    two used to be written independently — a fixed sentence per level against a rung
    computed from contribute.md — and drifted into contradicting each other. A level is a
    severity; only the rung knows who has to look. "Any reviewer can take it" belongs to
    one-reviewer and nowhere else.
    """
    rung = (requirement or {}).get("rung") or ""
    if rung == "named-approver":
        named = (requirement or {}).get("named_approver") or "the maintainer the guide names"
        # The guide asks that this rung "should have @handle review" — not that they sign
        # off. Saying "sign-off" here asked for more than contribute.md does.
        return f"needs a review from {named}"
    if rung == "two-with-expert":
        areas = [str(area) for area in (requirement or {}).get("expert_areas") or [] if str(area).strip()]
        clause = "needs 2 reviewers including 1 subject-area expert"
        return f"{clause} in {', '.join(areas)}" if areas else clause
    if rung == "one-reviewer":
        return "needs any 1 reviewer"
    return ""


def attention_todo_reasons(data):
    """The attention reasons that are work someone owes, minus the rung requirement.

    The requirement is stated once, in the meaning; repeating it in the parenthetical is
    how the same PR ended up telling the reader two different things about its rung. The
    "needs " prefix also catches rows stored before this wording, whose rung reason the
    exact match would miss; no to-do reason is phrased that way — they are all counts of
    work outstanding.
    """
    requirement = attention_requirement_reason(data.get("review_requirement"))
    return [
        reason
        for reason in data.get("attention_reasons") or []
        if reason != requirement and not str(reason).startswith("needs ")
    ]


def attention_meaning(data):
    """One sentence for what this PR's attention level asks of a reviewer."""
    who = attention_requirement_reason(data.get("review_requirement"))
    todos = attention_todo_reasons(data)
    if who and todos:
        return f"{who}, and the to-dos below need resolving before approval"
    if who:
        return who
    if todos:
        return "the to-dos below need resolving before approval"
    return "nothing flagged; a standard review pass is enough"


def attention_display(data):
    level = data.get("attention_level", "")
    meaning = attention_meaning(data)
    text = f"{level} — {meaning}" if meaning else level
    reasons = "; ".join(attention_todo_reasons(data))
    if reasons:
        text += f" ({reasons})"
    return text


def display_handle(handle):
    return str(handle or "").lstrip("@")


def breaking_change_cleared(change):
    return change.get("documented") is True and change.get("maintainer_approval") == "approved"


def breaking_change_status(change):
    documented = "documented in this PR" if change.get("documented") else "not documented in this PR"
    approval = change.get("maintainer_approval") or "unclear"
    if approval == "approved":
        approval_text = "maintainer-approved"
    elif approval == "not-approved":
        approval_text = "not maintainer-approved"
    else:
        approval_text = "needs maintainer confirmation"
    return f"{documented}; {approval_text}"


def rung_notes(data):
    """Why this PR sits on its rung, as {"why", "notes"} — the one source both renderers use.

    This used to be a "Review needed" section of its own, which restated the rung three
    times over: the attention line already names it in the guide's words, and
    attention_requirement_reason folds the expert areas into the two-with-expert clause
    and the handle into the named-approver one. Each restatement is dropped only for the
    rung that actually duplicates it — a named-approver PR's expert areas appear nowhere
    else, so dropping them wholesale would lose them. What is left is the one thing the
    attention line cannot carry: the surface and rationale that put the PR on that rung.

    Computed here rather than in each renderer because the dashboard and the posted comment
    have to say the same thing, and two copies of a rule this conditional will not stay equal.
    """
    requirement = data.get("review_requirement") or {}
    rung = requirement.get("rung")
    if not rung:
        return {"why": "", "notes": []}
    why = ". ".join(
        part for part in (requirement.get("surface"), requirement.get("rationale")) if part
    )
    notes = []
    areas = ", ".join(requirement.get("expert_areas") or [])
    if areas and rung != "two-with-expert":
        notes.append(f"Subject-area expertise this calls for: {areas}")
    if requirement.get("named_approver") and rung != "named-approver":
        notes.append(f"The guide names {display_handle(requirement['named_approver'])} for this rung.")
    return {"why": why, "notes": notes}


def rung_rationale_lines(data):
    """rung_notes rendered as the comment's markdown, under the attention level."""
    rung = rung_notes(data)
    lines = ["", f"Why this rung: {rung['why']}"] if rung["why"] else []
    for note in rung["notes"]:
        lines += ["", f"- {note}"]
    return lines


def ready_line(coverage):
    """The "Ready for review" sentence, which must not promise a slate we then withhold."""
    if not (coverage or {}).get("adequate"):
        return "**Ready for review** — the checks below passed, and suggested reviewers are at the end."
    who = ", ".join(display_handle(login) for login in coverage.get("who") or [])
    # Handles are rendered bare, like the slate's, so naming who has it does not ping them.
    covered = f" ({who})" if who else ""
    # Someone requested but not yet answering staffs the rung without satisfying it, and the
    # sentence has to say which — "already has the review" would be a claim about work nobody
    # has done yet, on a PR whose Status column is still reporting Waiting.
    if coverage.get("pending"):
        return (
            "**Ready for review** — the checks below passed. No reviewers are suggested: the "
            f"reviewers contribute.md's rung asks for are already on this PR{covered}."
        )
    return (
        "**Ready for review** — the checks below passed. No reviewers are suggested: this PR "
        f"already has the review contribute.md's rung asks for{covered}."
    )


PR_TIER_ORDER = ("triage", "quality", "reviewers")


def pr_tier_ran(tier_reached, tier):
    """Did the review get far enough to have actually looked at this check?"""
    order = list(PR_TIER_ORDER)
    return order.index(tier_reached or "reviewers") >= order.index(tier)


def pr_not_evaluated(gate):
    """What a section says when its tier never ran.

    "None found" would be a lie about a check nobody performed, so a gated section names the
    gate instead. This is the whole reason the comment cannot simply omit the section. The
    verdict itself lives on the summary line, so this is only the explanation under it.
    """
    where = f"the {gate.get('stopped_at')} gate" if gate.get("stopped_at") else "an earlier tier"
    why = f" because {gate['reason']}" if gate.get("reason") else ""
    return f"The review stopped at {where}{why}. This check runs once that is resolved."


def pr_todo_lines(items):
    """One checklist entry per finding, its supporting notes indented underneath."""
    lines = []
    for item in items:
        lines.append(f"- [ ] {item.get('action') or item.get('fallback') or ''}")
        for label, value in item.get("subs") or []:
            if value:
                lines.append(f"  - {label}: {value}")
    return lines


def pr_section(title, verdict, body, expanded):
    """One collapsible section: its answer on the summary line, its evidence folded inside.

    A clean bill is a line, not a page. The summary carries the whole answer, so a comment on
    a PR with nothing wrong is a short list of one-liners rather than screens of reasoning
    nobody asked for — and the reasoning is still one click away when somebody does.

    Findings default to open. A to-do behind a fold is a to-do nobody does, and the point of
    the comment is the author acting on it.
    """
    body = [line for line in body if line is not None]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    head = f"<details{' open' if expanded else ''}><summary>{title}: <b>{verdict}</b></summary>"
    if not body:
        return ["", f"{head}</details>"]
    # GitHub only renders markdown inside <details> when blank lines separate it from the tags.
    return ["", head, "", *body, "", "</details>"]


def pr_checked_line(evidence, key):
    """The "Checked:" note that earns a clean bill — what was inspected to find nothing."""
    value = (evidence or {}).get(key)
    return ["", f"Checked: {value}"] if value else []


def render_pr_review_comment(repo, pr_number, data, head_sha, coverage=None):
    """The review rendered as its PR comment.

    This is the single artifact: the dashboard previews exactly this text and the Post
    review comment button submits exactly this text. It used to be a deliberately lean
    subset of what the dashboard showed, which meant the two could — and did — disagree
    about what the review had found, and no amount of aligning them section by section
    fixed that. There is now one renderer, so there is nothing left to align.
    """
    marker_payload = json.dumps(
        {"repo": repo, "pr_number": pr_number, "rubric_version": PR_RUBRIC_VERSION},
        sort_keys=True,
    )
    tier = data.get("tier_reached", "reviewers")
    gate = data.get("gate") or {}
    stopped_at = gate.get("stopped_at")
    evidence = data.get("evidence") or {}
    lines = [
        f"{PR_REVIEW_COMMENT_MARKER} {marker_payload} -->",
        "**[AI-assisted review]** Automated pre-review from repo-manager — flags for the human reviewer, not a replacement for one.",
        "",
    ]
    if stopped_at == "triage":
        lines += [
            f"**Not ready for review yet** — {gate.get('reason', '')}. "
            "Fixing the item below is the next step; the documentation, testing, and reviewer checks "
            "have not run yet and will follow once this is resolved.",
        ]
    elif stopped_at == "quality":
        lines += [
            f"**Not ready for review yet** — {gate.get('reason', '')}. "
            "These are for the author to resolve. No reviewer has been suggested yet: "
            "contribute.md asks a PR to meet the Reviewer Expectation before a human is assigned.",
        ]
    else:
        lines += [ready_line(coverage)]
    lines += ["", f"**Attention level:** {attention_display(data)}"] + rung_rationale_lines(data)

    if data.get("summary"):
        lines += ["", f"**Description:** {data['summary']}"]

    description = data.get("description_check") or {}
    verdict = description.get("verdict") or "not assessed"
    todos = []
    if verdict == "missing" and not (description.get("discrepancies") or []):
        todos.append({"action": "Ask the author to describe the change — the PR has no usable description."})
    for item in description.get("discrepancies") or []:
        todos.append({
            "action": item.get("action"),
            "fallback": "Reconcile the description with the diff.",
            "subs": [("Described", item.get("described")), ("In the diff", item.get("actual")),
                     ("Reference", item.get("evidence"))],
        })
    lines += pr_section(
        "Author's description vs. the diff", verdict,
        ([description["notes"]] if description.get("notes") else []) + (pr_todo_lines(todos) if todos else []),
        expanded=verdict not in ("accurate", "not assessed"),
    )

    focus = data.get("focus") or {}
    verdict = focus.get("verdict") or "not assessed"
    bundled = verdict == "bundled"
    lines += pr_section(
        "Focus", verdict,
        ([focus["rationale"]] if focus.get("rationale") else [])
        + (pr_todo_lines([{"action": focus.get("action"),
                           "fallback": "Split the unrelated work into its own PR."}]) if bundled else [])
        + ([] if bundled else pr_checked_line(evidence, "focus")),
        expanded=bundled,
    )

    quality_ran = pr_tier_ran(tier, "quality")
    flags = data.get("alignment_flags") or []
    if not quality_ran:
        lines += pr_section("Alignment issues", "not evaluated", [pr_not_evaluated(gate)], expanded=False)
    else:
        lines += pr_section(
            "Alignment issues", "None found" if not flags else f"{len(flags)} to resolve",
            pr_checked_line(evidence, "alignment") if not flags else pr_todo_lines([{
                "action": flag.get("action"),
                "fallback": flag.get("concern"),
                "subs": [
                    ("Why", " — ".join(part for part in (flag.get("doc"), flag.get("section")) if part)
                            + ": " + (flag.get("concern") or "")
                            if (flag.get("doc") or flag.get("section")) else flag.get("concern")),
                    ("Reference", flag.get("evidence")),
                ],
            } for flag in flags]),
            expanded=bool(flags),
        )

    for title, key in (("Documentation", "documentation"), ("Testing", "testing")):
        block = data.get(key) or {}
        if not quality_ran:
            lines += pr_section(title, "not evaluated", [pr_not_evaluated(gate)], expanded=False)
            continue
        gaps = block.get("gaps") or []
        lines += pr_section(
            title, block.get("status") or "unknown",
            pr_todo_lines([{
                "action": gap.get("action"),
                "fallback": gap.get("what"),
                "subs": [("Gap", gap.get("what")), ("Where", gap.get("where")), ("Why", gap.get("policy"))],
            } for gap in gaps]) if gaps else pr_checked_line(evidence, key),
            expanded=bool(gaps),
        )

    breaking = data.get("breaking_changes") or []
    if not quality_ran:
        lines += pr_section("Breaking changes", "not evaluated", [pr_not_evaluated(gate)], expanded=False)
    else:
        unresolved = [change for change in breaking if not breaking_change_cleared(change)]
        body = []
        for change in breaking:
            summary = f"({change.get('surface', '')}) {change.get('change', '')}"
            if breaking_change_cleared(change):
                body.append(f"- {summary} — {breaking_change_status(change)}")
            else:
                body += pr_todo_lines([{
                    "action": change.get("action"),
                    "fallback": "Document this break and get a maintainer sign-off.",
                    "subs": [("Change", summary), ("Status", breaking_change_status(change))],
                }])
        if not breaking:
            body = pr_checked_line(evidence, "breaking_changes")
        lines += pr_section(
            "Breaking changes",
            "None found" if not breaking else f"{len(unresolved)} to resolve" if unresolved else "all cleared",
            body, expanded=bool(unresolved),
        )

    reviewers = data.get("suggested_reviewers") or []
    areas = data.get("maintainer_needed_areas") or []
    # A covered PR needs no slate, and the readiness line above has already said so. A gated
    # one still gets the section, because "covered" and "never asked" are different answers.
    covered = bool((coverage or {}).get("adequate"))
    if not (covered and pr_tier_ran(tier, "reviewers")):
        if not pr_tier_ran(tier, "reviewers"):
            lines += pr_section("Suggested reviewers", "not evaluated", [pr_not_evaluated(gate)], expanded=False)
        else:
            body = []
            for item in reviewers:
                note = "" if item.get("in_maintainer_table") else ", not in the maintainer table"
                body.append(
                    f"- {display_handle(item.get('handle'))} ({item.get('subject_area', '')}{note})"
                    f" — {item.get('reason', '')}"
                )
            for area in areas:
                body.append(f"- No maintainer listed for: {area}")
            count = len(reviewers)
            lines += pr_section(
                "Suggested reviewers",
                "none" if not body else f"{count} suggested" if count else "no maintainer listed",
                body, expanded=bool(body),
            )

    lines += [
        "",
        f"_Reviewed at head `{(head_sha or '')[:7]}` by repo-manager (tier: {tier}). "
        f"Regenerate with `repo-manager review-pr {pr_number}`._",
    ]
    return "\n".join(lines).rstrip() + "\n"


def pr_comment_key(repo, pr_number):
    return f"{repo}|{pr_number}|{PR_RUBRIC_VERSION}"


def pr_comment_mapping(workspace, repo, pr_number):
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT * FROM pr_review_comments WHERE comment_key=?",
            (pr_comment_key(repo, pr_number),),
        ).fetchone()
    return dict(row) if row else None


def save_pr_comment_mapping(workspace, repo, pr_number, comment, head_sha):
    with connect_db(workspace) as conn:
        conn.execute(
            """
            INSERT INTO pr_review_comments
            (comment_key, repo, pr_number, comment_id, comment_url, posted_head_sha, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(comment_key) DO UPDATE SET
              comment_id=excluded.comment_id,
              comment_url=excluded.comment_url,
              posted_head_sha=excluded.posted_head_sha,
              synced_at=excluded.synced_at
            """,
            (
                pr_comment_key(repo, pr_number),
                repo,
                pr_number,
                comment.get("id"),
                comment.get("html_url", ""),
                head_sha,
                now_iso(),
            ),
        )


def find_pr_review_comment(workspace, repo, pr_number):
    mapping = pr_comment_mapping(workspace, repo, pr_number)
    if mapping:
        comment = gh_json([f"repos/{repo}/issues/comments/{mapping['comment_id']}"], check=False)
        if comment and comment.get("id"):
            return comment
    comments = gh_json(
        ["--method", "GET", f"repos/{repo}/issues/{pr_number}/comments", "-f", "per_page=100"],
        check=False,
    )
    for comment in comments or []:
        if str(comment.get("body", "")).startswith(PR_REVIEW_COMMENT_MARKER):
            return comment
    return None


def post_pr_review(workspace, repo, pr_number, dry_run=False):
    """Post or update the stored PR review as a marker-tagged PR comment. Returns a result dict."""
    row = latest_pr_review(workspace, repo, pr_number)
    if not row:
        return {
            "ok": False,
            "error": f"No stored review for PR #{pr_number}. Run `repo-manager review-pr {pr_number}` first.",
        }
    data = pr_review_data_from_row(row)
    if data is None:
        return {"ok": False, "error": f"Stored review for PR #{pr_number} could not be parsed."}
    warnings = []
    meta = fetch_pr_metadata(repo, pr_number)
    live_head = meta.get("headRefOid", "")
    if live_head and row.get("head_sha") and live_head != row["head_sha"]:
        warnings.append(
            f"PR head has moved since the review ({row['head_sha'][:7]} -> {live_head[:7]}); "
            f"consider regenerating with `repo-manager review-pr {pr_number}`."
        )
    # web.py owns the coverage rules because the Status column is their other consumer;
    # imported here rather than at module scope, since web.py imports back from cli.
    from repo_manager.web import reviewer_coverage

    coverage = reviewer_coverage(
        workspace,
        repo,
        pr_number,
        row.get("author") or data.get("author"),
        data.get("review_requirement"),
    )
    body = render_pr_review_comment(repo, pr_number, data, row.get("head_sha", ""), coverage)
    existing = find_pr_review_comment(workspace, repo, pr_number)
    if dry_run:
        action = "update" if existing else "create"
        return {"ok": True, "action": action, "dry_run": True, "body": body, "warnings": warnings}
    if existing:
        comment = gh_json(
            ["--method", "PATCH", f"repos/{repo}/issues/comments/{existing['id']}", "-f", f"body={body}"]
        )
        action = "updated"
    else:
        comment = gh_json(
            ["--method", "POST", f"repos/{repo}/issues/{pr_number}/comments", "-f", f"body={body}"]
        )
        action = "created"
    if not comment or not comment.get("id"):
        return {"ok": False, "error": "GitHub did not return the posted comment.", "warnings": warnings}
    save_pr_comment_mapping(workspace, repo, pr_number, comment, row.get("head_sha", ""))
    return {"ok": True, "action": action, "url": comment.get("html_url", ""), "warnings": warnings}


def request_pr_reviewers(workspace, repo, pr_number, handles=None, dry_run=False):
    """Request the stored suggested reviewers (or an explicit list) on GitHub. Returns a result dict."""
    warnings = []
    if handles:
        suggestions = [
            {"handle": clean_github_handle(handle), "subject_area": "", "reason": "requested explicitly"}
            for handle in handles
            if clean_github_handle(handle)
        ]
    else:
        row = latest_pr_review(workspace, repo, pr_number)
        if not row:
            return {
                "ok": False,
                "error": f"No stored review for PR #{pr_number}. Run `repo-manager review-pr {pr_number}` "
                "first, or pass --reviewers.",
            }
        data = pr_review_data_from_row(row) or {}
        suggestions = data.get("suggested_reviewers", [])
        for area in data.get("maintainer_needed_areas", []):
            warnings.append(f"No maintainer listed for: {area}")
    meta = gh_cli_json(["pr", "view", str(pr_number), "--repo", repo, "--json", "author,reviewRequests,reviews"])
    if not meta:
        return {"ok": False, "error": f"Could not fetch PR #{pr_number} from {repo}.", "warnings": warnings}
    author = pr_author_handle(meta).lower()
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
        if not VALID_HANDLE.match(handle):
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
        result = gh_json(
            [
                "--method",
                "POST",
                f"repos/{repo}/pulls/{pr_number}/requested_reviewers",
                "-f",
                f"reviewers[]={login}",
            ],
            check=False,
        )
        if result:
            requested.append(item["handle"])
        else:
            failed.append({"handle": item["handle"], "reason": "GitHub rejected the request (not a collaborator?)"})
    return {"ok": True, "requested": requested, "skipped": skipped, "failed": failed, "warnings": warnings}


def list_commits(repo, workspace, branch, range_start):
    checkout = clone_or_fetch(repo, workspace)
    run(["git", "-C", str(checkout), "fetch", "origin", branch, "--tags", "--prune"])
    rev = f"{range_start}..origin/{branch}"
    result = run(["git", "-C", str(checkout), "rev-list", "--reverse", rev])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def version_parts(tag):
    return tuple(int(part) for part in re.findall(r"\d+", str(tag or "")))


def v_tags(repo, workspace):
    checkout = clone_or_fetch(repo, workspace)
    run(["git", "-C", str(checkout), "fetch", "origin", "--tags", "--prune"])
    result = run(["git", "-C", str(checkout), "tag", "--list", "v*"])
    tags = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return sorted(tags, key=version_parts)


def infer_range_start(repo, workspace, release_tag):
    tags = v_tags(repo, workspace)
    if not tags:
        raise SystemExit("No v* tags found. Pass --since explicitly.")
    if release_tag == "vNext":
        return tags[-1]
    if release_tag in tags:
        index = tags.index(release_tag)
        if index == 0:
            raise SystemExit(f"No previous v* tag found before {release_tag}. Pass --since explicitly.")
        return tags[index - 1]
    release_version = version_parts(release_tag)
    older = [tag for tag in tags if version_parts(tag) < release_version]
    if older:
        return older[-1]
    raise SystemExit(f"Could not infer previous v* tag for {release_tag}. Pass --since explicitly.")


def cmake_version_from_text(text):
    patterns = (
        r"\bproject\s*\([^)]*\bVERSION\s+([0-9]+(?:\.[0-9]+){1,3})",
        r"\bCMAKE_PROJECT_VERSION\s+([0-9]+(?:\.[0-9]+){1,3})",
        r"\bPROJECT_VERSION\s+([0-9]+(?:\.[0-9]+){1,3})",
        r"\bVERSION\s+([0-9]+(?:\.[0-9]+){1,3})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1)
    return ""


def cmake_release_tag(repo, workspace, branch):
    checkout = clone_or_fetch(repo, workspace)
    run(["git", "-C", str(checkout), "fetch", "origin", branch, "--prune"], check=False)
    result = run(["git", "-C", str(checkout), "show", f"origin/{branch}:CMakeLists.txt"], check=False)
    if result.returncode != 0:
        return ""
    version = cmake_version_from_text(result.stdout)
    return f"v{version}" if version else ""


def remote_branch_exists(repo, workspace, branch):
    checkout = clone_or_fetch(repo, workspace)
    run(["git", "-C", str(checkout), "fetch", "origin", branch, "--prune"], check=False)
    result = run(["git", "-C", str(checkout), "rev-parse", "--verify", f"origin/{branch}"], check=False)
    return result.returncode == 0


def migrate_release_issue_title(repo, issue_number, old_title, new_title, release_tag):
    issue = fetch_github_issue(repo, issue_number)
    if not issue:
        return
    body = (issue.get("body") or "").replace("vNext", release_tag)
    title = new_title if issue.get("title") == old_title else issue.get("title", "").replace("vNext", release_tag)
    if title != issue.get("title") or body != (issue.get("body") or ""):
        update_release_issue(repo, issue_number, title, body)


def migrate_vnext_release_state(workspace, repo, old_branch, new_branch, release_tag):
    """Move local vNext state to the concrete release bucket and branch.

    This runs before release commands read from the DB, so reviews collected while the release
    was still tracked as vNext/main remain visible after CMake names the release and the
    release branch appears.
    """
    if release_tag == "vNext":
        return
    new_review_key = release_review_key(repo, new_branch, release_tag)
    completed_todo_texts = set()
    with connect_db(workspace) as conn:
        old_branches = {old_branch}
        for table in ("commit_reviews", "release_reviews", "release_announcements"):
            for row in conn.execute(
                f"SELECT DISTINCT branch FROM {table} WHERE repo=? AND tag_start='vNext'",
                (repo,),
            ).fetchall():
                if row["branch"]:
                    old_branches.add(row["branch"])
        for row in conn.execute(
            "SELECT DISTINCT branch FROM release_review_issues WHERE repo=? AND tag_start='vNext'",
            (repo,),
        ).fetchall():
            if row["branch"]:
                old_branches.add(row["branch"])
        for row in conn.execute(
            "SELECT DISTINCT branch FROM release_announcement_issues WHERE repo=? AND tag_start='vNext'",
            (repo,),
        ).fetchall():
            if row["branch"]:
                old_branches.add(row["branch"])
        old_review_keys = [release_review_key(repo, branch, "vNext") for branch in old_branches]
        old_review_keys.extend(
            release_review_key(repo, branch, release_tag)
            for branch in old_branches
            if branch != new_branch
        )
        for row in conn.execute(
            """
            SELECT todo_text
            FROM review_todos
            WHERE review_kind='release' AND completed=1
              AND review_key IN ({})
            """.format(",".join("?" for _ in old_review_keys)),
            old_review_keys,
        ).fetchall():
            completed_todo_texts.add(normalized_todo_match_text(row["todo_text"]))
        for table in ("commit_reviews", "release_reviews", "release_announcements"):
            conn.execute(
                f"""
                UPDATE OR REPLACE {table}
                SET tag_start=?, branch=?
                WHERE repo=? AND tag_start='vNext'
                """,
                (release_tag, new_branch, repo),
            )
            conn.execute(
                f"""
                UPDATE OR REPLACE {table}
                SET branch=?
                WHERE repo=? AND tag_start=? AND branch<>?
                """,
                (new_branch, repo, release_tag, new_branch),
            )
        conn.execute(
            """
            UPDATE commit_reviews
            SET raw_output=replace(raw_output, 'vNext', ?)
            WHERE repo=? AND tag_start=? AND branch=?
            """,
            (release_tag, repo, release_tag, new_branch),
        )
        conn.execute(
            """
            UPDATE release_reviews
            SET raw_output=replace(raw_output, 'vNext', ?)
            WHERE repo=? AND tag_start=? AND branch=?
            """,
            (release_tag, repo, release_tag, new_branch),
        )
        conn.execute(
            """
            UPDATE release_announcements
            SET raw_output=replace(raw_output, 'vNext', ?),
                release_highlights_output=replace(release_highlights_output, 'vNext', ?)
            WHERE repo=? AND tag_start=? AND branch=?
            """,
            (release_tag, release_tag, repo, release_tag, new_branch),
        )
        delete_keys = [*old_review_keys, new_review_key]
        conn.execute(
            "DELETE FROM review_todos WHERE review_kind='release' AND review_key IN ({})".format(
                ",".join("?" for _ in delete_keys)
            ),
            delete_keys,
        )
        review_row = conn.execute(
            """
            SELECT raw_output
            FROM release_reviews
            WHERE repo=? AND branch=? AND tag_start=? AND rubric_version=?
            """,
            (repo, new_branch, release_tag, RELEASE_RUBRIC_VERSION),
        ).fetchone()
        if review_row:
            data = extract_json_object(review_row["raw_output"] or "")
            if data is not None:
                data = normalize_release_review_data(data)
                normalized = normalize_review_todos(conn, "release", new_review_key, data.get("prioritized_todos", []))
                for todo in normalized:
                    if normalized_todo_match_text(todo["text"]) in completed_todo_texts:
                        conn.execute(
                            "UPDATE review_todos SET completed=1, updated_at=? WHERE todo_id=?",
                            (now_iso(), todo["id"]),
                        )
        conn.execute(
            """
            UPDATE OR REPLACE release_review_issues
            SET review_key=?, branch=?, tag_start=?
            WHERE repo=? AND (tag_start='vNext' OR tag_start=?)
            """,
            (new_review_key, new_branch, release_tag, repo, release_tag),
        )
        for issue_kind in ANNOUNCEMENT_ISSUE_KINDS:
            new_key = announcement_issue_key(repo, new_branch, release_tag, issue_kind)
            conn.execute(
                """
                UPDATE OR REPLACE release_announcement_issues
                SET issue_key=?, branch=?, tag_start=?
                WHERE repo=? AND (tag_start='vNext' OR tag_start=?) AND issue_kind=?
                """,
                (new_key, new_branch, release_tag, repo, release_tag, issue_kind),
            )

    mappings = []
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT issue_number FROM release_review_issues WHERE review_key=?",
            (release_review_key(repo, new_branch, release_tag),),
        ).fetchone()
        if row:
            mappings.append((row["issue_number"], release_issue_title("vNext"), release_issue_title(release_tag)))
        for issue_kind in ANNOUNCEMENT_ISSUE_KINDS:
            row = conn.execute(
                "SELECT issue_number FROM release_announcement_issues WHERE issue_key=?",
                (announcement_issue_key(repo, new_branch, release_tag, issue_kind),),
            ).fetchone()
            if row:
                mappings.append(
                    (
                        row["issue_number"],
                        announcement_issue_title("vNext", issue_kind),
                        announcement_issue_title(release_tag, issue_kind),
                    )
                )
    mapped_numbers = {issue_number for issue_number, _, _ in mappings}
    fallback_titles = [
        (release_issue_title("vNext"), release_issue_title(release_tag)),
        *[
            (announcement_issue_title("vNext", issue_kind), announcement_issue_title(release_tag, issue_kind))
            for issue_kind in ANNOUNCEMENT_ISSUE_KINDS
        ],
    ]
    for old_title, new_title in fallback_titles:
        try:
            issue = find_github_issue_by_title(repo, old_title)
        except SystemExit:
            issue = None
        if issue and issue["number"] not in mapped_numbers:
            mappings.append((issue["number"], old_title, new_title))
            mapped_numbers.add(issue["number"])
    for issue_number, old_title, new_title in mappings:
        try:
            migrate_release_issue_title(repo, issue_number, old_title, new_title, release_tag)
        except SystemExit:
            pass


def resolve_release_state(workspace, repo, configured_branch, requested_release, explicit_branch=False):
    info = release_lifecycle_info(workspace, repo, configured_branch, requested_release, explicit_branch)
    release_tag = info["selected_release"]
    branch = info["selected_branch"]
    if requested_release == "vNext" and release_tag != "vNext":
        migrate_vnext_release_state(workspace, repo, configured_branch, branch, release_tag)
        if release_tag != requested_release:
            print(f"Resolved vNext to {release_tag} on {branch}.", flush=True)
    elif branch != configured_branch:
        migrate_vnext_release_state(workspace, repo, configured_branch, branch, release_tag)
    return release_tag, branch


def release_lifecycle_info(workspace, repo, configured_branch, requested_release="vNext", explicit_branch=False):
    cmake_tag = cmake_release_tag(repo, workspace, configured_branch)
    tags = v_tags(repo, workspace)
    latest_tag = tags[-1] if tags else ""
    inferred_release = requested_release
    if requested_release == "vNext" and cmake_tag and (not latest_tag or version_parts(cmake_tag) > version_parts(latest_tag)):
        inferred_release = cmake_tag
    release_branch = f"release-{inferred_release}" if inferred_release and inferred_release != "vNext" else ""
    release_branch_exists = bool(release_branch) and remote_branch_exists(repo, workspace, release_branch)
    selected_branch = configured_branch
    if release_branch_exists and not explicit_branch:
        selected_branch = release_branch
    return {
        "repo": repo,
        "requested_release": requested_release,
        "configured_branch": configured_branch,
        "cmake_release": cmake_tag,
        "latest_tag": latest_tag,
        "selected_release": inferred_release,
        "release_branch": release_branch,
        "release_branch_exists": release_branch_exists,
        "selected_branch": selected_branch,
        "explicit_branch": explicit_branch,
    }


def lifecycle_has_release_branch(info):
    return info["selected_release"] != "vNext" and info["release_branch_exists"]


def release_lifecycle_counts(workspace, repo, branch, release_tag):
    with connect_db(workspace) as conn:
        counts = {}
        counts["commit_reviews"] = conn.execute(
            "SELECT COUNT(*) FROM commit_reviews WHERE repo=? AND branch=? AND tag_start=?",
            (repo, branch, release_tag),
        ).fetchone()[0]
        counts["release_reviews"] = conn.execute(
            "SELECT COUNT(*) FROM release_reviews WHERE repo=? AND branch=? AND tag_start=?",
            (repo, branch, release_tag),
        ).fetchone()[0]
        counts["announcements"] = conn.execute(
            "SELECT COUNT(*) FROM release_announcements WHERE repo=? AND branch=? AND tag_start=?",
            (repo, branch, release_tag),
        ).fetchone()[0]
        counts["open_release_todos"] = conn.execute(
            """
            SELECT COUNT(*)
            FROM review_todos
            WHERE review_kind='release' AND review_key=? AND completed=0
            """,
            (release_review_key(repo, branch, release_tag),),
        ).fetchone()[0]
        counts["completed_release_todos"] = conn.execute(
            """
            SELECT COUNT(*)
            FROM review_todos
            WHERE review_kind='release' AND review_key=? AND completed=1
            """,
            (release_review_key(repo, branch, release_tag),),
        ).fetchone()[0]
        counts["checklist_issues"] = conn.execute(
            "SELECT COUNT(*) FROM release_review_issues WHERE repo=? AND branch=? AND tag_start=?",
            (repo, branch, release_tag),
        ).fetchone()[0]
        counts["announcement_issues"] = conn.execute(
            "SELECT COUNT(*) FROM release_announcement_issues WHERE repo=? AND branch=? AND tag_start=?",
            (repo, branch, release_tag),
        ).fetchone()[0]
    return counts


def prior_release_tags(repo, workspace, release_tag, limit=3):
    tags = v_tags(repo, workspace)
    if release_tag == "vNext":
        return tags[-limit:][::-1]
    if release_tag in tags:
        return tags[max(0, tags.index(release_tag) - limit) : tags.index(release_tag)][::-1]
    current_version = version_parts(release_tag)
    older = [tag for tag in tags if version_parts(tag) < current_version]
    return older[-limit:][::-1]


def head_sha(repo, workspace, branch):
    checkout = clone_or_fetch(repo, workspace)
    result = run(["git", "-C", str(checkout), "rev-parse", f"origin/{branch}"])
    return result.stdout.strip()


def release_head_sha(repo, workspace, branch, release_tag):
    checkout = clone_or_fetch(repo, workspace)
    if release_tag and release_tag != "vNext":
        run(["git", "-C", str(checkout), "fetch", "origin", "--tags", "--prune"])
        result = run(["git", "-C", str(checkout), "rev-parse", f"{release_tag}^{{}}"], check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    return head_sha(repo, workspace, branch)


def load_reviews(workspace, repo, branch=None, release_tag=None):
    where = ["repo=?"]
    values = [repo]
    if branch:
        where.append("branch=?")
        values.append(branch)
    if release_tag:
        where.append("tag_start=?")
        values.append(release_tag)
    query = "SELECT * FROM commit_reviews WHERE " + " AND ".join(where) + " ORDER BY reviewed_at, commit_sha"
    with connect_db(workspace) as conn:
        return [dict(row) for row in conn.execute(query, values).fetchall()]


def common_range_start(reviews):
    values = sorted({review.get("range_start", "") for review in reviews if review.get("range_start", "")})
    return values[0] if len(values) == 1 else ""


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


def completed_todo_ids(workspace):
    with connect_db(workspace) as conn:
        try:
            rows = conn.execute("SELECT todo_id FROM review_todos WHERE completed=1").fetchall()
        except sqlite3.OperationalError:
            return set()
    return {row["todo_id"] for row in rows}


def compact_reviews(workspace, reviews):
    completed_ids = completed_todo_ids(workspace)
    rows = []
    for review in reviews:
        data = read_json(review["json_path"]) if review.get("json_path") else {}
        review_key = f"{review['repo']}|{review['commit_sha']}|{review['rubric_version']}"
        todos = []
        for index, item in enumerate(data.get("maintainer_todos", [])):
            item_id = todo_id("commit", review_key, index, item)
            todos.append(
                {
                    "text": todo_display_text(item),
                    "priority": item.get("priority", "") if isinstance(item, dict) else "",
                    "completed": item_id in completed_ids,
                }
            )
        rows.append(
            {
                "commit_sha": review["commit_sha"],
                "pr_number": review["pr_number"],
                "author": data.get("author", review["author"]),
                "summary": data.get("summary", review["summary"]),
                "verdict": data.get("verdict", review["verdict"]),
                "verdict_reason": data.get("verdict_reason", review["verdict_reason"]),
                "maintainer_todos": todos,
                "open_maintainer_todos": [todo for todo in todos if not todo["completed"]],
                "completed_maintainer_todos": [todo for todo in todos if todo["completed"]],
                "shout_outs": data.get("shout_outs", []),
                "evidence": data.get("evidence", {}),
            }
        )
    return json.dumps(rows, indent=2)


RELEASE_REVIEW_EVIDENCE_KEYS = (
    "tests",
    "manual_release_testing",
    "api_compatibility",
    "security",
    "post_approval_commits",
    "documentation",
)


def release_review_context(workspace, reviews):
    completed_ids = completed_todo_ids(workspace)
    rows = []
    for review in reviews:
        data = read_json(review["json_path"]) if review.get("json_path") else {}
        review_key = f"{review['repo']}|{review['commit_sha']}|{review['rubric_version']}"
        open_todos = []
        completed = 0
        for todo_index, item in enumerate(data.get("maintainer_todos", [])):
            if todo_id("commit", review_key, todo_index, item) in completed_ids:
                completed += 1
            else:
                open_todos.append(todo_display_text(item))
        evidence = data.get("evidence") or {}
        rows.append(
            {
                "pr_number": review["pr_number"],
                "author": data.get("author", review["author"]),
                "summary": data.get("summary", review["summary"]),
                "verdict": data.get("verdict", review["verdict"]),
                "verdict_reason": data.get("verdict_reason", review["verdict_reason"]),
                "open_todos": open_todos,
                "completed_todos": completed,
                "evidence": {key: evidence[key] for key in RELEASE_REVIEW_EVIDENCE_KEYS if evidence.get(key)},
            }
        )
    return json.dumps(rows, indent=2)


def announcement_review_context(workspace, reviews):
    rows = []
    for review in reviews:
        data = read_json(review["json_path"]) if review.get("json_path") else {}
        credits = []
        for item in data.get("shout_outs", []):
            handle = item.get("handle", "") if isinstance(item, dict) else str(item)
            if handle:
                credits.append(handle)
        docs = truncate_pi_detail((data.get("evidence") or {}).get("documentation", ""), 200)
        rows.append(
            {
                "author": data.get("author", review.get("author", "")),
                "summary": data.get("summary", review.get("summary", "")),
                "credits": credits,
                "docs": docs,
            }
        )
    return json.dumps(rows, indent=2)


def latest_release_review(workspace, repo, branch, tag_start):
    with connect_db(workspace) as conn:
        row = conn.execute(
            """
            SELECT * FROM release_reviews
            WHERE repo=? AND branch=? AND tag_start=? AND rubric_version=?
            ORDER BY reviewed_at DESC
            LIMIT 1
            """,
            (repo, branch, tag_start, RELEASE_RUBRIC_VERSION),
        ).fetchone()
    return dict(row) if row else None


def release_review_key(repo, branch, release_tag):
    return f"{repo}|{branch}|{release_tag}|{RELEASE_RUBRIC_VERSION}"


def normalize_review_todos(conn, review_kind, review_key, todos):
    existing = {
        row["todo_id"]: row
        for row in conn.execute(
            "SELECT * FROM review_todos WHERE review_kind=? AND review_key=?",
            (review_kind, review_key),
        ).fetchall()
    }
    normalized = []
    for index, item in enumerate(todos or []):
        item_id = todo_id(review_kind, review_key, index, item)
        text = todo_display_text(item)
        priority = item.get("priority", "") if isinstance(item, dict) else ""
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
        normalized.append({"id": item_id, "text": text, "priority": priority, "completed": completed})
    return normalized


def stored_release_todos(workspace, repo, branch, release_tag):
    key = release_review_key(repo, branch, release_tag)
    with connect_db(workspace) as conn:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM review_todos
                WHERE review_kind='release' AND review_key=?
                ORDER BY todo_index, updated_at
                """,
                (key,),
            ).fetchall()
        ]


def sync_release_review_todos(workspace, repo, branch, release_tag, todos):
    key = release_review_key(repo, branch, release_tag)
    with connect_db(workspace) as conn:
        return normalize_review_todos(conn, "release", key, todos)


CHECKBOX_PATTERN = re.compile(
    r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<text>.*?)(?:\s*<!--\s*repo-manager:todo:(?P<id>[a-f0-9]{64})\s*-->)?\s*$"
)
RELEASE_ISSUE_MARKER = "<!-- repo-manager:release-review-checklist"


def normalized_todo_match_text(text):
    text = re.sub(r"<!--.*?-->", "", text or "")
    text = re.sub(r"^\s*P[01]\s*:\s*", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip().casefold()


def parse_issue_checkboxes(body):
    items = []
    for line in (body or "").splitlines():
        match = CHECKBOX_PATTERN.match(line)
        if not match:
            continue
        items.append(
            {
                "id": match.group("id") or "",
                "text": re.sub(r"\s*<!--.*?-->\s*$", "", match.group("text")).strip(),
                "completed": match.group("mark").lower() == "x",
            }
        )
    return items


def gh_json(args, check=True):
    if not shutil.which("gh"):
        if check:
            raise SystemExit("`gh` was not found on PATH; install and authenticate GitHub CLI to sync issues.")
        return None
    result = run(["gh", "api", *args], check=check)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GitHub API returned invalid JSON for {' '.join(args)}: {exc}")


def release_issue_title(release_tag):
    return f"Release {release_tag} final checklist"


def find_github_issue_by_title(repo, title):
    query = f'repo:{repo} is:issue in:title "{title}"'
    data = gh_json(["--method", "GET", "search/issues", "-f", f"q={query}", "-f", "per_page=10"], check=False)
    for item in (data or {}).get("items", []):
        if item.get("title") == title:
            return fetch_github_issue(repo, item["number"])
    return None


def find_release_issue(repo, release_tag):
    return find_github_issue_by_title(repo, release_issue_title(release_tag))


def fetch_github_issue(repo, issue_number):
    return gh_json(["-H", "Accept: application/vnd.github+json", f"repos/{repo}/issues/{issue_number}"])


def fetch_release_issue_comments(repo, issue_number):
    data = gh_json(
        [
            "-H",
            "Accept: application/vnd.github+json",
            "--method",
            "GET",
            f"repos/{repo}/issues/{issue_number}/comments",
            "-f",
            "per_page=100",
        ],
        check=False,
    )
    return data if isinstance(data, list) else []


def release_issue_mapping(workspace, review_key):
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT * FROM release_review_issues WHERE review_key=?",
            (review_key,),
        ).fetchone()
    return dict(row) if row else None


def save_release_issue_mapping(workspace, repo, branch, release_tag, issue):
    key = release_review_key(repo, branch, release_tag)
    with connect_db(workspace) as conn:
        conn.execute(
            """
            INSERT INTO release_review_issues
            (review_key, repo, branch, tag_start, issue_number, issue_url, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(review_key) DO UPDATE SET
              issue_number=excluded.issue_number,
              issue_url=excluded.issue_url,
              synced_at=excluded.synced_at
            """,
            (
                key,
                repo,
                branch,
                release_tag,
                issue["number"],
                issue.get("html_url", ""),
                now_iso(),
            ),
        )


def load_release_issue(workspace, repo, branch, release_tag):
    key = release_review_key(repo, branch, release_tag)
    mapping = release_issue_mapping(workspace, key)
    issue = None
    if mapping:
        issue = fetch_github_issue(repo, mapping["issue_number"])
    if not issue:
        issue = find_release_issue(repo, release_tag)
    if issue:
        save_release_issue_mapping(workspace, repo, branch, release_tag, issue)
    return issue


def sync_issue_checks_to_db(workspace, repo, branch, release_tag, issue_body):
    issue_items = parse_issue_checkboxes(issue_body)
    if not issue_items:
        return 0
    key = release_review_key(repo, branch, release_tag)
    stored = stored_release_todos(workspace, repo, branch, release_tag)
    by_id = {row["todo_id"]: row for row in stored}
    by_text = {normalized_todo_match_text(row["todo_text"]): row for row in stored}
    completed = []
    for item in issue_items:
        if not item["completed"]:
            continue
        row = by_id.get(item["id"]) or by_text.get(normalized_todo_match_text(item["text"]))
        if row:
            completed.append(row["todo_id"])
    if not completed:
        return 0
    with connect_db(workspace) as conn:
        for item_id in sorted(set(completed)):
            conn.execute(
                "UPDATE review_todos SET completed=1, updated_at=? WHERE todo_id=?",
                (now_iso(), item_id),
            )
    return len(set(completed))


def release_issue_context(workspace, repo, branch, release_tag):
    try:
        issue = load_release_issue(workspace, repo, branch, release_tag)
    except SystemExit:
        return ""
    if not issue:
        return ""
    sync_issue_checks_to_db(workspace, repo, branch, release_tag, issue.get("body") or "")
    comments = fetch_release_issue_comments(repo, issue["number"])
    checkboxes = parse_issue_checkboxes(issue.get("body") or "")
    feedback = []
    if checkboxes:
        feedback.append(
            "Existing GitHub checklist state:\n"
            + "\n".join(
                f"- [{'x' if item['completed'] else ' '}] {item['text']}"
                for item in checkboxes
            )
        )
    if comments:
        rendered = []
        for comment in comments[-20:]:
            body = " ".join((comment.get("body") or "").split())
            if body:
                rendered.append(f"- @{comment.get('user', {}).get('login', 'unknown')}: {truncate_pi_detail(body, 500)}")
        if rendered:
            feedback.append("Issue comments from maintainers:\n" + "\n".join(rendered))
    if not feedback:
        return ""
    return (
        "GitHub release checklist feedback exists for this release. Treat checked checklist items as resolved, "
        "and treat definitive maintainer comments as authoritative release-review input. For example, if a "
        "comment says a concern is not a problem because of specific evidence, do not re-add it unless newer "
        "digest evidence contradicts that; if a comment says the checklist is missing a real release blocker "
        "or verification item, include that item when it passes the release-review inclusion test.\n"
        + "\n\n".join(feedback)
        + "\n\n"
    )


def existing_release_review_context(workspace, repo, branch, release_tag):
    row = latest_release_review(workspace, repo, branch, release_tag)
    if not row:
        return ""
    parsed = extract_json_object(row.get("raw_output") or "")
    if parsed is None and row.get("json_path") and Path(row["json_path"]).exists():
        parsed = extract_json_object(Path(row["json_path"]).read_text(encoding="utf-8"))
    if parsed is None:
        return ""
    parsed = normalize_release_review_data(parsed)
    todos = sync_release_review_todos(workspace, repo, branch, release_tag, parsed.get("prioritized_todos", []))
    prior = {
        "verdict": parsed.get("verdict", ""),
        "verdict_reason": parsed.get("verdict_reason", ""),
        "prioritized_todos": [
            {
                "priority": todo.get("priority", ""),
                "text": todo.get("text", ""),
                "completed": bool(todo.get("completed")),
            }
            for todo in todos
        ],
        "breaking_changes": parsed.get("breaking_changes", []),
        "evidence": parsed.get("evidence", {}),
    }
    return (
        "Existing release review for this release. Use it as the continuity baseline for this regeneration: "
        "do not create a second wording of the same checklist item; preserve checked/completed items as resolved "
        "unless new evidence clearly reopens them; keep unresolved items stable when they are still valid; and "
        "only add genuinely new P0/P1 work that passes the inclusion test.\n"
        + json.dumps(prior, indent=2)
        + "\n\n"
    )


def render_release_issue_body(repo, branch, release_tag, review_row, todos):
    key = release_review_key(repo, branch, release_tag)
    marker_payload = json.dumps(
        {"repo": repo, "branch": branch, "release": release_tag, "review_key": key},
        sort_keys=True,
    )
    lines = [
        f"{RELEASE_ISSUE_MARKER} {marker_payload} -->",
        "",
        f"Synced from repo-manager release review for `{repo}` `{branch}` `{release_tag}`.",
        "",
        f"Verdict: `{review_row.get('verdict') or ''}`",
        f"Reviewed: `{review_row.get('reviewed_at') or ''}`",
        f"Head: `{review_row.get('head_sha') or ''}`",
        "",
        "## Checklist",
        "",
    ]
    if todos:
        for todo in todos:
            mark = "x" if todo.get("completed") else " "
            text = todo.get("text") or todo.get("todo_text") or ""
            priority = todo.get("priority") or ""
            prefix = f"{priority}: " if priority else ""
            lines.append(f"- [{mark}] {prefix}{text} <!-- repo-manager:todo:{todo['id']} -->")
    else:
        lines.append("No open release-review to-dos.")
    return "\n".join(lines).rstrip() + "\n"


def merge_issue_and_review_todos(issue_body, review_todos):
    completed_ids = set()
    completed_texts = set()
    for item in parse_issue_checkboxes(issue_body):
        if not item["completed"]:
            continue
        if item["id"]:
            completed_ids.add(item["id"])
        text = normalized_todo_match_text(item["text"])
        if text:
            completed_texts.add(text)
    merged = []
    for todo in review_todos:
        completed = (
            bool(todo.get("completed"))
            or todo.get("id") in completed_ids
            or normalized_todo_match_text(todo.get("text", "")) in completed_texts
        )
        merged.append({**todo, "completed": completed})
    return merged


def create_release_issue(repo, title, body):
    return gh_json(
        ["-H", "Accept: application/vnd.github+json", f"repos/{repo}/issues", "-f", f"title={title}", "-f", f"body={body}"]
    )


def update_release_issue(repo, issue_number, title, body):
    return gh_json(
        [
            "-H",
            "Accept: application/vnd.github+json",
            "-X",
            "PATCH",
            f"repos/{repo}/issues/{issue_number}",
            "-f",
            f"title={title}",
            "-f",
            f"body={body}",
        ]
    )


ANNOUNCEMENT_ISSUE_KINDS = {
    "release_notes": {
        "title_suffix": "release notes",
        "label": "release notes",
        "column": "release_highlights_output",
        "path_column": "release_highlights_path",
    },
    "announcement": {
        "title_suffix": "announcement",
        "label": "announcement",
        "column": "raw_output",
        "path_column": "markdown_path",
    },
}


def announcement_issue_key(repo, branch, release_tag, issue_kind):
    return f"{repo}|{branch}|{release_tag}|{issue_kind}|{ANNOUNCEMENT_VERSION}"


def announcement_issue_title(release_tag, issue_kind):
    return f"{release_tag} {ANNOUNCEMENT_ISSUE_KINDS[issue_kind]['title_suffix']}"


def announcement_issue_mapping(workspace, issue_key):
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT * FROM release_announcement_issues WHERE issue_key=?",
            (issue_key,),
        ).fetchone()
    return dict(row) if row else None


def save_announcement_issue_mapping(workspace, repo, branch, release_tag, issue_kind, issue):
    key = announcement_issue_key(repo, branch, release_tag, issue_kind)
    with connect_db(workspace) as conn:
        conn.execute(
            """
            INSERT INTO release_announcement_issues
            (issue_key, repo, branch, tag_start, issue_kind, issue_number, issue_url, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(issue_key) DO UPDATE SET
              issue_number=excluded.issue_number,
              issue_url=excluded.issue_url,
              synced_at=excluded.synced_at
            """,
            (
                key,
                repo,
                branch,
                release_tag,
                issue_kind,
                issue["number"],
                issue.get("html_url", ""),
                now_iso(),
            ),
        )


def load_announcement_issue(workspace, repo, branch, release_tag, issue_kind):
    key = announcement_issue_key(repo, branch, release_tag, issue_kind)
    mapping = announcement_issue_mapping(workspace, key)
    issue = None
    if mapping:
        issue = fetch_github_issue(repo, mapping["issue_number"])
    if not issue:
        issue = find_github_issue_by_title(repo, announcement_issue_title(release_tag, issue_kind))
    if issue:
        save_announcement_issue_mapping(workspace, repo, branch, release_tag, issue_kind, issue)
    return issue


def render_issue_comments(repo, issue):
    comments = fetch_release_issue_comments(repo, issue["number"])
    rendered = []
    for comment in comments[-20:]:
        body = " ".join((comment.get("body") or "").split())
        if body:
            rendered.append(f"- @{comment.get('user', {}).get('login', 'unknown')}: {truncate_pi_detail(body, 500)}")
    return rendered


def announcement_issues_context(workspace, repo, branch, release_tag):
    sections = []
    try:
        for issue_kind, meta in ANNOUNCEMENT_ISSUE_KINDS.items():
            issue = load_announcement_issue(workspace, repo, branch, release_tag, issue_kind)
            if not issue:
                continue
            comments = render_issue_comments(repo, issue)
            if comments:
                sections.append(
                    f"Comments on the GitHub {meta['label']} issue `{issue.get('title', '')}`:\n"
                    + "\n".join(comments)
                )
    except SystemExit:
        return ""
    if not sections:
        return ""
    return (
        "GitHub announcement artifact feedback exists for this release. Treat maintainer comments on these "
        "issues as editorial and factual input when regenerating the corresponding artifact, while still obeying "
        "the release-announcement skill contract and the commit-review evidence. If comments request missing "
        "coverage, clearer wording, or removal of an inaccurate claim, incorporate that feedback when supported "
        "by the source material.\n"
        + "\n\n".join(sections)
        + "\n\n"
    )


def announcement_artifact_content(row, issue_kind):
    meta = ANNOUNCEMENT_ISSUE_KINDS[issue_kind]
    content = row.get(meta["column"]) or ""
    path = row.get(meta["path_column"]) or ""
    if not content and path and Path(path).exists():
        content = Path(path).read_text(encoding="utf-8")
    return content.rstrip() + "\n" if content.strip() else ""


def existing_announcement_context(workspace, repo, branch, release_tag):
    row = latest_announcement(workspace, repo, branch, release_tag)
    if not row:
        return ""
    sections = []
    release_notes = announcement_artifact_content(row, "release_notes")
    if release_notes:
        sections.append("Existing release notes artifact:\n```markdown\n" + release_notes.rstrip() + "\n```")
    announcement = announcement_artifact_content(row, "announcement")
    if announcement:
        sections.append("Existing announcement artifact:\n```markdown\n" + announcement.rstrip() + "\n```")
    if not sections:
        return ""
    return (
        "Existing announcement artifacts for this same release. Use these as the continuity baseline: preserve "
        "accepted structure, story grouping, and wording when still accurate; change content only to incorporate "
        "new commit-review evidence, required validation fixes, or maintainer feedback from the GitHub artifact "
        "issues. Do not rewrite purely for novelty.\n"
        + "\n\n".join(sections)
        + "\n\n"
    )


def render_announcement_issue_body(repo, branch, release_tag, issue_kind, row, content):
    key = announcement_issue_key(repo, branch, release_tag, issue_kind)
    marker_payload = json.dumps(
        {"repo": repo, "branch": branch, "release": release_tag, "issue_key": key, "kind": issue_kind},
        sort_keys=True,
    )
    meta = ANNOUNCEMENT_ISSUE_KINDS[issue_kind]
    return (
        f"<!-- repo-manager:{issue_kind} {marker_payload} -->\n\n"
        f"Synced from repo-manager {meta['label']} artifact for `{repo}` `{branch}` `{release_tag}`.\n\n"
        f"Generated: `{row.get('generated_at') or ''}`\n"
        f"Head: `{row.get('head_sha') or ''}`\n\n"
        "## Content\n\n"
        f"{content.rstrip()}\n"
    )


def sync_announcement_issue(workspace, repo, branch, release_tag, row, issue_kind):
    content = announcement_artifact_content(row, issue_kind)
    if not content:
        return None
    title = announcement_issue_title(release_tag, issue_kind)
    issue = load_announcement_issue(workspace, repo, branch, release_tag, issue_kind)
    body = render_announcement_issue_body(repo, branch, release_tag, issue_kind, row, content)
    if issue:
        issue = update_release_issue(repo, issue["number"], title, body)
        action = "Updated"
    else:
        issue = create_release_issue(repo, title, body)
        action = "Created"
    save_announcement_issue_mapping(workspace, repo, branch, release_tag, issue_kind, issue)
    print(f"{action} {ANNOUNCEMENT_ISSUE_KINDS[issue_kind]['label']} issue: {issue.get('html_url', '')}")
    return issue


def review_breaking_changes(review_row):
    """Canonical breaking-change list from a stored release-review row, or None if no review.

    Returns None when no release review exists for the release (nothing to reconcile against),
    and a list (possibly empty) when one does. The row stores the full artifact JSON in
    raw_output; fall back to the on-disk artifact if that is somehow unparseable.
    """
    if not review_row:
        return None
    parsed = extract_json_object(review_row.get("raw_output") or "")
    if parsed is None:
        path = review_row.get("json_path")
        if path and Path(path).exists():
            parsed = extract_json_object(Path(path).read_text(encoding="utf-8"))
    if parsed is None:
        return None
    return release_review_breaking_changes(parsed)


def latest_announcement(workspace, repo, branch, tag_start):
    with connect_db(workspace) as conn:
        row = conn.execute(
            """
            SELECT * FROM release_announcements
            WHERE repo=? AND branch=? AND tag_start=? AND skill_version=?
            ORDER BY generated_at DESC
            LIMIT 1
            """,
            (repo, branch, tag_start, ANNOUNCEMENT_VERSION),
        ).fetchone()
    return dict(row) if row else None


def read_announcement_markdown(row):
    if not row:
        return ""
    path = row.get("markdown_path")
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8")
    return row.get("raw_output") or ""


def prior_announcements(workspace, repo, branch, release_tag, limit=3):
    with connect_db(workspace) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT *
                FROM release_announcements
                WHERE repo=? AND branch=? AND tag_start<>? AND skill_version=?
                """,
                (repo, branch, release_tag, ANNOUNCEMENT_VERSION),
            ).fetchall()
        ]
    if release_tag != "vNext":
        current_version = version_parts(release_tag)
        rows = [row for row in rows if version_parts(row.get("tag_start")) < current_version]
    rows.sort(key=lambda row: (version_parts(row.get("tag_start")), row.get("generated_at", "")), reverse=True)
    return rows[:limit]


def announcement_style_context(rows):
    if not rows:
        return ""
    sections = []
    for row in rows:
        markdown = read_announcement_markdown(row).strip()
        if not markdown:
            continue
        sections.append(f"### {row.get('tag_start')}\n\n{markdown}")
    if not sections:
        return ""
    return (
        "Use these prior release announcements as style references. Match their voice, level of detail, "
        "section density, and Discord-friendly formatting where appropriate, but do not copy facts from them "
        "into the new release. Do not reuse their closing sentence verbatim; vary the ending so release posts "
        "do not become repetitive:\n\n"
        + "\n\n---\n\n".join(sections)
        + "\n\n"
    )


def fetch_github_release_body(repo, tag):
    result = run(
        ["gh", "api", f"repos/{repo}/releases/tags/{tag}", "--jq", ".body // \"\""],
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def markdown_heading_level(line):
    match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    if not match:
        return None, ""
    return len(match.group(1)), match.group(2).strip()


def extract_release_note_sections(markdown):
    wanted = {"headline", "breaking changes"}
    lines = (markdown or "").splitlines()
    sections = {}
    current = None
    current_level = None
    for line in lines:
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


def release_highlights_reference_context(workspace, repo, release_tag):
    sections = []
    for tag in prior_release_tags(repo, workspace, release_tag):
        extracted = extract_release_note_sections(fetch_github_release_body(repo, tag))
        if extracted:
            sections.append(f"### {tag}\n\n{extracted}")
    if not sections:
        return ""
    return (
        "Use these Headline and Breaking Changes sections from the last three GitHub releases as the "
        "style and structure reference for the new website release highlights artifact. Match their level of abstraction, "
        "but do not copy old facts into the new release:\n\n"
        + "\n\n---\n\n".join(sections)
        + "\n\n"
    )


def github_repo_url(repo):
    return f"https://github.com/{repo}.git"


def pages_path(workspace):
    return workspace / CONFIG_DIR / "pages"


def ensure_pages_checkout(workspace, repo, branch):
    path = pages_path(workspace)
    url = github_repo_url(repo)
    if (path / ".git").exists():
        dirty = run(["git", "-C", str(path), "status", "--porcelain"]).stdout.strip()
        if dirty:
            raise SystemExit(
                f"Pages checkout has uncommitted changes at {path}. "
                "Commit, discard, or remove that checkout before publishing."
            )
        run(["git", "-C", str(path), "fetch", "origin", branch])
        run(["git", "-C", str(path), "checkout", branch])
        run(["git", "-C", str(path), "pull", "--ff-only", "origin", branch])
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--branch", branch, "--single-branch", url, str(path)])
    return path


def ensure_wiki_checkout(workspace, repo):
    """Clone or refresh the target repo's GitHub wiki into .repo-manager/wiki.

    Returns the checkout path, or None if the wiki is unavailable (no wiki, no
    auth, or offline) so callers can fall back to the proposed announcements.
    """
    path = workspace / CONFIG_DIR / "wiki"
    url = f"https://github.com/{repo}.wiki.git"
    if (path / ".git").exists():
        run(["git", "-C", str(path), "pull", "--ff-only"], check=False)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    result = run(["git", "clone", "--depth", "1", url, str(path)], check=False)
    if result.returncode != 0:
        return None
    return path


def write_release_announcement_row(
    conn, workspace, repo, branch, release_tag, range_start, head, discord_markdown, highlights_markdown
):
    """Write announcement artifact files and upsert the row using an open connection.

    Unlike store_release_announcement, this also persists the website highlights,
    so the Discord text (from the wiki) and the Headline/Breaking Changes (from
    the GitHub release page) can be saved together.
    """
    highlights_file, markdown_file = release_announcement_artifact_paths(workspace, repo, release_tag, head)
    write_text(markdown_file, discord_markdown.rstrip() + "\n")
    raw = Path(markdown_file).read_text(encoding="utf-8")
    highlights_raw = ""
    highlights_path = ""
    if highlights_markdown and highlights_markdown.strip():
        write_text(highlights_file, highlights_markdown.rstrip() + "\n")
        highlights_raw = Path(highlights_file).read_text(encoding="utf-8")
        highlights_path = str(highlights_file)
    conn.execute(
        """
        DELETE FROM release_announcements
        WHERE repo=? AND branch=? AND tag_start=? AND skill_version=?
        """,
        (repo, branch, release_tag, ANNOUNCEMENT_VERSION),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO release_announcements
        (repo, branch, tag_start, range_start, head_sha, raw_output, markdown_path,
         release_highlights_output, release_highlights_path, generated_at, skill_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            repo,
            branch,
            release_tag,
            range_start,
            head,
            raw,
            str(markdown_file),
            highlights_raw,
            highlights_path,
            now_iso(),
            ANNOUNCEMENT_VERSION,
        ),
    )
    return markdown_file


def override_announcements_from_sources(workspace, repo, branch, conn, published_announcements, wiki_page):
    """Replace proposed announcements with authoritative online copies.

    Discord text comes from the wiki page; the website highlights come from each
    version's GitHub release page body. Metadata (range_start/head_sha/branch) is
    reused from the published announcement row when available, so no local git
    checkout is required.
    """
    from repo_manager import sync_down

    wiki = ensure_wiki_checkout(workspace, repo)
    if wiki is None:
        print("Wiki unavailable; keeping proposed announcements.")
        return 0
    page = wiki / f"{wiki_page}.md"
    if not page.exists():
        print(f"Wiki page {wiki_page}.md not found; keeping proposed announcements.")
        return 0
    wiki_announcements = sync_down.parse_wiki_announcements(page.read_text(encoding="utf-8"))
    meta = sync_down.announcement_metadata(published_announcements)
    count = 0
    for tag, discord_markdown in wiki_announcements.items():
        if not discord_markdown.strip():
            continue
        info = meta.get(tag, {})
        tag_branch = info.get("branch") or branch
        range_start = info.get("range_start") or ""
        head = info.get("head_sha") or ""
        highlights = extract_release_note_sections(fetch_github_release_body(repo, tag))
        write_release_announcement_row(
            conn, workspace, repo, tag_branch, tag, range_start, head, discord_markdown, highlights
        )
        count += 1
    return count


def import_published_data(workspace, repo, branch, website_branch, target_dir, wiki_page):
    """Sync the published dashboard back into the local DB (merge/upsert)."""
    from repo_manager import sync_down

    checkout = ensure_pages_checkout(workspace, repo, website_branch)
    target_rel = Path(target_dir)
    if target_rel.is_absolute() or ".." in target_rel.parts:
        raise SystemExit("--target-dir must be a relative path inside the website branch checkout.")
    index_path = checkout / target_rel / "index.html"
    if not index_path.exists():
        raise SystemExit(
            f"No published dashboard found at {website_branch}:{target_rel.as_posix()}/index.html. "
            "Has publish-pages run for this repo?"
        )
    data = sync_down.extract_static_data(index_path.read_text(encoding="utf-8"))
    published_announcements = data.get("release_announcements", [])
    with connect_db(workspace) as conn:
        commits = sync_down.upsert_commit_reviews(conn, data.get("commit_reviews", []))
        releases = sync_down.upsert_release_reviews(conn, data.get("release_reviews", []))
        announcements = sync_down.upsert_release_announcements(conn, published_announcements)
        overridden = override_announcements_from_sources(
            workspace, repo, branch, conn, published_announcements, wiki_page
        )
    return {
        "commit_reviews": commits,
        "release_reviews": releases,
        "announcements": announcements,
        "announcements_overridden": overridden,
    }


def publish_pages_content(workspace, repo, website_branch, target_dir):
    from repo_manager.web import export_static_site

    checkout = ensure_pages_checkout(workspace, repo, website_branch)
    export_dir = workspace / CONFIG_DIR / "pages-export"
    if export_dir.exists():
        shutil.rmtree(export_dir)
    export_static_site(workspace, export_dir)

    target_rel = Path(target_dir)
    if target_rel.is_absolute() or ".." in target_rel.parts:
        raise SystemExit("--target-dir must be a relative path inside the website branch checkout.")
    target = checkout / target_rel
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(export_dir, target)
    return checkout, target_rel


def export_pages_preview(workspace, output_dir):
    from repo_manager.web import export_static_site

    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    export_static_site(workspace, output_dir)
    return output_dir


def commit_and_push_pages(checkout, branch, target_rel, message):
    run(["git", "-C", str(checkout), "add", "-A", target_rel.as_posix()])
    status = run(["git", "-C", str(checkout), "status", "--porcelain", target_rel.as_posix()]).stdout.strip()
    if not status:
        print("Pages dashboard already up to date.")
        return
    run(["git", "-C", str(checkout), "commit", "-m", message])
    run(["git", "-C", str(checkout), "pull", "--rebase", "origin", branch])
    run(["git", "-C", str(checkout), "push", "origin", branch])


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
    if not args.no_pull:
        try:
            summary = import_published_data(
                workspace, args.repo, args.branch, args.website_branch, args.target_dir, args.wiki_page
            )
            print(
                f"Pulled online data: {summary['commit_reviews']} commit reviews, "
                f"{summary['release_reviews']} release reviews, "
                f"{summary['announcements']} announcements "
                f"({summary['announcements_overridden']} from wiki/release pages)"
            )
        except SystemExit as exc:
            print(f"Skipped pulling online data: {exc}")
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
    release_tag = args.release or args.tag_start or ""
    if release_tag:
        release_tag, branch = resolve_release_state(workspace, repo, branch, release_tag, args.branch is not None)
    json_file = commit_artifact_paths(workspace, repo, args.commit)
    prompt = (
        f"/skill:commit-review {repo} {args.commit}\n\n"
        f"Write the machine-readable JSON result to: {json_file}\n"
        "The JSON must match the schema required by the skill."
    )
    started = time.monotonic()
    run_pi("commit-review", prompt, workspace)
    require_files(json_file)
    stamp_generation_seconds(json_file, started)
    range_start = args.since or (infer_range_start(repo, workspace, release_tag) if release_tag else "")
    store_commit_review(workspace, repo, branch, release_tag, range_start, args.commit, json_file)


def cmd_sweep(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    release_tag, branch = resolve_release_state(workspace, repo, branch, args.release, args.branch is not None)
    range_start = args.since or infer_range_start(repo, workspace, release_tag)
    commits = list_commits(repo, workspace, branch, range_start)
    print(f"Found {len(commits)} commits in {range_start}..origin/{branch} for {release_tag}")
    for commit in commits:
        if review_exists(workspace, repo, commit) and not args.force:
            print(f"Skipping existing review for {commit}")
            continue
        print(f"Reviewing {commit}")
        json_file = commit_artifact_paths(workspace, repo, commit)
        prompt = (
        f"/skill:commit-review {repo} {commit}\n\n"
            f"This commit is part of release {release_tag}, covering range {range_start}..{branch}.\n"
            f"Write the machine-readable JSON result to: {json_file}\n"
            "The JSON must match the schema required by the skill."
        )
        started = time.monotonic()
        run_pi("commit-review", prompt, workspace)
        require_files(json_file)
        stamp_generation_seconds(json_file, started)
        store_commit_review(workspace, repo, branch, release_tag, range_start, commit, json_file)


def cmd_review_pr(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    meta = fetch_pr_metadata(repo, args.pr_number)
    if str(meta.get("state", "")).upper() != "OPEN":
        print(f"Warning: PR #{args.pr_number} is {meta.get('state', 'unknown').lower()}, not open.")
    if meta.get("isDraft"):
        print(f"Warning: PR #{args.pr_number} is a draft.")
    generate_pr_review(workspace, repo, meta)


def cmd_sweep_prs(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    prs = list_open_prs(repo, args.limit)
    prs.sort(key=lambda meta: meta["number"])
    print(f"Found {len(prs)} open PRs in {repo}")
    generated, skipped, failed = 0, 0, []
    for meta in prs:
        number = meta["number"]
        if meta.get("isDraft") and not args.include_drafts:
            print(f"Skipping draft PR #{number}")
            skipped += 1
            continue
        existing = latest_pr_review(workspace, repo, number)
        if existing and existing.get("head_sha") == meta.get("headRefOid") and not args.force:
            print(f"Skipping PR #{number} (review current at {meta.get('headRefOid', '')[:7]})")
            skipped += 1
            continue
        print(f"Reviewing PR #{number}: {meta.get('title', '')}")
        try:
            generate_pr_review(workspace, repo, meta)
            generated += 1
        except SystemExit as exc:
            if exc.code in (130, None):
                raise
            print(f"PR #{number} review failed: {exc}")
            failed.append(number)
    summary = f"Swept {len(prs)} open PRs: {generated} reviewed, {skipped} skipped"
    if failed:
        summary += f", {len(failed)} failed ({', '.join(f'#{n}' for n in failed)})"
    print(summary)


def cmd_release_review(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    release_tag, branch = resolve_release_state(workspace, repo, branch, args.release, args.branch is not None)
    reviews = load_reviews(workspace, repo, branch, release_tag)
    if not reviews:
        raise SystemExit("No commit reviews found for that repo/branch/release.")
    range_start = args.since or common_range_start(reviews) or infer_range_start(repo, workspace, release_tag)
    head = head_sha(repo, workspace, branch)
    json_file, _ = release_artifact_paths(workspace, repo, release_tag, head, "review")
    context_json = release_review_context(workspace, reviews)
    github_context = release_issue_context(workspace, repo, branch, release_tag)
    prior_review_context = existing_release_review_context(workspace, repo, branch, release_tag)
    digest_context = (
        "Per-commit digest of the stored commit reviews. open_todos reflect maintainer completion state in the "
        "repo-manager database; completed_todos counts are resolved evidence:\n"
        + context_json
        + "\n\nFinal reminders: a to-do earns its place only if the maintainer would regret shipping without it "
        "AND users would notice the consequence; omit everything else entirely (there is no P2). Each to-do is "
        "one actionable sentence — action, user-visible stake, how to check — marked P0 (do not ship until "
        "resolved) or P1 (verify before shipping); merge related concerns into shared to-dos. End each to-do "
        "with an attribution tag naming who to ask and the source PR(s), pulled from the digest pr_number/author "
        "fields: '(#1234, @author)'. The verdict is "
        "computed from your to-do list, so you cannot contradict it. verdict_reason, to-dos, and evidence are "
        "for a human who has never seen this digest: name the feature or behavior, and let verdict_reason be "
        "just your one-or-two-sentence answer to 'can we ship?'.\n"
    )
    feedback = load_release_review_feedback(workspace, repo, release_tag)
    if feedback:
        print("Resuming with validation feedback from a previous interrupted release-review run.", flush=True)
    pending_dir = json_file.parent / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    max_attempts = 3
    data = None
    started = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        pending_json = pending_dir / f"{json_file.stem}.{int(time.time() * 1000)}.pending.json"
        prompt = (
            f"/skill:release-review\n\nRepo: {repo}\nBranch: {branch}\n"
            f"Release: {release_tag}\nRange start: {range_start or 'unknown'}\nHead SHA: {head}\n\n"
            f"Write the machine-readable JSON result to: {pending_json}\n\n"
            f"{feedback}"
            f"{github_context}"
            f"{prior_review_context}"
            f"{digest_context}"
        )
        try:
            output = run_pi("release-review", prompt, workspace)
        except SystemExit as exc:
            if exc.code in (130, None):
                raise
            if attempt == max_attempts:
                raise
            print(f"Pi run failed (exit {exc.code}); retrying with the same instructions.", flush=True)
            continue
        if not pending_json.exists() and write_json_artifact_from_output(pending_json, output):
            print(f"Wrote release-review artifact from Pi output: {pending_json}")
        artifact_raw = ""
        errors = []
        candidate = None
        if pending_json.exists():
            artifact_raw = pending_json.read_text(encoding="utf-8")
            candidate = extract_json_object(artifact_raw)
            if candidate is None:
                errors.append("Artifact file must contain a valid JSON object.")
            else:
                candidate = normalize_release_review_data(candidate)
                errors.extend(release_review_validation_errors(candidate))
        else:
            errors.append(f"Expected release-review JSON file was not created: {pending_json}")
        if not errors:
            data = candidate
            break
        error_list = "\n".join(f"- {error}" for error in errors)
        if attempt == max_attempts:
            raise SystemExit(f"Release review failed validation after {max_attempts} attempts:\n{error_list}")
        print(f"\nAttempt {attempt} failed validation; asking Pi to revise:\n{error_list}\n", flush=True)
        save_release_review_feedback(workspace, repo, release_tag, error_list, artifact_raw)
        feedback = build_release_review_feedback(error_list, artifact_raw)
    clear_release_review_feedback(workspace, repo, release_tag)
    data["repo"] = repo
    data["branch"] = branch
    data["tag_start"] = release_tag
    data["range_start"] = range_start
    data["head_sha"] = head
    data["generation_seconds"] = round(time.monotonic() - started, 1)
    json_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    raw = json.dumps(data, indent=2)
    verdict = data.get("verdict", "")
    with connect_db(workspace) as conn:
        conn.execute(
            """
            DELETE FROM release_reviews
            WHERE repo=? AND branch=? AND tag_start=? AND rubric_version=?
            """,
            (repo, branch, release_tag, RELEASE_RUBRIC_VERSION),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO release_reviews
            (repo, branch, tag_start, range_start, head_sha, verdict, raw_output, json_path, reviewed_at, skill_version, rubric_version, generation_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo,
                branch,
                release_tag,
                range_start,
                head,
                verdict,
                raw,
                str(json_file),
                now_iso(),
                __version__,
                RELEASE_RUBRIC_VERSION,
                float(data.get("generation_seconds") or 0),
            ),
        )
        normalize_review_todos(conn, "release", release_review_key(repo, branch, release_tag), data.get("prioritized_todos", []))


def cmd_sync_release_review_issue(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    release_tag, branch = resolve_release_state(workspace, repo, branch, args.release, args.branch is not None)
    synced = False

    review_row = latest_release_review(workspace, repo, branch, release_tag)
    if review_row:
        data = extract_json_object(review_row.get("raw_output") or "")
        if data is None and review_row.get("json_path") and Path(review_row["json_path"]).exists():
            data = extract_json_object(Path(review_row["json_path"]).read_text(encoding="utf-8"))
        if data is None:
            raise SystemExit("Stored release review does not contain valid JSON.")
        data = normalize_release_review_data(data)
        review_todos = sync_release_review_todos(workspace, repo, branch, release_tag, data.get("prioritized_todos", []))
        title = release_issue_title(release_tag)
        issue = load_release_issue(workspace, repo, branch, release_tag)
        existing_body = (issue.get("body") or "") if issue else ""
        if issue:
            checked = sync_issue_checks_to_db(workspace, repo, branch, release_tag, existing_body)
            if checked:
                review_todos = sync_release_review_todos(
                    workspace, repo, branch, release_tag, data.get("prioritized_todos", [])
                )
                print(f"Marked {checked} release-review to-do(s) complete from the GitHub issue.")
        merged_todos = merge_issue_and_review_todos(existing_body, review_todos)
        body = render_release_issue_body(repo, branch, release_tag, review_row, merged_todos)
        if issue:
            issue = update_release_issue(repo, issue["number"], title, body)
            action = "Updated"
        else:
            issue = create_release_issue(repo, title, body)
            action = "Created"
        save_release_issue_mapping(workspace, repo, branch, release_tag, issue)
        print(f"{action} release checklist issue: {issue.get('html_url', '')}")
        synced = True

    announcement_row = latest_announcement(workspace, repo, branch, release_tag)
    if announcement_row:
        for issue_kind in ANNOUNCEMENT_ISSUE_KINDS:
            if sync_announcement_issue(workspace, repo, branch, release_tag, announcement_row, issue_kind):
                synced = True

    if not synced:
        raise SystemExit(
            f"No stored release review or announcement found for {repo} {branch} {release_tag}. "
            "Run `repo-manager release-review` or `repo-manager announce` first."
        )


def announcement_feedback_file(workspace, repo, release_tag, kind="announce"):
    base = artifact_dir(workspace, repo, "releases")
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", release_tag)
    pending_dir = base / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    return pending_dir / f"{safe_tag}.{kind}-feedback.json"


def build_release_review_feedback(error_list, artifact_raw):
    section = ""
    if (artifact_raw or "").strip():
        section = "Previous attempt:\n```json\n" + artifact_raw.strip() + "\n```\n\n"
    return (
        "A previous attempt at this task failed validation. Fix every problem listed below and write the "
        "corrected JSON to the path given above.\n"
        f"Validation problems:\n{error_list}\n\n{section}"
    )


def load_release_review_feedback(workspace, repo, release_tag):
    path = announcement_feedback_file(workspace, repo, release_tag, kind="release-review")
    if not path.exists():
        return ""
    try:
        data = read_json(path)
    except (json.JSONDecodeError, OSError):
        return ""
    return build_release_review_feedback(data.get("errors", ""), data.get("artifact", ""))


def save_release_review_feedback(workspace, repo, release_tag, error_list, artifact_raw):
    path = announcement_feedback_file(workspace, repo, release_tag, kind="release-review")
    path.write_text(
        json.dumps({"errors": error_list, "artifact": artifact_raw, "saved_at": now_iso()}, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_release_review_feedback(workspace, repo, release_tag):
    path = announcement_feedback_file(workspace, repo, release_tag, kind="release-review")
    if path.exists():
        path.unlink()


def build_announcement_feedback(error_list, release_highlights_raw, raw):
    previous_sections = []
    if (release_highlights_raw or "").strip():
        previous_sections.append(
            "Previous website release highlights attempt:\n```markdown\n" + release_highlights_raw.strip() + "\n```"
        )
    if (raw or "").strip():
        previous_sections.append("Previous Discord announcement attempt:\n```markdown\n" + raw.strip() + "\n```")
    return (
        "A previous attempt at this task failed validation. Fix every problem listed below while keeping the "
        "content accurate, then write corrected versions of both files to the paths given above.\n"
        f"Validation problems:\n{error_list}\n\n" + "\n\n".join(previous_sections) + "\n\n"
    )


def load_announcement_feedback(workspace, repo, release_tag):
    path = announcement_feedback_file(workspace, repo, release_tag)
    if not path.exists():
        return ""
    try:
        data = read_json(path)
    except (json.JSONDecodeError, OSError):
        return ""
    return build_announcement_feedback(
        data.get("errors", ""),
        data.get("release_highlights", ""),
        data.get("announcement", ""),
    )


def save_announcement_feedback(workspace, repo, release_tag, error_list, release_highlights_raw, raw):
    path = announcement_feedback_file(workspace, repo, release_tag)
    path.write_text(
        json.dumps(
            {
                "errors": error_list,
                "release_highlights": release_highlights_raw,
                "announcement": raw,
                "saved_at": now_iso(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def clear_announcement_feedback(workspace, repo, release_tag):
    path = announcement_feedback_file(workspace, repo, release_tag)
    if path.exists():
        path.unlink()


def cmd_announce(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    release_tag, branch = resolve_release_state(workspace, repo, branch, args.release, args.branch is not None)
    reviews = load_reviews(workspace, repo, branch, release_tag)
    if not reviews:
        raise SystemExit("No commit reviews found for that repo/branch/release.")
    range_start = args.since or common_range_start(reviews) or infer_range_start(repo, workspace, release_tag)
    head = release_head_sha(repo, workspace, branch, release_tag)
    release_highlights_file, markdown_file = release_announcement_artifact_paths(workspace, repo, release_tag, head)
    release_highlights_context = release_highlights_reference_context(workspace, repo, release_tag)
    prior_rows = prior_announcements(workspace, repo, branch, release_tag)
    style_context = announcement_style_context(prior_rows)
    canonical_breaking = review_breaking_changes(
        latest_release_review(workspace, repo, branch, release_tag)
    )
    if canonical_breaking is None:
        print(
            "Warning: no release review found for this release; skipping breaking-change reconciliation. "
            "Run `release-review` first so the announcement's breaking changes are checked against it.",
            flush=True,
        )
    breaking_context = canonical_breaking_changes_context(canonical_breaking)
    issue_feedback = announcement_issues_context(workspace, repo, branch, release_tag)
    continuity_context = existing_announcement_context(workspace, repo, branch, release_tag)
    review_context = (
        "Commit summaries for this release (the announcement's only source material):\n"
        + announcement_review_context(workspace, reviews)
        + "\n\nFinal editorial reminders: tell the release as 3-5 stories, one Discord section each; if two "
        "candidate sections would answer the same reader question, they are one story, and leftover changes that "
        "are not a story become Additional Improvements bullets. Describe outcomes, never the work behind them — "
        "credit people as a clause in the feature sentence, and let enabling fixes be subsumed by the outcome "
        "they enabled.\n"
    )
    feedback = load_announcement_feedback(workspace, repo, release_tag)
    if feedback:
        print("Resuming with validation feedback from a previous interrupted announce run.", flush=True)
    max_attempts = 3
    started = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        pending_release_highlights_file, pending_markdown_file = (
            pending_release_announcement_artifact_paths(workspace, repo, release_tag, head)
        )
        prompt = (
            f"/skill:release-announcement\n\nRepo: {repo}\nBranch: {branch}\n"
            f"Release: {release_tag}\nRange start: {range_start or 'unknown'}\nHead SHA: {head}\n\n"
            f"Write the website release highlights Markdown to: {pending_release_highlights_file}\n"
            f"Then write the Discord-friendly Markdown announcement to: {pending_markdown_file}\n\n"
            f"{release_highlights_context}"
            f"{style_context}"
            f"{breaking_context}"
            f"{feedback}"
            f"{issue_feedback}"
            f"{continuity_context}"
            f"{review_context}"
        )
        try:
            run_pi("release-announcement", prompt, workspace)
        except SystemExit as exc:
            if exc.code in (130, None):
                raise
            if attempt == max_attempts:
                raise
            print(f"Pi run failed (exit {exc.code}); retrying with the same instructions.", flush=True)
            continue
        release_highlights_raw = ""
        raw = ""
        errors = []
        if Path(pending_release_highlights_file).exists():
            release_highlights_raw = Path(pending_release_highlights_file).read_text(encoding="utf-8")
            errors.extend(release_highlights_validation_errors(release_highlights_raw))
        else:
            errors.append(f"Expected release highlights file was not created: {pending_release_highlights_file}")
        if Path(pending_markdown_file).exists():
            raw = Path(pending_markdown_file).read_text(encoding="utf-8")
            errors.extend(announcement_nonempty_errors(raw))
        else:
            errors.append(f"Expected announcement file was not created: {pending_markdown_file}")
        errors.extend(announcement_breaking_changes_errors(release_highlights_raw, raw, canonical_breaking))
        if not errors:
            break
        error_list = "\n".join(f"- {error}" for error in errors)
        if attempt == max_attempts:
            raise SystemExit(
                f"Announcement failed validation after {max_attempts} attempts:\n{error_list}"
            )
        print(f"\nAttempt {attempt} failed validation; asking Pi to revise:\n{error_list}\n", flush=True)
        save_announcement_feedback(workspace, repo, release_tag, error_list, release_highlights_raw, raw)
        feedback = build_announcement_feedback(error_list, release_highlights_raw, raw)
    clear_announcement_feedback(workspace, repo, release_tag)
    write_text(release_highlights_file, release_highlights_raw)
    write_text(markdown_file, raw)
    with connect_db(workspace) as conn:
        conn.execute(
            """
            DELETE FROM release_announcements
            WHERE repo=? AND branch=? AND tag_start=? AND skill_version=?
            """,
            (repo, branch, release_tag, ANNOUNCEMENT_VERSION),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO release_announcements
            (repo, branch, tag_start, range_start, head_sha, raw_output, markdown_path,
             release_highlights_output, release_highlights_path, generated_at, skill_version,
             generation_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repo,
                branch,
                release_tag,
                range_start,
                head,
                raw,
                str(markdown_file),
                release_highlights_raw,
                str(release_highlights_file),
                now_iso(),
                ANNOUNCEMENT_VERSION,
                round(time.monotonic() - started, 1),
            ),
        )


def read_stdin_or_file(path):
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def store_release_announcement(workspace, repo, branch, release_tag, range_start, head, markdown):
    _, markdown_file = release_announcement_artifact_paths(workspace, repo, release_tag, head)
    write_text(markdown_file, markdown.rstrip() + "\n")
    raw = Path(markdown_file).read_text(encoding="utf-8")
    with connect_db(workspace) as conn:
        conn.execute(
            """
            DELETE FROM release_announcements
            WHERE repo=? AND branch=? AND tag_start=? AND skill_version=?
            """,
            (repo, branch, release_tag, ANNOUNCEMENT_VERSION),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO release_announcements
            (repo, branch, tag_start, range_start, head_sha, raw_output, markdown_path, generated_at, skill_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo, branch, release_tag, range_start, head, raw, str(markdown_file), now_iso(), ANNOUNCEMENT_VERSION),
        )
    return markdown_file


def cmd_override_announcement(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    release_tag, branch = resolve_release_state(workspace, repo, branch, args.release, args.branch is not None)
    range_start = args.since or infer_range_start(repo, workspace, release_tag)
    head = args.head or release_head_sha(repo, workspace, branch, release_tag)
    markdown = read_stdin_or_file(args.file)
    if not markdown.strip():
        raise SystemExit("Announcement markdown is empty.")
    markdown_file = store_release_announcement(workspace, repo, branch, release_tag, range_start, head, markdown)
    print(f"Stored announcement override for {release_tag}: {markdown_file}")


def cmd_status(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    configured_branch = args.branch or config.get("branch", "main")
    info = release_lifecycle_info(workspace, repo, configured_branch, args.release, args.branch is not None)
    counts = release_lifecycle_counts(workspace, repo, info["selected_branch"], info["selected_release"])
    phase = "next development"
    if info["selected_release"] != "vNext":
        phase = "release branch" if info["release_branch_exists"] else "pre-branch release prep"

    print("Release Lifecycle")
    print(f"Repo: {info['repo']}")
    print(f"Requested release: {info['requested_release']}")
    print(f"CMake release: {info['cmake_release'] or 'unknown'}")
    print(f"Latest v* tag: {info['latest_tag'] or 'none'}")
    print(f"Selected release: {info['selected_release']}")
    print(f"Configured branch: {info['configured_branch']}")
    if info["release_branch"]:
        exists = "yes" if info["release_branch_exists"] else "no"
        print(f"Release branch: {info['release_branch']} ({exists})")
    else:
        print("Release branch: none")
    print(f"Selected branch: {info['selected_branch']}")
    print(f"Lifecycle phase: {phase}")
    print()
    print("Local State")
    print(f"Commit reviews: {counts['commit_reviews']}")
    print(f"Release reviews: {counts['release_reviews']}")
    print(f"Announcements: {counts['announcements']}")
    print(f"Release to-dos: {counts['open_release_todos']} open, {counts['completed_release_todos']} completed")
    print(f"Checklist issues mapped: {counts['checklist_issues']}")
    print(f"Announcement issues mapped: {counts['announcement_issues']}")


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


def pr_review_rows(workspace):
    with connect_db(workspace) as conn:
        rows = conn.execute(
            "SELECT * FROM pr_reviews WHERE rubric_version=? ORDER BY reviewed_at, pr_number",
            (PR_RUBRIC_VERSION,),
        ).fetchall()
    return [dict(row) for row in rows]


def cmd_pr_table(args):
    workspace = find_workspace()
    rows = pr_review_rows(workspace)
    if not rows:
        print("No PR reviews in the database.")
        return
    headers = ("#", "PR", "Attention", "Review rung", "Description")
    widths = (5, 6, 9, 5, 80)
    print(
        f"{headers[0]:>{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]:<{widths[2]}}  "
        f"{headers[3]:<{widths[3]}}  {headers[4]}"
    )
    print("  ".join("-" * width for width in widths))
    for index, row in enumerate(rows, start=1):
        pr = truncate(f"#{row['pr_number']}", widths[1])
        attention = truncate(row["attention_level"], widths[2])
        scope = truncate(row["review_rung"], widths[3])
        description = truncate(row["summary"], widths[4])
        print(f"{index:>{widths[0]}}  {pr:<{widths[1]}}  {attention:<{widths[2]}}  {scope:<{widths[3]}}  {description}")


def cmd_pr_row(args):
    workspace = find_workspace()
    rows = pr_review_rows(workspace)
    if args.index < 1 or args.index > len(rows):
        raise SystemExit("Row index out of range. Use `repo-manager pr-table` to see valid indexes.")
    row = rows[args.index - 1]
    print(f"Row: {args.index}")
    print(f"Repo: {row['repo']}")
    print(f"PR: #{row['pr_number']} — {row['pr_title'] or ''}")
    print(f"Author: {row['author'] or ''}")
    print(f"Head: {row['head_sha'] or ''}")
    print(f"Reviewed At: {row['reviewed_at']}")
    if row.get("generation_seconds"):
        print(f"Generated in: {round(row['generation_seconds'])} seconds")
    print(f"JSON: {row['json_path'] or ''}")
    print()
    data = pr_review_data_from_row(row)
    if data is not None:
        print_pretty_pr_review(data)
    elif row["raw_output"]:
        print(row["raw_output"])


def print_pretty_pr_review(data):
    evidence = data.get("evidence") or {}

    def checked(key):
        value = evidence.get(key)
        if value:
            print(f"Checked: {value}")

    print("Description")
    print(data.get("summary", ""))
    print()

    description = data.get("description_check") or {}
    if description.get("verdict"):
        print("Author's Description vs. the Diff")
        print(f"{description.get('verdict', '')}: {description.get('notes', '')}")
        if description.get("verdict") == "missing" and not (description.get("discrepancies") or []):
            print("- [ ] Ask the author to describe the change — the PR has no usable description.")
        for item in description.get("discrepancies") or []:
            print(f"- [ ] {item.get('action') or 'Reconcile the description with the diff.'}")
            if item.get("described"):
                print(f"      Described: {item['described']}")
            if item.get("actual"):
                print(f"      In the diff: {item['actual']}")
            if item.get("evidence"):
                print(f"      Reference: {item['evidence']}")
        print()

    print("Attention")
    print(attention_display(data))
    print()

    requirement = data.get("review_requirement") or {}
    print("Review needed")
    print(f"{requirement.get('rung', '')} ({requirement.get('surface', '')}): {requirement.get('rationale', '')}")
    if requirement.get("expert_areas"):
        print(f"  subject-area expertise: {', '.join(requirement['expert_areas'])}")
    if requirement.get("named_approver"):
        print(f"  the guide names: {requirement['named_approver']}")
    print()

    flags = data.get("alignment_flags") or []
    print("Alignment Issues")
    if not flags:
        print("None found.")
        checked("alignment")
    for flag in flags:
        where = " — ".join(part for part in (flag.get("doc"), flag.get("section")) if part)
        print(f"- [ ] {flag.get('action') or flag.get('concern', '')}")
        if flag.get("concern"):
            prefix = f"{where}: " if where else ""
            print(f"      Why: {prefix}{flag['concern']}")
        if flag.get("evidence"):
            print(f"      Reference: {flag['evidence']}")
    print()

    documentation = data.get("documentation") or {}
    print("Documentation")
    print(documentation.get("status", ""))
    if not documentation.get("gaps"):
        checked("documentation")
    for gap in documentation.get("gaps") or []:
        print(f"- [ ] {gap.get('action') or gap.get('what', '')}")
        if gap.get("what"):
            print(f"      Gap: {gap['what']}")
        if gap.get("where"):
            print(f"      Where: {gap['where']}")
        if gap.get("policy"):
            print(f"      Why: {gap['policy']}")
    print()

    testing = data.get("testing") or {}
    print("Testing")
    print(testing.get("status", ""))
    if not testing.get("gaps"):
        checked("testing")
    for gap in testing.get("gaps") or []:
        print(f"- [ ] {gap.get('action') or gap.get('what', '')}")
        if gap.get("what"):
            print(f"      Gap: {gap['what']}")
        if gap.get("where"):
            print(f"      Where: {gap['where']}")
        if gap.get("policy"):
            print(f"      Why: {gap['policy']}")
    print()

    breaking = data.get("breaking_changes") or []
    print("Breaking Changes")
    if not breaking:
        print("None found.")
        checked("breaking_changes")
    for change in breaking:
        summary = f"({change.get('surface', '')}) {change.get('change', '')}"
        if breaking_change_cleared(change):
            print(f"- {summary} — {breaking_change_status(change)}")
            continue
        print(f"- [ ] {change.get('action') or 'Document this break and get a maintainer sign-off.'}")
        print(f"      Change: {summary}")
        print(f"      Status: {breaking_change_status(change)}")
    print()

    reviewers = data.get("suggested_reviewers") or []
    if reviewers:
        print("Suggested Reviewers")
        for item in reviewers:
            print(f"- {display_handle(item.get('handle'))} ({item.get('subject_area', '')}): {item.get('reason', '')}")
        print()
    areas = data.get("maintainer_needed_areas") or []
    if areas:
        print("Maintainer Needed")
        for area in areas:
            print(f"- {area}")
        print()


def cmd_post_pr_review(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    result = post_pr_review(workspace, repo, args.pr_number, dry_run=args.dry_run)
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}")
    if not result.get("ok"):
        raise SystemExit(result.get("error", "post-pr-review failed"))
    if result.get("dry_run"):
        print(f"Would {result['action']} this comment on {repo}#{args.pr_number}:\n")
        print(result["body"])
        return
    print(f"{result['action'].capitalize()} review comment on {repo}#{args.pr_number}: {result.get('url', '')}")


def cmd_request_pr_reviewers(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    handles = [part.strip() for part in (args.reviewers or "").split(",") if part.strip()] or None
    result = request_pr_reviewers(workspace, repo, args.pr_number, handles=handles, dry_run=args.dry_run)
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}")
    if not result.get("ok"):
        raise SystemExit(result.get("error", "request-pr-reviewers failed"))
    for item in result.get("skipped", []):
        print(f"Skipping {item['handle']}: {item['reason']}")
    if result.get("dry_run"):
        if result["to_request"]:
            print(f"Would request reviews on {repo}#{args.pr_number} from:")
            for item in result["to_request"]:
                area = f" ({item['subject_area']})" if item.get("subject_area") else ""
                print(f"- {item['handle']}{area}: {item.get('reason', '')}")
        else:
            print("No reviewers left to request.")
        return
    for handle in result.get("requested", []):
        print(f"Requested review from {handle}")
    for item in result.get("failed", []):
        print(f"Failed to request {item['handle']}: {item['reason']}")
    if not result.get("requested") and not result.get("failed"):
        print("No reviewers left to request.")


def cmd_ui(args):
    from repo_manager.web import serve

    workspace = find_workspace()
    serve(workspace, args.host, args.port, not args.no_open)


def cmd_publish_pages(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    if args.dry_run:
        output_dir = export_pages_preview(workspace, args.out or (workspace / CONFIG_DIR / "pages-preview"))
        index = output_dir / "index.html"
        print(f"Wrote static repo-manager dashboard preview to {index}")
        if args.open:
            webbrowser.open(index.resolve().as_uri())
        print("Dry run only; did not fetch, commit, or push the website branch.")
        return
    checkout, target_rel = publish_pages_content(workspace, repo, args.website_branch, args.target_dir)
    commit_and_push_pages(checkout, args.website_branch, target_rel, args.message)
    print(f"Published static repo-manager dashboard to {repo}:{args.website_branch}:{target_rel.as_posix()}/")


def cmd_pull(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    summary = import_published_data(
        workspace, repo, branch, args.website_branch, args.target_dir, args.wiki_page
    )
    print(f"Synced online data from {repo}:{args.website_branch}:{args.target_dir}/")
    print(f"  commit reviews:  {summary['commit_reviews']}")
    print(f"  release reviews: {summary['release_reviews']}")
    print(
        f"  announcements:   {summary['announcements']} "
        f"({summary['announcements_overridden']} replaced from wiki/release pages)"
    )


def cmd_all(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    configured_branch = args.branch or config.get("branch", "main")
    lifecycle = release_lifecycle_info(workspace, repo, configured_branch, args.release, args.branch is not None)
    release_steps_ready = lifecycle_has_release_branch(lifecycle)
    # Pull the published database into local first so `all` is safe to run from
    # any machine without clobbering newer online state when it publishes.
    pull_steps = () if args.no_pull else (("pull", cmd_pull),)
    if release_steps_ready:
        steps = pull_steps + (
            ("sweep", cmd_sweep),
            ("release-review", cmd_release_review),
            ("announce", cmd_announce),
            ("sync", cmd_sync_release_review_issue),
            ("publish-pages", cmd_publish_pages),
        )
    else:
        reason = "no concrete release branch exists"
        if lifecycle["selected_release"] == "vNext":
            reason = "CMake has not advanced past the latest v* tag"
        print(
            f"Skipping release-review, announce, and sync: {reason}. "
            "publish-pages will still run.",
            flush=True,
        )
        steps = pull_steps + (
            ("sweep", cmd_sweep),
            ("publish-pages", cmd_publish_pages),
        )
    for index, (name, func) in enumerate(steps, start=1):
        print(f"\n=== [{index}/{len(steps)}] {name} ===\n", flush=True)
        func(args)
    print(f"\nCompleted all {len(steps)} steps for {args.release}.", flush=True)


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
    init.add_argument("--no-pull", action="store_true", help="Skip pulling the published online database after init.")
    init.add_argument("--website-branch", default="website", help="Branch backing the published dashboard to pull from.")
    init.add_argument("--target-dir", default="docs/repo-manager", help="Directory holding the published dashboard.")
    init.add_argument("--wiki-page", default="Release-Announcements", help="Wiki page holding real release announcements.")
    init.set_defaults(func=cmd_init)

    review = sub.add_parser("review-commit", help="Run commit-review for one commit and save it.")
    review.add_argument("commit")
    review.add_argument("--repo")
    review.add_argument("--branch")
    review.add_argument("--release", help="Release bucket to store the review under, e.g. v10.7.0 or vNext.")
    review.add_argument("--since", default="", help="Override the inferred previous v* tag for the release range.")
    review.add_argument("--tag-start", default="", help=argparse.SUPPRESS)
    review.set_defaults(func=cmd_review_commit)

    sweep = sub.add_parser("sweep", help="Review every commit in a release range on the tracked branch.")
    sweep.add_argument(
        "release",
        nargs="?",
        default="vNext",
        help="Release bucket to store reviews under, e.g. v10.7.0 or vNext. Defaults to inferred current release.",
    )
    sweep.add_argument("--since", help="Override the inferred previous v* tag for the release range.")
    sweep.add_argument("--repo")
    sweep.add_argument("--branch")
    sweep.add_argument("--force", action="store_true", help="Re-run reviews that already exist.")
    sweep.set_defaults(func=cmd_sweep)

    review_pr = sub.add_parser("review-pr", help="Run pr-review for one open PR and save it.")
    review_pr.add_argument("pr_number", type=int)
    review_pr.add_argument("--repo")
    review_pr.set_defaults(func=cmd_review_pr)

    sweep_prs = sub.add_parser("sweep-prs", help="Review every open PR that lacks a current review.")
    sweep_prs.add_argument("--repo")
    sweep_prs.add_argument("--limit", type=int, default=100, help="Maximum number of open PRs to consider.")
    sweep_prs.add_argument("--force", action="store_true", help="Re-run reviews even when the head SHA is unchanged.")
    sweep_prs.add_argument("--include-drafts", action="store_true", help="Also review draft PRs.")
    sweep_prs.set_defaults(func=cmd_sweep_prs)

    post_pr = sub.add_parser("post-pr-review", help="Post or update the saved PR review as a PR comment.")
    post_pr.add_argument("pr_number", type=int)
    post_pr.add_argument("--repo")
    post_pr.add_argument("--dry-run", action="store_true", help="Print the comment without posting it.")
    post_pr.set_defaults(func=cmd_post_pr_review)

    request_reviewers = sub.add_parser(
        "request-pr-reviewers", help="Request the saved suggested reviewers on GitHub."
    )
    request_reviewers.add_argument("pr_number", type=int)
    request_reviewers.add_argument("--repo")
    request_reviewers.add_argument("--reviewers", help="Comma-separated handles to request instead of the saved suggestions.")
    request_reviewers.add_argument("--dry-run", action="store_true", help="Print who would be requested without requesting.")
    request_reviewers.set_defaults(func=cmd_request_pr_reviewers)

    release = sub.add_parser("release-review", help="Run release-level review from stored commit reviews.")
    release.add_argument(
        "release",
        nargs="?",
        default="vNext",
        help="Release bucket to review, e.g. v10.7.0 or vNext. Defaults to inferred current release.",
    )
    release.add_argument("--since", help="Override the stored or inferred previous v* tag for the release range.")
    release.add_argument("--repo")
    release.add_argument("--branch")
    release.set_defaults(func=cmd_release_review)

    sync_release_issue = sub.add_parser(
        "sync",
        help="Create or update GitHub issues for stored release artifacts.",
    )
    sync_release_issue.add_argument(
        "release",
        nargs="?",
        default="vNext",
        help="Release bucket to sync, e.g. v10.7.0 or vNext. Defaults to inferred current release.",
    )
    sync_release_issue.add_argument("--repo")
    sync_release_issue.add_argument("--branch")
    sync_release_issue.set_defaults(func=cmd_sync_release_review_issue)

    announce = sub.add_parser("announce", help="Generate a Discord-friendly release announcement.")
    announce.add_argument(
        "release",
        nargs="?",
        default="vNext",
        help="Release bucket to announce, e.g. v10.7.0 or vNext. Defaults to inferred current release.",
    )
    announce.add_argument("--since", help="Override the stored or inferred previous v* tag for the release range.")
    announce.add_argument("--repo")
    announce.add_argument("--branch")
    announce.set_defaults(func=cmd_announce)

    override_announcement = sub.add_parser("override-announcement", help="Replace a saved release announcement from Markdown.")
    override_announcement.add_argument("release", help="Release bucket to replace, e.g. v10.7.0 or vNext.")
    override_announcement.add_argument("file", help="Markdown file to store, or - to read from stdin.")
    override_announcement.add_argument("--since", help="Override the inferred previous v* tag for the release range.")
    override_announcement.add_argument("--head", help="Override the release head SHA stored with the announcement.")
    override_announcement.add_argument("--repo")
    override_announcement.add_argument("--branch")
    override_announcement.set_defaults(func=cmd_override_announcement)

    all_cmd = sub.add_parser(
        "all",
        help="Run pull, sweep, release-review, announce, sync, and publish-pages in order for one release.",
    )
    all_cmd.add_argument(
        "release",
        nargs="?",
        default="vNext",
        help="Release bucket to process, e.g. v10.7.0 or vNext. Defaults to inferred current release.",
    )
    all_cmd.add_argument("--since", help="Override the inferred previous v* tag for the release range.")
    all_cmd.add_argument("--repo")
    all_cmd.add_argument("--branch")
    all_cmd.add_argument("--force", action="store_true", help="Re-run commit reviews that already exist (sweep step).")
    all_cmd.add_argument("--website-branch", default="website", help="Branch backing the GitHub Pages site.")
    all_cmd.add_argument("--target-dir", default="docs/repo-manager", help="Directory to replace on the website branch.")
    all_cmd.add_argument("--message", default="Update repo-manager dashboard", help="Commit message for the website branch.")
    all_cmd.add_argument("--dry-run", action="store_true", help="Write a local static preview instead of pushing the website branch (publish step).")
    all_cmd.add_argument("--out", help="Output directory for --dry-run. Defaults to .repo-manager/pages-preview in the workspace.")
    all_cmd.add_argument("--open", action=argparse.BooleanOptionalAction, default=True, help="Open the dry-run preview in the default browser.")
    all_cmd.add_argument("--wiki-page", default="Release-Announcements", help="Wiki page holding real release announcements (pull step).")
    all_cmd.add_argument("--no-pull", action="store_true", help="Skip syncing the published database into local before running.")
    all_cmd.set_defaults(func=cmd_all)

    status = sub.add_parser("status", help="Print inferred release lifecycle state.")
    status.add_argument(
        "release",
        nargs="?",
        default="vNext",
        help="Release bucket to inspect, e.g. v10.7.0 or vNext. Defaults to inferred current release.",
    )
    status.add_argument("--repo")
    status.add_argument("--branch")
    status.set_defaults(func=cmd_status)

    wipe = sub.add_parser("wipe-db", help="Delete and recreate the local SQLite database.")
    wipe.set_defaults(func=cmd_wipe_db)

    db_table = sub.add_parser("db-table", help="Print saved commit reviews as a table.")
    db_table.set_defaults(func=cmd_db_table)

    db_row = sub.add_parser("db-row", help="Print saved commit review contents by table row index.")
    db_row.add_argument("index", type=int)
    db_row.set_defaults(func=cmd_db_row)

    pr_table = sub.add_parser("pr-table", help="Print saved PR reviews as a table.")
    pr_table.set_defaults(func=cmd_pr_table)

    pr_row = sub.add_parser("pr-row", help="Print saved PR review contents by table row index.")
    pr_row.add_argument("index", type=int)
    pr_row.set_defaults(func=cmd_pr_row)

    ui = sub.add_parser("ui", help="Serve a local web UI for saved reviews and announcements.")
    ui.add_argument(
        "--host",
        default="127.0.0.1",
        help="Address to bind. Use 0.0.0.0 to serve the UI to other machines on your LAN.",
    )
    ui.add_argument("--port", type=int, default=8765, help="Port to bind (default: 8765).")
    ui.add_argument("--no-open", action="store_true", help="Print the URL without opening a browser.")
    ui.set_defaults(func=cmd_ui)

    publish_pages = sub.add_parser("publish-pages", help="Publish a static read-only dashboard to GitHub Pages.")
    publish_pages.add_argument("--repo")
    publish_pages.add_argument("--website-branch", default="website", help="Branch backing the GitHub Pages site.")
    publish_pages.add_argument("--target-dir", default="docs/repo-manager", help="Directory to replace on the website branch.")
    publish_pages.add_argument("--message", default="Update repo-manager dashboard", help="Commit message for the website branch.")
    publish_pages.add_argument("--dry-run", action="store_true", help="Write a local static preview without fetching, committing, or pushing.")
    publish_pages.add_argument("--out", help="Output directory for --dry-run. Defaults to .repo-manager/pages-preview in the workspace.")
    publish_pages.add_argument("--open", action=argparse.BooleanOptionalAction, default=True, help="Open the dry-run preview in the default browser.")
    publish_pages.set_defaults(func=cmd_publish_pages)

    pull = sub.add_parser("pull", help="Sync the published online dashboard back into the local database.")
    pull.add_argument("--repo")
    pull.add_argument("--branch")
    pull.add_argument("--website-branch", default="website", help="Branch backing the published dashboard.")
    pull.add_argument("--target-dir", default="docs/repo-manager", help="Directory holding the published dashboard.")
    pull.add_argument("--wiki-page", default="Release-Announcements", help="Wiki page holding real release announcements.")
    pull.set_defaults(func=cmd_pull)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

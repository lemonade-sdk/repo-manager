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

    data["breaking_changes"] = normalize_breaking_changes(data.get("breaking_changes"))

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
              reviewed_at, skill_version, rubric_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo, commit_sha, rubric_version) DO UPDATE SET
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
            ),
        )


def review_exists(workspace, repo, commit):
    with connect_db(workspace) as conn:
        row = conn.execute(
            "SELECT 1 FROM commit_reviews WHERE repo=? AND commit_sha=? AND rubric_version=?",
            (repo, commit, RUBRIC_VERSION),
        ).fetchone()
    return row is not None


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
    return normalize_breaking_changes(parsed.get("breaking_changes"))


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
    release_tag = args.release or args.tag_start or ""
    range_start = args.since or (infer_range_start(repo, workspace, release_tag) if release_tag else "")
    store_commit_review(workspace, repo, branch, release_tag, range_start, args.commit, json_file)


def cmd_sweep(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    range_start = args.since or infer_range_start(repo, workspace, args.release)
    commits = list_commits(repo, workspace, branch, range_start)
    print(f"Found {len(commits)} commits in {range_start}..origin/{branch} for {args.release}")
    for commit in commits:
        if review_exists(workspace, repo, commit) and not args.force:
            print(f"Skipping existing review for {commit}")
            continue
        print(f"Reviewing {commit}")
        json_file = commit_artifact_paths(workspace, repo, commit)
        prompt = (
        f"/skill:commit-review {repo} {commit}\n\n"
            f"This commit is part of release {args.release}, covering range {range_start}..{branch}.\n"
            f"Write the machine-readable JSON result to: {json_file}\n"
            "The JSON must match the schema required by the skill."
        )
        run_pi("commit-review", prompt, workspace)
        require_files(json_file)
        store_commit_review(workspace, repo, branch, args.release, range_start, commit, json_file)


def cmd_release_review(args):
    workspace = find_workspace()
    config = load_config(workspace)
    repo = resolve_repo(args, config)
    branch = args.branch or config.get("branch", "main")
    reviews = load_reviews(workspace, repo, branch, args.release)
    if not reviews:
        raise SystemExit("No commit reviews found for that repo/branch/release.")
    range_start = args.since or common_range_start(reviews) or infer_range_start(repo, workspace, args.release)
    head = head_sha(repo, workspace, branch)
    json_file, _ = release_artifact_paths(workspace, repo, args.release, head, "review")
    context_json = release_review_context(workspace, reviews)
    digest_context = (
        "Per-commit digest of the stored commit reviews. open_todos reflect maintainer completion state in the "
        "repo-manager database; completed_todos counts are resolved evidence:\n"
        + context_json
        + "\n\nFinal reminders: a to-do earns its place only if the maintainer would regret shipping without it "
        "AND users would notice the consequence; omit everything else entirely (there is no P2). Each to-do is "
        "one actionable sentence — action, user-visible stake, how to check — marked P0 (do not ship until "
        "resolved) or P1 (verify before shipping); merge related concerns into shared to-dos. The verdict is "
        "computed from your to-do list, so you cannot contradict it. verdict_reason, to-dos, and evidence are "
        "for a human who has never seen this digest: name the feature or behavior, and let verdict_reason be "
        "just your one-or-two-sentence answer to 'can we ship?'.\n"
    )
    feedback = load_release_review_feedback(workspace, repo, args.release)
    if feedback:
        print("Resuming with validation feedback from a previous interrupted release-review run.", flush=True)
    pending_dir = json_file.parent / ".pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    max_attempts = 3
    data = None
    for attempt in range(1, max_attempts + 1):
        pending_json = pending_dir / f"{json_file.stem}.{int(time.time() * 1000)}.pending.json"
        prompt = (
            f"/skill:release-review\n\nRepo: {repo}\nBranch: {branch}\n"
            f"Release: {args.release}\nRange start: {range_start or 'unknown'}\nHead SHA: {head}\n\n"
            f"Write the machine-readable JSON result to: {pending_json}\n\n"
            f"{feedback}"
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
        save_release_review_feedback(workspace, repo, args.release, error_list, artifact_raw)
        feedback = build_release_review_feedback(error_list, artifact_raw)
    clear_release_review_feedback(workspace, repo, args.release)
    data["repo"] = repo
    data["branch"] = branch
    data["tag_start"] = args.release
    data["range_start"] = range_start
    data["head_sha"] = head
    json_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    raw = json.dumps(data, indent=2)
    verdict = data.get("verdict", "")
    with connect_db(workspace) as conn:
        conn.execute(
            """
            DELETE FROM release_reviews
            WHERE repo=? AND branch=? AND tag_start=? AND rubric_version=?
            """,
            (repo, branch, args.release, RELEASE_RUBRIC_VERSION),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO release_reviews
            (repo, branch, tag_start, range_start, head_sha, verdict, raw_output, json_path, reviewed_at, skill_version, rubric_version)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo, branch, args.release, range_start, head, verdict, raw, str(json_file), now_iso(), __version__, RELEASE_RUBRIC_VERSION),
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
    reviews = load_reviews(workspace, repo, branch, args.release)
    if not reviews:
        raise SystemExit("No commit reviews found for that repo/branch/release.")
    range_start = args.since or common_range_start(reviews) or infer_range_start(repo, workspace, args.release)
    head = release_head_sha(repo, workspace, branch, args.release)
    release_highlights_file, markdown_file = release_announcement_artifact_paths(workspace, repo, args.release, head)
    release_highlights_context = release_highlights_reference_context(workspace, repo, args.release)
    prior_rows = prior_announcements(workspace, repo, branch, args.release)
    style_context = announcement_style_context(prior_rows)
    canonical_breaking = review_breaking_changes(
        latest_release_review(workspace, repo, branch, args.release)
    )
    if canonical_breaking is None:
        print(
            "Warning: no release review found for this release; skipping breaking-change reconciliation. "
            "Run `release-review` first so the announcement's breaking changes are checked against it.",
            flush=True,
        )
    breaking_context = canonical_breaking_changes_context(canonical_breaking)
    review_context = (
        "Commit summaries for this release (the announcement's only source material):\n"
        + announcement_review_context(workspace, reviews)
        + "\n\nFinal editorial reminders: tell the release as 3-5 stories, one Discord section each; if two "
        "candidate sections would answer the same reader question, they are one story, and leftover changes that "
        "are not a story become Additional Improvements bullets. Describe outcomes, never the work behind them — "
        "credit people as a clause in the feature sentence, and let enabling fixes be subsumed by the outcome "
        "they enabled.\n"
    )
    feedback = load_announcement_feedback(workspace, repo, args.release)
    if feedback:
        print("Resuming with validation feedback from a previous interrupted announce run.", flush=True)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        pending_release_highlights_file, pending_markdown_file = (
            pending_release_announcement_artifact_paths(workspace, repo, args.release, head)
        )
        prompt = (
            f"/skill:release-announcement\n\nRepo: {repo}\nBranch: {branch}\n"
            f"Release: {args.release}\nRange start: {range_start or 'unknown'}\nHead SHA: {head}\n\n"
            f"Write the website release highlights Markdown to: {pending_release_highlights_file}\n"
            f"Then write the Discord-friendly Markdown announcement to: {pending_markdown_file}\n\n"
            f"{release_highlights_context}"
            f"{style_context}"
            f"{breaking_context}"
            f"{feedback}"
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
        save_announcement_feedback(workspace, repo, args.release, error_list, release_highlights_raw, raw)
        feedback = build_announcement_feedback(error_list, release_highlights_raw, raw)
    clear_announcement_feedback(workspace, repo, args.release)
    write_text(release_highlights_file, release_highlights_raw)
    write_text(markdown_file, raw)
    with connect_db(workspace) as conn:
        conn.execute(
            """
            DELETE FROM release_announcements
            WHERE repo=? AND branch=? AND tag_start=? AND skill_version=?
            """,
            (repo, branch, args.release, ANNOUNCEMENT_VERSION),
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
                args.release,
                range_start,
                head,
                raw,
                str(markdown_file),
                release_highlights_raw,
                str(release_highlights_file),
                now_iso(),
                ANNOUNCEMENT_VERSION,
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
    range_start = args.since or infer_range_start(repo, workspace, args.release)
    head = args.head or release_head_sha(repo, workspace, branch, args.release)
    markdown = read_stdin_or_file(args.file)
    if not markdown.strip():
        raise SystemExit("Announcement markdown is empty.")
    markdown_file = store_release_announcement(workspace, repo, branch, args.release, range_start, head, markdown)
    print(f"Stored announcement override for {args.release}: {markdown_file}")


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


def cmd_all(args):
    steps = (
        ("sweep", cmd_sweep),
        ("release-review", cmd_release_review),
        ("announce", cmd_announce),
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
    sweep.add_argument("release", help="Release bucket to store reviews under, e.g. v10.7.0 or vNext.")
    sweep.add_argument("--since", help="Override the inferred previous v* tag for the release range.")
    sweep.add_argument("--repo")
    sweep.add_argument("--branch")
    sweep.add_argument("--force", action="store_true", help="Re-run reviews that already exist.")
    sweep.set_defaults(func=cmd_sweep)

    release = sub.add_parser("release-review", help="Run release-level review from stored commit reviews.")
    release.add_argument("release", help="Release bucket to review, e.g. v10.7.0 or vNext.")
    release.add_argument("--since", help="Override the stored or inferred previous v* tag for the release range.")
    release.add_argument("--repo")
    release.add_argument("--branch")
    release.set_defaults(func=cmd_release_review)

    announce = sub.add_parser("announce", help="Generate a Discord-friendly release announcement.")
    announce.add_argument("release", help="Release bucket to announce, e.g. v10.7.0 or vNext.")
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
        help="Run sweep, release-review, announce, and publish-pages in order for one release.",
    )
    all_cmd.add_argument("release", help="Release bucket to process, e.g. v10.7.0 or vNext.")
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
    all_cmd.set_defaults(func=cmd_all)

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

    publish_pages = sub.add_parser("publish-pages", help="Publish a static read-only dashboard to GitHub Pages.")
    publish_pages.add_argument("--repo")
    publish_pages.add_argument("--website-branch", default="website", help="Branch backing the GitHub Pages site.")
    publish_pages.add_argument("--target-dir", default="docs/repo-manager", help="Directory to replace on the website branch.")
    publish_pages.add_argument("--message", default="Update repo-manager dashboard", help="Commit message for the website branch.")
    publish_pages.add_argument("--dry-run", action="store_true", help="Write a local static preview without fetching, committing, or pushing.")
    publish_pages.add_argument("--out", help="Output directory for --dry-run. Defaults to .repo-manager/pages-preview in the workspace.")
    publish_pages.add_argument("--open", action=argparse.BooleanOptionalAction, default=True, help="Open the dry-run preview in the default browser.")
    publish_pages.set_defaults(func=cmd_publish_pages)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

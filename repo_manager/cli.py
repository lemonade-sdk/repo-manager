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


def release_highlights_validation_errors(markdown):
    text = (markdown or "").strip()
    if not text:
        return ["Release highlights artifact is empty."]
    lines = text.splitlines()
    nonblank = [line for line in lines if line.strip()]
    if not nonblank or nonblank[0].strip() != "## Headline":
        return ["Release highlights artifact must start with exactly `## Headline`."]

    heading_lines = [(index, line.strip()) for index, line in enumerate(lines) if re.match(r"^#{1,6}\s+", line)]
    headings = [line for _, line in heading_lines]
    if headings != ["## Headline", "## Breaking Changes"]:
        return ["Release highlights artifact must contain only `## Headline` followed by `## Breaking Changes`."]

    errors = []
    breaking_index = heading_lines[1][0]
    headline_lines = [line for line in lines[heading_lines[0][0] + 1 : breaking_index] if line.strip()]
    breaking_lines = [line for line in lines[breaking_index + 1 :] if line.strip()]
    headline_bullets = [line.strip() for line in headline_lines if line.strip().startswith("- ")]
    if len(headline_bullets) != len(headline_lines):
        errors.append("Release highlights Headline section must contain only single-depth `- ` bullets.")
    if not 3 <= len(headline_bullets) <= 5:
        errors.append("Release highlights Headline section must contain 3-5 bullets.")

    casual_terms = re.compile(r"\b(finally|huge|massive|awesome|fresh|super|great)\b|glow up", re.IGNORECASE)
    # Inline code (backticks) is allowed: real Lemonade headlines use it for commands and flags.
    formatting_terms = re.compile(r"(\*\*|\[[^\]]+\]\(|https?://|@)")
    for bullet in headline_bullets:
        body = bullet[2:].strip()
        if casual_terms.search(body):
            errors.append(f"Release highlights headline is too casual: {body}")
        if formatting_terms.search(body):
            errors.append(
                f"Release highlights headline must not include bold, links, or @handles (inline code is fine): {body}"
            )
        if len(body) > 220:
            errors.append(f"Release highlights headline bullet must be one short sentence: {body}")

    for line in breaking_lines:
        stripped = line.strip()
        if not stripped.startswith(("- ", "* ")):
            errors.append("Release highlights Breaking Changes section must contain only bullets or be empty.")
            continue
        body = stripped[2:].strip().lower().strip(".* ")
        if body in ("none", "none this release", "no breaking changes", "no breaking changes this release"):
            errors.append("Leave `## Breaking Changes` empty when there are no breaking changes.")
    return errors


def story_plan_validation_errors(plan_text):
    plan = extract_json_object(plan_text or "")
    if plan is None:
        return ["Story plan file must contain a valid JSON object."], None
    stories = plan.get("stories")
    if not isinstance(stories, list) or not stories:
        return ["Story plan must contain a non-empty `stories` list."], None
    errors = []
    if not 3 <= len(stories) <= 5:
        errors.append(
            f"Story plan has {len(stories)} stories; it must have 3-5. "
            "Merge stories that answer the same reader question; drop ones users would not notice."
        )
    grab_bag_endings = ("improvements", "fixes", "updates", "miscellaneous", "misc")
    for index, story in enumerate(stories):
        if not isinstance(story, dict) or not str(story.get("title", "")).strip():
            errors.append(f"Story plan stories[{index}] must be an object with a non-empty `title`.")
            continue
        title = str(story["title"]).strip()
        if "&" in title or title.lower().split()[-1] in grab_bag_endings:
            errors.append(
                f"Story title {title!r} is a grab-bag, not a story. Each story answers one reader question; "
                "split it into real stories or move its contents to Additional Improvements bullets."
            )
    if not any(isinstance(story, dict) and story.get("section") for story in stories):
        errors.append("At least one story must have `section`: true.")
    return errors, plan


def normalize_heading_title(text):
    text = re.sub(r"[^0-9A-Za-z&+./'’-]+", " ", text or "")
    return " ".join(text.split()).lower()


RESERVED_ANNOUNCEMENT_HEADINGS = {"breaking changes", "additional improvements", "news"}


def announcement_plan_consistency_errors(markdown, plan):
    if not plan:
        return []
    headings = []
    for line in (markdown or "").splitlines():
        match = re.match(r"^###\s+(.+?)\s*$", line)
        if match:
            normalized = normalize_heading_title(match.group(1))
            if normalized not in RESERVED_ANNOUNCEMENT_HEADINGS:
                headings.append((match.group(1).strip(), normalized))
    section_titles = [
        str(story.get("title", "")).strip()
        for story in plan.get("stories", [])
        if isinstance(story, dict) and story.get("section")
    ]
    if [normalized for _, normalized in headings] != [normalize_heading_title(title) for title in section_titles]:
        return [
            "Discord feature headings must be exactly the planned section-story titles, in order. "
            f"Planned sections: {section_titles}. Found headings: {[original for original, _ in headings]}. "
            "Fix the announcement or revise the plan so the two agree."
        ]
    return []


def normalize_announcement_words(text):
    words = []
    for raw in (text or "").split():
        token = raw.strip("*_`~:;,.!?()[]{}<>\"'#-—").lower()
        if not token or token.startswith("http") or token.startswith("www."):
            continue
        words.append(token)
    return words


def repeated_prior_phrases(markdown, prior_rows, n=8, max_reports=3):
    words = normalize_announcement_words(markdown)
    grams = {}
    for index in range(len(words) - n + 1):
        grams.setdefault(tuple(words[index : index + n]), index)
    hits = []
    for row in prior_rows:
        prior_words = normalize_announcement_words(read_announcement_markdown(row))
        prior_grams = {tuple(prior_words[index : index + n]) for index in range(len(prior_words) - n + 1)}
        matches = [gram for gram in grams if gram in prior_grams]
        if matches:
            earliest = min(matches, key=grams.get)
            hits.append((row.get("tag_start"), " ".join(earliest)))
        if len(hits) >= max_reports:
            break
    return hits


ANNOUNCEMENT_CANNED_PHRASES = (
    "let's dive in",
    "dive in!",
    "buckle up",
    "without further ado",
    "to the next level",
    "seamlessly",
)

ANNOUNCEMENT_FILLER_PATTERN = re.compile(
    r"\bwe(?:'re| are)\s+(?:excited|thrilled|delighted|proud|pleased)\b"
    r"|\b(?:excited|thrilled|delighted|proud|pleased)\s+to\s+(?:introduce|announce|share|welcome)\b",
    re.IGNORECASE,
)


def announcement_validation_errors(markdown, prior_rows):
    text = (markdown or "").strip()
    if not text:
        return ["Discord announcement is empty."]
    errors = []
    nonblank = [line for line in text.splitlines() if line.strip()]
    if not nonblank[0].strip().startswith("## Lemonade "):
        errors.append("Discord announcement must start with a `## Lemonade <release>` title line.")
    if text.count("**") // 2 > 6:
        errors.append(
            "Discord announcement uses bold too often; bold at most one or two introduced product names in the whole post."
        )
    if re.search(r"\*\*\s*@", text):
        errors.append("Do not bold @handles; credit contributors inline as plain `@handle`.")
    lowered = text.lower()
    for phrase in ANNOUNCEMENT_CANNED_PHRASES:
        if phrase in lowered:
            errors.append(f"Drop the canned phrase {phrase!r}; the prior announcements never talk like that.")
    filler = ANNOUNCEMENT_FILLER_PATTERN.search(text)
    if filler:
        errors.append(
            f"Drop the marketing filler {filler.group(0)!r}; state what shipped directly, the way the prior announcements do."
        )
    pr_ref = re.search(r"\bPR\s*#\d+|\(#\d{2,}\)", text)
    if pr_ref:
        errors.append(
            f"Remove the PR reference {pr_ref.group(0)!r}; announcements never include PR numbers — link docs or describe the user action instead."
        )
    if len(nonblank) > 45:
        errors.append(
            f"Discord announcement is too long ({len(nonblank)} non-blank lines); match the prior announcements "
            "(roughly 15-30 non-blank lines) by tightening sections and merging minor items."
        )
    for tag, phrase in repeated_prior_phrases(text, prior_rows):
        errors.append(
            f'Reused wording from the {tag} announcement: "{phrase} ..." — rewrite so no phrase of 8+ consecutive '
            "words repeats a prior announcement."
        )
    improvements_bullets = 0
    in_improvements = False
    for line in text.splitlines():
        heading_match = re.match(r"^#{2,3}\s+(.+?)\s*$", line)
        if heading_match:
            in_improvements = normalize_heading_title(heading_match.group(1)) == "additional improvements"
            continue
        if in_improvements and line.strip().startswith(("- ", "* ")):
            improvements_bullets += 1
    if improvements_bullets > 7:
        errors.append(
            f"Additional Improvements has {improvements_bullets} bullets; keep it to at most 7 by merging "
            "related work into shared bullets and omitting changes with no audience."
        )
    return errors


def normalize_release_priority(value):
    text = str(value or "").strip().upper()
    if text in ("P0", "BLOCKING", "BLOCKER", "HIGH"):
        return "P0" if text in ("P0", "BLOCKING", "BLOCKER") else "P1"
    if text in ("P1", "RECOMMENDED", "MEDIUM"):
        return "P1"
    if text in ("P2", "FUTURE", "LOW"):
        return "P2"
    return "P1"


def normalize_release_review_data(data):
    if not isinstance(data.get("triage"), list):
        for alias in ("decisions", "todo_triage"):
            if isinstance(data.get(alias), list):
                data["triage"] = data[alias]
                break
    if not isinstance(data.get("triage"), list):
        rebuilt = []
        for entry in data.get("omitted_todos") or []:
            if isinstance(entry, dict) and (entry.get("commit") or entry.get("id")):
                rebuilt.append(
                    {
                        "id": entry.get("commit") or entry.get("id"),
                        "decision": "omit",
                        "why": entry.get("reason") or entry.get("why") or "",
                    }
                )
        for entry in data.get("action_items") or []:
            if not isinstance(entry, dict):
                continue
            blob = " ".join(
                str(entry.get(key, "")) for key in ("id", "commit", "commits", "description", "text", "rationale")
            )
            for ref in sorted(set(re.findall(r"\bc\d+\b", blob))):
                rebuilt.append(
                    {
                        "id": ref,
                        "decision": entry.get("priority") or "P1",
                        "why": str(entry.get("description") or entry.get("text") or "")[:160],
                    }
                )
        if rebuilt:
            data["triage"] = rebuilt
    if isinstance(data.get("triage"), list):
        normalized_triage = []
        for entry in data["triage"]:
            if not isinstance(entry, dict):
                continue
            decision = str(entry.get("decision") or entry.get("priority") or "").strip()
            decision = "omit" if decision.lower() in ("omit", "omitted", "drop", "skip") else decision.upper()
            normalized_triage.append(
                {
                    "id": str(entry.get("id") or entry.get("commit") or "").strip(),
                    "decision": decision,
                    "why": str(entry.get("why") or entry.get("reason") or entry.get("rationale") or "").strip(),
                }
            )
        data["triage"] = normalized_triage

    todos = data.get("prioritized_todos")
    if not isinstance(todos, list):
        todos = []

    normalized_todos = []
    for item in todos:
        if isinstance(item, dict):
            text = item.get("text") or item.get("action") or item.get("risk") or item.get("description")
            if text:
                normalized_todos.append({"priority": normalize_release_priority(item.get("priority")), "text": str(text)})
        elif item:
            normalized_todos.append({"priority": "P1", "text": str(item)})

    if not normalized_todos:
        for item in data.get("action_items") or []:
            if isinstance(item, dict):
                text = item.get("text") or item.get("description") or item.get("action")
                if text:
                    normalized_todos.append(
                        {"priority": normalize_release_priority(item.get("priority")), "text": str(text)}
                    )

    if not normalized_todos:
        for item in data.get("recommendations") or []:
            if isinstance(item, dict):
                text = item.get("action") or item.get("text")
                if text:
                    normalized_todos.append(
                        {"priority": normalize_release_priority(item.get("priority")), "text": str(text)}
                    )

    if not normalized_todos:
        open_items = ((data.get("maintainer_todos_summary") or {}).get("open_items") or [])
        for item in open_items:
            if isinstance(item, dict):
                text = item.get("text") or item.get("reason")
                if text:
                    normalized_todos.append(
                        {"priority": normalize_release_priority(item.get("priority")), "text": str(text)}
                    )

    if not normalized_todos:
        for item in data.get("open_release_risks") or []:
            if isinstance(item, dict):
                text = item.get("risk") or item.get("description")
                if text:
                    normalized_todos.append(
                        {"priority": normalize_release_priority(item.get("severity")), "text": str(text)}
                    )

    data["prioritized_todos"] = normalized_todos

    if not str(data.get("verdict_reason") or "").strip() and str(data.get("summary") or "").strip():
        data["verdict_reason"] = str(data["summary"]).strip()

    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict == "ready":
        normalized_verdict = "Ready"
    elif verdict in ("blocked", "blocker", "fail", "failed"):
        normalized_verdict = "Blocked"
    elif verdict in (
        "needs attention",
        "conditional pass",
        "conditional",
        "pass with conditions",
        "release with conditions",
        "ship with conditions",
        "conditional release",
    ):
        normalized_verdict = "Needs Attention"
    elif any(todo.get("priority") == "P0" for todo in normalized_todos):
        normalized_verdict = "Blocked"
    elif normalized_todos:
        normalized_verdict = "Needs Attention"
    else:
        normalized_verdict = "Ready"

    if normalized_verdict == "Ready" and normalized_todos:
        normalized_verdict = "Needs Attention"
    if normalized_verdict == "Needs Attention" and any(todo.get("priority") == "P0" for todo in normalized_todos):
        normalized_verdict = "Blocked"
    data["verdict"] = normalized_verdict
    return data


RELEASE_TODO_WEASEL_STARTS = ("consider ", "note that", "be aware", "maybe ", "possibly ", "think about")

READY_CONTRADICTION_PATTERN = re.compile(
    r"\b(?:before (?:shipping|release|releasing|tagging)|should be (?:addressed|checked|verified|fixed|resolved|"
    r"documented|tested)|needs? (?:attention|verification|testing|fixing|documentation)|must be (?:addressed|"
    r"checked|verified|fixed|documented|tested))\b",
    re.IGNORECASE,
)


def release_review_validation_errors(data, required_triage_ids=None):
    errors = []
    verdict = data.get("verdict")
    todos = data.get("prioritized_todos")
    if verdict not in ("Ready", "Needs Attention", "Blocked"):
        errors.append(f"Verdict must be Ready, Needs Attention, or Blocked; got {verdict!r}.")
    if not isinstance(todos, list):
        return errors + ["prioritized_todos must be a list (empty for Ready)."]
    if verdict in ("Needs Attention", "Blocked") and not todos:
        errors.append("Verdict requires maintainer attention but prioritized_todos is empty.")
    if verdict == "Ready" and todos:
        errors.append("Ready requires an empty prioritized_todos list.")
    if verdict == "Ready":
        prose = " ".join(
            [str(data.get("verdict_reason", ""))] + [str(value) for value in (data.get("evidence") or {}).values()]
        )
        contradiction = READY_CONTRADICTION_PATTERN.search(prose)
        if contradiction:
            errors.append(
                f"Verdict is Ready but the prose says {contradiction.group(0)!r} — pre-release work buried in "
                "text is the worst possible output. Either nothing needs to happen before shipping (rewrite the "
                "prose), or it does (each item becomes a P0/P1 to-do and the verdict changes)."
            )
    if required_triage_ids is not None:
        triage = data.get("triage")
        if not isinstance(triage, list):
            triage = []
        decisions = {}
        for index, entry in enumerate(triage):
            if not isinstance(entry, dict) or not entry.get("id"):
                errors.append(f"triage[{index}] must be an object with id, decision, and why.")
                continue
            decision = entry.get("decision")
            if decision not in ("P0", "P1", "omit"):
                errors.append(f"triage[{index}] decision must be P0, P1, or omit; got {decision!r}.")
                continue
            if not str(entry.get("why", "")).strip():
                errors.append(f"triage[{index}] ({entry['id']}) needs a one-clause `why`.")
            decisions[str(entry["id"])] = decision
        missing = [tid for tid in required_triage_ids if tid not in decisions]
        if missing:
            errors.append(
                f"Every digest entry with open_todos must be triaged; missing decisions for: {', '.join(missing)}. "
                "Each gets P0 (do not ship until resolved), P1 (verify before shipping), or omit (with why)."
            )
        kept = [d for d in decisions.values() if d != "omit"]
        expected = "Blocked" if "P0" in kept else ("Needs Attention" if kept else "Ready")
        if verdict in ("Ready", "Needs Attention", "Blocked") and verdict != expected:
            errors.append(
                f"Verdict must follow from the triage decisions: {len(kept)} kept "
                f"({sorted(set(kept)) or 'none'}) implies {expected!r}, not {verdict!r}."
            )
        if kept and isinstance(todos, list):
            if "P0" in kept and not any(t.get("priority") == "P0" for t in todos if isinstance(t, dict)):
                errors.append("Triage kept a P0 but prioritized_todos has no P0 item.")
            if not todos:
                errors.append("Triage kept items but prioritized_todos is empty; each kept theme needs a to-do.")
    if len(todos) > 6:
        errors.append(
            f"prioritized_todos has {len(todos)} items; at most 6. Merge verification work into release-test "
            "themes and omit anything the maintainer would not regret shipping without."
        )
    for index, item in enumerate(todos):
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            errors.append(f"prioritized_todos[{index}] must be an object with priority and non-empty text.")
            continue
        priority = item.get("priority")
        text = str(item["text"]).strip()
        if priority not in ("P0", "P1"):
            errors.append(
                f"prioritized_todos[{index}] has priority {priority!r}; only P0 (do not ship until resolved) and "
                "P1 (verify before shipping) exist. If it matters for this release it is P0 or P1; "
                "if it does not, remove it."
            )
        if len(text) > 300:
            errors.append(
                f"prioritized_todos[{index}] is too long; one actionable sentence: action, user-visible stake, how to check."
            )
        lowered = text.lower()
        for weasel in RELEASE_TODO_WEASEL_STARTS:
            if lowered.startswith(weasel):
                errors.append(
                    f"prioritized_todos[{index}] starts with {weasel.strip()!r}; rewrite as a direct action "
                    "(Run/Verify/Confirm/Fix X and check Y)."
                )
                break
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
    safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", tag_start)
    return (
        pending_dir / f"{release_highlights_file.stem}.{stamp}.pending.md",
        pending_dir / f"{markdown_file.stem}.{stamp}.pending.md",
        pending_dir / f"{safe_tag}.{stamp}.story-plan.json",
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
    for index, review in enumerate(reviews, start=1):
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
                "id": f"c{index}",
                "summary": data.get("summary", review["summary"]),
                "verdict": data.get("verdict", review["verdict"]),
                "verdict_reason": data.get("verdict_reason", review["verdict_reason"]),
                "open_todos": open_todos,
                "completed_todos": completed,
                "evidence": {key: evidence[key] for key in RELEASE_REVIEW_EVIDENCE_KEYS if evidence.get(key)},
            }
        )
    return json.dumps(rows, indent=2)


def release_review_triage_ids(context_json):
    return [row["id"] for row in json.loads(context_json) if row.get("open_todos")]


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
    triage_ids = release_review_triage_ids(context_json)
    digest_context = (
        "Per-commit digest of the stored commit reviews. open_todos reflect maintainer completion state in the "
        "repo-manager database; completed_todos counts are resolved evidence:\n"
        + context_json
        + "\n\nFinal reminders: triage every digest entry that has open_todos "
        f"({', '.join(triage_ids) or 'none'}) with an explicit P0/P1/omit decision and a one-clause why — "
        "the verdict must follow from those decisions. A to-do earns its place only if the maintainer would "
        "regret shipping without it AND users would notice the consequence; omit everything else entirely "
        "(there is no P2). At most 6 items, each one actionable sentence with how to check; merge related "
        "decisions into shared to-dos.\n"
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
                errors.extend(release_review_validation_errors(candidate, triage_ids))
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


def build_announcement_feedback(error_list, release_highlights_raw, raw, plan_raw=""):
    previous_sections = []
    if (plan_raw or "").strip():
        previous_sections.append("Previous story plan attempt:\n```json\n" + plan_raw.strip() + "\n```")
    if (release_highlights_raw or "").strip():
        previous_sections.append(
            "Previous website release highlights attempt:\n```markdown\n" + release_highlights_raw.strip() + "\n```"
        )
    if (raw or "").strip():
        previous_sections.append("Previous Discord announcement attempt:\n```markdown\n" + raw.strip() + "\n```")
    return (
        "A previous attempt at this task failed validation. Fix every problem listed below while keeping the "
        "content accurate, then write corrected versions of ALL the files to the paths given above.\n"
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
        data.get("plan", ""),
    )


def save_announcement_feedback(workspace, repo, release_tag, error_list, release_highlights_raw, raw, plan_raw=""):
    path = announcement_feedback_file(workspace, repo, release_tag)
    path.write_text(
        json.dumps(
            {
                "errors": error_list,
                "release_highlights": release_highlights_raw,
                "announcement": raw,
                "plan": plan_raw,
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
    review_context = (
        "Commit summaries for this release (the announcement's only source material):\n"
        + announcement_review_context(workspace, reviews)
        + "\n\nFinal editorial reminders: tell the release as 3-5 stories, one section each; if two candidate "
        "sections would answer the same reader question, they are one story. Describe outcomes, never the work "
        "behind them — credit people as a clause in the feature sentence, and let enabling fixes be subsumed by "
        "the outcome they enabled.\n"
    )
    feedback = load_announcement_feedback(workspace, repo, args.release)
    if feedback:
        print("Resuming with validation feedback from a previous interrupted announce run.", flush=True)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        pending_release_highlights_file, pending_markdown_file, pending_plan_file = (
            pending_release_announcement_artifact_paths(workspace, repo, args.release, head)
        )
        prompt = (
            f"/skill:release-announcement\n\nRepo: {repo}\nBranch: {branch}\n"
            f"Release: {args.release}\nRange start: {range_start or 'unknown'}\nHead SHA: {head}\n\n"
            f"First write the story plan JSON to: {pending_plan_file}\n"
            f"Then write the website release highlights Markdown to: {pending_release_highlights_file}\n"
            f"Then write the Discord-friendly Markdown announcement to: {pending_markdown_file}\n\n"
            f"{release_highlights_context}"
            f"{style_context}"
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
        plan_raw = ""
        plan = None
        errors = []
        if Path(pending_plan_file).exists():
            plan_raw = Path(pending_plan_file).read_text(encoding="utf-8")
            plan_errors, plan = story_plan_validation_errors(plan_raw)
            errors.extend(plan_errors)
        else:
            errors.append(f"Expected story plan file was not created: {pending_plan_file}")
        if Path(pending_release_highlights_file).exists():
            release_highlights_raw = Path(pending_release_highlights_file).read_text(encoding="utf-8")
            errors.extend(release_highlights_validation_errors(release_highlights_raw))
        else:
            errors.append(f"Expected release highlights file was not created: {pending_release_highlights_file}")
        if Path(pending_markdown_file).exists():
            raw = Path(pending_markdown_file).read_text(encoding="utf-8")
            errors.extend(announcement_validation_errors(raw, prior_rows))
            errors.extend(announcement_plan_consistency_errors(raw, plan))
        else:
            errors.append(f"Expected announcement file was not created: {pending_markdown_file}")
        if not errors:
            break
        error_list = "\n".join(f"- {error}" for error in errors)
        if attempt == max_attempts:
            raise SystemExit(
                f"Announcement failed validation after {max_attempts} attempts:\n{error_list}"
            )
        print(f"\nAttempt {attempt} failed validation; asking Pi to revise:\n{error_list}\n", flush=True)
        save_announcement_feedback(workspace, repo, args.release, error_list, release_highlights_raw, raw, plan_raw)
        feedback = build_announcement_feedback(error_list, release_highlights_raw, raw, plan_raw)
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

"""The dashboard: one page, built from the state directory and nothing else.

`site render` writes it as a static file for GitHub Pages; `site serve` serves the same page
over a live directory and adds the three PR actions. Neither needs a git checkout of the
tracked repo, and `render` needs no network at all — a rendering bug can never block a
release, because rendering happens in the state repo and nowhere near one.
"""

import json
import re
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from repo_manager import buckets, release, store


def load(state):
    """Everything the page shows, read from files."""
    commits = []
    repo = ""
    for key in state.keys("commits", ".json"):
        data = state.read_json(key)
        if not data:
            continue
        repo = repo or data.get("repo", "")
        commits.append({
            "sha": data.get("sha") or Path(key).stem,
            "bucket": data.get("bucket", ""),
            "branch": data.get("branch", ""),
            "pr_number": data.get("pr_number"),
            "author": data.get("author", ""),
            "summary": data.get("summary", ""),
            "subject": data.get("subject", ""),
            "verdict": data.get("verdict", ""),
            "verdict_reason": data.get("verdict_reason", ""),
            "committed_at": data.get("committed_at", ""),
            "reviewed_at": data.get("reviewed_at", ""),
            "maintainer_todos": [todo_text(item) for item in data.get("maintainer_todos") or []],
            "shout_outs": data.get("shout_outs") or [],
            "reviewers": data.get("reviewers") or [],
            "evidence": ordered_evidence(data.get("evidence") or {}, COMMIT_EVIDENCE_ORDER),
            "cherry_picked_from": data.get("cherry_picked_from", ""),
        })
    commits.sort(key=lambda row: (row["committed_at"] or row["reviewed_at"] or "", row["sha"]), reverse=True)

    releases = []
    for name in bucket_names(state):
        review = state.read_json(store.review_key(name)) or {}
        releases.append({
            "bucket": name,
            "branch": review.get("branch", ""),
            "verdict": review.get("verdict", ""),
            "verdict_reason": review.get("verdict_reason", ""),
            "range_start": review.get("range_start", ""),
            "head_sha": review.get("head_sha", ""),
            "is_hotfix": bool(review.get("is_hotfix")),
            "last_stable_tag": review.get("last_stable_tag", ""),
            "prioritized_todos": blockers_first(review.get("prioritized_todos") or []),
            "breaking_changes": review.get("breaking_changes") or [],
            "tester_plan": review.get("tester_plan") or [],
            "evidence": ordered_evidence(review.get("evidence") or {}, release.EVIDENCE_KEYS),
            "candidate_issues": review.get("candidate_issues") or [],
            "commits_reviewed": review.get("commits_reviewed"),
            "reviewed_at": review.get("reviewed_at", ""),
            "notes": state.read_text(store.notes_key(name)),
            "announcement": state.read_text(store.announcement_key(name)),
            "frozen": [
                filename for filename in ("review.json", "notes.md", "announcement.md")
                if store.is_frozen(state, name, filename)
            ],
            "commits": sum(1 for row in commits if row["bucket"] == name),
        })
    releases.sort(key=lambda row: buckets.version_parts(row["bucket"]), reverse=True)

    prs = []
    for key in state.keys("prs", ".json"):
        data = state.read_json(key)
        if not data:
            continue
        outputs = data.get("outputs") or {}
        repo = repo or data.get("repo", "")
        prs.append({
            "number": data.get("pr_number"),
            "title": data.get("title", ""),
            "author": data.get("author", ""),
            "url": data.get("url", ""),
            "state": data.get("state", ""),
            "is_draft": bool(data.get("is_draft")),
            "labels": data.get("labels") or [],
            "label": outputs.get("label", ""),
            "scope": outputs.get("scope_display", ""),
            "body_matches_diff": outputs.get("body_matches_diff", ""),
            "docs_and_tests": outputs.get("docs_and_tests", ""),
            "suggested_reviewers": outputs.get("suggested_reviewers") or [],
            "explanation": data.get("explanation") or {},
            "concerns": concerns_of(data),
            "reviewed_at": data.get("reviewed_at", ""),
            "head_sha": data.get("head_sha", ""),
            "comment_url": data.get("comment_url", ""),
        })
    prs.sort(key=lambda row: row["reviewed_at"] or "", reverse=True)

    return {
        "repo": repo,
        "releases": releases,
        "commits": commits,
        "prs": prs,
        "counts": {
            "commits": len(commits),
            "prs": len(prs),
            "releases": len(releases),
            "verdicts": tally(row["verdict"] for row in commits),
            "labels": tally(row["label"] for row in prs),
            "blockers": sum(
                1 for release in releases for todo in release["prioritized_todos"]
                if todo.get("priority") == "P0"
            ),
        },
    }


# The files are stored with sorted keys, which is what makes their diffs readable, and
# alphabetical is the wrong order to read evidence in. The page puts it back.
COMMIT_EVIDENCE_ORDER = (
    "review", "post_approval_commits", "tests", "manual_release_testing",
    "api_compatibility", "security", "documentation",
)


def ordered_evidence(evidence, order):
    named = [(key, evidence[key]) for key in order if str(evidence.get(key, "")).strip()]
    rest = [(key, value) for key, value in evidence.items()
            if key not in order and str(value).strip()]
    return dict(named + rest)


def blockers_first(todos):
    """P0 before P1. A maintainer reading "before shipping" should hit what stops the release
    first, whatever order the model happened to write the list in."""
    return sorted(todos, key=lambda todo: 0 if todo.get("priority") == "P0" else 1)


def bucket_names(state):
    names = set()
    for key in state.keys("releases"):
        parts = key.split("/")
        if len(parts) > 2:
            names.add(parts[1])
    return sorted(names, key=buckets.version_parts)


def todo_text(item):
    if isinstance(item, dict):
        for key in ("text", "todo", "task", "action", "description"):
            if str(item.get(key, "")).strip():
                return str(item[key]).strip()
        return json.dumps(item, sort_keys=True)
    return str(item)


def concerns_of(data):
    from repo_manager import triage

    try:
        return triage.concerns(data)
    except (KeyError, TypeError, IndexError):
        return []


def tally(values):
    counts = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def script_safe_json(value):
    """JSON safe to embed in a <script> element.

    Inside JSON text `<` only occurs within strings, so escaping it cannot change the parsed
    value — but it stops review content containing a literal `</script>` from ending the
    element. U+2028 and U+2029 are legal in JSON strings and were historically illegal in JS.
    """
    payload = json.dumps(value, ensure_ascii=False)
    return (
        payload.replace("<", "\\u003c").replace(">", "\\u003e")
        .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )


def page(data=None):
    template = (Path(__file__).resolve().parent / "dashboard.html").read_text(encoding="utf-8")
    if data is None:
        return template
    bootstrap = (
        "<script>window.REPO_MANAGER_STATIC = true;"
        f"window.REPO_MANAGER_DATA = {script_safe_json(data)};</script>"
    )
    return template.replace("</head>", f"{bootstrap}\n</head>", 1)


def render(state, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page(load(state)), encoding="utf-8", newline="\n")
    return out_dir / "index.html"


# --- serving ------------------------------------------------------------------------------


def make_handler(ctx):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.send(page(), "text/html; charset=utf-8")
            elif path == "/api/data":
                self.send_json(load(ctx.store))
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            actions = {"/api/pr-comment": "comment", "/api/pr-label": "label",
                       "/api/pr-reviewers": "reviewers"}
            if path not in actions:
                self.send_error(404)
                return
            # These act on GitHub with the operator's own `gh` credentials, so a request the
            # browser labelled as coming from somewhere else is refused.
            origin = self.headers.get("Origin", "")
            if origin and urlparse(origin).netloc != self.headers.get("Host", ""):
                self.send_json({"ok": False, "error": "Cross-origin request rejected"}, status=403)
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except json.JSONDecodeError:
                self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
                return
            result = run_action(ctx, actions[path], payload)
            self.send_json(result, status=200 if result.get("ok") else 400)

        def send_json(self, payload, status=200):
            self.send(json.dumps(payload, ensure_ascii=False), "application/json", status)

        def send(self, text, content_type, status=200):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # Nothing here is ever safe to reuse: the page embeds the app itself, so a cached
            # copy survives a restart and hides every change.
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    return Handler


def run_action(ctx, action, payload):
    """A PR action from a request thread. SystemExit would otherwise kill the thread silently."""
    number = payload.get("number")
    if not isinstance(number, int):
        return {"ok": False, "error": "Missing PR number"}
    if not ctx.repo:
        return {"ok": False, "error": "Start `site serve` with --repo OWNER/REPO to act on PRs."}
    from repo_manager import triage

    try:
        if action == "comment":
            return triage.post_comment(ctx, number)
        if action == "label":
            return triage.apply_label(ctx, number)
        return triage.request_reviewers(ctx, number)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc) or "Command failed"}


def lan_address():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except OSError:
        return ""
    finally:
        probe.close()


def serve(ctx, host, port, open_browser=True):
    server = ThreadingHTTPServer((host, port), make_handler(ctx))
    actual_host, actual_port = server.server_address
    wildcard = actual_host in ("0.0.0.0", "", "::")
    url = f"http://{'127.0.0.1' if wildcard else actual_host}:{actual_port}/"
    print(f"Serving {ctx.store.root} at {url}")
    if wildcard:
        lan = lan_address()
        if lan:
            print(f"Reachable on the LAN at http://{lan}:{actual_port}/")
        print("Anyone on this network can browse this directory and act on GitHub as you.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()

"""The dashboard: the page, and the payload it reads.

`site render` writes it as a static file for GitHub Pages; `site serve` serves the same page
over a live directory, adds the PR actions, and keeps a live mirror of GitHub for the Status
column. Rendering needs no git checkout and no network, which is the point: it runs in the
state repo, so a rendering bug can never block a lemonade release.
"""

import hashlib
import json
import socket
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from repo_manager import buckets, prstate, release, store


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
    """P0 before P1. A maintainer reading the to-do list should hit what stops the release
    first, whatever order the model happened to write it in."""
    return sorted(todos, key=lambda todo: 0 if todo.get("priority") == "P0" else 1)


def todo_items(items):
    """The checklist as the page renders it.

    No completion state travels with it. Ticking an item off is a note the reader makes to
    themselves — it lives in their browser, under their GitHub login — and the artifact the
    item came from stays the only record of what the release still owes.
    """
    rows = []
    for item in items or []:
        if isinstance(item, dict):
            text = next((str(item[k]).strip() for k in ("text", "todo", "task", "action", "description")
                         if str(item.get(k, "")).strip()), json.dumps(item, sort_keys=True))
            rows.append({"text": text, "priority": item.get("priority", ""),
                         "platforms": item.get("platforms") or []})
        elif str(item).strip():
            rows.append({"text": str(item).strip(), "priority": "", "platforms": []})
    return rows


def bucket_names(state, commits=()):
    """Every bucket the directory knows about, newest first.

    A bucket exists as soon as a commit is filed under it, not when its release artifacts
    appear — otherwise the filter is empty exactly when a sweep is all that has run.
    """
    names = {row["bucket"] for row in commits if row.get("bucket")}
    for key in state.keys("releases"):
        parts = key.split("/")
        if len(parts) > 2:
            names.add(parts[1])
    return sorted(names, key=buckets.version_parts, reverse=True)


def commit_rows(state):
    rows, repo = [], ""
    for key in state.keys("commits", ".json"):
        data = state.read_json(key)
        if not data:
            continue
        repo = repo or data.get("repo", "")
        rows.append({
            "commit_sha": data.get("sha") or Path(key).stem,
            "bucket": data.get("bucket", ""),
            "branch": data.get("branch", ""),
            "range_start": data.get("range_start", ""),
            "pr_number": data.get("pr_number"),
            "author": data.get("author", ""),
            "summary": data.get("summary", ""),
            "verdict": data.get("verdict", ""),
            "verdict_reason": data.get("verdict_reason", ""),
            "merge_date": data.get("merge_date", ""),
            "commit_date": data.get("committed_at", ""),
            "reviewed_at": data.get("reviewed_at", ""),
            "reviewers": data.get("reviewers") or [],
            "shout_outs": data.get("shout_outs") or [],
            "evidence": ordered_evidence(data.get("evidence") or {}, COMMIT_EVIDENCE_ORDER),
            "cherry_picked_from": data.get("cherry_picked_from", ""),
            "generation_seconds": data.get("generation_seconds", 0),
            "todo_items": todo_items(data.get("maintainer_todos")),
            "details": data,
        })
    rows.sort(key=lambda row: (row["commit_date"] or row["reviewed_at"] or "", row["commit_sha"]), reverse=True)
    return rows, repo


def release_rows(state, commits):
    """One row per bucket, carrying everything that bucket ships with.

    The verdict, the notes and the Discord post are three views of one release, so they are
    one row with three fields rather than three lists to line up by name. A bucket with
    nothing but commits filed under it is still a release — it is the one on `main`, before
    anybody has built it — and it appears here with an empty verdict rather than not at all.
    """
    rows = []
    for name in bucket_names(state, commits):
        review = state.read_json(store.review_key(name)) or {}
        rows.append({
            "bucket": name,
            "repo": review.get("repo", ""),
            "branch": review.get("branch", ""),
            "range_start": review.get("range_start", ""),
            "last_stable_tag": review.get("last_stable_tag", ""),
            "head_sha": review.get("head_sha", ""),
            "verdict": review.get("verdict", ""),
            "verdict_reason": review.get("verdict_reason", ""),
            "reviewed_at": review.get("reviewed_at", ""),
            "generation_seconds": review.get("generation_seconds", 0),
            "reviewed": bool(review),
            "todo_items": todo_items(blockers_first(review.get("checklist") or [])),
            "breaking_changes": review.get("breaking_changes") or [],
            "candidate_issues": review.get("candidate_issues") or [],
            "is_hotfix": bool(review.get("is_hotfix")),
            "commits": sum(1 for row in commits if row["bucket"] == name),
            "commits_unreviewed": review.get("commits_unreviewed", 0),
            "frozen": [f for f in ("review.json", "notes.md", "announcement.md")
                       if store.is_frozen(state, name, f)],
            "notes_markdown": state.read_text(store.notes_key(name)),
            "announcement_markdown": state.read_text(store.announcement_key(name)),
            "evidence": ordered_evidence(review.get("evidence") or {}, release.EVIDENCE_KEYS),
        })
    return rows


def pr_rows(state, mirror=None, viewer=""):
    rows, repo = [], ""
    for key in state.keys("prs", ".json"):
        data = state.read_json(key)
        if not data:
            continue
        repo = repo or data.get("repo", "")
        outputs = data.get("outputs") or {}
        row = {
            "pr_number": data.get("pr_number"),
            "pr_title": data.get("title", ""),
            "author": data.get("author", ""),
            "url": data.get("url", ""),
            "label": outputs.get("label", ""),
            "scope": outputs.get("scope_display", ""),
            "body_matches_diff": outputs.get("body_matches_diff", ""),
            "docs_and_tests": outputs.get("docs_and_tests", ""),
            "suggested_reviewers": outputs.get("suggested_reviewers") or [],
            "head_sha": data.get("head_sha", ""),
            "reviewed_at": data.get("reviewed_at", ""),
            "generation_seconds": data.get("generation_seconds", 0),
            "comment_url": data.get("comment_url", ""),
            "comment_markdown": comment_preview(data),
            "requirement": prstate.requirement_of(data),
            "details": data,
            # Without a live mirror these are what the triage saw when it ran. The page shows
            # the Status column as "not live" rather than drawing a stale answer as a fresh one.
            "pr_state": data.get("state", ""),
            "base_ref": data.get("base_ref", ""),
            "pr_labels": data.get("labels") or [],
            "pr_is_draft": bool(data.get("is_draft")),
            "state_known": False,
            "state_fetched_at": "",
            "review_status": "",
            "review_status_detail": "",
            "coverage": {"adequate": False, "who": [], "pending": []},
        }
        row["attention"], row["attention_detail"] = prstate.attention_of(row)
        rows.append(row)
    rows.sort(key=lambda row: (row["reviewed_at"] or "", row["pr_number"] or 0), reverse=True)
    if mirror is not None:
        apply_live_state(rows, mirror, repo, viewer)
    return rows, repo


def apply_live_state(rows, mirror, repo, viewer):
    """Overlay what GitHub says right now. Only `site serve` ever calls this."""
    states = mirror.read(viewer)
    table = {}
    try:
        table = prstate.maintainer_table(repo or mirror.repo)
    except SystemExit:
        table = {}
    for row in rows:
        info = states.get(row["pr_number"]) or {}
        if not info:
            continue
        row.update({
            "pr_state": info.get("state", ""),
            "base_ref": info.get("base", ""),
            "pr_labels": info.get("labels") or [],
            "pr_is_draft": bool(info.get("is_draft")),
            "state_known": True,
            "state_fetched_at": info.get("fetched_at", ""),
        })
        status, detail = prstate.derive_review_status(
            info, row["author"], viewer, requirement=row["requirement"], maintainers=table)
        row["review_status"], row["review_status_detail"] = status, detail
        row["coverage"] = prstate.coverage_verdict(info, row["author"], row["requirement"], table)


def comment_preview(data):
    """The comment this PR's triage would post, for the dashboard to show verbatim."""
    from repo_manager import triage

    try:
        body = triage.render_comment(data, data.get("head_sha", ""))
    except (KeyError, TypeError, IndexError):
        return ""
    return "\n".join(l for l in body.splitlines() if not l.startswith(triage.COMMENT_MARKER)).strip()


def tally(values):
    counts = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return counts


def load(state, mirror=None, viewer=""):
    """Everything the page shows, read from files (plus the live mirror when serving)."""
    commits, repo = commit_rows(state)
    releases = release_rows(state, commits)
    prs, pr_repo = pr_rows(state, mirror, viewer)
    repo = repo or pr_repo or next((r["repo"] for r in releases if r["repo"]), "")
    payload = {
        "config": {"repo": repo, "branch": next((r["branch"] for r in releases if r["branch"]), "main")},
        "buckets": [row["bucket"] for row in releases],
        "counts": {
            "commits": len(commits),
            "releases": len(releases),
            "release_reviews": sum(1 for r in releases if r["reviewed"]),
            "announcements": sum(1 for r in releases
                                 if r["notes_markdown"] or r["announcement_markdown"]),
            "pr_reviews": len(prs),
            "todos": sum(len(r["todo_items"]) for r in commits + releases),
            "blockers": sum(1 for r in releases for t in r["todo_items"] if t.get("priority") == "P0"),
            "verdicts": tally(row["verdict"] for row in commits),
            "labels": tally(row["label"] for row in prs),
        },
        "commit_reviews": commits,
        "releases": releases,
        "pr_reviews": prs,
        "viewer": viewer or (mirror.sync.get("viewer", "") if mirror else ""),
        "pr_sync": dict(mirror.sync) if mirror else {},
    }
    # A fingerprint of everything except the freshness block, so a poll that finds nothing new
    # can say so and leave the page alone. Without it every poll would redraw the table under
    # the reader's cursor, and the only difference between the two payloads would be the clock.
    payload["digest"] = hashlib.sha256(json.dumps(
        {k: v for k, v in payload.items() if k != "pr_sync"},
        sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    return payload


def script_safe_json(value):
    """JSON safe to embed in a <script> element.

    Inside JSON text `<` only occurs within strings, so escaping it cannot change the parsed
    value — but it stops review content containing a literal `</script>` from ending the
    element. U+2028 and U+2029 are legal in JSON strings and were historically illegal in JS.
    """
    payload = json.dumps(value, ensure_ascii=False)
    return (payload.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def page(data=None):
    template = (Path(__file__).resolve().parent / "dashboard.html").read_text(encoding="utf-8")
    if data is None:
        return template
    bootstrap = ("<script>window.REPO_MANAGER_STATIC = true;"
                 f"window.REPO_MANAGER_STATIC_DATA = {script_safe_json(data)};</script>")
    return template.replace("</head>", f"{bootstrap}\n</head>", 1)


def render(state, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(page(load(state)), encoding="utf-8", newline="\n")
    return out_dir / "index.html"


# --- serving ------------------------------------------------------------------------------


def reviewed_numbers(state):
    return [int(Path(key).stem) for key in state.keys("prs", ".json") if Path(key).stem.isdigit()]


def serve_payload(ctx, mirror, viewer=""):
    """The payload, and the decision about whether to go and get fresh facts.

    Stale-while-revalidate, with one exception. Reading the directory always works, so the
    answer goes out immediately and a sync — if the mirror has aged past a minute — runs
    behind it for the next poll to pick up. The reader is never made to wait on GitHub to
    find out what they already knew. The exception is a mirror that has never been filled:
    a first load that flashes an empty Status column looks broken in a way that one saying
    "syncing…" does not.
    """
    numbers = reviewed_numbers(ctx.store)
    if numbers:
        if not mirror.states and not mirror.sync.get("synced_at"):
            mirror.refresh(numbers, wait=True)
        elif mirror.is_stale():
            mirror.refresh_async(numbers)
    return load(ctx.store, mirror, viewer)


def make_handler(ctx, mirror):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                self.send(page(), "text/html; charset=utf-8")
            elif path == "/api/data":
                params = parse_qs(urlparse(self.path).query)
                viewer = (params.get("viewer") or [""])[0][:64].lstrip("@")
                self.send_json(serve_payload(ctx, mirror, viewer))
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            actions = {"/api/pr-comment": "comment", "/api/pr-apply-label": "label",
                       "/api/pr-reviewers": "reviewers"}
            if path not in actions and path != "/api/sync":
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
            if path == "/api/sync":
                result = run_sync(ctx, mirror, payload)
            else:
                result = run_action(ctx, mirror, actions[path], payload)
            self.send_json(result, status=200 if result.get("ok") else 400)

        def send_json(self, payload, status=200):
            self.send(json.dumps(payload, ensure_ascii=False), "application/json", status)

        def send(self, text, content_type, status=200):
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            # Nothing here is ever safe to reuse: the page embeds the app itself, so a cached
            # copy survives a restart and hides every change, and the JSON is a live view.
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    return Handler


def run_sync(ctx, mirror, payload):
    """The Refresh button: the one path that waits for GitHub, because someone asked it to."""
    result = mirror.refresh(reviewed_numbers(ctx.store), full=bool(payload.get("full")), wait=True)
    viewer = str(payload.get("viewer") or "")[:64].lstrip("@")
    result["data"] = load(ctx.store, mirror, viewer)
    # A failed sync still answers with data — the mirror it could not refresh is exactly what
    # the reader should keep looking at, now labelled with why it did not move.
    result["ok"] = True
    return result


def run_action(ctx, mirror, action, payload):
    """A PR action from a request thread. SystemExit would otherwise kill the thread silently."""
    number = payload.get("pr_number", payload.get("number"))
    if not isinstance(number, int):
        return {"ok": False, "error": "Missing pr_number"}
    from repo_manager import triage

    try:
        if action == "comment":
            result = triage.post_comment(ctx, number)
        elif action == "label":
            result = triage.apply_label(ctx, number)
        else:
            result = triage.request_reviewers(ctx, number)
    except SystemExit as exc:
        return {"ok": False, "error": str(exc) or "Command failed"}
    # Acting on a PR changes it, so the row the reader is looking at is now out of date.
    mirror.refresh([number], numbers=[number])
    return result


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
    mirror = prstate.Mirror(ctx.repo)
    server = ThreadingHTTPServer((host, port), make_handler(ctx, mirror))
    # Started before the browser is, so the walk from the command to a rendered page usually
    # overlaps the first sync rather than following it.
    numbers = reviewed_numbers(ctx.store)
    if numbers:
        mirror.refresh_async(numbers)
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

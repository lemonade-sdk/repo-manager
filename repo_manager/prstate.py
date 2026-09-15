"""What GitHub says about the PRs we hold triages for, and what that means for the reader.

This is a cache, not a record: every field is re-derivable from GitHub, nothing a reviewer
typed lives here, and dropping it costs one sync. That is what lets the dashboard read it
without hedging — a PR is either mirrored and dated, or absent and the page says so.

It lives in the serving process's memory rather than on disk. The state directory holds
artifacts, and a PR's review state is true only as of the moment it was fetched, so it is
not one. `site render` never builds a mirror at all, which is how the published site stays
network-free.
"""

import json
import subprocess
import threading
from datetime import datetime, timezone

from repo_manager import github


GH_TIMEOUT_SECONDS = 30
STALE_SECONDS = 60
TERMINAL_STATES = ("MERGED", "CLOSED")
STANDING_VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")

# Everything the Status column and the coverage rules read, conversation included. The
# conversation connections are the expensive part — 450 of the 571 nodes per PR — and
# dropping them was tempting, but they are what catches a reply buried in a review thread,
# and chunking turns out to be the real fix: forty PRs with the full set is 24,000 nodes and
# 2.3 seconds, against 123 PRs at 74,000 nodes and a coin-flip failure. The cost was never
# per PR. It was per PR times every review ever written, on every page load.
PR_DETAIL_FIELDS = (
    "state baseRefName reviewDecision isDraft headRefOid updatedAt labels(first: 20) { nodes { name } } "
    "isInMergeQueue autoMergeRequest { enabledBy { login } } "
    "commits(last: 1) { nodes { commit { committedDate } } } "
    "reviews(last: 50) { nodes { author { login } state submittedAt } } "
    "comments(last: 50) { nodes { author { login } createdAt } } "
    "reviewThreads(last: 30) { nodes { comments(last: 15) { nodes { author { login } createdAt } } } } "
    "reviewRequests(first: 20) { nodes { requestedReviewer { ... on User { login } ... on Team { name } } } }"
)


class GitHubError(Exception):
    """A sync could not read GitHub. Carries the sentence the dashboard will show."""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def gh_graphql(query):
    """Run one GraphQL query, or raise GitHubError with something a person can read.

    Every failure mode gets checked, because the one this replaces checked none of them: a
    non-zero exit, an unparseable body, and a 200 carrying an `errors` array all used to land
    in the same silent `return {}` that blanked the column.
    """
    try:
        proc = subprocess.run(
            ["gh", "api", "graphql", "-f", f"query={query}"],
            capture_output=True, text=True, timeout=GH_TIMEOUT_SECONDS,
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
        for node in ((entry.get("reviews") or {}).get("nodes")) or [] if _login_of(node)
    ]
    comments = [
        {"login": _login_of(node), "at": node.get("createdAt", ""), "kind": "comment"}
        for node in ((entry.get("comments") or {}).get("nodes")) or [] if _login_of(node)
    ]
    # Kept apart from conversation comments: a reply inside a review thread is easy to miss
    # on the PR page, so a status that turns on one has to say where to look.
    for thread in ((entry.get("reviewThreads") or {}).get("nodes")) or []:
        comments += [
            {"login": _login_of(node), "at": node.get("createdAt", ""), "kind": "thread"}
            for node in ((thread.get("comments") or {}).get("nodes")) or [] if _login_of(node)
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
        "labels": [n.get("name", "") for n in ((entry.get("labels") or {}).get("nodes")) or [] if n.get("name")],
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

    Chunked because this is the query that outgrew the timeout. Forty PRs is ~1.3s against a
    ceiling of ten, and a cold rebuild of several hundred triages walks it in batches rather
    than betting the whole sync on one oversized request.
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
            # mirrored row alone rather than overwriting a good one with nothing.
            if isinstance(entry, dict):
                states[number] = parse_pr_detail(entry)
    return states, viewer


def fetch_changed_prs(repo, since, page_limit=10):
    """Every PR touched since `since`, newest first — the cheap half of a sync.

    Ordering by UPDATED_AT descending over all states makes this a change log: page until a
    PR older than the watermark shows up and everything after it is older still, so the walk
    stops after one page on a quiet repo. Returns (changed, complete); `complete` is False
    when the walk hit the page limit without reaching the watermark, which means the caller
    should fall back to a full sync rather than trust a partial change log.
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


class Mirror:
    """The live PR state behind `site serve`, held in memory for the life of the process."""

    def __init__(self, repo):
        self.repo = repo
        self.states = {}
        self.sync = {"synced_at": "", "attempted_at": "", "watermark": "", "viewer": "", "error": ""}
        self._lock = threading.Lock()

    def is_stale(self, stale_seconds=STALE_SECONDS):
        synced_at = parse_iso(self.sync.get("synced_at"))
        if not synced_at:
            return True
        return (datetime.now(timezone.utc) - synced_at).total_seconds() >= stale_seconds

    def refresh_async(self, reviewed):
        threading.Thread(
            target=self.refresh, args=(reviewed,), name=f"pr-sync:{self.repo}", daemon=True
        ).start()

    def refresh(self, reviewed, full=False, numbers=None, wait=False):
        """Bring the mirror up to date. Three passes, cheapest first.

        Ask what changed since the watermark (one point, and on a quiet repo the answer is
        nothing); detail-fetch the changed PRs we hold triages for; then take in any triaged
        PR the mirror has never seen. Cost scales with what changed, not with how many
        triages have accumulated. Terminal PRs are not refreshed on their own account —
        merged is forever — but pass 1 still catches one that reopens.
        """
        if not self.repo or "/" not in self.repo:
            return {"ok": False, "error": "No repo configured", "fetched": 0}
        acquired = self._lock.acquire(timeout=45) if wait else self._lock.acquire(blocking=False)
        if not acquired:
            return {"ok": True, "skipped": "A sync is already running", "fetched": 0}
        try:
            reviewed = {int(number) for number in reviewed}
            if not reviewed:
                self.sync.update({"synced_at": now_iso(), "attempted_at": now_iso(), "error": ""})
                return {"ok": True, "fetched": 0, "changed": 0, "reason": "no saved triages"}
            watermark = self.sync.get("watermark") or ""
            stale_reviewed = {
                number for number in reviewed
                if (self.states.get(number) or {}).get("state") not in TERMINAL_STATES
            }
            targets, surveyed = set(numbers or ()), ""
            if numbers:
                mode = "targeted"
            elif full or not watermark:
                mode, targets = "full", stale_reviewed
            else:
                mode = "incremental"
                changed, complete = fetch_changed_prs(self.repo, watermark)
                if complete:
                    targets = {number for number in changed if number in reviewed}
                    # Everything up to the newest entry in the change log has now been looked
                    # at, including the PRs we chose not to fetch. Recording that is what
                    # keeps the next walk short.
                    surveyed = max(changed.values(), default=watermark)
                else:
                    mode, targets = "full", stale_reviewed
            # A triage written since the last sync has no mirrored state at all, whatever the
            # change log said — its PR may not have been touched since it was opened.
            targets |= {number for number in reviewed if number not in self.states}

            states, viewer = ({}, "")
            if targets:
                states, viewer = fetch_pr_details(self.repo, targets)
            fetched_at = now_iso()
            # The watermark is a claim that nothing before it went unseen, so only a pass that
            # actually surveyed the repo may move it. A targeted sync — one PR, on its way to
            # writing a comment — proves nothing about any other PR.
            if mode == "targeted":
                high_water = watermark
            elif mode == "full":
                high_water = max([watermark] + [i.get("updated_at", "") for i in states.values() if i.get("updated_at")])
            else:
                high_water = max(watermark, surveyed)
            for number, info in states.items():
                self.states[number] = {**info, "fetched_at": fetched_at}
            self.sync.update({
                # For the same reason the watermark does not move: `synced_at` is what the
                # freshness label reports about the whole column, and one PR refreshed on its
                # way to a comment does not make the other hundred any newer.
                "synced_at": self.sync.get("synced_at") if mode == "targeted" else fetched_at,
                "attempted_at": fetched_at,
                "watermark": high_water,
                "viewer": viewer or self.sync.get("viewer") or "",
                "error": "",
            })
            return {"ok": True, "mode": mode, "fetched": len(states), "changed": len(targets)}
        except GitHubError as exc:
            # The attempt is recorded and the mirror is left exactly as it was. Stale state
            # with a date on it is worth more than a blank column.
            self.sync.update({"attempted_at": now_iso(), "error": str(exc)})
            return {"ok": False, "error": str(exc), "fetched": 0}
        finally:
            self._lock.release()

    def read(self, viewer=""):
        return {
            number: {**info, "viewer": viewer or self.sync.get("viewer", "")}
            for number, info in self.states.items()
        }


# --- what the state means -----------------------------------------------------------------


def is_ai_reviewer(handle):
    return any(token in str(handle or "").lower() for token in ("chatgpt", "claude", "copilot"))


def latest_reviews(info):
    """{login: where each person currently stands} — their verdict if they gave one.

    A comment-verdict review says a person spoke, not what they decided: it fills a slot only
    for someone who has not decided anything yet, and never overwrites one. This is GitHub's
    own model — its reviewDecision survives a later comment too.
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


def covers_any_area(entry, expert_areas):
    """Does this maintainer's Subject Areas cell name any area the PR needs an expert for?"""
    if not entry or not expert_areas:
        return []
    owned = {area.lower() for area in entry.get("areas", [])}
    return [area for area in expert_areas if area.strip().lower() in owned]


def review_coverage(reviewer_logins, requirement, maintainers, approvals=()):
    """Does the review this PR has satisfy what the guide asks: one reviewer, and where
    possible one who lists a subject area the diff lands in.

    The areas come from the triage's facts pass, copied from the maintainer table, so an
    expert here is a table lookup and never an inference. Admin status is not consulted; it is
    a repo permission, not evidence that a person knows this code.
    """
    areas = [str(a).strip() for a in (requirement or {}).get("areas") or [] if str(a).strip()]
    reviewers = [login.lower() for login in reviewer_logins]
    experts = {}
    for login in reviewers:
        covered = covers_any_area(maintainers.get(login), areas)
        if covered:
            experts[login] = covered
    approving = sorted(
        login for login in {str(l).lstrip("@").lower() for l in approvals} if login in maintainers
    )
    return {"count": len(reviewers), "needed": 1, "experts": experts, "areas": areas,
            "approving_maintainers": approving}


def expert_names(coverage):
    return ", ".join(f"{login} ({', '.join(areas)})" for login, areas in sorted(coverage["experts"].items()))


def derive_review_status(info, author, viewer_override="", requirement=None, maintainers=None):
    """Does this PR need something from me? Four answers, from my perspective.

    **Merge** (it is done, I merge it), **Review** (my turn), **Needs reviewer** (nobody is on
    the hook), and **In progress** (someone else is on the hook — another reviewer, or the
    author). The tooltip keeps the detail. Returns ('', '') when the PR is not open or live
    data is missing.
    """
    if not info or info.get("state") != "OPEN":
        return "", ""
    viewer = str(viewer_override or info.get("viewer") or "").lstrip("@").lower()
    author_login = str(author or "").lstrip("@").lower()
    table = maintainers or {}
    latest = latest_reviews(info)

    # A PR that carries rfc:required is waiting on a discussion, not on a reviewer. Nothing
    # about who has or has not reviewed it changes that, so it is answered before any of the
    # reviewer arithmetic below.
    if "rfc:required" in (info.get("labels") or []):
        return "Waiting for RFC", "Labeled rfc:required; review waits for an approved RFC."
    if info.get("in_merge_queue"):
        return "In progress", "In the merge queue."
    if info.get("auto_merge_by"):
        return "In progress", f"Auto-merge enabled by {info['auto_merge_by']}; merges when checks pass."

    def counts(login):
        return login != author_login and not is_ai_reviewer(login)

    reviewed = {login for login, review in latest.items()
                if review.get("state") in ("APPROVED", "CHANGES_REQUESTED") and counts(login)}
    approvals = sorted(login for login, review in latest.items()
                       if review.get("state") == "APPROVED" and counts(login))
    requested = {str(h).lstrip("@").lower() for h in info.get("requested") or []
                 if h and counts(str(h).lstrip("@").lower())}
    engaged = {login for login, review in latest.items()
               if review.get("state") == "COMMENTED" and counts(login)}

    # --- 1. My turn.
    mine = latest.get(viewer)
    if mine and mine.get("state") == "CHANGES_REQUESTED":
        my_time = mine.get("at") or ""
        pushed_at = info.get("last_commit_at") or ""
        if pushed_at and pushed_at > my_time:
            return "Review", f"Author pushed {pushed_at.replace('T', ' ')[:16]} UTC, after your change request."
        replies = [c for c in info.get("comments", [])
                   if (c.get("login") or "").lower() == author_login and (c.get("at") or "") > my_time]
        detail = "Your change request is out; no push since."
        if replies:
            detail += f" {len(replies)} repl{'y' if len(replies) == 1 else 'ies'}, no code change."
        return "In progress", detail
    if viewer and viewer in requested and viewer not in reviewed:
        return "Review", "You are a requested reviewer."

    # --- 2. Blocked on the author.
    blocking = sorted(login for login in reviewed
                      if (latest.get(login) or {}).get("state") == "CHANGES_REQUESTED")
    if blocking:
        return "In progress", f"{', '.join(blocking)} requested changes."

    # --- 3. Done: an approval. The expert is noted, not required — the guide says
    # "whenever possible", which is a wish for the reviewer to weigh, not a gate.
    coverage = review_coverage(sorted(reviewed), requirement, table, approvals)
    if approvals:
        experts = expert_names(coverage)
        note = f" Subject area covered by {experts}." if experts else (
            f" Nobody approving lists {', '.join(coverage['areas'])}." if coverage["areas"] else "")
        return "Merge", f"Approved by {', '.join(approvals)}.{note}"

    # --- 4. Is anyone on the hook?
    prospective = sorted(reviewed | requested | engaged)
    if prospective:
        if all(login in engaged and login not in requested for login in prospective):
            return "In progress", f"Reviewed with comments by {', '.join(prospective)}; no verdict yet."
        return "In progress", f"On it: {', '.join(prospective)}."
    return "Needs reviewer", "Nobody is reviewing this PR."


def coverage_verdict(info, author, requirement, maintainers):
    """Counts everyone on the hook, not only everyone who has finished: a requested reviewer
    is staffed on this PR, and naming more candidates will not make them answer."""
    if info.get("state") != "OPEN":
        return {"adequate": False, "who": [], "pending": []}
    author_login = str(author or "").lstrip("@").lower()
    latest = latest_reviews(info)
    reviewing = {login for login, review in latest.items()
                 if review.get("state") in ("APPROVED", "CHANGES_REQUESTED")
                 and login != author_login and not is_ai_reviewer(login)}
    requested = {str(h).lstrip("@").lower() for h in info.get("requested") or []
                 if h and not is_ai_reviewer(h) and str(h).lstrip("@").lower() != author_login}
    who = sorted(set(reviewing) | requested)
    return {"adequate": bool(who), "who": who, "pending": sorted(requested - set(reviewing))}


def attention_of(item):
    """One word for the list: `routine` when every answer is the good one, `elevated` when any
    is not. The five answers themselves stay in the detail pane."""
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
    return {"areas": ((data or {}).get("facts") or {}).get("areas") or []}


def maintainer_table(repo, ref="main"):
    from repo_manager import triage

    return github.parse_maintainer_table(triage.fetch_file(repo, ref, "docs/dev/contribute.md"))

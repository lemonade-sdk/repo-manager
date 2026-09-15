"""Job 2: the three artifacts a release ships with, and the rules that protect them.

`review.json` is the tester's plan. Whether the release is ready is the release admin's
call, made from that plan; nothing here makes it for them. `notes.md` is what the
lemonade release action puts on the release page. `announcement.md` is the Discord post.
All three are regenerated in place on every candidate — except where a human has edited
one, which freezes it: the release admin's words win over the model's, always.
"""

import json
import re
import time

from repo_manager import buckets, github, gitops, prose, store
from repo_manager.pi import extract_json_object, generate


# The platforms a release is hand-tested on. Every checklist item names the ones it applies
# to, so a tester picking up a Fedora box reads only the items that concern them.
PLATFORMS = ("Windows", "Ubuntu PPA", "Snap", "Docker", "macOS", "Fedora", "Debian")
ALL_PLATFORMS = "all"

CANDIDATE_LABEL = "candidate"
MAX_ANNOUNCEMENT_LINES = 45

EVIDENCE_KEYS = ("coverage", "blockers", "manual_testing", "breaking_changes", "security")

DIGEST_EVIDENCE_KEYS = (
    "tests",
    "manual_release_testing",
    "api_compatibility",
    "security",
    "post_approval_commits",
    "documentation",
)


# --- reading the bucket ------------------------------------------------------------------


class Bucket:
    """Everything a release command has to work out before it can do anything.

    Branch, bucket, range, head, and whether this is a hotfix are all derived from the same
    two inputs — the branch and the tag list — so the review, the notes, and the post can
    never disagree about what release they are describing.
    """

    def __init__(self, ctx, branch, head=""):
        self.ctx = ctx
        self.branch = branch
        self.checkout = ctx.checkout()
        self.name = buckets.bucket_for_branch(branch, ctx.now())
        self.tags = ctx.tags()
        self.range_start = buckets.range_start(self.tags, self.name)
        self.head = self.checkout.resolve(head) if head else self.checkout.resolve(f"origin/{branch}")
        self.stable_tags = buckets.stable_tags_in_bucket(self.tags, self.name)
        self.is_hotfix = bool(self.stable_tags)
        self.last_stable = self.stable_tags[-1] if self.stable_tags else ""

    def commits(self):
        return self.checkout.commits(self.range_start, self.head)

    def hotfix_commits(self):
        """On a hotfix, only what came after the tag users already have."""
        return self.checkout.commits(self.last_stable, self.head) if self.is_hotfix else []

    def summary(self):
        line = f"{self.name} on {self.branch}: {self.range_start or 'the beginning'}..{self.head[:7]}"
        return line + (f" (hotfix over {self.last_stable})" if self.is_hotfix else "")


def digest(ctx, shas):
    """The per-commit digest the release skills read, in commit order, and an index of every
    to-do in it.

    Only what a release decision turns on: who to ask, what shipped, what the commit review
    concluded, and the evidence behind it. Not the full review — a release with eighty
    commits would bury the model in prose it has already been given a verdict for.

    Each to-do carries an `id`. That id is the whole contract with the release review: the
    model answers with a priority per id and never retypes the text, so the checklist cannot
    quietly lose an item, reword one, or drift from the commit review it came from.
    """
    rows, missing, index = [], [], {}
    for sha in shas:
        data = ctx.store.read_json(store.commit_key(sha))
        if not data:
            missing.append(sha)
            continue
        evidence = data.get("evidence") or {}
        todos = []
        for position, item in enumerate(data.get("maintainer_todos") or [], start=1):
            text = todo_text(item)
            if not text:
                continue
            todo_id = f"{sha[:7]}-{position}"
            todos.append({"id": todo_id, "text": text})
            index[todo_id] = {
                "text": text,
                "commit": sha,
                "pr_number": data.get("pr_number"),
                "author": data.get("author", ""),
            }
        rows.append({
            "sha": sha[:7],
            "pr_number": data.get("pr_number"),
            "author": data.get("author", ""),
            "summary": data.get("summary", ""),
            "verdict": data.get("verdict", ""),
            "verdict_reason": data.get("verdict_reason", ""),
            "todos": todos,
            "shout_outs": [
                item.get("handle", "") if isinstance(item, dict) else str(item)
                for item in data.get("shout_outs") or []
            ],
            "evidence": {
                key: evidence[key] for key in DIGEST_EVIDENCE_KEYS if str(evidence.get(key, "")).strip()
            },
        })
    return rows, missing, index


def todo_text(item):
    if isinstance(item, dict):
        for key in ("text", "todo", "task", "action", "description", "item"):
            if str(item.get(key, "")).strip():
                return str(item[key]).strip()
        return json.dumps(item, sort_keys=True)
    return str(item).strip()


def candidate_issues(ctx, bucket):
    """Open `candidate` issues filed since this bucket's branch was cut.

    This is the tester feedback loop: a human found something on a candidate build and filed
    it against lemonade. The release review has to account for every one of them, because an
    unaccounted-for tester report is exactly the thing a release should not ship past.
    """
    cut = gitops.branch_cut_date(ctx.checkout(), bucket.name, bucket.range_start)
    try:
        return github.open_issues_with_label(ctx.repo, CANDIDATE_LABEL, since=cut)
    except SystemExit as exc:
        print(f"Could not list {CANDIDATE_LABEL} issues: {exc}", flush=True)
        return []


# --- the review --------------------------------------------------------------------------


PRIORITIES = ("P0", "P1", "P2")

P0_WORDS = ("P0", "BLOCKING", "BLOCKER", "BLOCKED", "HIGH", "CRITICAL")
P2_WORDS = ("P2", "P3", "LOW", "MINOR", "LOWEST")


def normalize_priority(value):
    """P1 is the default, and deliberately so. An unrated or garbled item is one nobody has
    decided about yet, and parking it in P2 hides it from the tester who should be deciding."""
    text = str(value or "").strip().upper()
    if text in P0_WORDS:
        return "P0"
    if text in P2_WORDS:
        return "P2"
    return "P1"


def priority_rank(item):
    priority = item.get("priority", "P1")
    return PRIORITIES.index(priority) if priority in PRIORITIES else 1


# Pi does not reliably emit the documented key: across runs it has filed the same answer
# under two or three different names. Recognize the list by what its key name implies rather
# than chasing an ever-growing allowlist.
RATING_KEY_HINTS = ("rating", "priorit", "checklist", "todo", "triage")
EXTRA_KEY_HINTS = ("extra", "additional", "issue", "report", "new_item")


def _named_collection(data, documented, hints, kinds):
    value = data.get(documented)
    if isinstance(value, kinds) and value:
        return value
    for key, other in data.items():
        if isinstance(other, kinds) and other and any(hint in key.lower() for hint in hints):
            return other
    return value if isinstance(value, kinds) else None


def _read_ratings(raw):
    ratings = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            ratings[str(key).strip()] = value if isinstance(value, dict) else {"priority": value}
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("id") or entry.get("todo_id") or entry.get("todo") or "").strip()
            if key:
                ratings[key] = entry
    return ratings


def extract_ratings(data):
    """`{id: {priority, platforms}}`, however the model chose to shape its answer.

    A list of objects is the documented form; a bare mapping of id to priority is the other
    shape runs actually produce, and both say the same thing. A key name only suggests which
    field holds the answer — what settles it is whether the entries carry ids, so a
    hint-matching list of something else cannot answer for the ratings and read as "nothing
    was rated".
    """
    candidates = [data.get("ratings")]
    candidates += [value for key, value in data.items()
                   if key != "ratings" and any(hint in key.lower() for hint in RATING_KEY_HINTS)]
    for raw in candidates:
        if isinstance(raw, (list, dict)) and raw:
            ratings = _read_ratings(raw)
            if ratings:
                return ratings
    return {}


def extract_extra_items(data):
    """Checklist items that are not a commit's to-do — a tester's `candidate` issue report."""
    raw = _named_collection(data, "extra_items", EXTRA_KEY_HINTS, (list,))
    return [entry for entry in (raw or []) if isinstance(entry, (dict, str))]


def normalize_breaking_changes(value):
    """Pi files each entry as a bare string or a small object; read both and fold any
    migration pointer into the sentence, so one string fully describes the change."""
    entries = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    items = []
    for entry in entries:
        if isinstance(entry, dict):
            text = str(
                entry.get("change") or entry.get("text") or entry.get("summary")
                or entry.get("description") or ""
            ).strip()
            migration = str(entry.get("migration") or entry.get("action") or "").strip()
            if text and migration and migration.lower() not in text.lower():
                text = f"{text} — {migration}"
        else:
            text = str(entry).strip()
        if text:
            items.append(text)
    return items


def normalize_review(data, index=None):
    """Assemble the checklist here, from the index.

    The model rates; this builds. Every to-do a commit review wrote is on the checklist with
    the words that commit review used, whatever the model sent back — an item cannot be
    dropped by an omission, reworded into something the maintainer never approved, or split
    away from the commit it belongs to. What the model contributes is the priority and the
    platforms, which is the judgement the release actually needs from it.

    Carrying `commit` through is what lets the dashboard tick the same box in two places: the
    release checklist and the commit review are the same to-do, so they are one checkbox.
    """
    index = index or {}
    ratings = extract_ratings(data)
    todos = []
    for todo_id, source in index.items():
        rating = ratings.get(todo_id)
        rating = rating if isinstance(rating, dict) else {}
        todos.append({
            "priority": normalize_priority(rating.get("priority")),
            "platforms": normalize_platforms(rating.get("platforms")),
            "text": source["text"],
            "commit": source["commit"],
            "pr_number": source.get("pr_number"),
            "author": source.get("author", ""),
        })
    # A tester's `candidate` issue is a to-do no commit review ever wrote, so it is the one
    # kind of item the model still supplies the words for.
    for item in extract_extra_items(data):
        text = todo_text(item) if isinstance(item, dict) else str(item).strip()
        if not text:
            continue
        priority = item.get("priority") if isinstance(item, dict) else ""
        platforms = item.get("platforms") if isinstance(item, dict) else None
        todos.append({
            "priority": normalize_priority(priority),
            "platforms": normalize_platforms(platforms),
            "text": text,
            "commit": "",
            "pr_number": None,
            "author": "",
        })
    # Sorted by priority and otherwise left in commit order, so the file's diff moves only
    # when a priority does.
    data["checklist"] = sorted(todos, key=priority_rank)
    data["breaking_changes"] = normalize_breaking_changes(
        data.get("breaking_changes")
        or ((data.get("evidence") or {}) if isinstance(data.get("evidence"), dict) else {}).get("breaking_changes_list")
    )
    data["evidence"] = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    # No verdict is computed, and none is stored. Whether a release ships is the release
    # admin's decision, made from the checklist and from the testing they have actually seen
    # done — and a stored one-word answer, frozen at the moment the model ran, could only ever
    # be out of date the first time somebody worked an item.
    data.pop("verdict", None)
    data.pop("verdict_reason", None)
    data.pop("ratings", None)
    data.pop("extra_items", None)
    return data


def normalize_platforms(value):
    """The platforms an item applies to, in the caller's order, spelled the caller's way.

    An item that names nothing applies everywhere: a tester should never have to guess whether
    silence means "all of them" or "we forgot".
    """
    names = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    matched = []
    for name in names:
        text = str(name or "").strip()
        if not text:
            continue
        if text.lower() == ALL_PLATFORMS:
            return [ALL_PLATFORMS]
        hit = next((p for p in PLATFORMS if p.lower() == text.lower()), text)
        if hit not in matched:
            matched.append(hit)
    ordered = [p for p in PLATFORMS if p in matched] + [p for p in matched if p not in PLATFORMS]
    # Naming every platform is the same claim as "all", written seven times.
    if set(ordered) >= set(PLATFORMS):
        return [ALL_PLATFORMS]
    return ordered or [ALL_PLATFORMS]


HAS_BREAKING = re.compile(r"breaking change", re.IGNORECASE)
NO_BREAKING = re.compile(
    r"\b(no|none|zero|without|not any|aren['’]?t any|no user-facing)\b", re.IGNORECASE
)


def review_errors(data, issues, index=None, ratings=None):
    """Structural checks that protect the maintainer-facing panels — nothing more.

    The checklist's contents are guaranteed by `normalize_review`, so what is left is the
    judgement only the model can supply — a priority for every to-do — and the one
    contradiction that would mislead a reader: an empty breaking-change list under prose
    that describes breaking changes.
    """
    errors = []
    index = index or {}
    ratings = ratings if ratings is not None else {}
    todos = data.get("checklist") or []
    # Rating every to-do is the whole job. An unrated one is not an item the model decided to
    # leave out — it is a decision it did not make, and P1 is where the default parks it.
    unrated = [todo_id for todo_id in index if todo_id not in ratings]
    if unrated:
        shown = ", ".join(unrated[:12]) + (f", and {len(unrated) - 12} more" if len(unrated) > 12 else "")
        errors.append(
            f"{len(unrated)} of the {len(index)} to-do(s) in the digest have no rating: {shown}. "
            "Return one entry in `ratings` for every id — rating them is the job, and choosing "
            "which ones to leave out is not."
        )
    unknown = [todo_id for todo_id in ratings if todo_id not in index]
    if unknown:
        errors.append(
            f"`ratings` names {', '.join(sorted(unknown)[:12])}, which is not a to-do id in the "
            "digest. Use the `id` exactly as the digest spells it, and put anything that is not "
            "one of these to-dos in `extra_items`."
        )
    evidence = data.get("evidence") or {}
    for key in EVIDENCE_KEYS:
        if not str(evidence.get(key, "")).strip():
            errors.append(
                f"evidence.{key} is required: one or two sentences of synthesis for the dashboard "
                "(or 'none observed' when that is the honest answer)."
            )
    breaking = data.get("breaking_changes")
    claims = str(evidence.get("breaking_changes", ""))
    if isinstance(breaking, list) and not breaking and prose.asserts(claims, HAS_BREAKING, NO_BREAKING):
        errors.append(
            "breaking_changes is empty but evidence.breaking_changes describes breaking "
            "changes — enumerate every user-facing breaking change in the list, one entry each with "
            "its migration, since the notes and the announcement are reconciled against it."
        )
    known = {p.lower() for p in PLATFORMS} | {ALL_PLATFORMS}
    for index, todo in enumerate(todos):
        unknown = [p for p in todo.get("platforms", []) if p.lower() not in known]
        if unknown:
            errors.append(
                f"checklist[{index}].platforms names {', '.join(unknown)}, which is not a platform "
                f"this project tests on. Use one or more of {', '.join(PLATFORMS)}, or \"all\"."
            )
    listed = " ".join(todo["text"] for todo in todos)
    for issue in issues:
        if f"#{issue['number']}" not in listed:
            errors.append(
                f"Open {CANDIDATE_LABEL} issue #{issue['number']} ({issue.get('title', '')}) is not on "
                "the checklist — every tester report needs an item naming the outcome to choose "
                "(fix later, hotfix, or revert)."
            )
    return errors


def rating_targets(index):
    """The to-do ids, in commit order, as the prompt asks for them back."""
    return "\n".join(f"- {todo_id}: {source['text']}" for todo_id, source in index.items())


def issues_block(issues):
    if not issues:
        return f"No open `{CANDIDATE_LABEL}` issues were filed against this bucket.\n"
    lines = [
        f"Open `{CANDIDATE_LABEL}` issues filed by testers against this bucket. Each one is a to-do: "
        "say what it is, and which outcome the maintainer should pick — fix later, hotfix, or revert. "
        "Name the issue number in the to-do text so the maintainer can open it.",
        "",
    ]
    for issue in issues:
        body = " ".join((issue.get("body") or "").split())[:600]
        author = (issue.get("author") or {}).get("login", "")
        lines.append(f"- #{issue['number']} (@{author}): {issue.get('title', '')}\n    {body}")
    return "\n".join(lines) + "\n"


def prior_review_block(ctx, bucket):
    data = ctx.store.read_json(store.review_key(bucket.name))
    if not data:
        return ""
    prior = {
        "priorities_last_time": {
            f"{todo.get('commit', '')[:7]}: {todo['text'][:90]}": todo.get("priority", "")
            for todo in data.get("checklist", []) if todo.get("commit")
        },
        "extra_items": [todo for todo in data.get("checklist", []) if not todo.get("commit")],
        "breaking_changes": data.get("breaking_changes", []),
    }
    return (
        "The priorities this bucket was given last time. Use them as the continuity baseline: "
        "keep a priority where it was unless something in the digest changed it, and do not "
        "write a second wording of an extra item that is already here.\n"
        + json.dumps(prior, indent=2) + "\n\n"
    )


def review_prompt(ctx, bucket, rows, missing, issues, index, path, feedback):
    platforms = "\n".join(f"- {platform}" for platform in PLATFORMS)
    coverage = ""
    if missing:
        coverage = (
            f"\n{len(missing)} commit(s) in this range have no commit review and are not in the "
            "digest; say so in evidence.coverage.\n"
        )
    hotfix = ""
    if bucket.is_hotfix:
        hotfix = (
            f"This bucket already shipped {bucket.last_stable}. Everything since that tag is a "
            "hotfix on top of what users already have; weigh it accordingly.\n"
        )
    return f"""/skill:release-review

Repo: {ctx.repo}
Branch: {bucket.branch}
Release bucket: {bucket.name}
Range start: {bucket.range_start or 'unknown'}
Head SHA: {bucket.head}
{hotfix}
Write the machine-readable JSON result to: {path}

{prior_review_block(ctx, bucket)}{issues_block(issues)}
## Platforms this project is tested on

{platforms}

Every rating names the platforms its to-do applies to in `platforms`, or `["all"]`.

## Per-commit digest of the stored commit reviews

This digest holds exactly {len(rows)} commit review(s). Use that number if you state one;
do not count the entries yourself.
{coverage}
{json.dumps(rows, indent=2)}

## Rate every one of these {len(index)} to-do(s)

Return `ratings` with one entry per id below — P0, P1 or P2 — and the platforms it applies to.
Every one of them is on the checklist whichever way you rate it; the priority is how a tester
knows what to do first and what is not this release's problem. Do not retype the text: the
maintainer reads the words the commit review already wrote, and the caller carries them over
for you.

{rating_targets(index)}

Final reminders: rating is the job, and filtering is not — an id you leave out of `ratings` is
not an item you removed, it is a judgement you failed to make, and it lands in P1 by default.
Put a tester's `candidate` issue in `extra_items`, because no commit review wrote it. Do not
write a verdict; whether this release ships is the release admin's call, not yours. The
evidence is read by somebody who has never seen this digest, so name the feature or behavior.

{feedback}"""


def build_review(ctx, bucket, force=False):
    key = store.review_key(bucket.name)
    filename = "review.json"
    if store.is_frozen(ctx.store, bucket.name, filename) and not force:
        print(f"Frozen (edited by hand): {key} — skipping. Use --force to regenerate.", flush=True)
        return ctx.store.read_json(key) or {}
    shas = bucket.commits()
    rows, missing, index = digest(ctx, shas)
    if not rows:
        raise SystemExit(f"No commit reviews found for {bucket.name}. Run `commit sweep` first.")
    issues = candidate_issues(ctx, bucket)
    started = time.monotonic()

    def prompt(paths, feedback):
        return review_prompt(ctx, bucket, rows, missing, issues, index, paths["review"], feedback)

    def validate(contents):
        data = extract_json_object(contents["review"])
        if data is None:
            return None, ["The artifact file must contain a valid JSON object."]
        ratings = extract_ratings(data)
        data = normalize_review(data, index)
        return data, review_errors(data, issues, index, ratings)

    data = generate("release-review", {"review": ".json"}, prompt, validate,
                    checkout=str(bucket.checkout.path))
    data.update({
        "repo": ctx.repo,
        "bucket": bucket.name,
        "branch": bucket.branch,
        "range_start": bucket.range_start,
        "head_sha": bucket.head,
        "is_hotfix": bucket.is_hotfix,
        "last_stable_tag": bucket.last_stable,
        "commits_reviewed": len(rows),
        "commits_unreviewed": len(missing),
        "candidate_issues": [issue["number"] for issue in issues],
        "reviewed_at": ctx.now_iso(),
        "generation_seconds": round(time.monotonic() - started, 1),
    })
    write_bucket_file(ctx, bucket.name, filename, store.dumps(data))
    counts = ", ".join(
        f"{sum(1 for t in data['checklist'] if t['priority'] == p)} {p}" for p in PRIORITIES
    )
    print(f"{bucket.name} checklist: {len(data['checklist'])} item(s) — {counts}", flush=True)
    return data


# --- notes and announcement ---------------------------------------------------------------


HORIZONTAL_RULE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+")
BREAKING_HEADING = re.compile(r"^#{1,6}\s+.*breaking\s+changes", re.IGNORECASE)


def notes_errors(markdown, canonical, bucket=""):
    """The structural contract for the machine-parsed file, and nothing else.

    The lemonade release action parses this by its `## Headline` and `## Breaking Changes`
    sections, so that is the whole hard requirement. Wording and register are the skill's job;
    a style regex must never be able to block a release.
    """
    text = (markdown or "").strip()
    if not text:
        return ["notes.md is empty."]
    lines = [line for line in text.splitlines() if not HORIZONTAL_RULE.match(line)]
    headings = [(index, line.strip()) for index, line in enumerate(lines) if re.match(r"^##\s+", line)]
    if [title for _, title in headings] != ["## Headline", "## Breaking Changes"]:
        return [
            "notes.md must contain exactly `## Headline` then `## Breaking Changes` as its only "
            "`##` sections, and nothing else but their bullets."
        ]
    errors = []
    headline_index, breaking_index = headings[0][0], headings[1][0]
    headline_lines = [line for line in lines[headline_index + 1 : breaking_index] if line.strip()]
    breaking_lines = [line for line in lines[breaking_index + 1 :] if line.strip()]
    bullets = [line for line in headline_lines if line.strip().startswith(("- ", "* "))]
    if len(bullets) != len(headline_lines):
        errors.append("The Headline section must contain only single-depth bullets.")
    if not 3 <= len(bullets) <= 5:
        errors.append(f"The Headline section must contain 3-5 bullets; it has {len(bullets)}.")
    for line in breaking_lines:
        if not line.strip().startswith(("- ", "* ")):
            errors.append("The Breaking Changes section must contain only bullets, or be empty.")
            break
    errors.extend(breaking_count_errors(len(breaking_lines), canonical, "notes.md"))
    errors.extend(bucket_as_tag_errors(text, bucket, "notes.md") if bucket else [])
    return errors


def count_breaking_bullets(markdown):
    """Bullets under a `Breaking Changes` heading at any depth; -1 when there is no section."""
    in_section, found, count = False, False, 0
    for line in (markdown or "").splitlines():
        if MARKDOWN_HEADING.match(line):
            if BREAKING_HEADING.match(line):
                in_section, found = True, True
            elif in_section:
                break
        elif in_section and line.strip().startswith(("- ", "* ")):
            count += 1
    return count if found else -1


def breaking_count_errors(count, canonical, where):
    """Reconcile an artifact's Breaking Changes section against the release review's list.

    The review is the single source of truth for what counts as a breaking change. Both
    artifacts must surface exactly that set, one bullet each, so a dropped or merged breaking
    change can no longer reach users unannounced.
    """
    if canonical is None:
        return []
    expected = len(canonical)
    if count == expected:
        return []
    if not expected:
        return [
            f"{where} lists {max(count, 0)} breaking change(s) but the release review found none; "
            "the section stays present with no bullets."
        ]
    if count == -1:
        return [
            f"{where} has no Breaking Changes section, but the release review found {expected} "
            "breaking change(s) that must appear, one bullet each: " + "; ".join(canonical)
        ]
    return [
        f"{where} has {count} Breaking Changes bullet(s) but the release review found {expected}; "
        "cover exactly these, one bullet each: " + "; ".join(canonical)
    ]


def bucket_as_tag_errors(markdown, bucket, where):
    """A release link built from the bucket name points at a tag that cannot exist.

    The bucket is `v2026.39`; the tag a human eventually cuts is `v2026.39.1`. The post is
    written before anyone tags, so the number is unknowable — which means any
    `releases/tag/<bucket>` URL is wrong the moment it is written, not merely stale.
    """
    if re.search(rf"releases/tag/{re.escape(bucket)}(?![.\d])", markdown or ""):
        return [
            f"{where} links to releases/tag/{bucket}, which will never exist: {bucket} is the "
            f"release bucket, and the tag is {bucket}.<number>, unknown until a human cuts it. "
            "Link to the repository's releases page instead."
        ]
    return []


def closing_link_errors(markdown, repo):
    """The post has to leave the reader somewhere to go.

    Paired with the rule above on purpose: told only that one URL is wrong, the model dropped
    the link altogether and closed with "check out the full release notes on GitHub" pointing
    at nothing.
    """
    if re.search(r"https?://\S+", markdown or ""):
        return []
    return [
        "The closing line has no link. Send the reader to the releases page: "
        f"https://github.com/{repo}/releases"
    ]


def announcement_errors(markdown, canonical, hotfix, bucket="", repo=""):
    text = (markdown or "").strip()
    if not text:
        return ["announcement.md is empty."]
    errors = bucket_as_tag_errors(text, bucket, "The announcement") if bucket else []
    errors += closing_link_errors(text, repo) if repo else []
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_ANNOUNCEMENT_LINES:
        errors.append(
            f"The post is {len(lines)} non-blank lines; keep it under {MAX_ANNOUNCEMENT_LINES}. "
            "Merge sections that answer the same reader question."
        )
    if hotfix:
        if "@release" not in text:
            errors.append(
                "This bucket already shipped a stable tag, so the post is a hotfix announcement: "
                "open it with `@release`, not `@everyone`."
            )
        if "@everyone" in text:
            errors.append("A hotfix post pings `@release`, never `@everyone`; remove the `@everyone`.")
    elif "@everyone" not in text:
        errors.append("The opener pings `@everyone`.")
    if canonical:
        errors.extend(breaking_count_errors(count_breaking_bullets(text), canonical, "The announcement"))
    return errors


def canonical_breaking(ctx, bucket):
    """The release review's breaking-change list, or None when there is no review to read."""
    data = ctx.store.read_json(store.review_key(bucket.name))
    if not data:
        return None
    return normalize_breaking_changes(data.get("breaking_changes"))


def breaking_block(canonical):
    if canonical is None:
        return ""
    if not canonical:
        return (
            "Breaking changes (from the release review, the source of truth): none. Leave the "
            "`## Breaking Changes` section present with no bullets, and omit any Breaking Changes "
            "section from the Discord post entirely.\n\n"
        )
    bullets = "\n".join(f"- {item}" for item in canonical)
    return (
        f"Breaking changes (from the release review, the source of truth): exactly {len(canonical)} "
        f"user-facing breaking change(s) ship here:\n{bullets}\n\n"
        "Surface every one of them, one bullet each. Reword them in the right register, but do not "
        "drop, merge, or invent any: the bullet count must equal the number above.\n\n"
    )


def prior_notes_block(repo, bucket):
    """Headline and Breaking Changes from the last three releases, as the style reference."""
    sections = []
    for tag in reversed(buckets.sort_tags(bucket.tags)):
        if buckets.version_parts(buckets.tag_bucket(tag)) >= buckets.version_parts(bucket.name):
            continue
        extracted = extract_note_sections(github.release_body(repo, tag))
        if extracted:
            sections.append(f"### {tag}\n\n{extracted}")
        if len(sections) == 3:
            break
    if not sections:
        return ""
    return (
        "The Headline and Breaking Changes sections of the last releases, as the style and "
        "structure reference. Match their level of abstraction; never copy their facts:\n\n"
        + "\n\n---\n\n".join(sections)
        + "\n\n"
    )


def extract_note_sections(markdown):
    wanted = {"headline", "breaking changes"}
    sections, current, current_level = {}, None, None
    for line in (markdown or "").splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        level, title = (len(match.group(1)), match.group(2).strip()) if match else (None, "")
        if level is not None:
            if title.lower() in wanted:
                current, current_level = title.lower(), level
                sections[current] = [line]
                continue
            if current and level <= current_level:
                current, current_level = None, None
        if current:
            sections[current].append(line)
    ordered = [("\n".join(sections.get(key, [])).strip()) for key in ("headline", "breaking changes")]
    return "\n\n".join(part for part in ordered if part)


def prior_announcements_block(ctx, bucket, limit=3):
    """Earlier buckets' Discord posts, as the voice reference. They live in the store."""
    sections = []
    earlier = sorted(
        (key for key in ctx.store.keys("releases", "announcement.md")),
        key=lambda key: buckets.version_parts(key.split("/")[1]),
        reverse=True,
    )
    for key in earlier:
        name = key.split("/")[1]
        if buckets.version_parts(name) >= buckets.version_parts(bucket.name):
            continue
        text = ctx.store.read_text(key).strip()
        if text:
            sections.append(f"### {name}\n\n{text}")
        if len(sections) == limit:
            break
    if not sections:
        return ""
    return (
        "Prior release announcements, as the voice reference. Match their voice, level of detail, "
        "and Discord formatting; never copy their facts, and vary the closing line so posts do not "
        "become repetitive:\n\n" + "\n\n---\n\n".join(sections) + "\n\n"
    )


BOT_HANDLE = re.compile(r"\[bot\]$|^@(github-actions|dependabot|renovate)\b", re.IGNORECASE)


def human(handle):
    """A bot is not somebody to thank. It authors dependency bumps, and crediting it puts a
    Discord ping on an account nobody reads."""
    return "" if BOT_HANDLE.search(str(handle or "")) else handle


def announcement_digest(rows):
    """What the announcement is allowed to draw on: outcomes and the people behind them."""
    return [
        {
            "author": human(row["author"]),
            "summary": row["summary"],
            "credits": [c for c in (human(c) for c in row["shout_outs"]) if c],
            "docs": " ".join(str(row["evidence"].get("documentation", "")).split())[:200],
        }
        for row in rows
    ]


def build_notes(ctx, bucket, force=False):
    filename = "notes.md"
    key = store.notes_key(bucket.name)
    if store.is_frozen(ctx.store, bucket.name, filename) and not force:
        print(f"Frozen (edited by hand): {key} — skipping. Use --force to regenerate.", flush=True)
        return ctx.store.read_text(key)
    rows, _, _ = digest(ctx, bucket.commits())
    if not rows:
        raise SystemExit(f"No commit reviews found for {bucket.name}. Run `commit sweep` first.")
    canonical = canonical_breaking(ctx, bucket)
    if canonical is None:
        print(
            "Warning: no release review for this bucket, so breaking changes are not reconciled. "
            "Run `release review` first.",
            flush=True,
        )

    def prompt(paths, feedback):
        return f"""/skill:release-notes

Repo: {ctx.repo}
Branch: {bucket.branch}
Release bucket: {bucket.name}
Range start: {bucket.range_start or 'unknown'}
Head SHA: {bucket.head}

Write the website release highlights Markdown to: {paths['notes']}

{prior_notes_block(ctx.repo, bucket)}{breaking_block(canonical)}\
## Commit summaries for this release (the only source material)

{json.dumps(announcement_digest(rows), indent=2)}

{feedback}"""

    def validate(contents):
        text = contents["notes"]
        return text, notes_errors(text, canonical, bucket.name)

    text = generate("release-notes", {"notes": ".md"}, prompt, validate,
                    checkout=str(bucket.checkout.path))
    write_bucket_file(ctx, bucket.name, filename, text.strip() + "\n")
    return text


def build_announcement(ctx, bucket, force=False):
    filename = "announcement.md"
    key = store.announcement_key(bucket.name)
    if store.is_frozen(ctx.store, bucket.name, filename) and not force:
        print(f"Frozen (edited by hand): {key} — skipping. Use --force to regenerate.", flush=True)
        return ctx.store.read_text(key)
    shas = bucket.hotfix_commits() if bucket.is_hotfix else bucket.commits()
    rows, _, _ = digest(ctx, shas)
    if not rows:
        raise SystemExit(f"No commit reviews found for {bucket.name}. Run `commit sweep` first.")
    canonical = canonical_breaking(ctx, bucket)
    notes = ctx.store.read_text(store.notes_key(bucket.name)).strip()
    shaping = (
        "The website highlights for this same release, already written. Its headline bullets are "
        "the stories, in order — tell the same stories here, in the Discord voice:\n"
        f"```markdown\n{notes}\n```\n\n"
        if notes else ""
    )
    hotfix = (
        f"This is a hotfix post. {bucket.last_stable} already shipped, and the commits below are "
        "everything that came after it. Open with `@release`, not `@everyone`, say plainly what was "
        "wrong and what is fixed, and keep it short.\n\n"
        if bucket.is_hotfix else ""
    )

    def prompt(paths, feedback):
        return f"""/skill:release-announcement

Repo: {ctx.repo}
Branch: {bucket.branch}
Release bucket: {bucket.name}
Range start: {bucket.last_stable or bucket.range_start or 'unknown'}
Head SHA: {bucket.head}

Write the Discord-friendly Markdown announcement to: {paths['announcement']}

{hotfix}{prior_announcements_block(ctx, bucket)}{shaping}{breaking_block(canonical)}\
## Commit summaries for this release (the only source material)

{json.dumps(announcement_digest(rows), indent=2)}

Final editorial reminders: tell the release as 3-5 stories, one Discord section each; if two
candidate sections would answer the same reader question, they are one story, and leftover
changes that are not a story become Additional Improvements bullets. Describe outcomes, never
the work behind them — credit people as a clause in the feature sentence, and let enabling
fixes be subsumed by the outcome they enabled.

{feedback}"""

    def validate(contents):
        text = contents["announcement"]
        return text, announcement_errors(text, canonical, bucket.is_hotfix, bucket.name, ctx.repo)

    text = generate("release-announcement", {"announcement": ".md"}, prompt, validate,
                    checkout=str(bucket.checkout.path))
    write_bucket_file(ctx, bucket.name, filename, text.strip() + "\n")
    return text


def write_bucket_file(ctx, bucket_name, filename, content):
    """Write a generated file and record its hash, in one commit.

    The hash and the file travel together: a commit that carried one without the other would
    make the file look human-edited on the next run and freeze it for good.
    """
    key = f"releases/{bucket_name}/{filename}"
    ctx.store.write_text(key, content)
    hashes_key = store.record_generated(ctx.store, bucket_name, filename, content)
    ctx.store.save([key, hashes_key], f"releases/{bucket_name}: {filename.split('.')[0]}")
    print(f"Wrote {key}", flush=True)
    return key


def frozen_files(ctx, bucket_name):
    return [
        filename
        for filename in ("review.json", "notes.md", "announcement.md")
        if store.is_frozen(ctx.store, bucket_name, filename)
    ]

"""Job 2: the three artifacts a release ships with, and the rules that protect them.

`review.json` is the maintainer's verdict and the tester's plan. `notes.md` is what the
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
        self.name = buckets.bucket_for_branch(branch, self.checkout, ctx.now())
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
    """The per-commit digest the release skills read, in commit order.

    Only what a release decision turns on: who to ask, what shipped, what the commit review
    concluded, and the evidence behind it. Not the full review — a release with eighty
    commits would bury the model in prose it has already been given a verdict for.
    """
    rows, missing = [], []
    for sha in shas:
        data = ctx.store.read_json(store.commit_key(sha))
        if not data:
            missing.append(sha)
            continue
        evidence = data.get("evidence") or {}
        rows.append({
            "sha": sha[:7],
            "pr_number": data.get("pr_number"),
            "author": data.get("author", ""),
            "summary": data.get("summary", ""),
            "verdict": data.get("verdict", ""),
            "verdict_reason": data.get("verdict_reason", ""),
            "todos": [todo_text(item) for item in data.get("maintainer_todos") or []],
            "shout_outs": [
                item.get("handle", "") if isinstance(item, dict) else str(item)
                for item in data.get("shout_outs") or []
            ],
            "evidence": {
                key: evidence[key] for key in DIGEST_EVIDENCE_KEYS if str(evidence.get(key, "")).strip()
            },
        })
    return rows, missing


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


def normalize_priority(value):
    return "P0" if str(value or "").strip().upper() in ("P0", "BLOCKING", "BLOCKER", "HIGH") else "P1"


# Pi does not reliably emit the documented key: across runs it has filed the same list under
# `open_todos` and under `todos`. Recognize the list by what its key name implies rather than
# chasing an ever-growing allowlist — the verdict is derived from that list, so a
# misnamed-but-present list must never collapse into a false "Ready".
TODO_KEY_HINTS = ("checklist", "todo", "action", "risk", "recommend", "blocker", "attention")


def extract_todos(data):
    documented = data.get("checklist") or data.get("prioritized_todos")
    if isinstance(documented, list) and documented:
        return documented
    for key, value in data.items():
        if isinstance(value, list) and value and any(hint in key.lower() for hint in TODO_KEY_HINTS):
            return value
    return documented if isinstance(documented, list) else []


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


def normalize_review(data):
    """Coerce Pi's output into the stored shape and derive the verdict from the to-do list.

    The list is the only source of truth. Computing the verdict here means the two can never
    disagree, and there is no second ledger to reconcile against.
    """
    todos = []
    for item in extract_todos(data):
        if isinstance(item, dict):
            text = todo_text(item)
            if text:
                todos.append({
                    "priority": normalize_priority(item.get("priority")),
                    "platforms": normalize_platforms(item.get("platforms")),
                    "text": text,
                })
        elif str(item).strip():
            todos.append({"priority": "P1", "platforms": [ALL_PLATFORMS], "text": str(item).strip()})
    data["checklist"] = todos
    data["breaking_changes"] = normalize_breaking_changes(
        data.get("breaking_changes")
        or ((data.get("evidence") or {}) if isinstance(data.get("evidence"), dict) else {}).get("breaking_changes_list")
    )
    data["evidence"] = data.get("evidence") if isinstance(data.get("evidence"), dict) else {}
    if any(todo["priority"] == "P0" for todo in todos):
        data["verdict"] = "Blocked"
    elif todos:
        data["verdict"] = "Needs Attention"
    else:
        data["verdict"] = "Ready"
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


FALSE_GREEN = re.compile(
    r"\bP[01]\b|\bblock(?:s|er|ers|ing)?\b|before (?:shipping|release|releasing|tagging)",
    re.IGNORECASE,
)
# "Nothing blocks the release" and "no P0s remain" are what a Ready review says. Read the
# guard a sentence at a time so a negated clause cannot fire it.
NOT_BLOCKING = re.compile(r"\b(no|not|nothing|none|zero|never|without)\b", re.IGNORECASE)
HAS_BREAKING = re.compile(r"breaking change", re.IGNORECASE)
NO_BREAKING = re.compile(
    r"\b(no|none|zero|without|not any|aren['’]?t any|no user-facing)\b", re.IGNORECASE
)


def review_errors(data, issues):
    """Structural checks that protect the maintainer-facing panels — nothing more.

    The verdict and priorities are guaranteed by `normalize_review`, so what is left is the
    prose a human reads and the two contradictions that would mislead them: an empty to-do
    list under a reason that describes blocking work, and an empty breaking-change list under
    prose that describes breaking changes.
    """
    errors = []
    todos = data.get("checklist") or []
    reason = str(data.get("verdict_reason", "")).strip()
    if not reason:
        errors.append("verdict_reason is required: one or two sentences answering 'can we ship?'.")
    if not todos and prose.asserts(reason, FALSE_GREEN, NOT_BLOCKING):
        errors.append(
            "checklist is empty but verdict_reason still describes work a tester has to do — "
            "put each such item on the checklist so the verdict reflects it."
        )
    evidence = data.get("evidence") or {}
    for key in EVIDENCE_KEYS:
        if not str(evidence.get(key, "")).strip():
            errors.append(
                f"evidence.{key} is required: one or two sentences of synthesis for the dashboard "
                "(or 'none observed' when that is the honest answer)."
            )
    breaking = data.get("breaking_changes")
    claims = f"{evidence.get('breaking_changes', '')} {reason}"
    if isinstance(breaking, list) and not breaking and prose.asserts(claims, HAS_BREAKING, NO_BREAKING):
        errors.append(
            "breaking_changes is empty but the evidence or verdict_reason describes breaking "
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
        "verdict": data.get("verdict", ""),
        "verdict_reason": data.get("verdict_reason", ""),
        "checklist": data.get("checklist", []),
        "breaking_changes": data.get("breaking_changes", []),
    }
    return (
        "The review this bucket already has. Use it as the continuity baseline: do not write a "
        "second wording of the same item, keep unresolved items stable when they are still valid, "
        "and add only genuinely new work.\n" + json.dumps(prior, indent=2) + "\n\n"
    )


def review_prompt(ctx, bucket, rows, missing, issues, path, feedback):
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

Every checklist item names the platforms it applies to in `platforms`, or `["all"]`. Do not
write an item for a platform this release did not touch.

## Per-commit digest of the stored commit reviews

This digest holds exactly {len(rows)} commit review(s). Use that number if you state one;
do not count the entries yourself.
{coverage}
{json.dumps(rows, indent=2)}

Final reminders: the checklist is the artifact, and its reader is a tester working through a
release candidate. An item earns its place only if somebody would regret shipping without
checking it AND users would notice the consequence; omit everything else entirely (there is no
P2). Each item is one actionable sentence — action, user-visible stake, how to tell whether it
worked — marked P0 or P1, naming the platforms it applies to, and ending with the source PR(s)
and handle(s) from the digest: `(#1234, @author)`. Merge related concerns into one item. The
verdict is computed from the checklist, so you cannot contradict it. `verdict_reason` and the
evidence are read by somebody who has never seen this digest: name the feature or behavior,
and let `verdict_reason` be just your one-or-two-sentence answer to "can we ship?".

{feedback}"""


def build_review(ctx, bucket, force=False):
    key = store.review_key(bucket.name)
    filename = "review.json"
    if store.is_frozen(ctx.store, bucket.name, filename) and not force:
        print(f"Frozen (edited by hand): {key} — skipping. Use --force to regenerate.", flush=True)
        return ctx.store.read_json(key) or {}
    shas = bucket.commits()
    rows, missing = digest(ctx, shas)
    if not rows:
        raise SystemExit(f"No commit reviews found for {bucket.name}. Run `commit sweep` first.")
    issues = candidate_issues(ctx, bucket)
    started = time.monotonic()

    def prompt(paths, feedback):
        return review_prompt(ctx, bucket, rows, missing, issues, paths["review"], feedback)

    def validate(contents):
        data = extract_json_object(contents["review"])
        if data is None:
            return None, ["The artifact file must contain a valid JSON object."]
        data = normalize_review(data)
        return data, review_errors(data, issues)

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
    print(f"{bucket.name} verdict: {data['verdict']} — {data.get('verdict_reason', '')}", flush=True)
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
    rows, _ = digest(ctx, bucket.commits())
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
    rows, _ = digest(ctx, shas)
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

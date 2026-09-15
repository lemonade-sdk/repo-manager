"""Job 1: one commit, one file.

`commits/<sha>.json` is the unit of work everything else is built from. It is written once
and never rewritten, so a sweep is just "review what has no file yet" and a rerun is free.
"""

import re
import time

from repo_manager import github, prose, store
from repo_manager.pi import extract_json_object, generate


VERDICTS = ("Clean", "Needs Attention", "Blocker")
EVIDENCE_KEYS = (
    "review",
    "post_approval_commits",
    "tests",
    "manual_release_testing",
    "api_compatibility",
    "security",
    "documentation",
)

# A `Clean` verdict whose own reason says a human still has to look at something is the
# worst output the skill can produce: the maintainer reads the grade and ships. The skill
# says so in as many words; this is the gate that makes it true.
FALSE_GREEN = re.compile(
    r"(maintainer should|should verify|should be verified|warrants (?:manual )?verification"
    r"|needs? (?:manual )?(?:verification|testing)|remains? untested)",
    re.IGNORECASE,
)
# The same sentence, negated, is the honest answer a clean review gives.
NOT_REALLY = re.compile(r"\b(no|not|nothing|none|never|without)\b", re.IGNORECASE)


def normalize_verdict(value):
    text = " ".join(str(value or "").split()).lower()
    for verdict in VERDICTS:
        if text == verdict.lower():
            return verdict
    if text in ("blocked", "blocking", "blocker"):
        return "Blocker"
    if text in ("needs-attention", "needs attention", "attention"):
        return "Needs Attention"
    return ""


def validation_errors(data):
    errors = []
    if not str(data.get("summary", "")).strip():
        errors.append("summary is required: one sentence saying what the PR does.")
    verdict = normalize_verdict(data.get("verdict"))
    if not verdict:
        errors.append(f"verdict must be exactly one of {', '.join(VERDICTS)}.")
    reason = str(data.get("verdict_reason", "")).strip()
    if not reason:
        errors.append("verdict_reason is required: one sentence explaining the grade.")
    todos = data.get("maintainer_todos")
    if not isinstance(todos, list):
        errors.append("maintainer_todos must be a list, empty when there is nothing to do.")
        todos = []
    if verdict == "Clean" and not todos and prose.asserts(reason, FALSE_GREEN, NOT_REALLY):
        errors.append(
            "verdict is Clean but verdict_reason still says a maintainer has to verify something — "
            "either raise the verdict to Needs Attention and add that as a maintainer to-do, or say "
            "why the behavior is not in the release surface."
        )
    evidence = data.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence is required: an object with one or two sentences per key.")
    else:
        for key in EVIDENCE_KEYS:
            if not str(evidence.get(key, "")).strip():
                errors.append(
                    f"evidence.{key} is required: a sentence of what you found "
                    "(or 'none observed' when that is the honest answer)."
                )
    if not isinstance(data.get("shout_outs", []), list):
        errors.append("shout_outs must be a list, empty when nobody meets the bar.")
    return errors


def normalize_todos(value):
    """One shape for `maintainer_todos`, whatever the model emitted.

    Pi writes a to-do as an object about four times in five and as a bare string the rest of
    the time. Every reader here copes with both, but the files are the store: somebody running
    `jq` over them should not have to.
    """
    items = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            text = next(
                (str(item[key]).strip() for key in ("text", "todo", "task", "action", "description")
                 if str(item.get(key, "")).strip()),
                "",
            )
            if text:
                items.append({**item, "text": text})
        elif item is not None and str(item).strip():
            items.append({"text": str(item).strip()})
    return items


def origin_commit(checkout, sha):
    """The commit a review should attribute: the original when this one is a cherry-pick."""
    source = checkout.cherry_pick_source(sha)
    return source or sha


def build_prompt(repo, sha, meta, bucket, branch, range_start, pr_number, author, picked_from, path):
    lines = [
        f"/skill:commit-review {repo} {sha}",
        "",
        f"Repo: {repo}",
        f"Commit: {sha}",
        f"Subject: {meta.get('subject', '')}",
        f"Committed: {meta.get('committed_at', '')}",
        f"Release bucket: {bucket} on {branch}"
        + (f", covering {range_start}..{branch}" if range_start else ""),
    ]
    if pr_number:
        lines.append(f"Associated PR: #{pr_number}" + (f" by {author}" if author else ""))
    if picked_from:
        lines.append(
            f"This commit is a cherry-pick of {picked_from}. Review what it ships on this branch, "
            "but read the PR discussion and reviews on the original commit."
        )
    lines += [
        "",
        f"Write the machine-readable JSON result to: {path}",
        "The JSON must match the schema required by the skill.",
    ]
    return "\n".join(lines) + "\n"


def review(ctx, sha, bucket, branch="main", range_start="", force=False):
    """Review one commit and write its file. Returns the key, or "" when one already existed."""
    checkout = ctx.checkout()
    full_sha = checkout.resolve(sha)
    key = store.commit_key(full_sha)
    if ctx.store.exists(key) and not force:
        print(f"Skipping {full_sha[:7]}: {key} already exists.", flush=True)
        return ""
    meta = checkout.commit_meta(full_sha)
    picked_from = checkout.cherry_pick_source(full_sha)
    pr_number, pr_author = github.associated_pr(ctx.repo, picked_from or full_sha)

    started = time.monotonic()

    def prompt(paths, feedback):
        return build_prompt(
            ctx.repo, full_sha, meta, bucket, branch, range_start, pr_number, pr_author,
            picked_from, paths["review"],
        ) + ("\n" + feedback if feedback else "")

    def validate(contents):
        data = extract_json_object(contents["review"])
        if data is None:
            return None, ["The artifact file must contain a valid JSON object."]
        return data, validation_errors(data)

    data = generate(
        "commit-review", {"review": ".json"}, prompt, validate, checkout=str(checkout.path)
    )
    data.update({
        "repo": ctx.repo,
        "sha": full_sha,
        "commit_sha": full_sha,
        "bucket": bucket,
        "branch": branch,
        "range_start": range_start,
        "committed_at": meta.get("committed_at", ""),
        "subject": meta.get("subject", ""),
        "verdict": normalize_verdict(data.get("verdict")),
        "maintainer_todos": normalize_todos(data.get("maintainer_todos")),
        "reviewed_at": ctx.now_iso(),
        "generation_seconds": round(time.monotonic() - started, 1),
    })
    if picked_from:
        data["cherry_picked_from"] = picked_from
    # GitHub is the authority on which PR a commit came from and who wrote it; the model is
    # asked for them only so it can reason, and its answer is not what gets stored.
    if pr_number:
        data["pr_number"] = pr_number
    elif not isinstance(data.get("pr_number"), int):
        data["pr_number"] = None
    if pr_author:
        data["author"] = pr_author
    elif not str(data.get("author", "")).strip():
        data["author"] = ""
    ctx.store.put_json(key, data, store.describe([key]))
    print(f"{full_sha[:7]} {data['verdict']}: {data.get('summary', '')}", flush=True)
    return key


def sweep(ctx, branch, bucket, range_start, head, force=False):
    """Review every commit in the range that has no file. Returns (reviewed, skipped, failed)."""
    checkout = ctx.checkout()
    shas = checkout.commits(range_start, head)
    print(
        f"{len(shas)} commit(s) in {range_start or 'the beginning'}..{head[:7]} for {bucket}",
        flush=True,
    )
    reviewed, skipped, failed = [], [], []
    for sha in shas:
        if ctx.store.exists(store.commit_key(sha)) and not force:
            skipped.append(sha)
            continue
        try:
            if review(ctx, sha, bucket, branch=branch, range_start=range_start, force=force):
                reviewed.append(sha)
        except SystemExit as exc:
            if exc.code in (130, None):
                raise
            print(f"Commit {sha[:7]} failed to review: {exc}", flush=True)
            failed.append(sha)
    print(
        f"Swept {len(shas)} commit(s): {len(reviewed)} reviewed, {len(skipped)} already had files"
        + (f", {len(failed)} failed" if failed else ""),
        flush=True,
    )
    return reviewed, skipped, failed

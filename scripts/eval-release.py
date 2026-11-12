#!/usr/bin/env python3
"""Regenerate recent releases' notes and posts, and score them against the admin's own edits.

    scripts/eval-release.py --state ~/lemonade-testing --checkout ~/lemonade --out runs/r1
    scripts/eval-release.py ... --out runs/r1 v2026.44          # one release
    scripts/eval-release.py ... --out runs/r1 --steps notes      # keep the stored review
    scripts/eval-release.py --out runs/r1 --score-only

The cases come from the store, never from a list kept here. A release is a case when a human
edited its notes or post after repo-manager wrote them: the file no longer matches its
recorded hash, and the draft it replaced is still in the store's git history. The edits are
the ground truth. By default the eval takes the `--last` few such releases, so it measures
the code against what the admin asks for now, and next week's edits join it on their own.

Each release runs in its own copy of the state directory with that release's notes and post
removed, so the admin's words cannot leak into the prompts; earlier releases stay, with their
history, because their edit diffs are an input under test. The release is rebuilt as it
stood at its candidate: stable tags at or after the head are ignored. Nothing is pushed.

Scoring: the `release-edits` skill reads each case's draft and published file side by side
and records the admin's decisions in a fixed shape — the lead, stories added to or cut from
the headline, breaking changes added or removed, and at most two style patterns — which
become checkable statements ("It leads with the container backends."). They are cached by
the two texts and the skill's version, so every run is held to the same list. The `release-judge` skill then says whether a regenerated file makes each
decision on its own. The score is the share of the admin's decisions the new code makes.

Faster runs:

- `--steps notes,announcement` keeps the release's stored review instead of regenerating it,
  when the change under test is downstream of the review.
- `--refresh-reviews` re-reviews, in the copy, only the commits that can fold as in-range
  fixes: their review predates `fixes_prs` and their PR mentions another PR in the same range.
  Refreshed reviews are cached by commit and commit-review skill, so later runs reuse them.
- `--proxy DIR` runs no model. Each skill call writes its full prompt (skill plus input) to
  DIR and stops; another model writes the artifact beside it, and the next invocation
  validates and uses it. Rerun until no prompts are pending. `--proxy-skills` limits which
  skills are proxied.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import traceback
from pathlib import Path


def package_root():
    """`--package DIR` runs another checkout of repo-manager, such as a released tag, with
    this harness. It has to be read before the imports below."""
    for i, arg in enumerate(sys.argv):
        if arg == "--package" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).resolve()
        if arg.startswith("--package="):
            return Path(arg.split("=", 1)[1]).resolve()
    return Path(__file__).resolve().parents[1]


sys.path.insert(0, str(package_root()))

from repo_manager import buckets, commits, github, pi, release, store  # noqa: E402
from repo_manager.context import Context  # noqa: E402
from repo_manager.store import Store  # noqa: E402

FILES = ("notes.md", "announcement.md")
STEPS = ("review", "notes", "announcement")
VERDICTS = ("made", "missed")


class Args:
    """The global options a Context wants, for a run that pushes nothing."""

    def __init__(self, repo, checkout, state):
        self.repo = repo
        self.checkout = checkout
        self.state = str(state)
        self.no_push = True
        self.force = True


# --- Cases ------------------------------------------------------------------------------


def changed(draft, published):
    """Whether the admin changed anything a reader would see, the AI warning aside."""
    clean = lambda text: release.strip_ai_warning(text).strip()  # noqa: E731
    return clean(draft) != clean(published)


def find_cases(state):
    """Every release a human edited, oldest first: {bucket: {"branch", "head", "files"}}."""
    s = Store(state, push=False)
    found = {}
    for key in s.keys("releases", "review.json"):
        name = key.split("/")[1]
        review = s.read_json(key) or {}
        if not review.get("branch") or not review.get("head_sha"):
            continue
        files = {}
        for filename in FILES:
            if not store.is_frozen(s, name, filename):
                continue
            draft = store.generated_draft(s, name, filename)
            if not draft:
                continue
            published = s.read_text(f"releases/{name}/{filename}")
            if changed(draft, published):
                files[filename] = {"draft": draft, "published": published}
        if files:
            found[name] = {"branch": review["branch"], "head": review["head_sha"], "files": files}
    return dict(sorted(found.items(), key=lambda item: buckets.version_parts(item[0])))


# --- Proxy ------------------------------------------------------------------------------


class Pending(Exception):
    """A proxied skill call is waiting for its artifact."""


def proxy_generate(directory, skills, real, tag):
    """A `generate` that hands the prompt to another model through files, for `skills`."""

    def generate(skill, outputs, build_prompt, validate, checkout="", attempts=3, base_ref="",
                 notes=None):
        if skills and skill not in skills:
            return real(skill, outputs, build_prompt, validate, checkout=checkout,
                        attempts=attempts, base_ref=base_ref, notes=notes)
        stem = f"{tag()}.{skill}"
        paths = {name: (directory / f"{stem}.{name}{suffix}").resolve() for name, suffix in outputs.items()}
        if not all(path.exists() for path in paths.values()):
            skill_text = pi.skill_path(skill).read_text()
            (directory / f"{stem}.prompt.md").write_text(
                f"# System prompt (the skill)\n\n{skill_text}\n\n# User prompt\n\n"
                + pi.environment_block(checkout) + build_prompt(paths, ""),
                encoding="utf-8",
            )
            raise Pending(stem)
        contents = {name: path.read_text(encoding="utf-8") for name, path in paths.items()}
        value, errors = validate(contents)
        if value is None:
            raise SystemExit(f"{stem}: the proxied artifact did not parse: {errors}")
        if errors:
            print(f"{stem}: kept with {len(errors)} validation problem(s):\n"
                  + "\n".join(f"- {e}" for e in errors), flush=True)
            if notes is not None:
                notes.extend(errors)
        return value

    return generate


# --- Running ----------------------------------------------------------------------------


def as_candidate(bucket):
    """The bucket as it stood before any stable tag was cut at or after its head."""
    checkout = bucket.checkout
    bucket.stable_tags = [
        tag for tag in bucket.stable_tags
        if checkout.git("merge-base", "--is-ancestor", bucket.head, tag, check=False).returncode != 0
    ]
    bucket.is_hotfix = bool(bucket.stable_tags)
    bucket.last_stable = bucket.stable_tags[-1] if bucket.stable_tags else ""
    return bucket


def skill_hash(name):
    return hashlib.sha256(pi.skill_path(name).read_bytes()).hexdigest()[:10]


def fix_candidates(ctx, bucket):
    """Commits that may repair another PR in this range but were reviewed before reviews
    recorded `fixes_prs`. Only these can change how the release folds, so only these are
    worth a fresh review. A cherry-pick's message rarely names the PR it fixes; the PR's
    own description does."""
    rows = {sha: ctx.store.read_json(store.commit_key(sha)) or {} for sha in bucket.commits()}
    in_range = {row.get("pr_number") for row in rows.values() if row.get("pr_number")}
    stale = {sha: row for sha, row in rows.items() if row and "fixes_prs" not in row}
    texts = github.pr_texts(ctx.repo, [row.get("pr_number") for row in stale.values()])
    found = []
    for sha, row in stale.items():
        number = row.get("pr_number")
        text = (texts.get(number) or {}).get("text", "") + "\n" + str(row.get("subject", ""))
        if (set(github.referenced_numbers(text)) & in_range) - {number}:
            found.append(sha)
    return found


def refresh_reviews(ctx, bucket, cache):
    shas = fix_candidates(ctx, bucket)
    version = skill_hash("commit-review")
    print(f"{bucket.name}: {len(shas)} commit review(s) to refresh: "
          f"{', '.join(sha[:7] for sha in shas) or 'none'}", flush=True)
    for sha in shas:
        cached = cache / f"{sha}.{version}.json"
        if cached.exists():
            ctx.store.write_json(store.commit_key(sha), json.loads(cached.read_text()))
            continue
        commits.review(ctx, sha, bucket.name, branch=bucket.branch, range_start=bucket.range_start, force=True)
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(ctx.store.read_json(store.commit_key(sha)), indent=2))


def run_bucket(args, name, case, out, current):
    """Build what `--steps` asks for, resuming a copy an earlier invocation started."""
    state = out / f"state-{name}"
    marker = state / ".eval-started"
    if not marker.exists():
        shutil.rmtree(state, ignore_errors=True)
        shutil.copytree(args.state, state, symlinks=True)
        for step in STEPS:
            if step in args.steps:
                (state / store.bucket_dir(name) / f"{step}.{'json' if step == 'review' else 'md'}").unlink(missing_ok=True)
        marker.write_text("")
    ctx = Context(Args(args.repo, args.checkout, state))
    bucket = as_candidate(release.Bucket(ctx, case["branch"], case["head"]))
    print(f"{name}: {bucket.summary()}", flush=True)
    current["bucket"] = name
    if args.refresh_reviews:
        refresh_reviews(ctx, bucket, Path(args.review_cache).expanduser())
    builders = {"review": release.build_review, "notes": release.build_notes,
                "announcement": release.build_announcement}
    for step in STEPS:
        done = state / store.bucket_dir(name) / f".eval-{step}"
        if step not in args.steps or done.exists():
            continue
        builders[step](ctx, bucket, force=True)
        done.write_text("")


# --- Judging ----------------------------------------------------------------------------


def json_artifact(contents, key):
    data = pi.extract_json_object(contents)
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        return None
    return data[key]


EDIT_FIELDS = {
    "added_stories": "{} is a headline story.",
    "removed_stories": "{} is not a headline story.",
    "added_breaks": "Breaking Changes lists that {}.",
    "removed_breaks": "Breaking Changes does not list that {}.",
    "style": "{}",
}


UNKNOWABLE = re.compile(r"(?i)\b(video|youtube|links?|urls?|contributors?|thanks|credit(?:s|ed)?)\b")


def knowable(statements):
    """A backstop for the skill's own rule: a writer working from the commits cannot know a
    video, a link, or who the admin wants to thank, so no draft is held to one."""
    return [statement for statement in statements if not UNKNOWABLE.search(statement)]


def statements_from(data):
    """The extracted fields as statements a judge can check one by one."""
    found = []
    lead = str(data.get("lead") or "").strip().rstrip(".")
    if lead:
        found.append(f"It leads with {lead}.")
    for field, template in EDIT_FIELDS.items():
        for item in data.get(field) or []:
            item = str(item).strip().rstrip(".")
            if item:
                statement = template.format(item).rstrip(".") + "."
                found.append(statement[0].upper() + statement[1:])
    return knowable(found)


def decisions(cache_dir, name, filename, case):
    """The admin's decisions on this file, as statements. Extracted once per pair of texts and
    version of the release-edits skill, so every run is held to the same list."""
    digest = hashlib.sha256(
        (case["draft"] + "\0" + case["published"] + "\0" + skill_hash("release-edits")).encode()
    ).hexdigest()[:16]
    cache = cache_dir / f"{name}.{filename}.{digest}.json"
    if cache.exists():
        return knowable(json.loads(cache.read_text())["decisions"])

    def prompt(paths, feedback):
        return (
            f"Release: {name}\nFile: {filename}\n\nWrite the decisions JSON to: {paths['decisions']}\n\n"
            f"## The draft repo-manager wrote\n\n```markdown\n{release.strip_ai_warning(case['draft']).strip()}\n```\n\n"
            f"## What the admin published\n\n```markdown\n{release.strip_ai_warning(case['published']).strip()}\n```\n"
            + feedback
        )

    def validate(contents):
        data = pi.extract_json_object(contents["decisions"])
        if not isinstance(data, dict):
            return None, ['Write {"lead", "added_stories", "removed_stories", "added_breaks", '
                          '"removed_breaks", "style"}.']
        found = statements_from(data)
        return found, [] if found else ["Record at least one decision."]

    found = release.generate("release-edits", {"decisions": ".json"}, prompt, validate)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"decisions": found}, indent=2))
    return found


def judge(out, name, filename, statements, text):
    """{statement number: (verdict, why)}, cached beside the run."""
    cache = out / f"{name}.{filename}.verdicts.json"
    digest = hashlib.sha256(
        (text + "\0" + json.dumps(statements) + "\0" + skill_hash("release-judge")).encode()
    ).hexdigest()
    if cache.exists():
        stored = json.loads(cache.read_text())
        if stored.get("sha256") == digest:
            return {int(k): tuple(v) for k, v in stored["verdicts"].items()}
    ids = set(range(1, len(statements) + 1))

    def prompt(paths, feedback):
        listed = "\n".join(f"{i}. {statement}" for i, statement in enumerate(statements, 1))
        return (
            f"Release: {name}\nFile: {filename}\n\nWrite the verdicts JSON to: {paths['verdicts']}\n\n"
            f"## The admin's decisions\n\n{listed}\n\n"
            f"## The new draft\n\n```markdown\n{release.strip_ai_warning(text).strip()}\n```\n" + feedback
        )

    def validate(contents):
        items = json_artifact(contents["verdicts"], "verdicts")
        if items is None:
            return None, ['Write {"verdicts": [{"id", "verdict", "why"}, ...]}.']
        got = {}
        for item in items:
            if isinstance(item, dict) and item.get("verdict") in VERDICTS:
                try:
                    got[int(item.get("id"))] = (item["verdict"], str(item.get("why", "")))
                except (TypeError, ValueError):
                    continue
        missing = sorted(ids - set(got))
        errors = [f"Give a verdict ({', '.join(VERDICTS)}) for statement(s) {missing}."] if missing else []
        return {k: v for k, v in got.items() if k in ids}, errors

    verdicts = release.generate("release-judge", {"verdicts": ".json"}, prompt, validate)
    cache.write_text(json.dumps({"sha256": digest,
                                 "verdicts": {str(k): list(v) for k, v in verdicts.items()}}, indent=2))
    return verdicts


def score(args, out, cases, names, current, pending):
    made = total = 0
    for name in names:
        print(f"\n{name}")
        for filename, case in cases[name]["files"].items():
            current["bucket"] = f"{name}.{Path(filename).stem}"
            try:
                statements = decisions(Path(args.decisions_cache).expanduser(), name, filename, case)
            except Pending as stem:
                pending.append(str(stem))
                print(f"  {filename:<16} waiting for {stem}")
                continue
            path = out / f"state-{name}" / store.bucket_dir(name) / filename
            if not path.exists() or not (path.parent / f".eval-{Path(filename).stem}").exists():
                print(f"  {filename:<16} not generated ({len(statements)} decisions)")
                continue
            try:
                verdicts = judge(out, name, filename, statements, path.read_text(encoding="utf-8"))
            except Pending as stem:
                pending.append(str(stem))
                print(f"  {filename:<16} waiting for {stem}")
                continue
            hits = 0
            for i, statement in enumerate(statements, 1):
                verdict, why = verdicts.get(i, ("missed", "no verdict"))
                hits += verdict == "made"
                print(f"    {verdict:<7} {statement}\n            {why}")
            made, total = made + hits, total + len(statements)
            print(f"  {filename:<16} {hits}/{len(statements)} of the admin's decisions")
    print(f"\n{made}/{total} of the admin's decisions made")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("buckets", nargs="*", help="Releases to run (default: the --last edited ones).")
    parser.add_argument("--last", type=int, default=3, help="How many recent edited releases to run.")
    parser.add_argument("--out", required=True)
    parser.add_argument("--state", required=True, help="A clone of the state repository; the cases come from it.")
    parser.add_argument("--checkout", default="", help="A clone of the tracked repository.")
    parser.add_argument("--repo", default="lemonade-sdk/lemonade")
    parser.add_argument("--package", default="", help="The repo-manager checkout to run (default: this one).")
    parser.add_argument("--steps", default=",".join(STEPS),
                        help="Which files to regenerate; the rest are kept as stored.")
    parser.add_argument("--score-only", action="store_true", help="Judge what is already in --out.")
    parser.add_argument("--refresh-reviews", action="store_true",
                        help="Re-review, in the copy, commits that may fold as in-range fixes.")
    parser.add_argument("--review-cache", default="~/.cache/repo-manager/eval-reviews")
    parser.add_argument("--decisions-cache", default="~/.cache/repo-manager/eval-decisions",
                        help="Where each case's extracted decisions are kept, so every run is held to the same list.")
    parser.add_argument("--proxy", default="", help="Hand skill prompts to another model through this directory.")
    parser.add_argument("--proxy-skills", default="", help="Comma-separated skills to proxy (default: all).")
    args = parser.parse_args()
    args.steps = [step.strip() for step in args.steps.split(",") if step.strip()]
    unknown = set(args.steps) - set(STEPS)
    if unknown:
        raise SystemExit(f"Unknown steps: {sorted(unknown)}; choose from {STEPS}.")

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cases = find_cases(args.state)
    names = args.buckets or list(cases)[-args.last:]
    missing = [name for name in names if name not in cases]
    if missing:
        raise SystemExit(f"Not an edited release with a recoverable draft: {missing}. Cases: {list(cases)}")
    print("Cases: " + ", ".join(f"{n} ({', '.join(cases[n]['files'])})" for n in names), flush=True)

    current = {"bucket": ""}
    pending = []
    if args.proxy:
        directory = Path(args.proxy).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        skills = {s.strip() for s in args.proxy_skills.split(",") if s.strip()}
        release.generate = proxy_generate(directory, skills, release.generate, lambda: current["bucket"])
        commits.generate = proxy_generate(directory, skills, commits.generate, lambda: current["bucket"])

    if not args.score_only:
        if not args.checkout:
            raise SystemExit("--checkout is required unless --score-only.")
        for name in names:
            try:
                run_bucket(args, name, cases[name], out, current)
            except Pending as stem:
                pending.append(str(stem))
                print(f"{name}: waiting for {stem}", flush=True)
            except SystemExit as exc:
                if exc.code in (130, None):
                    raise
                (out / f"{name}.error.txt").write_text(str(exc))
                print(f"{name}: failed: {exc}")
            except Exception:
                (out / f"{name}.error.txt").write_text(traceback.format_exc())
                print(f"{name}: crashed")

    score(args, out, cases, names, current, pending)
    if pending:
        print(f"\nPending proxy prompts in {args.proxy}: " + ", ".join(pending)
              + ". Write their artifacts and run again.")


if __name__ == "__main__":
    main()

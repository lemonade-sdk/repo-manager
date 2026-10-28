#!/usr/bin/env python3
"""Regenerate past releases' notes and posts, and score them against what the admin published.

    scripts/eval-release.py --state ~/lemonade-testing --checkout ~/lemonade --out runs/r1
    scripts/eval-release.py --state ~/lemonade-testing --checkout ~/lemonade --out runs/r1 v2026.44
    scripts/eval-release.py --out runs/r1 --score-only

Each bucket runs in its own copy of the state directory, with that bucket's own release files
removed, so neither the admin's published words nor an earlier draft of the same bucket can
leak into the prompts. Earlier buckets stay, with their git history, because the edit diffs
are part of what is being evaluated. The release is rebuilt as it stood at its candidate:
stable tags at or after the head are ignored. Nothing is pushed.

Ground truth is {"<bucket>": {"branch", "head", "checks": [{"file", "expect", "pattern",
"why"}]}}, where `expect` is `present`, `absent`, or `first_headline` (the first Headline
bullet matches). Each check records a mistake a draft made and the admin fixed.

Runs are sequential on purpose: the model is one local server, and concurrency only
lengthens every run.
"""

import argparse
import json
import re
import shutil
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_manager import commits, release, store  # noqa: E402
from repo_manager.context import Context  # noqa: E402


DEFAULT_TRUTH = Path(__file__).with_name("release-ground-truth.json")


class Args:
    """The global options a Context wants, for a run that pushes nothing."""

    def __init__(self, repo, checkout, state):
        self.repo = repo
        self.checkout = checkout
        self.state = str(state)
        self.no_push = True
        self.force = True


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


def run_bucket(args, name, spec, out):
    state = out / f"state-{name}"
    if state.exists():
        print(f"{name}: already in {out}, skipping")
        return
    shutil.copytree(args.state, state, symlinks=True)
    shutil.rmtree(state / store.bucket_dir(name), ignore_errors=True)
    ctx = Context(Args(args.repo, args.checkout, state))
    bucket = as_candidate(release.Bucket(ctx, spec["branch"], spec["head"]))
    print(f"{name}: {bucket.summary()}", flush=True)
    stale = [sha for sha in bucket.commits()
             if "fixes_prs" not in (ctx.store.read_json(store.commit_key(sha)) or {"fixes_prs": []})]
    if stale and args.refresh_reviews:
        for sha in stale:
            commits.review(ctx, sha, name, branch=bucket.branch, range_start=bucket.range_start, force=True)
    elif stale:
        print(f"{name}: {len(stale)} commit review(s) predate `fixes_prs`; --refresh-reviews "
              "re-reviews them in the copy so in-range fixes can fold.", flush=True)
    for step in (release.build_review, release.build_notes, release.build_announcement):
        step(ctx, bucket, force=True)


def first_headline(markdown):
    lines = release.strip_ai_warning(markdown).splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "## Headline"), None)
    if start is None:
        return ""
    return next((line for line in lines[start + 1:] if line.strip().startswith(("- ", "* "))), "")


def check_passes(check, text):
    pattern = re.compile(check["pattern"])
    if check["expect"] == "first_headline":
        return bool(pattern.search(first_headline(text)))
    found = bool(pattern.search(release.strip_ai_warning(text)))
    return found if check["expect"] == "present" else not found


def score(out, truth, names):
    passed = total = 0
    for name in names:
        bucket_dir = out / f"state-{name}" / store.bucket_dir(name)
        print(f"\n{name}")
        for check in truth[name]["checks"]:
            path = bucket_dir / check["file"]
            total += 1
            if not path.exists():
                print(f"  MISS  {check['file']:<16} (not generated)  {check['why']}")
                continue
            ok = check_passes(check, path.read_text(encoding="utf-8"))
            passed += ok
            print(f"  {'ok  ' if ok else 'FAIL'}  {check['file']:<16} {check['expect']:<14} {check['why']}")
    print(f"\n{passed}/{total} checks pass")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("buckets", nargs="*")
    parser.add_argument("--truth", default=str(DEFAULT_TRUTH))
    parser.add_argument("--out", required=True)
    parser.add_argument("--state", default="", help="A clone of the state repository to copy from.")
    parser.add_argument("--checkout", default="", help="A clone of the tracked repository.")
    parser.add_argument("--repo", default="lemonade-sdk/lemonade")
    parser.add_argument("--score-only", action="store_true", help="Score existing artifacts in --out without running")
    parser.add_argument("--refresh-reviews", action="store_true",
                        help="Re-review, in the copy, every commit whose review predates fixes_prs.")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    truth = json.loads(Path(args.truth).read_text())
    names = args.buckets or sorted(truth)

    if not args.score_only:
        if not args.state:
            raise SystemExit("--state is required unless --score-only.")
        for name in names:
            try:
                run_bucket(args, name, truth[name], out)
            except SystemExit as exc:
                if exc.code in (130, None):
                    raise
                (out / f"{name}.error.txt").write_text(str(exc))
                print(f"{name}: failed: {exc}")
            except Exception:
                (out / f"{name}.error.txt").write_text(traceback.format_exc())
                print(f"{name}: crashed")

    score(out, truth, names)


if __name__ == "__main__":
    main()

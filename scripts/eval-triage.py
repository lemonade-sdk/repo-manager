#!/usr/bin/env python3
"""Run the triage on a batch of PRs and score it against a ground-truth file.

    scripts/eval-triage.py --truth ground-truth.json --out runs/batch-1 3468 3494 3470

Ground truth is {"<pr>": {"label": "rfc:required", "expected_tool_label": "...", "weight": 3, ...}}.
`expected_tool_label` wins over `label` when present (it records where the tool is meant to
disagree with the human, such as router PRs with no charter). Nothing is stored in the
workspace database; each artifact lands in --out as pr-N.json next to its rendered comment.

Runs are sequential on purpose: the model is one local server, and concurrency only
lengthens every run.
"""

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_manager import cli, triage  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prs", nargs="*", type=int)
    parser.add_argument("--truth", default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--replay", default="", help="pr=sha,pr=sha for merged PRs to judge pre-review")
    parser.add_argument("--score-only", action="store_true", help="Score existing artifacts in --out without running")
    args = parser.parse_args()

    workspace = cli.find_workspace()
    repo = cli.load_config(workspace)["repo"]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    truth = json.loads(Path(args.truth).read_text()) if args.truth else {}
    replays = dict(part.split("=") for part in args.replay.split(",") if "=" in part)
    numbers = args.prs or sorted(int(n) for n in truth)

    if not args.score_only:
        for number in numbers:
            target = out / f"pr-{number}.json"
            if target.exists():
                print(f"PR #{number}: already in {out}, skipping")
                continue
            started = time.monotonic()
            try:
                data = triage.triage(workspace, repo, number, replay_sha=replays.get(str(number), ""), save=False)
            except SystemExit as exc:
                if exc.code in (130, None):
                    raise
                (out / f"pr-{number}.error.txt").write_text(str(exc))
                print(f"PR #{number}: failed: {exc}")
                continue
            except Exception:
                (out / f"pr-{number}.error.txt").write_text(traceback.format_exc())
                print(f"PR #{number}: crashed")
                continue
            target.write_text(json.dumps(data, indent=2))
            (out / f"pr-{number}.md").write_text(triage.render_comment(data))
            print(f"PR #{number}: {round(time.monotonic() - started)}s")

    score(out, truth, numbers)


def score(out, truth, numbers):
    rows, hits, weight_total, weight_hit = [], 0, 0, 0
    for number in numbers:
        path = out / f"pr-{number}.json"
        expected = truth.get(str(number), {})
        want = expected.get("expected_tool_label") or expected.get("label") or ""
        if not path.exists():
            rows.append((number, want, "(no artifact)", "", "", ""))
            continue
        data = json.loads(path.read_text())
        o = data["outputs"]
        got = o["label"]
        ok = (got == want) if want else None
        weight = expected.get("weight", 1)
        if want:
            weight_total += weight
            if ok:
                hits += 1
                weight_hit += weight
        rows.append((number, want, got, "ok" if ok else ("MISS" if ok is False else ""), o["scope_display"], f"body={o['body_matches_diff']} dt={o['docs_and_tests']} rev={','.join(h.lstrip('@') for h in o['suggested_reviewers'])}"))
    print()
    print(f"{'PR':>6}  {'expected':<17} {'got':<17} {'':<4} {'scope':<32} detail")
    for row in rows:
        print(f"{row[0]:>6}  {row[1]:<17} {row[2]:<17} {row[3]:<4} {row[4]:<32} {row[5]}")
    scored = sum(1 for r in rows if r[1] and r[2] != "(no artifact)")
    if scored:
        print(f"\nlabel accuracy: {hits}/{scored}  weighted: {weight_hit}/{weight_total}")


if __name__ == "__main__":
    main()

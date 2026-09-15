"""`repo-manager pr ...` — job 3."""

from repo_manager import triage
from repo_manager.context import Context


def add_parser(sub, shared):
    parser = sub.add_parser("pr", help="Triage open pull requests and act on the result.")
    nouns = parser.add_subparsers(dest="verb", required=True)

    review = nouns.add_parser("review", parents=[shared], help="Triage one PR and write its file.")
    review.add_argument("number", type=int)
    review.add_argument("--replay", default="", help="Judge the PR as it stood at this commit.")
    review.add_argument("--no-store", action="store_true", help="Print the triage instead of storing it.")
    review.set_defaults(func=cmd_review)

    sweep = nouns.add_parser("sweep", parents=[shared], help="Triage every open PR whose file is out of date.")
    sweep.add_argument("--limit", type=int, default=100, help="Most open PRs to consider.")
    sweep.add_argument("--force", action="store_true", help="Triage again even when the head SHA is unchanged.")
    sweep.add_argument("--include-drafts", action="store_true", help="Also triage draft PRs.")
    sweep.add_argument(
        "--since", default=triage.SWEEP_SINCE,
        help=f"Only PRs opened on or after this date (default {triage.SWEEP_SINCE}, when the "
             "spec-driven development policy landed). Pass 'all' for no cutoff.",
    )
    sweep.set_defaults(func=cmd_sweep)

    post = nouns.add_parser("post", parents=[shared], help="Post or update the triage comment on the PR.")
    post.add_argument("number", type=int)
    post.add_argument("--dry-run", action="store_true", help="Print the comment without posting it.")
    post.set_defaults(func=cmd_post)

    label = nouns.add_parser(
        "label", parents=[shared],
        help="Apply the triage's rfc: label; rfc:required also drafts the PR and posts the RFC request.",
    )
    label.add_argument("number", type=int)
    label.add_argument("--label", default="", help="Apply this label instead of the stored triage's.")
    label.add_argument("--dry-run", action="store_true", help="Print the gh commands without running them.")
    label.set_defaults(func=cmd_label)

    reviewers = nouns.add_parser(
        "request-reviewers", parents=[shared], help="Request the suggested reviewers on GitHub."
    )
    reviewers.add_argument("number", type=int)
    reviewers.add_argument("--reviewers", default="", help="Comma-separated handles, instead of the suggestions.")
    reviewers.add_argument("--dry-run", action="store_true", help="Print who would be requested.")
    reviewers.set_defaults(func=cmd_request_reviewers)


def cmd_review(args):
    ctx = Context(args)
    ctx.require_repo()
    data = triage.triage(ctx, args.number, replay_sha=args.replay, save_result=not args.no_store)
    if args.no_store:
        print(triage.render_comment(data))
    return 0


def cmd_sweep(args):
    ctx = Context(args)
    ctx.require_repo()
    failed = triage.sweep(
        ctx, since=args.since, limit=args.limit,
        include_drafts=args.include_drafts, force=args.force,
    )
    return 1 if failed else 0


def report(result):
    for warning in result.get("warnings", []):
        print(f"Warning: {warning}")


def cmd_post(args):
    ctx = Context(args)
    ctx.require_repo()
    result = triage.post_comment(ctx, args.number, dry_run=args.dry_run)
    report(result)
    if not result.get("ok"):
        raise SystemExit(result.get("error", "pr post failed"))
    if result.get("dry_run"):
        print(f"Would {result['action']} this comment on {ctx.repo}#{args.number}:\n")
        print(result["body"])
        return 0
    print(f"{result['action'].capitalize()} triage comment on {ctx.repo}#{args.number}: {result.get('url', '')}")
    return 0


def cmd_label(args):
    ctx = Context(args)
    ctx.require_repo()
    result = triage.apply_label(ctx, args.number, label=args.label, dry_run=args.dry_run)
    report(result)
    if not result.get("ok") and not result.get("dry_run"):
        raise SystemExit(result.get("error", "pr label failed"))
    if result.get("dry_run"):
        if not result["actions"]:
            print(f"PR #{args.number} already carries {result['label']} with nothing left to do.")
        for action in result["actions"]:
            print(f"Would run: gh {' '.join(action['cmd'])}")
        return 0
    print(f"PR #{args.number}: {result['label']} — {', '.join(result['done']) or 'nothing to do'}")
    return 0


def cmd_request_reviewers(args):
    ctx = Context(args)
    ctx.require_repo()
    handles = [part.strip() for part in args.reviewers.split(",") if part.strip()] or None
    result = triage.request_reviewers(ctx, args.number, handles=handles, dry_run=args.dry_run)
    report(result)
    if not result.get("ok"):
        raise SystemExit(result.get("error", "pr request-reviewers failed"))
    for item in result.get("skipped", []):
        print(f"Skipping {item['handle']}: {item['reason']}")
    if result.get("dry_run"):
        if result["to_request"]:
            print(f"Would request reviews on {ctx.repo}#{args.number} from:")
            for item in result["to_request"]:
                print(f"- {item['handle']} ({item.get('basis', '')}): {item.get('detail', '')}")
        else:
            print("No reviewers left to request.")
        return 0
    for handle in result.get("requested", []):
        print(f"Requested review from {handle}")
    for item in result.get("failed", []):
        print(f"Failed to request {item['handle']}: {item['reason']}")
    if not result.get("requested") and not result.get("failed"):
        print("No reviewers left to request.")
    return 0

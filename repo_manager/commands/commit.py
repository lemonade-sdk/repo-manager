"""`repo-manager commit ...` — job 1."""

from repo_manager import buckets, commits
from repo_manager.context import Context


def add_parser(sub, shared):
    parser = sub.add_parser("commit", help="Review merged commits.")
    nouns = parser.add_subparsers(dest="verb", required=True)

    review = nouns.add_parser(
        "review", parents=[shared], help="Review one commit and write its file."
    )
    review.add_argument("sha")
    review.add_argument("--branch", default="main", help="The branch the commit is on (default: main).")
    review.add_argument("--force", action="store_true", help="Review again even if the file exists.")
    review.set_defaults(func=cmd_review)

    sweep = nouns.add_parser(
        "sweep", parents=[shared], help="Review every commit in the release range that has no file."
    )
    sweep.add_argument("--branch", default="main", help="The branch to sweep (default: main).")
    sweep.add_argument("--since", default="", help="Range start, instead of the newest earlier v* tag.")
    sweep.add_argument("--head", default="", help="Range end (default: the branch tip).")
    sweep.add_argument("--force", action="store_true", help="Review again even where a file exists.")
    sweep.set_defaults(func=cmd_sweep)


def cmd_review(args):
    ctx = Context(args)
    ctx.require_repo()
    bucket = buckets.bucket_for_branch(args.branch, ctx.now())
    tags = ctx.tags()
    commits.review(
        ctx, args.sha, bucket,
        branch=args.branch,
        range_start=buckets.range_start(tags, bucket),
        force=args.force,
    )
    return 0


def cmd_sweep(args):
    ctx = Context(args)
    ctx.require_repo()
    checkout = ctx.checkout()
    bucket = buckets.bucket_for_branch(args.branch, ctx.now())
    range_start = args.since or buckets.range_start(ctx.tags(), bucket)
    head = checkout.resolve(args.head) if args.head else checkout.resolve(f"origin/{args.branch}")
    _, _, failed = commits.sweep(ctx, args.branch, bucket, range_start, head, force=args.force)
    return 1 if failed else 0

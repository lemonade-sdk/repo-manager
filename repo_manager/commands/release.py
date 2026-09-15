"""`repo-manager release ...` — job 2, and the job 4 placeholder."""

from repo_manager import commits, release, store
from repo_manager.context import Context


def add_parser(sub, shared):
    parser = sub.add_parser("release", help="Build a bucket's release artifacts.")
    nouns = parser.add_subparsers(dest="verb", required=True)

    def bucket_parser(name, help_text, func):
        command = nouns.add_parser(name, parents=[shared], help=help_text)
        command.add_argument("--branch", required=True, help="The branch being released.")
        command.add_argument("--head", default="", help="The commit being described (default: the branch tip).")
        command.add_argument("--force", action="store_true", help="Regenerate files a human has edited.")
        command.set_defaults(func=func)
        return command

    bucket_parser(
        "build",
        "Review the range, then write review.json, notes.md, and announcement.md.",
        cmd_build,
    )
    bucket_parser("review", "Write the bucket's review.json.", cmd_review)
    bucket_parser("notes", "Write the bucket's notes.md.", cmd_notes)
    bucket_parser("announce", "Write the bucket's announcement.md.", cmd_announce)

    candidate = nouns.add_parser(
        "candidate", parents=[shared], help="Write a candidate's delta (not implemented yet)."
    )
    candidate.add_argument("--branch", required=True)
    candidate.add_argument("--number", type=int, help="The candidate number.")
    candidate.set_defaults(func=cmd_candidate)


def open_bucket(args):
    ctx = Context(args)
    ctx.require_repo()
    bucket = release.Bucket(ctx, args.branch, args.head)
    print(f"Release {bucket.summary()}", flush=True)
    return ctx, bucket


def cmd_build(args):
    """The whole of job 2, in the order the artifacts depend on each other.

    Every step is attempted even when an earlier one failed, so one run tells the maintainer
    everything that is wrong rather than only the first thing. The exit code is what the
    release workflow reads: a nonzero here fails the release job, which is the point — a
    candidate with no release notes is worse than a candidate that did not publish.
    """
    ctx, bucket = open_bucket(args)
    frozen = release.frozen_files(ctx, bucket.name)
    if frozen and not args.force:
        print(f"Frozen by a human edit, and left alone: {', '.join(frozen)}", flush=True)
    commits.sweep(ctx, bucket.branch, bucket.name, bucket.range_start, bucket.head, force=False)
    failures = []
    for name, step in (
        ("review", release.build_review),
        ("notes", release.build_notes),
        ("announcement", release.build_announcement),
    ):
        try:
            step(ctx, bucket, force=args.force)
        except SystemExit as exc:
            if exc.code in (130, None):
                raise
            print(f"Could not produce the {name}: {exc}", flush=True)
            failures.append(name)
    if failures:
        print(f"\n{bucket.name}: failed to produce {', '.join(failures)}.", flush=True)
        return 1
    print(f"\n{bucket.name} is built: {store.bucket_dir(bucket.name)}", flush=True)
    return 0


def cmd_review(args):
    ctx, bucket = open_bucket(args)
    release.build_review(ctx, bucket, force=args.force)
    return 0


def cmd_notes(args):
    ctx, bucket = open_bucket(args)
    release.build_notes(ctx, bucket, force=args.force)
    return 0


def cmd_announce(args):
    ctx, bucket = open_bucket(args)
    release.build_announcement(ctx, bucket, force=args.force)
    return 0


def cmd_candidate(args):
    raise SystemExit("release candidate: not implemented")

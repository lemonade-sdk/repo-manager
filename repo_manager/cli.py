"""The command tree.

Commands are grouped by noun — `commit`, `pr`, `release`, `site`, `pi` — and every one of
them takes the same four global options after the subcommand, because every one of them
works on a state directory and, usually, a repository.

There are no read commands. Browsing what is stored is the web UI's job (`site serve`), or
`ls` and `jq` on the files.
"""

import argparse

from repo_manager import __version__
from repo_manager.commands import commit, pi, pr, release, site
from repo_manager.context import DEFAULT_REPO


def global_options():
    """The options every command shares, added after the subcommand rather than before it."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--state", default=".",
        help="The state directory (default: the current directory). A clone of the state repo in "
             "production, any directory for a scratch run.",
    )
    parser.add_argument(
        "--repo", default="",
        help=f"Repository to act on, as OWNER/REPO (default: {DEFAULT_REPO}; REPO_MANAGER_REPO "
             "also overrides it).",
    )
    parser.add_argument(
        "--checkout", default="",
        help="A clone of the tracked repo for git and diff operations. One is kept under the cache "
             "directory when this is not given.",
    )
    parser.add_argument(
        "--no-push", action="store_true",
        help="Commit to the state directory without pushing. Pushing also needs an `origin` remote.",
    )
    return parser


def build_parser():
    parser = argparse.ArgumentParser(
        prog="repo-manager",
        description="Commit reviews, PR triage, and release artifacts, stored as files.",
    )
    parser.add_argument("--version", action="version", version=f"repo-manager {__version__}")
    sub = parser.add_subparsers(dest="noun", required=True)
    shared = global_options()
    for module in (commit, pr, release, site, pi):
        module.add_parser(sub, shared)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    raise SystemExit(args.func(args) or 0)


if __name__ == "__main__":
    main()

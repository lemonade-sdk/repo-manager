"""Git reads against a clone of the repository being tracked.

`--checkout DIR` names that clone. When it is absent — a maintainer running one review on a
laptop — repo-manager keeps its own clone under the cache directory, so nothing has to be
set up before the first command works.
"""

import re
import subprocess
from pathlib import Path

from repo_manager import buckets
from repo_manager.pi import cache_dir


CHERRY_PICK = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")


class Checkout:
    def __init__(self, path, repo=""):
        self.path = Path(path).resolve()
        self.repo = repo

    def git(self, *args, check=True):
        result = subprocess.run(
            ["git", "-C", str(self.path), *args], text=True, capture_output=True, check=False
        )
        if check and result.returncode != 0:
            raise SystemExit(
                f"git {' '.join(args)} failed in {self.path}:\n"
                + (result.stderr or result.stdout or "").strip()
            )
        return result

    def out(self, *args, check=True):
        return (self.git(*args, check=check).stdout or "").strip()

    # --- fetching ----------------------------------------------------------------------

    def fetch(self, branch=""):
        args = ["fetch", "origin", "--tags", "--prune"]
        if branch:
            args.insert(2, branch)
        self.git(*args, check=False)

    def has_branch(self, branch):
        return self.git("rev-parse", "--verify", f"origin/{branch}", check=False).returncode == 0

    # --- reading -----------------------------------------------------------------------

    def resolve(self, ref):
        return self.out("rev-parse", f"{ref}^{{commit}}")

    def tags(self):
        listed = self.out("tag", "--list", "v*").splitlines()
        return buckets.sort_tags(tag.strip() for tag in listed if tag.strip())

    def commits(self, start, end):
        """Commits in `start..end`, oldest first. An empty start means every commit up to end."""
        rev = f"{start}..{end}" if start else end
        return [line.strip() for line in self.out("rev-list", "--reverse", rev).splitlines() if line.strip()]

    def commit_meta(self, sha):
        raw = self.out("show", "-s", "--format=%H%x00%cI%x00%an%x00%s%x00%B", sha)
        parts = raw.split("\x00")
        if len(parts) < 5:
            return {"sha": sha, "committed_at": "", "author_name": "", "subject": "", "body": ""}
        return {
            "sha": parts[0],
            "committed_at": parts[1],
            "author_name": parts[2],
            "subject": parts[3],
            "body": parts[4],
        }

    def commit_date(self, ref):
        return self.out("show", "-s", "--format=%cI", ref, check=False)

    def merge_base(self, a, b):
        return self.out("merge-base", a, b, check=False)

    def cherry_pick_source(self, sha):
        """The commit this one was cherry-picked from, if the trailer says so.

        `git cherry-pick -x` appends the trailer, and picking a pick appends another, so the
        first trailer names the original. A hotfix on a release branch is a pick of a commit
        that merged on main, and it is that original commit that GitHub can name a PR and an
        author for — the pick itself belongs to no PR at all.
        """
        message = self.commit_meta(sha).get("body", "")
        match = CHERRY_PICK.search(message)
        if not match:
            return ""
        source = match.group(1)
        resolved = self.git("rev-parse", "--verify", f"{source}^{{commit}}", check=False)
        return (resolved.stdout or "").strip() if resolved.returncode == 0 else source


def default_checkout_path(repo):
    return cache_dir() / "checkouts" / repo.replace("/", "__")


def open_checkout(repo, path="", fetch=True):
    """The clone to read git from: the one that was passed, or one repo-manager keeps."""
    if path:
        checkout = Checkout(path, repo)
        if not (checkout.path / ".git").exists():
            raise SystemExit(f"--checkout {checkout.path} is not a git checkout.")
    else:
        target = default_checkout_path(repo)
        if not (target / ".git").exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            print(f"Cloning {repo} into {target}", flush=True)
            result = subprocess.run(
                ["gh", "repo", "clone", repo, str(target)], text=True, capture_output=True, check=False
            )
            if result.returncode != 0:
                raise SystemExit(
                    f"Could not clone {repo} into {target}:\n"
                    + (result.stderr or result.stdout or "").strip()
                )
        checkout = Checkout(target, repo)
    if fetch:
        checkout.fetch()
    return checkout


def branch_cut_date(checkout, bucket, range_start_tag=""):
    """When this bucket's release branch was cut from main.

    Used to decide which `candidate` issues belong to the bucket: an issue filed before the
    branch existed is about an earlier release. Before the branch is cut there is nothing to
    test, so the bucket accumulating on main dates from its range start instead.
    """
    branch = buckets.branch_for_bucket(bucket)
    if checkout.has_branch(branch):
        base = checkout.merge_base(f"origin/{branch}", "origin/main")
        if base:
            return checkout.commit_date(base)
    return checkout.commit_date(range_start_tag) if range_start_tag else ""

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

    # How far back to look for a patch this range start already delivered. A duplicate is
    # always near the tag in time — it is the same fix landing on both the release branch and
    # main — so a couple of hundred commits is generous and keeps the walk cheap.
    SHIPPED_WINDOW = 200

    def commits(self, start, end):
        """Commits in `start..end`, oldest first — the ones this range actually ships.

        Two kinds of commit are in that range without shipping anything, and both would be
        reviewed, digested, and written up as new work:

        A **merge that carries no change of its own.** lemonade squash-merges, so a real merge
        commit is an integration artifact; the one that links a release tag back into main has
        an empty diff against both its parents. A merge that *does* differ from its first
        parent kept a conflict resolution, so it stays.

        A **patch that already shipped under another SHA.** A fix applied to the release branch
        and to main lands as two commits with one patch-id. Git sees no ancestry between them,
        so the tag does not exclude main's copy — and `--cherry-pick` cannot help once the tag
        has been merged into main, because then the symmetric difference has no left side to
        match against. Comparing patch-ids is what catches it.
        """
        rev = f"{start}..{end}" if start else end
        shas = [line.strip() for line in self.out("rev-list", "--reverse", rev).splitlines() if line.strip()]
        shas = [sha for sha in shas if not self.is_empty_merge(sha)]
        if not start or not shas:
            return shas
        shipped = set(self.patch_ids("--max-count", str(self.SHIPPED_WINDOW), start).values())
        if not shipped:
            return shas
        mine = self.patch_ids(rev)
        duplicates = {sha for sha, patch in mine.items() if patch in shipped}
        for sha in shas:
            if sha in duplicates:
                print(
                    f"Skipping {sha[:7]}: its patch already shipped in {start} under another SHA.",
                    flush=True,
                )
        return [sha for sha in shas if sha not in duplicates]

    def is_empty_merge(self, sha):
        """A merge whose tree matches its first parent contributed nothing to review."""
        parents = self.out("rev-list", "--parents", "-n", "1", sha, check=False).split()
        if len(parents) < 3:
            return False
        return not self.out("diff", "--name-only", f"{sha}^1", sha, check=False).strip()

    def patch_ids(self, *rev_args):
        """{sha: patch-id} for the non-merge commits named by these rev-list arguments.

        A patch-id is a hash of the diff, so the same change written on two branches has one
        id whatever its SHA or its commit message says.
        """
        log = self.git("log", "--no-merges", "-p", "--format=commit %H", *rev_args, check=False)
        if log.returncode != 0 or not log.stdout:
            return {}
        result = subprocess.run(
            ["git", "patch-id", "--stable"], input=log.stdout, text=True,
            capture_output=True, check=False,
        )
        ids = {}
        for line in (result.stdout or "").splitlines():
            parts = line.split()
            if len(parts) == 2:
                ids[parts[1]] = parts[0]
        return ids

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

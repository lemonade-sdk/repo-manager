import unittest

from repo_manager import gitops
from tests.helpers import TempDirCase, commit_file, git, init_repo


class CherryPickTrailer(TempDirCase):
    """A hotfix on a release branch is a cherry-pick of a commit that merged on main, and it
    is the original that GitHub can name a PR and an author for. The pick itself belongs to no
    PR at all, so following the trailer is what keeps a hotfix attributed to whoever wrote it."""

    def setUp(self):
        super().setUp()
        self.repo = init_repo(self.tmp / "repo")
        self.original = commit_file(self.repo, "a.txt", "one\n", "fix the thing (#3456)")
        self.checkout = gitops.Checkout(self.repo)

    def pick(self, source, message_suffix):
        (self.repo / "b.txt").write_text("two\n")
        git(self.repo, "add", "b.txt")
        git(self.repo, "commit", "-q", "-m",
            f"fix the thing (#3456)\n\n(cherry picked from commit {source}){message_suffix}")
        return git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def test_a_plain_commit_has_no_source(self):
        self.assertEqual(self.checkout.cherry_pick_source(self.original), "")

    def test_a_pick_resolves_to_the_original(self):
        picked = self.pick(self.original, "")
        self.assertEqual(self.checkout.cherry_pick_source(picked), self.original)

    def test_a_pick_of_a_pick_resolves_to_the_first_original(self):
        # `git cherry-pick -x` appends its trailer, so picking a pick leaves both lines and the
        # first one names the commit the chain started from.
        picked = self.pick(self.original, f"\n(cherry picked from commit {'f' * 40})")
        self.assertEqual(self.checkout.cherry_pick_source(picked), self.original)

    def test_an_abbreviated_trailer_is_expanded_to_the_full_sha(self):
        picked = self.pick(self.original[:10], "")
        self.assertEqual(self.checkout.cherry_pick_source(picked), self.original)

    def test_a_trailer_naming_an_unknown_commit_is_returned_as_written(self):
        unknown = "0" * 40
        picked = self.pick(unknown, "")
        self.assertEqual(self.checkout.cherry_pick_source(picked), unknown)


class ReadingCommits(TempDirCase):
    def setUp(self):
        super().setUp()
        self.repo = init_repo(self.tmp / "repo")
        self.first = commit_file(self.repo, "a.txt", "one\n", "first")
        self.second = commit_file(self.repo, "b.txt", "two\n", "second")
        self.checkout = gitops.Checkout(self.repo)

    def test_commit_meta_carries_what_a_review_file_needs(self):
        meta = self.checkout.commit_meta(self.second)
        self.assertEqual(meta["sha"], self.second)
        self.assertEqual(meta["subject"], "second")
        self.assertTrue(meta["committed_at"].startswith("20"))

    def test_commits_are_listed_oldest_first(self):
        self.assertEqual(self.checkout.commits(self.first, self.second), [self.second])
        self.assertEqual(self.checkout.commits("", self.second), [self.first, self.second])

    def test_tags_are_sorted_and_filtered(self):
        git(self.repo, "tag", "v11.9.0", self.first)
        git(self.repo, "tag", "v2026.38.1", self.second)
        git(self.repo, "tag", "candidate-v2026.38.2", self.second)
        self.assertEqual(self.checkout.tags(), ["v11.9.0", "v2026.38.1"])


if __name__ == "__main__":
    unittest.main()

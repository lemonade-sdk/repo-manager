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


class WhatTheRangeActuallyShips(TempDirCase):
    """The shape that bit us on v11.9.0 → v2026.39.

    lemonade fixed a bug on the release branch and on main independently, tagged the release
    branch, then merged the tag into main so `git describe` works. Main therefore holds both
    copies of one patch, and the tag excludes only its own — so a plain `tag..main` range hands
    the reviewer a change users already have, plus a merge that ships nothing.
    """

    def setUp(self):
        super().setUp()
        self.repo = init_repo(self.tmp / "repo")
        commit_file(self.repo, "b.txt", "broken\n", "first")
        git(self.repo, "branch", "release")
        # The fix, on the release branch, tagged.
        git(self.repo, "checkout", "-q", "release")
        self.tagged = commit_file(self.repo, "b.txt", "fixed\n", "fix the thing (#3386) (#3477)")
        git(self.repo, "tag", "v1.0.0")
        # Main moved on, so when the same fix is written again there it has a different parent
        # and a different SHA — which is exactly why git cannot tell the two copies apart by
        # ancestry, and exactly what made this worth catching.
        git(self.repo, "checkout", "-q", "main")
        commit_file(self.repo, "a.txt", "meanwhile\n", "unrelated work on main")
        self.twin = commit_file(self.repo, "b.txt", "fixed\n", "fix the thing (#3386) (#3477)")
        # Linking the tag into main's history, which is what defeats --cherry-pick.
        git(self.repo, "merge", "-q", "--no-ff", "-m", "chore: link v1.0.0 into main history", "v1.0.0")
        self.merge = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.real = commit_file(self.repo, "c.txt", "new\n", "a genuinely new change")
        self.checkout = gitops.Checkout(self.repo)

    def test_the_setup_really_is_the_one_that_bit_us(self):
        self.assertNotEqual(self.tagged, self.twin)
        # The tag is reachable from main, so the symmetric difference has no left side and
        # `--cherry-pick` has nothing to match the duplicate against.
        self.assertEqual(
            git(self.repo, "merge-base", "--is-ancestor", self.tagged, "main", check=False).returncode, 0
        )
        plain = git(self.repo, "rev-list", "v1.0.0..main").stdout.split()
        self.assertIn(self.twin, plain)
        self.assertIn(self.merge, plain)

    def test_a_patch_that_already_shipped_is_not_reviewed_again(self):
        self.assertNotIn(self.twin, self.checkout.commits("v1.0.0", "main"))

    def test_a_merge_that_ships_nothing_is_not_reviewed(self):
        self.assertNotIn(self.merge, self.checkout.commits("v1.0.0", "main"))

    def test_the_genuinely_new_commit_survives(self):
        shipped = self.checkout.commits("v1.0.0", "main")
        self.assertIn(self.real, shipped)
        self.assertNotIn(self.twin, shipped)
        self.assertNotIn(self.merge, shipped)

    def test_a_merge_that_kept_a_conflict_resolution_is_reviewed(self):
        # A merge whose tree differs from its first parent decided something, so it stays.
        git(self.repo, "checkout", "-q", "-b", "topic", self.real)
        commit_file(self.repo, "c.txt", "theirs\n", "topic edit")
        git(self.repo, "checkout", "-q", "main")
        commit_file(self.repo, "c.txt", "ours\n", "main edit")
        git(self.repo, "merge", "-q", "--no-ff", "topic", check=False)
        (self.repo / "c.txt").write_text("resolved\n")
        git(self.repo, "add", "c.txt")
        git(self.repo, "commit", "-q", "--no-edit")
        resolved = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertFalse(self.checkout.is_empty_merge(resolved))
        self.assertIn(resolved, self.checkout.commits("v1.0.0", "main"))

    def test_the_same_message_with_a_different_patch_is_still_reviewed(self):
        # Only the diff decides. A commit that reuses a shipped subject but changes something
        # else is new work.
        again = commit_file(self.repo, "b.txt", "fixed differently\n", "fix the thing (#3386) (#3477)")
        self.assertIn(again, self.checkout.commits("v1.0.0", "main"))

    def test_patch_ids_pair_a_sha_with_the_hash_of_its_diff(self):
        ids = self.checkout.patch_ids("v1.0.0..main")
        self.assertIn(self.twin, ids)
        tagged = self.checkout.patch_ids("--max-count", "5", "v1.0.0")
        self.assertEqual(ids[self.twin], tagged[self.tagged])

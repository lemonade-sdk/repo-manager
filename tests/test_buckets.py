import datetime
import unittest

from repo_manager import buckets, gitops
from tests.helpers import TempDirCase, commit_file, init_repo


# Stands in for lemonade's tools/version.py. It is deliberately not lemonade's clock, so a
# test can only pass by asking the checkout, never by agreeing with a copy kept here.
STUB_VERSION_TOOL = """
def upcoming_release_week(now):
    return now.year, now.isocalendar().week + 1
"""


class CheckoutCase(TempDirCase):
    def setUp(self):
        super().setUp()
        self.repo = init_repo(self.tmp / "repo")
        commit_file(self.repo, "README.md", "lemonade\n")
        self.checkout = gitops.Checkout(self.repo)

    def add_version_tool(self, source=STUB_VERSION_TOOL):
        commit_file(self.repo, buckets.VERSION_TOOL, source)


class UpcomingReleaseWeek(CheckoutCase):
    def test_the_week_comes_from_the_checkouts_version_tool(self):
        self.add_version_tool()
        now = datetime.datetime(2026, 9, 15, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(buckets.upcoming_release_week(self.checkout, now), (2026, 39))

    def test_a_checkout_without_the_version_tool_is_an_error(self):
        now = datetime.datetime(2026, 9, 15, 12, 0, tzinfo=datetime.timezone.utc)
        with self.assertRaises(SystemExit) as raised:
            buckets.upcoming_release_week(self.checkout, now)
        self.assertIn(buckets.VERSION_TOOL, str(raised.exception))


class BucketForBranch(CheckoutCase):
    def setUp(self):
        super().setUp()
        self.now = datetime.datetime(2026, 9, 15, 12, 0, tzinfo=datetime.timezone.utc)

    def test_a_release_branch_names_its_own_bucket(self):
        self.assertEqual(buckets.bucket_for_branch("release-v2026.38"), "v2026.38")

    def test_a_single_digit_week_is_read_as_written(self):
        self.assertEqual(buckets.bucket_for_branch("release-v2026.3"), "v2026.3")

    def test_main_asks_the_checkouts_version_tool(self):
        self.add_version_tool()
        self.assertEqual(buckets.bucket_for_branch("main", self.checkout, self.now), "v2026.39")

    def test_a_topic_branch_is_treated_like_main(self):
        self.add_version_tool()
        self.assertEqual(
            buckets.bucket_for_branch("jfowers/experiment", self.checkout, self.now), "v2026.39"
        )

    def test_main_without_a_checkout_is_an_error(self):
        with self.assertRaises(ValueError):
            buckets.bucket_for_branch("main", None, self.now)

    def test_a_bucket_names_its_branch_back(self):
        self.assertEqual(buckets.branch_for_bucket("v2026.38"), "release-v2026.38")
    def test_the_old_scheme_sorts_before_the_new_one(self):
        self.assertLess(buckets.version_parts("v11.9.0"), buckets.version_parts("v2026.38"))

    def test_a_bucket_sorts_before_its_own_tags(self):
        self.assertLess(buckets.version_parts("v2026.38"), buckets.version_parts("v2026.38.2"))

    def test_patch_numbers_sort_numerically_not_as_text(self):
        self.assertLess(buckets.version_parts("v2026.38.2"), buckets.version_parts("v2026.38.10"))

    def test_sorting_a_mixed_tag_list(self):
        tags = ["v2026.38.1", "v11.9.0", "v2026.37.0", "v11.10.0"]
        self.assertEqual(
            buckets.sort_tags(tags), ["v11.9.0", "v11.10.0", "v2026.37.0", "v2026.38.1"]
        )

    def test_candidate_tags_are_not_version_tags(self):
        self.assertFalse(buckets.is_version_tag("candidate-v2026.38.2"))
        self.assertFalse(buckets.is_version_tag("v2026"))
        self.assertTrue(buckets.is_version_tag("v2026.38.2"))

    def test_sorting_drops_everything_that_is_not_a_version_tag(self):
        self.assertEqual(
            buckets.sort_tags(["candidate-v2026.38.2", "v2026.38.1", "nightly"]), ["v2026.38.1"]
        )


class RangeStart(unittest.TestCase):
    TAGS = ["v11.8.0", "v11.9.0", "v2026.37.0", "v2026.37.1", "v2026.38.1", "candidate-v2026.38.2"]

    def test_the_newest_earlier_tag_is_the_range_start(self):
        self.assertEqual(buckets.range_start(self.TAGS, "v2026.38"), "v2026.37.1")

    def test_a_buckets_own_stable_tag_never_truncates_its_range(self):
        # This is the hotfix case: v2026.38 already shipped v2026.38.1, and the range still
        # covers everything the bucket ships rather than only what came after that tag.
        self.assertEqual(buckets.range_start(self.TAGS, "v2026.38"), "v2026.37.1")

    def test_the_scheme_change_is_crossed_cleanly(self):
        self.assertEqual(buckets.range_start(["v11.8.0", "v11.9.0"], "v2026.39"), "v11.9.0")

    def test_no_earlier_tag_means_no_range_start(self):
        self.assertEqual(buckets.range_start(["v2026.38.1"], "v2026.38"), "")

    def test_stable_tags_in_bucket_is_the_hotfix_signal(self):
        self.assertEqual(
            buckets.stable_tags_in_bucket(self.TAGS, "v2026.38"), ["v2026.38.1"]
        )
        self.assertEqual(buckets.stable_tags_in_bucket(self.TAGS, "v2026.39"), [])


if __name__ == "__main__":
    unittest.main()
